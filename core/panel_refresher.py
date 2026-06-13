"""Autonomous sensor-panel refresher (Design A2, AxiS 2026-06-13).

The chameleon can only WEAR a meta composed of >= 2 ACTIVE panel wallets with no
single wallet supplying > MAX_TOP_SHARE of its episodes. Panel wallets ROTATE OUT
and go dormant (V21GW8 -> 1 episode/24h), so without replenishment archetypes fall
below consensus one by one and the chameleon idles toward "only conviction
qualifies". This loop keeps every wearable archetype stocked with FRESH ACTIVE
wallets so the rotate-to-the-meta thesis stays alive:

  1. MEASURE thin archetypes — n_wallets < 2 OR top_wallet_share > cap (the real
     blockers; a single dominant wallet fails consensus even at n_wallets >= 2).
  2. CANDIDATES — recurring runner early-buyers from the already-running
     core/wallet_discovery feed (GeckoTerminal runners -> DexScreener trade-log
     early buyers, Railway-resident). Recurrence (>= min_days) is the validator.
  3. CLASSIFY each candidate off the event loop (self-contained async RPC
     trade-map -> hold/WR/loss geometry -> archetype), gated by diversity (reject
     single-token MM bots) and a min closed-trip count.
  4. STOCK thin archetypes: add qualifiers to the LIVE sensor.panel (the RPC sweep
     re-reads it every 120s -> ingests within ~2min, no restart) AND to a DATA_DIR
     overlay merged on top of the repo panel by meta_sensor.load_panel
     (deploy-amnesia-safe).
  5. RETIRE refresher-added wallets that never produced episodes (self-cleaning).

Paper-only impact (sensor -> chameleon, a $50 paper bot). Bounded RPC. Never
raises into the loop. See reference_golive_audit / wallet_decode intel.

Env: PANEL_REFRESH_ENABLED (default on), PANEL_REFRESH_INTERVAL_SECS (5400=1.5h),
     PANEL_TARGET unused (consensus is the bar), PANEL_DECODE_BUDGET (5),
     PANEL_DORMANT_GRACE_SECS (86400), PANEL_MIN_DISTINCT_TOKENS (8),
     PANEL_TRADE_SIGS (60).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_OVERLAY = os.path.join(_DATA_DIR, "sensor_panel_runtime.json")

# Match meta_chameleon's consensus constants (the chameleon's wearability bar).
TOP_SHARE_CAP = 0.75
MIN_WALLETS = 2
WEARABLE = ("conviction", "thesis_holder", "time_boxer", "surgical", "swing", "lottery")
_STABLE = {"So11111111111111111111111111111111111111112",
           "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}

_last_run = 0.0


# ── config helpers ───────────────────────────────────────────────────────────
def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    return _flag("PANEL_REFRESH_ENABLED", "1")


# ── overlay (DATA_DIR), merged on top of the repo panel in meta_sensor.load_panel ──
def load_overlay() -> dict:
    try:
        with open(_OVERLAY) as f:
            return json.load(f)
    except Exception:
        return {}


def save_overlay(d: dict) -> None:
    try:
        tmp = _OVERLAY + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, _OVERLAY)
    except Exception as e:
        logger.warning(f"[PanelRefresh] overlay save failed: {e}")


# ── classifier: decoded geometry -> archetype label (priority-ordered) ─────────
def classify_archetype(median_hold_min: float, wr: float, win_med_pct: float,
                       loss_med_pct: float, n_closed: int, n_tokens: int) -> Optional[str]:
    """Map a wallet's realized geometry to a chameleon archetype, or None if it's
    too thin / a single-token MM bot to trust. Mirrors the manual decode labels:
    thesis_holder (multi-day holds), time_boxer (minutes + tight losses), lottery
    (low-WR big-tail), surgical (high-WR disciplined scalp), swing (mid-hold),
    conviction (catch-all). Diversity gate rejects MM/churn bots."""
    if n_closed < 4 or n_tokens < _i("PANEL_MIN_DISTINCT_TOKENS", 8):
        return None
    lm = abs(loss_med_pct or 0.0)
    if median_hold_min >= 1440:                       # >= 1 day held
        return "thesis_holder"
    if median_hold_min <= 12 and lm <= 15:            # minutes + tight losses
        return "time_boxer"
    if wr <= 0.45 and (win_med_pct or 0.0) >= 80:     # low WR, big tail wins
        return "lottery"
    if wr >= 0.65 and median_hold_min <= 600 and lm <= 20:   # disciplined high-WR
        return "surgical"
    if median_hold_min <= 600:
        return "swing"
    return "conviction"


# ── self-contained async RPC trade-map (Railway-native; no scripts dependency) ─
async def _trade_map(addr: str, session, rpc_url: str, sigs: int = 60) -> dict:
    """Per-token {spent, recv, buys:[ts], sells:[ts]} from on-chain history.
    Async (non-blocking I/O), concurrency-capped. Mirrors wallet_decode.trade_map."""
    async def _rpc(method, params):
        try:
            import aiohttp
            async with session.post(rpc_url,
                                    json={"jsonrpc": "2.0", "id": 1, "method": method,
                                          "params": params},
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                return (await r.json()).get("result")
        except Exception:
            return None

    sl = await _rpc("getSignaturesForAddress", [addr, {"limit": sigs}]) or []
    tok: dict = {}
    sem = asyncio.Semaphore(4)

    async def _one(s):
        sig = s.get("signature")
        if not sig or s.get("err") or not s.get("blockTime"):
            return
        async with sem:
            tx = await _rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0,
                                                     "encoding": "jsonParsed"}])
        if not tx or not tx.get("meta"):
            return
        meta, bt = tx["meta"], s["blockTime"]
        pre = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in (meta.get("preTokenBalances") or []) if b.get("owner") == addr}
        post = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in (meta.get("postTokenBalances") or []) if b.get("owner") == addr}
        try:
            keys = [k if isinstance(k, str) else k.get("pubkey")
                    for k in tx["transaction"]["message"]["accountKeys"]]
            wi = keys.index(addr)
            sol_d = (meta["postBalances"][wi] - meta["preBalances"][wi]) / 1e9
        except Exception:
            return
        deltas = {m: post.get(m, 0) - pre.get(m, 0)
                  for m in set(list(pre) + list(post)) if m not in _STABLE}
        deltas = {m: d for m, d in deltas.items() if abs(d) > 0}
        if not deltas:
            return
        mint = max(deltas, key=lambda m: abs(deltas[m]))
        d = deltas[mint]
        r = tok.setdefault(mint, {"spent": 0.0, "recv": 0.0, "buys": [], "sells": []})
        if d > 0 and sol_d < 0:
            r["buys"].append(bt); r["spent"] += -sol_d
        elif d < 0 and sol_d > 0:
            r["sells"].append(bt); r["recv"] += sol_d

    await asyncio.gather(*[_one(s) for s in sl])
    return tok


def _metrics(tok: dict) -> Optional[dict]:
    holds, rets, closed = [], [], 0
    for _m, r in tok.items():
        if not r["buys"] or not r["sells"]:
            continue
        holds.append(max(0, max(r["sells"]) - min(r["buys"])))
        if r["spent"]:
            rets.append(r["recv"] / r["spent"] - 1.0)
            closed += 1
    if closed < 4 or not rets:
        return None
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    return {
        "median_hold_min": statistics.median(holds) / 60.0 if holds else 0.0,
        "wr": len(wins) / len(rets),
        "win_med_pct": statistics.median(wins) * 100 if wins else 0.0,
        "loss_med_pct": statistics.median(losses) * 100 if losses else 0.0,
        "n_closed": closed,
        "n_tokens": len(tok),
    }


async def _classify_candidate(addr: str, session, rpc_url: str) -> Optional[dict]:
    try:
        mx = _metrics(await _trade_map(addr, session, rpc_url, sigs=_i("PANEL_TRADE_SIGS", 60)))
        if not mx:
            return None
        arch = classify_archetype(mx["median_hold_min"], mx["wr"], mx["win_med_pct"],
                                  mx["loss_med_pct"], mx["n_closed"], mx["n_tokens"])
        return {"archetype": arch, **mx} if arch else None
    except Exception:
        return None


# ── thin-archetype detection (the chameleon's wearability bar) ─────────────────
def thin_archetypes(sensor, now: float) -> dict:
    """{archetype: reason} for wearable archetypes BELOW consensus — n_wallets < 2
    OR one wallet > TOP_SHARE_CAP of episodes (the real blocker we observed: a lone
    dominant time_boxer fails top_share even with bursty 2nd/3rd wallets present)."""
    thin = {}
    for arch in WEARABLE:
        try:
            geo = sensor.archetype_geometry(arch, now, min_n=1)
        except Exception:
            geo = None
        if not geo:
            thin[arch] = "no_episodes"
        elif geo.get("n_wallets", 0) < MIN_WALLETS:
            thin[arch] = f"n_wallets={geo.get('n_wallets', 0)}<{MIN_WALLETS}"
        elif geo.get("top_wallet_share", 1.0) > TOP_SHARE_CAP:
            thin[arch] = f"top_share={geo.get('top_wallet_share'):.2f}>{TOP_SHARE_CAP}"
    return thin


def _prune_dead_additions(sensor, overlay: dict, active_prefixes: set, now: float) -> int:
    """Drop refresher-added wallets that produced NO episodes within the grace
    window (they didn't pan out) — keeps the overlay/panel from accumulating dead
    weight. Never touches curated (non-refresher) wallets."""
    grace = _f("PANEL_DORMANT_GRACE_SECS", 86400.0)
    dropped = 0
    for addr in list(overlay.keys()):
        meta = overlay.get(addr) or {}
        if meta.get("source") != "panel-refresher":
            continue
        age = now - float(meta.get("added_at") or now)
        if age >= grace and addr[:8] not in active_prefixes:
            overlay.pop(addr, None)
            sensor.panel.pop(addr, None)
            dropped += 1
    return dropped


async def maybe_refresh_panel(sensor, discovery, rpc_url: str, now: float) -> None:
    """Periodic hook (called every ~10min from main; self-gates to the refresh
    interval). Stocks thin archetypes from the runner-buyer discovery feed. Never
    raises into the loop."""
    global _last_run
    if not enabled() or sensor is None:
        return
    if now - _last_run < _f("PANEL_REFRESH_INTERVAL_SECS", 5400.0):
        return
    _last_run = now
    try:
        overlay = load_overlay()
        try:
            active_prefixes = set((sensor.scoreboard(now).get("wallet_episodes_24h") or {}).keys())
        except Exception:
            active_prefixes = set()
        dropped = _prune_dead_additions(sensor, overlay, active_prefixes, now)

        thin = thin_archetypes(sensor, now)
        if not thin:
            if dropped:
                save_overlay(overlay)
            logger.info("[PanelRefresh] all wearable archetypes >= consensus; "
                        "nothing to fill (pruned %d dead adds)", dropped)
            return

        # Candidate pool: recurring runner early-buyers (recurrence = the validator),
        # widened to single-day if recurrent is thin. Skip wallets already paneled.
        cands = []
        if discovery is not None:
            try:
                seen = set(sensor.panel)
                for md in (2, 1):
                    for c in discovery.recurrent(min_days=md):
                        w = c.get("wallet")
                        if w and w not in seen:
                            cands.append(w); seen.add(w)
                    if len(cands) >= 4:
                        break
            except Exception as e:
                logger.warning(f"[PanelRefresh] candidate pull failed: {e}")

        if not cands:
            logger.info(f"[PanelRefresh] thin={thin} but discovery feed has no fresh "
                        f"candidates yet (it harvests hourly) — waiting")
            if dropped:
                save_overlay(overlay)
            return

        budget = _i("PANEL_DECODE_BUDGET", 5)
        added = []
        import aiohttp
        async with aiohttp.ClientSession() as session:
            for addr in cands[:budget]:
                res = await _classify_candidate(addr, session, rpc_url)
                if not res or res["archetype"] not in thin:
                    continue
                entry = {"archetype": res["archetype"], "status": "active",
                         "source": "panel-refresher", "added_at": now,
                         "labeled": (f"auto A2: hold~{res['median_hold_min']:.0f}m "
                                     f"wr={res['wr']:.0%} loss~{res['loss_med_pct']:.0f}% "
                                     f"n={res['n_closed']} tok={res['n_tokens']}")}
                sensor.panel[addr] = entry      # live -> RPC sweep ingests within ~120s
                overlay[addr] = entry           # durable across restarts
                added.append((addr[:8], res["archetype"]))

        if added or dropped:
            save_overlay(overlay)
        logger.info("[PanelRefresh] thin=%s | added=%s | pruned=%d | pool=%d",
                    thin, added, dropped, len(cands))
    except Exception as e:
        logger.error(f"[PanelRefresh] error: {e}")
