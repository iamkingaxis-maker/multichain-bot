#!/usr/bin/env python3
"""RESTING-LIMIT-BID simulator (reachability mission #1, 2026-06-30).

Thesis (T4): we fill on the bounce, chasing; the knife keeps falling AFTER entry.
A resting bid placed BELOW our entry would fill when the knife comes back down,
giving a better cost basis on the SAME token. Every closed trade records mae_pct
(max adverse excursion = how far below entry price went), so we can simulate
exactly: a bid at offset b fills iff mae_pct <= b, at cost basis entry*(1+b).

First-order model: SAME EXIT PRICE (the token's actual exit), recomputed off the
lower entry: new_pnl = (1+pnl/100)/(1+b/100) - 1. (Approx: pct-based stops/TPs
shift with a lower entry; this is the standard first-order go/no-go estimate.)

A bid that does NOT fill (mae>b, price never dipped that far) = NO ENTRY = the
trade is SKIPPED. The trade-off: better basis on fills vs missing never-dipped
trades. We also report what the SKIPPED trades would have done (are we skipping
winners or losers?).
"""
import json, statistics as st
from collections import defaultdict

d = json.load(open("_full_trades.json"))
sells = [t for t in d if t.get("type") == "sell"
         and t.get("mae_pct") is not None and t.get("pnl_pct") is not None]

def fl(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None

rows = []
for s in sells:
    mae = fl(s.get("mae_pct")); pnl = fl(s.get("pnl_pct"))
    if mae is None or pnl is None:
        continue
    rows.append((s.get("address") or s.get("token"), mae, pnl))

print(f"=== RESTING-LIMIT-BID SIM | {len(rows)} closed trades w/ MAE ===")
print("(first-order: same exit price, new entry = entry*(1+bid_off); fills iff mae<=bid_off)\n")

def tok_agg(items):
    """token-level: mean pnl per token, then aggregate. (de-correlate fleet dup)"""
    by = defaultdict(list)
    for addr, pnl in items:
        by[addr].append(pnl)
    tm = [st.mean(v) for v in by.values()]
    return tm

# baseline (what we book now): every trade at its entry, pnl as recorded
base = [(a, pnl) for (a, mae, pnl) in rows]
btm = tok_agg(base)
print(f"{'book':22} {'fill%':>6} {'trades':>7} {'toks':>5} {'tok_mean':>9} {'tok_med':>8} {'raw_mean':>9} {'tok_win%':>8}")
def line(label, filled_rows, n_all):
    # filled_rows: list of (addr, new_pnl)
    if not filled_rows:
        print(f"{label:22} {'0':>6} {0:>7} {0:>5} {'-':>9} {'-':>8} {'-':>9} {'-':>8}"); return
    fillpct = 100.0 * len(filled_rows) / n_all
    tm = tok_agg(filled_rows)
    raw = [p for _, p in filled_rows]
    toks = len(set(a for a, _ in filled_rows))
    tokwin = 100.0 * sum(1 for m in tm if m > 0) / len(tm)
    print(f"{label:22} {fillpct:6.0f} {len(filled_rows):7d} {toks:5d} {st.mean(tm):+9.2f} {st.median(tm):+8.2f} {st.mean(raw):+9.2f} {tokwin:8.0f}")

line("BASELINE (all, now)", base, len(rows))
print()
for b in (-1.0, -2.0, -3.0, -5.0, -8.0, -12.0, -18.0):
    filled = []
    skipped = []
    for addr, mae, pnl in rows:
        if mae <= b:  # price dipped to the bid -> fills
            new_pnl = (1.0 + pnl / 100.0) / (1.0 + b / 100.0) - 1.0
            filled.append((addr, new_pnl * 100.0))
        else:
            skipped.append((addr, pnl))
    line(f"resting bid {b:+.0f}%", filled, len(rows))
# what do the SKIPPED trades look like (for a representative mid bid)?
print()
for b in (-3.0, -5.0):
    skip = [(a, pnl) for (a, mae, pnl) in rows if not (mae <= b)]
    if skip:
        stm = tok_agg(skip)
        print(f"SKIPPED at {b:+.0f}% (never dipped to bid): {len(skip)} trades | tok_mean {st.mean(stm):+.2f}% "
              f"tok_med {st.median(stm):+.2f}% (are these winners we'd miss, or losers we'd dodge?)")
