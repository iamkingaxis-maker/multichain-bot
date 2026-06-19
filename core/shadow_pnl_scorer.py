"""In-bot LOW-CADENCE background scorer for the shadow-P&L family.

Operationalizes the TWO shadow-P&L scorers IN-PROCESS (they need the Railway
/data volume, which a remote agent can't see). Every SHADOW_PNL_SCORE_EVERY_SECS
(default 6h) it runs BOTH scorers OFF THE EVENT LOOP via asyncio.to_thread and
writes the small precomputed JSONs the unified /api/filter-shadow endpoint reads:

  * forward-candle  : scripts.audit_filter_shadow_log.compute_filter_pnl
                      -> DATA_DIR/filter_shadow_pnl.json
                      (egress-bounded: DEDUP-BY-PAIR + per-filter sample + pacing)
  * trade-join      : scripts.shadow_gate_pnl.compute_gate_pnl
                      -> DATA_DIR/shadow_gate_pnl.json
                      (trades read OFF-LOOP from the local trades dump; this loop
                       does NOT trigger the big trades_multi.json reparse)

CONTRACT:
  * Flag-gated: SHADOW_PNL_SCORER_MODE (on|off, default 'on'). 'off' = no spawn.
  * FAIL-OPEN: a scorer error logs + retries next cycle; NEVER crashes the loop.
  * Off-loop: the forward-candle fetch is async but paced/dedup'd/sampled; the
    trades file read + the gate join run inside asyncio.to_thread.
  * Runs ONCE shortly after boot (short initial delay) so data appears without
    waiting a full cycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_INITIAL_DELAY_SECS = 90.0


def _enabled() -> bool:
    return os.environ.get("SHADOW_PNL_SCORER_MODE", "on").strip().lower() != "off"


def _data_dir() -> str:
    return os.environ.get("DATA_DIR", "/data")


def _interval_secs() -> float:
    try:
        return max(60.0, float(os.environ.get("SHADOW_PNL_SCORE_EVERY_SECS", 21600)))
    except (TypeError, ValueError):
        return 21600.0


def _load_trades_file() -> list:
    """Blocking read of the local trades dump (run inside to_thread). Reads a
    SMALL pre-dumped JSON if present; does NOT reparse trades_multi.json on the
    loop. Returns [] on any problem (fail-open)."""
    dd = _data_dir()
    # Prefer an explicit scorer-trades dump; fall back to common dump names.
    candidates = [
        os.environ.get("SHADOW_PNL_TRADES_PATH", ""),
        os.path.join(dd, "shadow_pnl_trades.json"),
        os.path.join(dd, "trades_dump.json"),
    ]
    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                d = json.load(f)
            if isinstance(d, list):
                return d
            if isinstance(d, dict):
                for k in ("trades", "data", "results"):
                    if isinstance(d.get(k), list):
                        return d[k]
        except Exception:
            continue
    return []


async def _run_forward_candle_scorer() -> None:
    """Forward-candle per-filter P&L -> filter_shadow_pnl.json. Egress-bounded."""
    from scripts.audit_filter_shadow_log import compute_filter_pnl
    from feeds.dexscreener_client import DexScreenerClient

    dd = _data_dir()
    log_path = os.path.join(dd, "filter_shadow_log.jsonl")
    out_path = os.path.join(dd, "filter_shadow_pnl.json")
    if not os.path.exists(log_path):
        logger.info("[shadow-pnl] no filter_shadow_log.jsonl yet — skipping forward scorer")
        return

    # Load records OFF-LOOP (the .jsonl can be large).
    def _load() -> list:
        recs = []
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        # bound: only score the most-recent slice
        max_n = 4000
        return recs[-max_n:] if len(recs) > max_n else recs

    records = await asyncio.to_thread(_load)
    if not records:
        return
    client = DexScreenerClient()
    # The fetch itself is async + paced + dedup-by-pair + sampled inside
    # compute_filter_pnl; this is the only network in the scorer.
    result = await compute_filter_pnl(
        records=records,
        client=client,
        min_forward_min=int(float(os.environ.get("SHADOW_PNL_MIN_FORWARD_MIN", 30))),
        sample_per_filter=int(float(os.environ.get("SHADOW_PNL_SAMPLE_PER_FILTER", 200))),
        pace_secs=float(os.environ.get("SHADOW_PNL_PACE_SECS", 0.4)),
        out_path=out_path,
    )
    logger.info("[shadow-pnl] forward-candle scored %d filters -> %s",
                len(result), out_path)


async def _run_trade_join_scorer() -> None:
    """Trade-join per-gate P&L -> shadow_gate_pnl.json. All IO off-loop."""
    from scripts.shadow_gate_pnl import compute_gate_pnl

    dd = _data_dir()
    events_path = os.path.join(dd, "shadow_gate_events.jsonl")
    out_path = os.path.join(dd, "shadow_gate_pnl.json")
    if not os.path.exists(events_path):
        logger.info("[shadow-pnl] no shadow_gate_events.jsonl yet — skipping gate scorer")
        return

    def _work() -> dict:
        trades = _load_trades_file()
        return compute_gate_pnl(events_path, trades, max_skew=600.0, out_path=out_path)

    result = await asyncio.to_thread(_work)
    logger.info("[shadow-pnl] trade-join scored %d gates -> %s",
                len(result or {}), out_path)


async def _run_once() -> None:
    """Run BOTH scorers; each is independently fail-open."""
    try:
        await _run_forward_candle_scorer()
    except Exception as e:
        logger.warning("[shadow-pnl] forward-candle scorer error (retry next cycle): %s", e)
    try:
        await _run_trade_join_scorer()
    except Exception as e:
        logger.warning("[shadow-pnl] trade-join scorer error (retry next cycle): %s", e)


async def run() -> None:
    """Low-cadence loop. No-op when SHADOW_PNL_SCORER_MODE=off. Never crashes."""
    if not _enabled():
        logger.info("[shadow-pnl] SHADOW_PNL_SCORER_MODE=off — scorer disabled")
        return
    interval = _interval_secs()
    logger.info("[shadow-pnl] started: every %.0fs (initial delay %.0fs)",
                interval, _INITIAL_DELAY_SECS)
    # Short initial delay so data appears soon after boot (not after a full cycle).
    try:
        await asyncio.sleep(_INITIAL_DELAY_SECS)
    except asyncio.CancelledError:
        return
    while True:
        await _run_once()
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return


def maybe_spawn() -> None:
    """Spawn the loop once (best-effort, no-op when disabled). Mirrors the
    scanner's other _maybe_spawn_* helpers — never raises into run()."""
    try:
        if not _enabled():
            logger.info("[shadow-pnl] SHADOW_PNL_SCORER_MODE=off — not spawned")
            return
        asyncio.create_task(run())
        logger.info("[shadow-pnl] spawned")
    except Exception as e:
        logger.error("[shadow-pnl] spawn error: %s", e)
