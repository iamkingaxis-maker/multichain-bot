"""Held-out sim: post-TP1 trail-pp comparison on last 7d dip_buy trades.

For each closed dip_buy trade where TP1 would have fired (peak >= +3%),
fetch DexScreener 1m candles spanning the hold window and re-simulate
the trail at multiple pp settings.

Assumption set (approximate the live exit ladder):
  - TP1 fires when running peak_pnl_pct first hits >= +3% → sells 50%.
    Realized P&L on TP1 leg = +3% (minus typical slippage 1.1%).
  - Post-TP1 trail fires when candle LOW retraces >= trail_pp from
    running peak. Realized = peak * (1 - trail_pp/100) - slippage.
  - Hard stop at -4% (any candle low below entry * 0.96 with no TP1
    yet → 100% sold at -4% - slippage).
  - If neither trail nor stop fires by exit_time, position is closed
    at actual exit_price (= what really happened).

This isolates the trail variable. Stop/TP1 thresholds match live config.
Slippage modeled at 1.1% per leg (median observed in actual sells).
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))

from feeds.dexscreener_client import DexScreenerClient

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"

# Live config (utils/config.py)
TP1_PCT = 3.0           # dip_tp1_pct
TP1_SELL = 0.50         # dip_tp1_sell
TP2_PCT = 5.0           # dip_tp2_pct  — also sells some, but for simplicity
                        #   we lump TP1+TP2 portions together since both fire
                        #   before the trail kicks in
TP2_SELL = 0.50         # of remainder
STOP_PCT = -4.0         # dip_stop_pct (negative)
SLIPPAGE_PCT = 1.1      # realized_slippage_pct median


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00") if "Z" in s else s
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fetch_trades() -> list[dict]:
    url = f"{DASHBOARD_URL}/api/trades?limit=1000"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read())
    return data if isinstance(data, list) else data.get("trades", [])


def pair_buy_sell(trades: list[dict]) -> list[dict]:
    """Pair each buy with its corresponding sell(s). Returns list of
    {entry_time, exit_time, entry_price, exit_price, pair_address,
     peak_pnl_pct, peak_pnl_at_secs, hold_secs, pnl_pct, token, reason}.
    Multi-leg sells get aggregate metrics from the LAST leg.
    """
    # Group by (token, entry_price) — each buy creates a position with
    # unique entry_price (paper sim is deterministic).
    by_key: dict[tuple, list[dict]] = {}
    for t in trades:
        # Both 'dip_buy' and 'scanner' strategies route through the dip
        # TP/trail ladder (DipScanner-bought positions are labeled scanner).
        # Filter by exit reason mentioning "Dip " to catch ladder-managed trades.
        if t.get("strategy") not in ("dip_buy", "scanner"):
            continue
        key = (t.get("token"), round(t.get("entry_price", 0), 10))
        by_key.setdefault(key, []).append(t)
    out: list[dict] = []
    for key, events in by_key.items():
        buys = [e for e in events if e.get("type") == "buy"]
        sells = [e for e in events if e.get("type") == "sell"]
        if not buys or not sells:
            continue
        buy = buys[0]
        sells.sort(key=lambda x: x.get("time", ""))
        last = sells[-1]
        # Sum pnl across all legs for realized $ — but pnl_pct in /api is
        # already the position-aggregate-as-of-that-leg, so the LAST sell's
        # pnl_pct is the final realized pct of the WHOLE position.
        out.append({
            "token": key[0],
            "entry_price": buy.get("entry_price"),
            "entry_time": buy.get("time"),
            "exit_time": last.get("time"),
            "exit_price_actual": last.get("exit_price"),
            "pair_address": last.get("pair_address") or buy.get("pair_address"),
            "peak_pnl_pct": last.get("peak_pnl_pct", 0) or 0,
            "peak_pnl_at_secs": last.get("peak_pnl_at_secs", 0) or 0,
            "hold_secs": last.get("hold_secs", 0) or 0,
            "actual_pnl_pct": last.get("pnl_pct", 0) or 0,
            "reason": last.get("reason", "?"),
            "n_legs": len(sells),
        })
    return out


def simulate_one(trade: dict, candles: list[Any], trail_pp: float) -> dict:
    """Walk candles in candle-relative pct space (price-denomination-invariant).

    DexScreener candles are SOL-denominated; trade entry_price is USD. We
    anchor to the candle closest to entry_time as "candle_entry" and
    compute all % moves relative to it. Calibrate to USD by matching
    the candle-peak-pct to the trade's recorded peak_pnl_pct.
    """
    # Filter candles to the hold window
    entry_dt = parse_iso(trade["entry_time"])
    exit_dt = parse_iso(trade["exit_time"])
    if not entry_dt or not exit_dt:
        return {"realized_pnl_pct": 0.0, "skipped": "bad_times"}
    entry_ts = entry_dt.timestamp()
    exit_ts = exit_dt.timestamp() + 60  # 1-min buffer past exit

    window = [c for c in candles if entry_ts <= c.open_time <= exit_ts]
    if len(window) < 2:
        return {"realized_pnl_pct": 0.0, "skipped": "no_candles"}

    # Anchor: closest candle to entry_ts
    candle_entry = min(window, key=lambda c: abs(c.open_time - entry_ts))
    anchor = float(candle_entry.close)
    if anchor <= 0:
        return {"realized_pnl_pct": 0.0, "skipped": "bad_anchor"}

    # Calibrate SOL-pct → USD-pct by matching candle-peak to recorded peak_pnl
    window_max_high = max(c.high for c in window)
    candle_peak_pct = (window_max_high / anchor - 1) * 100
    recorded_peak_pct = float(trade["peak_pnl_pct"] or 0)
    if candle_peak_pct > 0.1 and recorded_peak_pct > 0.1:
        calibrate = recorded_peak_pct / candle_peak_pct
    else:
        calibrate = 1.0

    # Helper: pct-from-anchor for a price, calibrated to USD
    def _pct(price: float) -> float:
        return (price / anchor - 1) * 100 * calibrate

    # Tracking state (in pct space)
    peak_pct = 0.0
    tp1_hit = False
    tp2_hit = False
    realized_legs: list[tuple[float, float]] = []
    portion_remaining = 1.0

    def _add_leg(portion: float, exit_pct: float):
        nonlocal portion_remaining
        actual_portion = min(portion, portion_remaining)
        if actual_portion <= 0:
            return
        realized_legs.append((actual_portion, exit_pct - SLIPPAGE_PCT))
        portion_remaining -= actual_portion

    for c in window:
        if portion_remaining <= 0.001:
            break
        hi_pct = _pct(c.high)
        lo_pct = _pct(c.low)
        # Update peak
        if hi_pct > peak_pct:
            peak_pct = hi_pct

        # Hard stop (only pre-TP1)
        if not tp1_hit and lo_pct <= STOP_PCT:
            _add_leg(portion_remaining, STOP_PCT)
            break

        # TP1 fires when high crosses +3%
        if not tp1_hit and hi_pct >= TP1_PCT:
            tp1_hit = True
            _add_leg(TP1_SELL, TP1_PCT)

        # TP2 fires when high crosses +5%
        if tp1_hit and not tp2_hit and hi_pct >= TP2_PCT:
            tp2_hit = True
            _add_leg(TP2_SELL * (1 - TP1_SELL), TP2_PCT)

        # Post-TP1 trail
        if tp1_hit and portion_remaining > 0:
            trail_target_pct = peak_pct - trail_pp
            if lo_pct <= trail_target_pct:
                _add_leg(portion_remaining, trail_target_pct)
                break

    # Position still open → close at actual final pnl
    if portion_remaining > 0.001:
        actual_pnl = float(trade["actual_pnl_pct"]) or 0
        realized_legs.append((portion_remaining, actual_pnl))

    total = sum(p * v for p, v in realized_legs)
    return {
        "realized_pnl_pct": total,
        "n_legs": len(realized_legs),
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "calibrate": calibrate,
        "candle_peak_pct": candle_peak_pct,
    }


async def fetch_candles_for_trade(client: DexScreenerClient, trade: dict) -> list[Any]:
    pair = trade.get("pair_address")
    if not pair:
        return []
    try:
        # 200 1m candles ≈ 3h+ coverage; positions rarely hold past 30m
        return await client.fetch_1m(pair, limit=200)
    except Exception as e:
        print(f"  [!] {trade['token']:<12} candle fetch err: {e}", file=sys.stderr)
        return []


async def main():
    print(f"Pulling trades from {DASHBOARD_URL}/api/trades ...")
    trades = fetch_trades()
    pairs = pair_buy_sell(trades)
    print(f"  {len(pairs)} unique dip_buy positions")

    # Filter to last 7d + meaningful trades
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 24 * 3600
    recent = []
    for p in pairs:
        dt = parse_iso(p["entry_time"])
        if dt and dt.timestamp() >= cutoff:
            recent.append(p)
    print(f"  {len(recent)} in last 7d")

    # Where the trail would matter: peak >= +3% (TP1 cohort)
    cohort = [p for p in recent if (p["peak_pnl_pct"] or 0) >= 3.0]
    print(f"  {len(cohort)} with peak_pnl_pct >= +3% (TP1-relevant cohort)")
    if not cohort:
        print("nothing to sim"); return

    client = DexScreenerClient()
    trail_settings = [1.0, 2.0, 2.5, 3.0, 4.0]
    results: dict[float, list[dict]] = {pp: [] for pp in trail_settings}
    actual_pnls: list[float] = []
    skipped = 0

    print(f"\nSimulating {len(cohort)} trades × {len(trail_settings)} trail settings ...")
    for i, trade in enumerate(cohort):
        candles = await fetch_candles_for_trade(client, trade)
        if not candles:
            skipped += 1
            continue
        actual_pnls.append(trade["actual_pnl_pct"])
        for pp in trail_settings:
            sim = simulate_one(trade, candles, trail_pp=pp)
            results[pp].append({
                "token": trade["token"],
                "actual": trade["actual_pnl_pct"],
                "sim": sim["realized_pnl_pct"],
                "peak": trade["peak_pnl_pct"],
            })
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(cohort)} done ...")
        await asyncio.sleep(0.05)  # gentle pacing

    print(f"\n=== Trail-PP sweep (n={len(actual_pnls)}, skipped={skipped}) ===")
    actual_mean = sum(actual_pnls) / len(actual_pnls) if actual_pnls else 0
    actual_total = sum(actual_pnls)
    print(f"  ACTUAL (live 1.0pp):    mean=+{actual_mean:.2f}% per trade   total={actual_total:+.1f}%")

    print(f"  {'Setting':<12} {'mean/trade':>11} {'total':>8} {'vs actual':>10} {'winners_kept':>13}")
    for pp in trail_settings:
        sims = [r["sim"] for r in results[pp]]
        mean = sum(sims) / len(sims) if sims else 0
        total = sum(sims)
        delta = mean - actual_mean
        big_wins_kept = sum(1 for r in results[pp]
                            if r["peak"] >= 5.0 and r["sim"] >= r["peak"] * 0.5)
        big_wins_total = sum(1 for r in results[pp] if r["peak"] >= 5.0)
        kr = (big_wins_kept / big_wins_total * 100) if big_wins_total else 0
        print(f"  {pp}pp        +{mean:>5.2f}%/t   {total:>+5.1f}%   {delta:>+5.2f}pp   "
              f"{big_wins_kept:>3}/{big_wins_total} ({kr:.0f}%)")

    # Per-trade detail at the best setting
    print(f"\nPer-trade detail (peak vs actual vs sims):")
    print(f"  {'Token':<12} {'peak':>6} {'actual':>7} {'1.0pp':>7} {'2.5pp':>7} {'3.0pp':>7} {'4.0pp':>7}")
    by_token = {r["token"] + str(r["peak"]): r for r in results[1.0]}
    pp_lookup = {pp: {r["token"] + str(r["peak"]): r["sim"] for r in results[pp]}
                 for pp in trail_settings}
    for k, r in sorted(by_token.items(), key=lambda kv: -kv[1]["peak"]):
        line = f"  {r['token']:<12} {r['peak']:>+5.1f}% {r['actual']:>+6.2f}% "
        for pp in [1.0, 2.5, 3.0, 4.0]:
            v = pp_lookup[pp].get(k, 0)
            line += f" {v:>+5.2f}%"
        print(line)


if __name__ == "__main__":
    asyncio.run(main())
