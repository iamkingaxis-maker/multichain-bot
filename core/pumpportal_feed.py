"""PUMPPORTAL FEED (2026-06-11) — free realtime firehose, keyless.

wss://pumpportal.fun/api/data (official public data API) gives three taps that
map directly onto the machine:

  subscribeAccountTrade  - our watchlist wallets' trades, ALREADY PARSED,
                           pushed in realtime at ZERO RPC cost. This is a
                           second independent eye for smart_follow: faster
                           than the poll sweep, cheaper than getTransaction
                           parsing (the standing RPC bottleneck).
  subscribeMigration     - pump.fun -> Raydium graduations as they happen
                           (the birth moment of the fresh-grad momentum pond).
  subscribeNewToken      - the launch firehose: exact age-zero timestamps
                           (feeds attention recency + the young pipeline).

Coverage caveat: pump.fun ecosystem only — AUGMENTS the RPC/DexScreener taps,
does not replace them. Dedupe vs the RPC sweep is by tx signature through the
strategy's _seen sets, so an event seen here is skipped there and vice versa.

Fail-soft: reconnect with backoff; the poll sweep continues regardless.
Env: PUMPPORTAL_FEED=on|off (default on).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

WS_URL = "wss://pumpportal.fun/api/data"
_DATA_DIR = os.environ.get("DATA_DIR", ".")
_MIGRATIONS_LOG = os.path.join(_DATA_DIR, "migrations.jsonl")


def feed_enabled() -> bool:
    return os.environ.get("PUMPPORTAL_FEED", "on").strip().lower() not in ("off", "0", "false")


class PumpPortalFeed:
    def __init__(self, wallets=None, strategy=None, attention=None, sensor=None):
        self.wallets = list(wallets or [])
        self.strategy = strategy          # SmartMoneyFollowStrategy (ingest target)
        self.attention = attention        # AttentionFeed (launch recency)
        self.sensor = sensor              # MetaSensor (panel day-meta reader)
        self.launches: dict = {}          # mint -> launch_ts (rolling)
        self.stats = {"account_trades": 0, "migrations": 0, "new_tokens": 0,
                      "reconnects": 0, "connected": False}

    async def _handle(self, d: dict):
        tx_type = d.get("txType")
        mint = d.get("mint")
        if tx_type in ("buy", "sell") and d.get("traderPublicKey"):
            self.stats["account_trades"] += 1
            if self.strategy is not None:
                try:
                    await self.strategy.ingest_realtime_trade(
                        wallet=d["traderPublicKey"], mint=mint, side=tx_type,
                        sol=float(d.get("solAmount") or 0),
                        ts=int(time.time()), signature=d.get("signature"))
                except Exception as e:
                    logger.warning(f"[PumpPortal] ingest error: {e}")
            # Meta sensor (2026-06-12): same parsed trade feeds the panel
            # day-meta reader. Measure-only; sync + never raises. launch_ts
            # from our own launch registry (subscribeNewToken) gives the
            # episode's token-age-at-entry for POND tuning at zero RPC cost.
            if self.sensor is not None:
                self.sensor.ingest(wallet=d["traderPublicKey"], mint=mint or "",
                                   side=tx_type, sol=float(d.get("solAmount") or 0),
                                   ts=time.time(),
                                   launch_ts=self.launches.get((mint or "").lower()))
        elif tx_type == "create" or (d.get("name") and mint and "marketCapSol" in d):
            self.stats["new_tokens"] += 1
            self.launches[(mint or "").lower()] = time.time()
            if len(self.launches) > 20000:   # rolling cap
                cutoff = time.time() - 48 * 3600
                self.launches = {m: t for m, t in self.launches.items() if t >= cutoff}
        elif d.get("pool") or tx_type == "migrate":
            self.stats["migrations"] += 1
            try:
                with open(_MIGRATIONS_LOG, "a") as f:
                    f.write(json.dumps({"ts": time.time(), "mint": mint,
                                        "pool": d.get("pool")}) + "\n")
            except Exception:
                pass

    async def run(self):
        if not feed_enabled():
            logger.info("[PumpPortal] disabled (PUMPPORTAL_FEED=off)")
            return
        import aiohttp
        backoff = 5
        while True:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.ws_connect(WS_URL, heartbeat=20) as ws:
                        await ws.send_json({"method": "subscribeNewToken"})
                        await ws.send_json({"method": "subscribeMigration"})
                        if self.wallets:
                            await ws.send_json({"method": "subscribeAccountTrade",
                                                "keys": self.wallets})
                        self.stats["connected"] = True
                        logger.info(f"[PumpPortal] connected: launches + migrations + "
                                    f"{len(self.wallets)} wallet trade streams (free, 0 RPC)")
                        backoff = 5
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                break
                            try:
                                d = json.loads(msg.data)
                            except Exception:
                                continue
                            if isinstance(d, dict) and "message" not in d:
                                await self._handle(d)
            except Exception as e:
                logger.info(f"[PumpPortal] feed error ({type(e).__name__}: {e}) — "
                            f"reconnect in {backoff}s")
            self.stats["connected"] = False
            self.stats["reconnects"] += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    def launch_candidates(self, min_age_s: int = 900, max_age_s: int = 86400,
                          cap: int = 25) -> list:
        """Discovery lane (2026-06-12, AxiS: 68% of a green-in-red wallet's
        tokens never reached our scanner — every feed is popularity-ranked,
        so our universe is the POST-trending tape). Returns up to `cap`
        unoffered launch mints aged [min_age_s, max_age_s] (min age lets the
        LP form). Offered mints are marked so each is enriched once; tokens
        that later trend re-enter via the normal feeds."""
        if not hasattr(self, "_offered"):
            self._offered: set = set()
        now = time.time()
        out = []
        for m, ts in self.launches.items():
            if m in self._offered:
                continue
            age = now - ts
            if min_age_s <= age <= max_age_s:
                out.append((ts, m))
        out.sort()   # oldest first (closest to falling out of window)
        picked = [m for _, m in out[:cap]]
        self._offered.update(picked)
        if len(self._offered) > 30000:
            self._offered = set(list(self._offered)[-15000:])
        return picked

    def launch_age_min(self, mint: str):
        t = self.launches.get((mint or "").lower())
        return round((time.time() - t) / 60, 1) if t else None

    def summary(self) -> dict:
        return {"enabled": feed_enabled(), **self.stats,
                "launches_tracked": len(self.launches),
                "wallet_streams": len(self.wallets)}
