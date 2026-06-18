"""
Cut-winner detector — READ-ONLY analysis.

Answers AxiS's fear: "does our stop/exit logic cut winners?" — i.e. did we exit
a position and then the token RECOVERED above our exit price, meaning we stopped
out of what would have been a winner.

Definition of a CUT WINNER: a closed trade whose token's price, in the N minutes
AFTER our exit, recovered by >= RECOVERY_PCT above our exit price.

This script touches NO bot/money path. It only:
  - reads /api/trades  (closed sells, with exit price/time/reason)
  - reads post-exit minute OHLC via the EXISTING DexScreenerClient.fetch_1m
    (io.dexscreener binary endpoint — same path the bot uses)

Pure, testable core (no network):
  - is_cut_winner(exit_price, post_exit_highs, recovery_pct)
  - classify_exit(reason)

Usage:
    PYTHONIOENCODING=utf-8 python scripts/cut_winner_detector.py \
        --hours 24 --recovery-pct 15 --window-min 30 --exit-kinds stop --limit 40
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DEFAULT_API = "https://gracious-inspiration-production.up.railway.app/api/trades?limit=2000"

# ── PURE LOGIC (no network — unit-tested) ────────────────────────────────────

# Exit-reason classification. Reason strings observed in /api/trades, e.g.:
#   "never_runner peak=0.00%<3.0 pnl=-13.61% hold=1min (floor)"  -> stop
#   "hard stop pnl=-15.93% <= -15.0"                              -> stop
#   "giveback floor pnl=-20.04% after peak +8.5% (gap-through ...)"-> stop
#   "fast-dump bail pnl=-15.05% <= -15.0 (any volume, ...)"       -> stop
#   "time-box exit 6min (pnl=-3.22%)"                             -> stop (forced clock exit)
#   "TP1 pnl=23.27% >= 20.0" / "TP2 ..."                          -> tp
#   "trail pnl=2.31% <= peak(9.47%) - 2.0pp"                      -> tp (realizes off a peak)
_STOP_MARKERS = (
    "never_runner",
    "hard stop",
    "hard_stop",
    "giveback",
    "fast-dump",
    "fast_dump",
    "stop",          # generic stop / dip stop
    "floor",
    "time-box",      # forced clock exit can cut a position that hadn't peaked
    "time_box",
    "timebox",
    "bail",
)
_TP_MARKERS = (
    "take_profit",
    "take-profit",
    "tp1",
    "tp2",
    "tp3",
    "tp ",
    "trail",         # trailing-stop realizes profit off a peak
)


def classify_exit(reason: Optional[str]) -> str:
    """Classify an exit reason into 'stop' | 'tp' | 'other'.

    stop = hard_stop / stop / never_runner / giveback / fast-dump / floor /
           time-box (forced exits that can cut a not-yet-peaked position).
    tp   = take_profit / TP1/TP2 / trail (exits that realize profit off a peak).
    """
    if not reason:
        return "other"
    r = reason.strip().lower()
    # Stop-family takes precedence: a "giveback floor" / "fast-dump bail" is a
    # hard cut even though it mentions a peak. TP/trail are profit realizations.
    for m in _STOP_MARKERS:
        if m in r:
            return "stop"
    for m in _TP_MARKERS:
        if m in r:
            return "tp"
    return "other"


def is_cut_winner(
    exit_price: float,
    post_exit_highs: List[Tuple[int, float]],
    recovery_pct: float,
) -> Tuple[bool, float, Optional[int]]:
    """Decide whether a closed trade was a CUT WINNER.

    Args:
        exit_price: our realized exit price.
        post_exit_highs: list of (minute_offset, high) bars AFTER the exit.
        recovery_pct: recovery threshold in percent (e.g. 15 = +15%).

    Returns:
        (is_cut, peak_recovery_pct, mins_to_peak)
        - is_cut: True iff max(high)/exit_price - 1 >= recovery_pct/100.
        - peak_recovery_pct: the best (highest) recovery % over the window.
        - mins_to_peak: minute_offset of the bar with the highest high
          (None when there is no usable data / bad exit_price).
    Guards exit_price <= 0 -> (False, 0.0, None).
    """
    if exit_price is None or exit_price <= 0 or not post_exit_highs:
        return (False, 0.0, None)

    best_high = None
    best_min: Optional[int] = None
    for minute_offset, high in post_exit_highs:
        if high is None:
            continue
        if best_high is None or high > best_high:
            best_high = high
            best_min = minute_offset

    if best_high is None:
        return (False, 0.0, None)

    # Round to kill float noise at the threshold (e.g. 1.15/1.0-1 -> 14.9999%).
    peak_recovery_pct = round((best_high / exit_price - 1.0) * 100.0, 6)
    is_cut = peak_recovery_pct >= recovery_pct
    return (is_cut, peak_recovery_pct, best_min)


# ── IMPURE: API + chart fetch ────────────────────────────────────────────────

def fetch_trades(api_url: str) -> List[Dict[str, Any]]:
    req = urllib.request.Request(api_url, headers={"User-Agent": "cut-winner-detector"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data if isinstance(data, list) else (data.get("trades") or [])


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def select_closed_trades(
    trades: List[Dict[str, Any]],
    hours: float,
    exit_kinds: str,
    since: Optional[datetime],
    now: datetime,
) -> List[Dict[str, Any]]:
    """Closed sells within the lookback window matching exit-kinds (+ optional since)."""
    cutoff = now.timestamp() - hours * 3600.0
    want_kind = exit_kinds.strip().lower()
    out: List[Dict[str, Any]] = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "sell" or t.get("exit_price") is None:
            continue
        dt = _parse_iso(t.get("time", ""))
        if dt is None or dt.timestamp() < cutoff:
            continue
        if since is not None and dt < since:
            continue
        kind = classify_exit(t.get("reason"))
        if want_kind != "all" and kind != want_kind:
            continue
        t = dict(t)
        t["_exit_kind"] = kind
        t["_exit_dt"] = dt
        out.append(t)
    # Most-recent first, then cap happens in main.
    out.sort(key=lambda x: x["_exit_dt"], reverse=True)
    return out


def fetch_post_exit_highs(
    client,
    pair_address: str,
    exit_dt: datetime,
    window_min: int,
) -> Optional[List[Tuple[int, float]]]:
    """Fetch minute OHLC and return [(minute_offset, high), ...] for bars whose
    open_time is in (exit_dt, exit_dt + window_min]. Returns None on no data so
    the caller can count it as 'no_data' (vs an empty list = data but no bars in
    window, which we also treat as no_data)."""
    import asyncio

    # fetch_1m returns the most-recent `limit` minute bars. We need bars covering
    # the post-exit window; pull generously so the window is included even if the
    # token kept trading after. Cap to a reasonable bar count.
    limit = max(window_min + 10, 60)
    try:
        candles = asyncio.run(client.fetch_1m(pair_address, limit=limit))
    except Exception:
        return None
    if not candles:
        return None

    exit_ts = exit_dt.timestamp()
    end_ts = exit_ts + window_min * 60.0
    highs: List[Tuple[int, float]] = []
    for c in candles:
        # open_time is epoch SECONDS (per dexscreener_client). Use bars that open
        # at/after the exit minute and within the window.
        if c.open_time < exit_ts - 60.0:  # allow the in-progress exit minute
            continue
        if c.open_time > end_ts:
            continue
        minute_offset = max(0, int(round((c.open_time - exit_ts) / 60.0)))
        highs.append((minute_offset, c.high))
    if not highs:
        return None
    highs.sort(key=lambda x: x[0])
    return highs


def main() -> int:
    p = argparse.ArgumentParser(description="Cut-winner detector (stop-then-recovered scan)")
    p.add_argument("--hours", type=float, default=24.0,
                   help="Lookback for closed trades (default 24)")
    p.add_argument("--recovery-pct", type=float, default=15.0,
                   help="Recovery threshold above exit price, percent (default 15)")
    p.add_argument("--window-min", type=int, default=30,
                   help="Post-exit window in minutes (default 30)")
    p.add_argument("--since", default=None,
                   help="ISO ts: only trades exited after this (e.g. 2026-06-18T23:26Z)")
    p.add_argument("--exit-kinds", default="stop",
                   help="'stop' (default), 'tp', 'other', or 'all'")
    p.add_argument("--limit", type=int, default=60,
                   help="Max trades to scan, gentle on chart API (default 60)")
    p.add_argument("--api-base", default=DEFAULT_API)
    p.add_argument("--pace", type=float, default=1.5,
                   help="Seconds to sleep between token chart fetches (default 1.5)")
    args = p.parse_args()

    since_dt = None
    if args.since:
        # accept trailing 'Z'
        since_dt = _parse_iso(args.since.replace("Z", "+00:00"))
        if since_dt is None:
            print(f"WARNING: could not parse --since {args.since!r}; ignoring.")

    now = datetime.now(timezone.utc)

    try:
        trades = fetch_trades(args.api_base)
    except Exception as e:
        print(f"BLOCKED: could not fetch /api/trades: {e}")
        return 1
    if not trades:
        print("No trades returned from API.")
        return 1

    selected = select_closed_trades(trades, args.hours, args.exit_kinds, since_dt, now)
    selected = selected[: args.limit]

    if not selected:
        print(f"No closed trades matched (hours={args.hours}, exit-kinds={args.exit_kinds}, "
              f"since={args.since}).")
        return 0

    from feeds.dexscreener_client import DexScreenerClient
    client = DexScreenerClient()

    cut_winners: List[Dict[str, Any]] = []
    no_data = 0
    scanned = 0
    all_recoveries: List[float] = []

    for i, t in enumerate(selected):
        pair = t.get("pair_address") or t.get("address")
        exit_price = t.get("exit_price")
        if not pair or exit_price is None:
            no_data += 1
            continue

        highs = fetch_post_exit_highs(client, pair, t["_exit_dt"], args.window_min)
        if highs is None:
            no_data += 1
        else:
            scanned += 1
            cut, peak_pct, mins = is_cut_winner(exit_price, highs, args.recovery_pct)
            all_recoveries.append(peak_pct)
            if cut:
                cut_winners.append({
                    "token": t.get("token") or "?",
                    "bot": t.get("bot_id") or "?",
                    "reason": (t.get("reason") or "")[:48],
                    "exit_price": exit_price,
                    "peak_recovery_pct": peak_pct,
                    "mins_to_peak": mins,
                    "pnl_pct": t.get("pnl_pct"),
                })

        # Gentle pacing between tokens.
        if i < len(selected) - 1:
            time.sleep(args.pace)

    # ── report ──
    print("=" * 78)
    print("CUT-WINNER DETECTOR — stop-then-recovered scan")
    print("=" * 78)
    print(f"  matched closed trades : {len(selected)} (cap --limit {args.limit})")
    print(f"  scanned (had OHLC)    : {scanned}")
    print(f"  no_data (skipped)     : {no_data}")
    print(f"  recovery threshold    : >= {args.recovery_pct:.1f}%")
    print(f"  post-exit window      : {args.window_min} min")
    print(f"  exit-kinds            : {args.exit_kinds}")
    print(f"  since                 : {args.since}")
    print()

    cut_n = len(cut_winners)
    rate = (cut_n / scanned * 100.0) if scanned else 0.0
    print(f"  CUT WINNERS           : {cut_n} / {scanned} ({rate:.1f}% of scannable)")
    print()

    if cut_winners:
        cut_winners.sort(key=lambda c: c["peak_recovery_pct"], reverse=True)
        print(f"  {'TOKEN':<12} {'BOT':<26} {'REASON':<32} "
              f"{'EXIT_PX':>12} {'PEAK_REC%':>9} {'MIN':>4}")
        print("  " + "-" * 100)
        for c in cut_winners:
            print(f"  {str(c['token'])[:12]:<12} {str(c['bot'])[:26]:<26} "
                  f"{str(c['reason'])[:32]:<32} {c['exit_price']:>12.8f} "
                  f"{c['peak_recovery_pct']:>8.1f}% {str(c['mins_to_peak']):>4}")
        print()
        recs = sorted(c["peak_recovery_pct"] for c in cut_winners)
        mid = recs[len(recs) // 2]
        print(f"  cut-winner recovery: median {mid:.1f}% / worst (max) {recs[-1]:.1f}%")
    else:
        print("  (no cut winners in this window)")

    print()
    since_str = args.since if args.since else "none"
    print(f"SUMMARY: CUT-WINNER RATE: {cut_n}/{scanned} ({rate:.1f}%) over "
          f"{args.window_min}min, recovery>={args.recovery_pct:.0f}%, "
          f"exit-kinds={args.exit_kinds}, since={since_str}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
