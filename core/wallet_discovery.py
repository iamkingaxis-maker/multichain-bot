"""On-bot continuous wallet discovery (2026-06-10) — Railway-resident, 24/7.

Replaces the PC-dependent local runs of scripts/discover_wallets_dexscreener.py
with an hourly pass that runs alongside the fleet:

  1. Pull current Solana runners from GeckoTerminal (trending + h6-volume; a
     LIGHT 4-call pass, paced — the free GT tier throttles bursts hard).
  2. Harvest EARLY buyers from each runner's DexScreener trade log (the same
     feeds.dexscreener_client the bot already uses in production).
  3. Persist hourly snapshots to DATA_DIR/wallet_discovery_log.json (same
     format as the offline script -> offline analysis stays compatible).
  4. Cross-day recurrence (the protocol's only validator: one snapshot cannot
     rank wallets) is computed on demand for /api/wallet-discovery.

New candidates still need the diversity/selection scorer + forward-tracking
before joining the follow set — this finds the catch, not the quality.

Env: WALLET_DISCOVERY_ENABLED=on|off (default on),
     WALLET_DISCOVERY_INTERVAL_MIN (default 60).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

MAX_AGE_H = 24.0          # runner young enough that early buyers are in-window
MIN_LIQ = 15_000
MIN_PUMP_H6 = 20.0
EARLY_FRAC = 0.35         # earliest 35% of harvested buys = "early"
MIN_BUY_USD = 15.0
TRADE_LIMIT = 200
MAX_RUNNERS_PER_PASS = 12  # bounds DexScreener calls + pass duration
LOG_RETENTION_DAYS = 14


class WalletDiscovery:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or os.environ.get("DATA_DIR", ".")
        self.enabled = os.environ.get(
            "WALLET_DISCOVERY_ENABLED", "on").strip().lower() != "off"
        try:
            self.interval_sec = float(os.environ.get(
                "WALLET_DISCOVERY_INTERVAL_MIN", "60")) * 60
        except Exception:
            self.interval_sec = 3600.0
        self.log_path = os.path.join(self.data_dir, "wallet_discovery_log.json")
        self._gt_session = None   # lazy curl_cffi session (sync, used via to_thread)
        self.last_pass_at = None
        self.last_pass_stats: dict = {}
        self.passes_run = 0

    # ── persistence ────────────────────────────────────────────────────────
    def _load_log(self) -> dict:
        try:
            with open(self.log_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_log(self, log: dict):
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=LOG_RETENTION_DAYS)).strftime("%Y-%m-%dT%H")
        log = {k: v for k, v in log.items() if k >= cutoff}
        tmp = self.log_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(log, f)
            os.replace(tmp, self.log_path)
        except Exception as e:
            logger.warning(f"[WalletDiscovery] log save failed: {e}")

    # ── GT fetch (sync curl_cffi bridged off-loop) ───────────────────────────
    def _gt_get(self, url: str):
        try:
            from curl_cffi import requests as cr
            if self._gt_session is None:
                self._gt_session = cr.Session(impersonate="chrome")
            r = self._gt_session.get(url, timeout=30)
            if r.status_code == 200:
                return r.json()
            self._last_gt_error = f"HTTP {r.status_code}"
            return None
        except Exception as e:
            # pass #1 on 2026-06-10 returned 0 runners with zero diagnostics —
            # never fail silently again
            self._last_gt_error = f"{type(e).__name__}: {e}"
            return None

    async def _find_runners(self) -> list[dict]:
        urls = [
            "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1",
            "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=2",
            "https://api.geckoterminal.com/api/v2/networks/solana/pools?sort=h6_volume_usd_desc&page=1",
            "https://api.geckoterminal.com/api/v2/networks/solana/pools?sort=h6_volume_usd_desc&page=2",
        ]
        now = datetime.now(timezone.utc)
        seen: dict = {}
        gt_ok = gt_fail = 0
        for u in urls:
            j = await asyncio.to_thread(self._gt_get, u)
            if not j:
                gt_fail += 1
                await asyncio.sleep(4)
                continue
            gt_ok += 1
            for it in j.get("data", []):
                a = it.get("attributes", {})
                ca = a.get("pool_created_at")
                if not ca:
                    continue
                try:
                    age_h = (now - datetime.fromisoformat(
                        ca.replace("Z", "+00:00"))).total_seconds() / 3600
                except Exception:
                    continue
                pcp = a.get("price_change_percentage") or {}
                try:
                    h6 = float(pcp.get("h6") or 0)
                except Exception:
                    h6 = 0.0
                pair = it.get("id", "").replace("solana_", "")
                try:
                    tok = (((it.get("relationships") or {}).get("base_token") or {})
                           .get("data") or {}).get("id", "").replace("solana_", "")
                except Exception:
                    tok = ""
                seen[pair] = {"pair": pair, "token": tok or pair,
                              "name": a.get("name", ""), "age_h": age_h,
                              "liq": float(a.get("reserve_in_usd") or 0), "h6": h6}
            await asyncio.sleep(4)   # free-GT pacing
        runners = [c for c in seen.values()
                   if c["age_h"] <= MAX_AGE_H and c["liq"] >= MIN_LIQ
                   and c["h6"] >= MIN_PUMP_H6]
        runners.sort(key=lambda x: -x["h6"])
        if gt_fail:
            logger.warning(f"[WalletDiscovery] GT: {gt_ok} ok / {gt_fail} failed "
                           f"(last: {getattr(self, '_last_gt_error', '?')}) | "
                           f"pools seen={len(seen)} runners={len(runners)}")
        else:
            logger.info(f"[WalletDiscovery] GT ok: pools={len(seen)} runners={len(runners)}")
        return runners[:MAX_RUNNERS_PER_PASS]

    async def _harvest(self, runners: list[dict]) -> dict:
        from feeds.dexscreener_client import DexScreenerClient
        client = DexScreenerClient()
        early_usd: dict = {}   # wallet -> early buy $ this pass
        for c in runners:
            try:
                trades = await client.fetch_recent_trades(c["pair"], limit=TRADE_LIMIT)
            except Exception:
                trades = []
            buys = [t for t in trades if t.get("kind") == "buy" and t.get("maker")
                    and float(t.get("volume_usd") or 0) >= MIN_BUY_USD]
            buys.sort(key=lambda t: t.get("ts", ""))
            for t in buys[:max(1, int(len(buys) * EARLY_FRAC))]:
                m = str(t["maker"])
                early_usd[m] = early_usd.get(m, 0.0) + float(t.get("volume_usd") or 0)
            await asyncio.sleep(0.4)
        return early_usd

    async def _one_pass(self):
        runners = await self._find_runners()
        early = await self._harvest(runners) if runners else {}
        hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        if early:
            log = self._load_log()
            merged = log.get(hour_key, {})
            for w, v in early.items():
                merged[w] = round(merged.get(w, 0.0) + v, 2)
            log[hour_key] = merged
            self._save_log(log)
        self.passes_run += 1
        self.last_pass_at = datetime.now(timezone.utc).isoformat()
        self.last_pass_stats = {"runners": len(runners), "early_buyers": len(early)}
        logger.info(f"[WalletDiscovery] pass #{self.passes_run}: "
                    f"runners={len(runners)} early_buyers={len(early)}")

    # ── recurrence (the validator) ───────────────────────────────────────────
    def recurrent(self, min_days: int = 2) -> list[dict]:
        """Wallets early-buying runners on >= min_days DISTINCT CT days."""
        log = self._load_log()
        by_wallet: dict = {}
        for hour_key, wallets in log.items():
            try:
                dt = datetime.strptime(hour_key, "%Y-%m-%dT%H")
                ct_day = (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
            except Exception:
                continue
            for w, usd in wallets.items():
                rec = by_wallet.setdefault(w, {"days": set(), "usd": 0.0})
                rec["days"].add(ct_day)
                rec["usd"] += usd
        out = [{"wallet": w, "days_seen": len(r["days"]),
                "early_usd": round(r["usd"], 2)}
               for w, r in by_wallet.items() if len(r["days"]) >= min_days]
        out.sort(key=lambda x: (-x["days_seen"], -x["early_usd"]))
        return out

    def summary(self) -> dict:
        return {
            "enabled": self.enabled,
            "passes_run": self.passes_run,
            "last_pass_at": self.last_pass_at,
            "last_pass_stats": self.last_pass_stats,
            "interval_min": self.interval_sec / 60,
            "cross_day_recurrent": self.recurrent(min_days=2)[:40],
        }

    async def run(self):
        if not self.enabled:
            logger.info("[WalletDiscovery] disabled (WALLET_DISCOVERY_ENABLED=off)")
            return
        logger.info(f"[WalletDiscovery] starting: every {self.interval_sec/60:.0f}min, "
                    f"log -> {self.log_path}")
        while True:
            try:
                await self._one_pass()
            except Exception as e:
                logger.warning(f"[WalletDiscovery] pass error: {e}")
            await asyncio.sleep(self.interval_sec)
