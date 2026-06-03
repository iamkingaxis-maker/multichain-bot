"""Unified exit backtester (#4.4, 2026-06-02).

Replays a BotConfig's FULL exit lifecycle over post-entry OHLCV using the PRODUCTION
PerBotPositionManager.tick() — so TP1/TP2/trail/hard_stop/pre_stop_bail/slow_bleed/
stall_exit/never_runner all behave EXACTLY as the live bots (DRY: one source of truth,
new levers included automatically). Replaces the ad-hoc per-strategy phantom sims with
one config-driven harness producing episode-blended P&L + held-out-by-token aggregates.

FIDELITY CAVEATS (honest):
- Candle replay ticks LOW -> HIGH -> CLOSE per candle (LOW first = stop-priority, since
  sub-candle ordering is unknowable). A TP that GAPS through its trigger books at the
  candle extreme (optimistic) — apply core/gap_capture.realistic_exit_pnl_pct for a
  realistic haircut on TP legs.
- It cannot reconstruct 1m bear-flip / sub-5m-candle paths.

DATA: needs post-entry OHLCV per entry. The live trade store does NOT persist it (the
phantom sims re-fetch from GeckoTerminal live). The deterministic, reproducible version
needs an OHLCV-after capture sidecar (persist candles on each trade) — that's the
follow-up that makes this harness fully self-contained. Until then, feed it candles from
any source (GT fetch, a captured sidecar, or synthetic for tests).
"""
from __future__ import annotations
import statistics as st
from collections import defaultdict


def _oldest_first(candles):
    """Normalize to oldest-first by timestamp."""
    if not candles or len(candles) < 2:
        return list(candles or [])
    try:
        if float(candles[0][0]) > float(candles[-1][0]):
            return list(reversed(candles))
    except (TypeError, ValueError, IndexError):
        pass
    return list(candles)


def replay_exits(config, entry_price, ohlcv_after, size_usd=100.0):
    """Replay one entry's exit lifecycle. ohlcv_after = [[ts_ms,o,h,l,c,v], ...].
    Returns {blended_pnl_pct, legs:[(frac,pnl_pct,kind)], n_legs, exit_reason}."""
    from core.per_bot_position_manager import PerBotPositionManager
    if not ohlcv_after or not entry_price or entry_price <= 0:
        return {"blended_pnl_pct": None, "legs": [], "n_legs": 0, "exit_reason": "no_data"}
    pm = PerBotPositionManager(config)
    tok = "BT"
    pm.open_position(tok, entry_price, size_usd, entry_time=0.0)
    candles = _oldest_first(ohlcv_after)
    t0 = float(candles[0][0])
    legs = []
    now = 0.0
    for k in candles:
        if len(k) < 5:
            continue
        try:
            ts = float(k[0]); high = float(k[2]); low = float(k[3]); close = float(k[4])
            vol = float(k[5]) if len(k) > 5 and k[5] is not None else None
        except (TypeError, ValueError):
            continue
        now = (ts - t0) / 1000.0
        for px in (low, high, close):     # LOW first = stop-priority intra-candle
            if pm.get_position(tok) is None:
                break
            if px <= 0:
                continue
            for d in pm.tick(tok, px, now, vol_m5_usd=vol):
                if pm.get_position(tok) is None:
                    break
                try:
                    r = pm.close_position(tok, exit_price=px, exit_time=now,
                                          reason=d.reason, sell_fraction=d.sell_fraction)
                except (KeyError, ValueError):
                    continue
                legs.append((r.sell_fraction, r.pnl_pct, d.kind))
        if pm.get_position(tok) is None:
            break
    # resolve any remainder at the last close (open-at-resolve)
    if pm.get_position(tok) is not None:
        last_close = float(candles[-1][4])
        if last_close > 0:
            try:
                r = pm.close_position(tok, exit_price=last_close, exit_time=now,
                                      reason="resolve", sell_fraction=1.0)
                legs.append((r.sell_fraction, r.pnl_pct, "RESOLVE"))
            except (KeyError, ValueError):
                pass
    blended = sum(f * p for f, p, _ in legs)
    return {"blended_pnl_pct": round(blended, 4), "legs": legs, "n_legs": len(legs),
            "exit_reason": legs[0][2] if legs else "none"}


def backtest(config, dataset, size_usd=100.0):
    """dataset = [{entry_price, ohlcv_after, token}, ...]. Returns episode-blended
    aggregates + a held-out-by-token (alternating-token group) train/test split."""
    rows = []
    for d in dataset:
        r = replay_exits(config, d.get("entry_price"), d.get("ohlcv_after"), size_usd)
        if r["blended_pnl_pct"] is None:
            continue
        rows.append({"token": d.get("token"), "pnl": r["blended_pnl_pct"], "exit": r["exit_reason"]})
    if not rows:
        return {"n": 0}
    pnls = [r["pnl"] for r in rows]
    byt = defaultdict(list)
    for r in rows:
        byt[r["token"]].append(r["pnl"])
    toks = sorted(byt)
    train = [p for i, t in enumerate(toks) if i % 2 == 0 for p in byt[t]]
    test = [p for i, t in enumerate(toks) if i % 2 == 1 for p in byt[t]]
    return {
        "n": len(rows),
        "n_tokens": len(byt),
        "wr_pct": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
        "mean_pnl_pct": round(st.mean(pnls), 3),
        "eqw_usd_per_tr": round(st.mean(pnls) / 100 * size_usd, 3),
        "heldout_train_mean": round(st.mean(train), 3) if train else None,
        "heldout_test_mean": round(st.mean(test), 3) if test else None,
    }
