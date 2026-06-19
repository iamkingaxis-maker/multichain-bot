#!/usr/bin/env python
"""
FILL-SPEED P&L COMPARISON  (read-only counterfactual analysis)
==============================================================

THE QUESTION
------------
Does filling FASTER actually make more money, or is faster sometimes worse
(front-running a further drop)?

The fast-watch (when enforced) would fill ~3s after a dip (hot subset) or ~9s
(full universe); the OLD main sweep fills ~85s after the dip. For each real
closed dip-buy trade we reconstruct the entry price the bot WOULD have gotten
at each fill latency and compute realized P&L against the SAME exit, then
compare the ~3s / ~9s / ~85s tiers.

APPROACH (counterfactual, anchored on the REAL trade)
-----------------------------------------------------
- The ACTUAL entry = the main-sweep fill (~SWEEP_LATENCY_SECS, default 85s,
  after the dip). So actual entry_price IS the ~85s tier.
- The fast tiers entered EARLIER on the wall clock:
      lead_before_actual = SWEEP_SECS - tier_secs
  e.g. with sweep=85: the 3s tier entered 82s BEFORE the actual entry, the
  9s tier 76s before. We look up the token's price at
  (actual_entry_ts - lead_before_actual) from its price trajectory.
- P&L per tier = (exit - entry_tier) / entry_tier * 100. The exit is the SAME
  for every tier. To keep the price SCALE consistent (the API entry/exit prices
  are in a different unit than the chart close), every tier — including the ~85s
  actual — is priced off the SAME DexScreener trajectory: entry_85 = price at
  the entry timestamp, and the exit is reconstructed in trajectory units from
  the recorded actual pnl_pct (exit = entry_85 * (1 + pnl_pct/100)). The scale
  then cancels out of the tier-vs-tier comparison.

PRICE TRAJECTORY SOURCE
-----------------------
We reuse feeds.dexscreener_client.DexScreenerClient (no new dependency).
fetch_recent_trades() returns {kind, volume_usd, ts, maker} — it does NOT
expose tick price (the binary trade parser only pulls volume), so we use
fetch_1m() MINUTE BARS (close price per minute) as the trajectory.

RESOLUTION LIMIT (reported honestly): at minute resolution, a 3s vs 9s
difference usually falls in the SAME bar -> the ~3s and ~9s tiers may be
indistinguishable. The ~85s-vs-fast gap (often crosses a bar boundary) is the
measurable signal.

DATA-RETENTION LIMIT (reported honestly): the DexScreener chart endpoint
returns only the most-recent `cb` candles for a pool; it does NOT reach back
to entry times that are days old. Trades whose entry-time trajectory is no
longer retained are counted as "no usable trajectory" and excluded — the tool
reports that fraction so the result is never overstated.

This is a COUNTERFACTUAL: it ASSUMES the fast tier would have actually filled
at that earlier observed price (no extra slippage / no fill-failure modeled).

READ-ONLY: reads the trades API + the chart endpoint only. Never modifies bot
state, config, or money.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import statistics
import sys
from typing import Dict, List, Optional, Sequence, Tuple

# Allow running as a bare script (python scripts/fill_speed_pnl.py): make the
# repo root importable so `from feeds...` resolves.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ──────────────────────────────────────────────────────────────────────────────
# PURE / TESTABLE CORE
# ──────────────────────────────────────────────────────────────────────────────

def tier_entry_price(
    trajectory: Sequence[Tuple[float, float]],
    anchor_ts: float,
    lead_secs: float,
) -> Optional[float]:
    """Price the bot would have gotten filling `lead_secs` BEFORE `anchor_ts`.

    trajectory: list of (unix_ts_seconds, price), any order.
    Returns the price of the nearest trajectory point at or before
    (anchor_ts - lead_secs). None if no such point exists (no data that early).
    """
    if not trajectory:
        return None
    target = anchor_ts - lead_secs
    best_ts = None
    best_price = None
    for ts, price in trajectory:
        if ts <= target and (best_ts is None or ts > best_ts):
            best_ts = ts
            best_price = price
    return best_price


def tier_pnl_pct(
    entry_price: Optional[float],
    exit_price: Optional[float],
) -> Optional[float]:
    """(exit/entry - 1) * 100. None if entry is missing/<=0 or exit missing."""
    if entry_price is None or exit_price is None:
        return None
    if entry_price <= 0:
        return None
    return (exit_price - entry_price) / entry_price * 100.0


def summarize_tier(pnls: Sequence[Optional[float]]) -> Dict[str, Optional[float]]:
    """Aggregate a tier's per-trade pnl% list. Drops None entries.

    Returns dict: n, wr (% pnl>0), median, mean, sum.
    WR/median/mean are None when n == 0.
    """
    vals = [p for p in pnls if p is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0, "wr": None, "median": None, "mean": None, "sum": 0.0}
    wins = sum(1 for p in vals if p > 0)
    return {
        "n": n,
        "wr": 100.0 * wins / n,
        "median": statistics.median(vals),
        "mean": statistics.fmean(vals),
        "sum": sum(vals),
    }


def verdict_line(
    fast_median: Optional[float],
    slow_median: Optional[float],
    n: int,
    fast_label: str,
    slow_label: str,
    neutral_band_pp: float = 1.0,
) -> str:
    """One-line verdict comparing the fast tier vs the slow (actual) tier.

    HELPS  if fast_median - slow_median >  neutral_band_pp
    HURTS  if fast_median - slow_median < -neutral_band_pp
    NEUTRAL otherwise. NO DATA if either median is None or n == 0.
    """
    if fast_median is None or slow_median is None or n == 0:
        return (f"VERDICT: NO DATA — could not reconstruct trajectory for the "
                f"~{fast_label} tier (n={n}).")
    delta = fast_median - slow_median
    if delta > neutral_band_pp:
        word = "HELPS"
    elif delta < -neutral_band_pp:
        word = "HURTS"
    else:
        word = "NEUTRAL"
    return (
        f"VERDICT: FASTER {word}: ~{fast_label} tier median {fast_median:+.2f}% "
        f"vs ~{slow_label} {slow_median:+.2f}% "
        f"(delta {delta:+.2f}pp over n={n} trades)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# LIVE GLUE  (not unit-tested — hits network; exercised by the live run)
# ──────────────────────────────────────────────────────────────────────────────

TRADES_URL = ("https://gracious-inspiration-production.up.railway.app"
              "/api/trades?all=1")


def _parse_iso(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _load_trades(path: str) -> List[dict]:
    with open(path) as f:
        d = json.load(f)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("trades", "data", "results"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def _select_dip_sells(
    trades: List[dict], hours: int, strategy: str
) -> List[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for t in trades:
        if t.get("type") != "sell":
            continue
        if t.get("strategy") != strategy:
            continue
        if not (t.get("pair_address") and t.get("entry_price")
                and t.get("exit_price")):
            continue
        ts = _parse_iso(t.get("time"))
        if ts is None:
            continue
        if (now - ts).total_seconds() > hours * 3600:
            continue
        out.append(t)
    out.sort(key=lambda x: x.get("time", ""), reverse=True)
    return out


async def _trajectory_for(client, pair_address: str, limit_bars: int = 200):
    """Return [(unix_ts_s, close_price)] minute-bar trajectory, or []."""
    bars = await client.fetch_1m(pair_address, limit=limit_bars)
    return [(float(b.open_time), float(b.close)) for b in bars if b.close]


def _entry_ts_for_trade(t: dict, sweep_secs: float) -> Optional[float]:
    """Unix-seconds of the ACTUAL (main-sweep) entry.

    The trade `time` is the SELL time. entry_ts = sell_ts - hold_secs.
    Fall back to sell_ts if hold_secs is missing.
    """
    sell_ts = _parse_iso(t.get("time"))
    if sell_ts is None:
        return None
    hold = t.get("hold_secs")
    try:
        hold = float(hold) if hold is not None else 0.0
    except (TypeError, ValueError):
        hold = 0.0
    return sell_ts.timestamp() - hold


async def run(args) -> int:
    repo_json = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "_fillpnl.json")
    repo_json = os.path.abspath(repo_json)
    if not os.path.exists(repo_json):
        print(f"ERROR: {repo_json} not found. Fetch it first:\n"
              f'  curl -s "{TRADES_URL}" -o ./_fillpnl.json', file=sys.stderr)
        return 2

    trades = _load_trades(repo_json)
    sells = _select_dip_sells(trades, args.hours, args.strategy)
    sells = sells[: args.limit]

    tiers = [float(x) for x in args.tiers.split(",") if x.strip()]
    sweep = float(args.sweep_secs)

    print("=" * 72)
    print("FILL-SPEED P&L COMPARISON  (counterfactual, read-only)")
    print("=" * 72)
    print(f"strategy={args.strategy}  lookback={args.hours}h  "
          f"sweep_secs={sweep:.0f}  tiers={tiers}  limit={args.limit}")
    print(f"candidate closed trades in window: {len(sells)}")
    print("trajectory source: DexScreener fetch_1m (MINUTE bars, close price)")
    print("-" * 72)

    from feeds.dexscreener_client import DexScreenerClient
    client = DexScreenerClient()

    # tier_secs -> list of per-trade pnl%
    per_tier: Dict[float, List[Optional[float]]] = {ts: [] for ts in tiers}
    n_with_traj = 0
    n_attempted = 0

    for i, t in enumerate(sells):
        n_attempted += 1
        pair = t["pair_address"]
        # The recorded actual P&L of the real trade (main-sweep fill). We
        # reconstruct the exit IN TRAJECTORY UNITS from this, so every tier is
        # priced off ONE consistent series (the DexScreener minute bars) and the
        # absolute price SCALE cancels out of the tier comparison. (The API's
        # entry_price/exit_price are in a different unit scale than the chart
        # close — mixing them produced nonsense pnl%.)
        recorded_pnl = t.get("pnl_pct")
        try:
            recorded_pnl = float(recorded_pnl) if recorded_pnl is not None else None
        except (TypeError, ValueError):
            recorded_pnl = None
        entry_ts = _entry_ts_for_trade(t, sweep)
        try:
            traj = await _trajectory_for(client, pair)
        except Exception as e:
            print(f"  [{i+1}/{len(sells)}] {pair[:12]} trajectory error: {e}")
            traj = []

        # Actual (~sweep) entry price from the SAME trajectory (lead = 0).
        entry_85_traj = (tier_entry_price(traj, entry_ts, 0.0)
                         if (traj and entry_ts is not None) else None)
        # Exit in trajectory units, derived from the recorded actual P&L.
        exit_traj = (entry_85_traj * (1.0 + recorded_pnl / 100.0)
                     if (entry_85_traj is not None and recorded_pnl is not None)
                     else None)

        has_pre_entry = False
        if traj and entry_ts is not None and entry_85_traj is not None \
                and exit_traj is not None:
            # usable only if a bar exists at/before the EARLIEST tier target
            earliest_target = entry_ts - (sweep - min(tiers))
            if any(ts <= earliest_target for ts, _ in traj):
                has_pre_entry = True
        if has_pre_entry:
            n_with_traj += 1

        for tier_secs in tiers:
            if not has_pre_entry:
                per_tier[tier_secs].append(None)
                continue
            if abs(tier_secs - sweep) < 1e-9:
                # The actual main-sweep fill: trajectory price at entry_ts.
                per_tier[tier_secs].append(
                    tier_pnl_pct(entry_85_traj, exit_traj))
                continue
            lead_before_actual = sweep - tier_secs
            ep = tier_entry_price(traj, entry_ts, lead_before_actual)
            per_tier[tier_secs].append(tier_pnl_pct(ep, exit_traj))

        # pace chart fetches
        if i < len(sells) - 1:
            await asyncio.sleep(args.pace)

    # ── output table ──────────────────────────────────────────────────────────
    print()
    print(f"DATA COVERAGE: {n_with_traj}/{n_attempted} trades had a usable "
          f"pre-entry trajectory "
          f"({(100.0*n_with_traj/n_attempted) if n_attempted else 0:.0f}%).")
    print()
    hdr = f"{'tier':>10} | {'n':>4} | {'WR%':>6} | {'median%':>8} | {'mean%':>8} | {'sum%':>9}"
    print(hdr)
    print("-" * len(hdr))
    summaries: Dict[float, Dict[str, Optional[float]]] = {}
    for tier_secs in tiers:
        s = summarize_tier(per_tier[tier_secs])
        summaries[tier_secs] = s
        label = f"~{tier_secs:.0f}s" + (
            " (actual)" if abs(tier_secs - sweep) < 1e-9 else "")
        def fmt(v, suf=""):
            return f"{v:{suf}}" if v is not None else "   --"
        wr = f"{s['wr']:.1f}" if s['wr'] is not None else "--"
        med = f"{s['median']:+.2f}" if s['median'] is not None else "--"
        mean = f"{s['mean']:+.2f}" if s['mean'] is not None else "--"
        print(f"{label:>10} | {s['n']:>4} | {wr:>6} | {med:>8} | "
              f"{mean:>8} | {s['sum']:>+9.2f}")

    # ── verdict ───────────────────────────────────────────────────────────────
    print()
    fast = min(tiers)
    slow = sweep if sweep in summaries else max(tiers)
    fast_med = summaries[fast]["median"]
    slow_med = summaries.get(slow, {}).get("median")
    n_compare = min(
        summaries[fast]["n"],
        summaries.get(slow, {}).get("n", 0),
    )
    print(verdict_line(
        fast_med, slow_med, n_compare,
        fast_label=f"{fast:.0f}s", slow_label=f"{slow:.0f}s"))

    # ── caveats ───────────────────────────────────────────────────────────────
    print()
    print("CAVEATS:")
    print(f"  - COUNTERFACTUAL: assumes the fast tier would have filled at the "
          f"observed earlier price (no extra slippage / fill-fail modeled).")
    print(f"  - RESOLUTION: minute bars -> a 3s vs 9s gap usually lands in the "
          f"SAME bar, so those two fast tiers may be INDISTINGUISHABLE; the "
          f"~85s-vs-fast gap is the measurable signal.")
    print(f"  - RETENTION: the chart endpoint only returns recent bars; trades "
          f"whose entry time is no longer retained are excluded "
          f"({n_attempted - n_with_traj} of {n_attempted} here).")
    if n_with_traj < 30:
        print(f"  - LOW n: only {n_with_traj} trades had usable trajectory — "
              f"treat the verdict as directional, not conclusive.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fill-speed P&L comparison (counterfactual, read-only).")
    p.add_argument("--hours", type=int, default=24,
                   help="lookback window in hours (default 24)")
    p.add_argument("--sweep-secs", type=float, default=85.0,
                   help="main-sweep fill latency after the dip (default 85)")
    p.add_argument("--tiers", type=str, default="3,9,85",
                   help="comma fill latencies to compare (default 3,9,85)")
    p.add_argument("--limit", type=int, default=40,
                   help="max trades to analyze (default 40)")
    p.add_argument("--strategy", type=str, default="dip_buy",
                   help="strategy to filter sells (default dip_buy)")
    p.add_argument("--pace", type=float, default=1.5,
                   help="seconds between chart fetches (default 1.5)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
