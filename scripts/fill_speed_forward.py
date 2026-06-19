#!/usr/bin/env python
"""
FORWARD FILL-SPEED P&L  (read-only join of captured fast-vs-sweep entries)
==========================================================================

THE QUESTION
------------
Does filling FASTER actually make more money? The historical counterfactual
(scripts/fill_speed_pnl.py) is DATA-BLOCKED: DexScreener doesn't retain the
pre-entry price trajectory for old trades, so it could reconstruct almost no
fast-tier entry price (coverage ~5%, n~1).

THE FIX (this tool)
-------------------
Instead of fetching the comparison BACK, the bot now CAPTURES it FORWARD, at the
moment it exists: the fast-watch loop (shadow) records the price it WOULD have
filled at, and the main sweep records the ACTUAL fill. dip_scanner writes ONE
JSONL record per buy to DATA_DIR/fill_speed_forward.jsonl:
    {ts, token_address, symbol, bot, fast_price, fast_ts, sweep_price, sweep_ts,
     lead_secs, delta_pct}

This joiner reads that log, joins each record to its CLOSED trade (from the
trades API) by token_address + nearest entry time, applies the SAME exit to BOTH
the fast price and the sweep price (the scale cancels — same exit, two entries),
and reports per-side (fast vs sweep): n, WR, median pnl%, mean, sum, AND the
decisive number — median edge_pp (fast_pnl minus sweep_pnl). >0 = faster helps.

READ-ONLY: reads the JSONL + the trades API only. Never touches money/state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
from typing import Dict, List, Optional, Tuple

# Allow running as a bare script: make the repo root importable.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.fast_watch import realized_pair  # the pure, unit-tested core

TRADES_URL = ("https://gracious-inspiration-production.up.railway.app"
              "/api/trades?all=1")


# ──────────────────────────────────────────────────────────────────────────────
# PURE / TESTABLE CORE
# ──────────────────────────────────────────────────────────────────────────────

def _parse_iso(s) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def match_trade(record: dict, trades_by_addr: Dict[str, List[dict]],
                max_skew_secs: float = 600.0) -> Optional[dict]:
    """Find the closed sell whose ENTRY time is nearest the record's capture ts,
    for the SAME token_address (lowercased). None if no sell within max_skew_secs.

    `trades_by_addr`: addr_lower -> list of sell dicts that carry an `entry_ts`
    (unix seconds), `exit_price`, `pnl_pct`. ADDRESS-keyed join (never symbol)."""
    addr = (record.get("token_address") or "").lower()
    if not addr:
        return None
    cand = trades_by_addr.get(addr)
    if not cand:
        return None
    rec_ts = _parse_iso(record.get("ts"))
    rec_secs = rec_ts.timestamp() if rec_ts else None
    best = None
    best_skew = None
    for t in cand:
        ets = t.get("entry_ts")
        if ets is None:
            continue
        if rec_secs is None:
            # no capture ts -> take the single closest by absence; pick first
            return t
        skew = abs(float(ets) - rec_secs)
        if skew <= max_skew_secs and (best_skew is None or skew < best_skew):
            best = t
            best_skew = skew
    return best


def summarize_side(pnls: List[Optional[float]]) -> Dict[str, Optional[float]]:
    """n, WR%, median, mean, sum over a side's per-trade pnl% list (drops None)."""
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


def build_pairs(records: List[dict], trades_by_addr: Dict[str, List[dict]],
                max_skew_secs: float = 600.0
                ) -> Tuple[List[float], List[float], List[float], int, int]:
    """For every forward record that joins to a closed trade, compute the realized
    (fast_pnl, sweep_pnl, edge_pp) with the trade's SAME exit applied to both
    captured entry prices.

    Returns (fast_pnls, sweep_pnls, edges, n_records, n_joined)."""
    fast_pnls: List[float] = []
    sweep_pnls: List[float] = []
    edges: List[float] = []
    n_joined = 0
    for r in records:
        t = match_trade(r, trades_by_addr, max_skew_secs)
        if t is None:
            continue
        exit_price = t.get("exit_price")
        try:
            exit_price = float(exit_price) if exit_price is not None else None
        except (TypeError, ValueError):
            exit_price = None
        rp = realized_pair(r.get("fast_price"), r.get("sweep_price"), exit_price)
        if rp is None:
            continue
        fast_pnl, sweep_pnl, edge = rp
        fast_pnls.append(fast_pnl)
        sweep_pnls.append(sweep_pnl)
        edges.append(edge)
        n_joined += 1
    return fast_pnls, sweep_pnls, edges, len(records), n_joined


# ──────────────────────────────────────────────────────────────────────────────
# LIVE GLUE  (not unit-tested — file/network IO; exercised by the live run)
# ──────────────────────────────────────────────────────────────────────────────

def _load_jsonl(path: str) -> List[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


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


def _index_sells_by_addr(trades: List[dict]) -> Dict[str, List[dict]]:
    """addr_lower -> [sell dicts] each annotated with entry_ts (unix s)."""
    by_addr: Dict[str, List[dict]] = {}
    for t in trades:
        if t.get("type") != "sell":
            continue
        addr = (t.get("address") or t.get("token") or "")
        if not addr:
            continue
        if not (t.get("exit_price") and t.get("entry_price")):
            continue
        sell_ts = _parse_iso(t.get("time"))
        if sell_ts is None:
            continue
        hold = t.get("hold_secs")
        try:
            hold = float(hold) if hold is not None else 0.0
        except (TypeError, ValueError):
            hold = 0.0
        rec = dict(t)
        rec["entry_ts"] = sell_ts.timestamp() - hold
        by_addr.setdefault(addr.lower(), []).append(rec)
    return by_addr


def _fmt(s: Dict[str, Optional[float]], label: str) -> str:
    wr = f"{s['wr']:.1f}" if s['wr'] is not None else "--"
    med = f"{s['median']:+.2f}" if s['median'] is not None else "--"
    mean = f"{s['mean']:+.2f}" if s['mean'] is not None else "--"
    return (f"{label:>8} | {s['n']:>4} | {wr:>6} | {med:>8} | "
            f"{mean:>8} | {s['sum']:>+9.2f}")


def run(args) -> int:
    jsonl_path = args.log or os.path.join(
        os.environ.get("DATA_DIR", "/data"), "fill_speed_forward.jsonl")
    if not os.path.exists(jsonl_path):
        print(f"ERROR: fill-speed log not found: {jsonl_path}\n"
              f"  (the bot writes it when FILL_SPEED_LOG_MODE != off and a buy "
              f"joins a stashed fast price). Copy it from the /data volume.",
              file=sys.stderr)
        return 2
    trades_path = os.path.abspath(args.trades)
    if not os.path.exists(trades_path):
        print(f"ERROR: {trades_path} not found. Fetch it first:\n"
              f'  curl -s "{TRADES_URL}" -o {trades_path}', file=sys.stderr)
        return 2

    records = _load_jsonl(jsonl_path)
    trades = _load_trades(trades_path)
    by_addr = _index_sells_by_addr(trades)

    fast_pnls, sweep_pnls, edges, n_rec, n_join = build_pairs(
        records, by_addr, args.max_skew)

    print("=" * 72)
    print("FORWARD FILL-SPEED P&L  (captured fast-vs-sweep entries, read-only)")
    print("=" * 72)
    print(f"log={jsonl_path}")
    print(f"forward records: {n_rec}   joined to a closed trade: {n_join}   "
          f"max_skew={args.max_skew:.0f}s")
    print("-" * 72)

    fast_s = summarize_side(fast_pnls)
    sweep_s = summarize_side(sweep_pnls)
    edge_vals = [e for e in edges if e is not None]

    hdr = f"{'side':>8} | {'n':>4} | {'WR%':>6} | {'median%':>8} | {'mean%':>8} | {'sum%':>9}"
    print(hdr)
    print("-" * len(hdr))
    print(_fmt(fast_s, "FAST"))
    print(_fmt(sweep_s, "SWEEP"))
    print("-" * len(hdr))

    print()
    if edge_vals:
        med_edge = statistics.median(edge_vals)
        mean_edge = statistics.fmean(edge_vals)
        n_help = sum(1 for e in edge_vals if e > 0)
        print(f"EDGE (fast minus sweep, the decisive number): "
              f"median {med_edge:+.3f}pp   mean {mean_edge:+.3f}pp   "
              f"n={len(edge_vals)}   faster-helped {n_help}/{len(edge_vals)}")
        if med_edge > 0:
            verdict = "FASTER HELPS"
        elif med_edge < 0:
            verdict = "FASTER HURTS"
        else:
            verdict = "NEUTRAL"
        print(f"VERDICT: {verdict} (median edge {med_edge:+.3f}pp over "
              f"n={len(edge_vals)})")
    else:
        print("VERDICT: NO DATA — no forward record joined to a closed trade yet.")

    print()
    if len(edge_vals) < 30:
        print(f"  - LOW n: only {len(edge_vals)} joined pairs — directional only, "
              f"not conclusive. Need n>=30 (keep accumulating; the bot logs forward).")
    print("  - Both sides priced off the captured entries with the SAME trade exit, "
          "so the absolute price scale cancels out of the comparison.")
    print("  - ADDRESS-keyed join (token_address + nearest entry time within "
          "max_skew); never symbol-keyed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Forward fill-speed P&L join (read-only).")
    p.add_argument("--log", type=str, default=None,
                   help="path to fill_speed_forward.jsonl "
                        "(default $DATA_DIR/fill_speed_forward.jsonl)")
    p.add_argument("--trades", type=str, default="./_fillpnl.json",
                   help="path to the trades API dump (default ./_fillpnl.json)")
    p.add_argument("--max-skew", type=float, default=600.0,
                   help="max secs between capture ts and trade entry to join "
                        "(default 600)")
    return p


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
