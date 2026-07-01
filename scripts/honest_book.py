#!/usr/bin/env python3
"""
honest_book.py — THE scoreboard (2026-07-01, 4-agent diagnosis P1).

Every enforce / go-live decision must quote THIS script's SCRUBBED figures.

Two illusions poisoned the raw paper book:
  1. Stale-snapshot fills (fixed in the engine: PAPER_FIDELITY/BUY/EXIT_REPRICE
     enforce — already inside pnl_pct).
  2. LATENCY SPIKES: trades whose first sell fired <10s after fill at +22..106%
     with price never below entry (mae>=0). 77 such prints on 11 tokens were
     >100% of the 06-26..28 "great era". Live execution can NEVER capture an
     ignition candle print — by the time you fill, the spike IS your price.

Scrub rule: a closed position is UNREALIZABLE when hold_secs < SPIKE_HOLD_SECS
(default 10) AND mae_pct >= 0 AND realized pnl > 0. Reported separately, never
mixed into decision numbers. Per-token dedup is reported alongside (nominal n
is ~6x inflated by near-identical mirror bots buying the same fill).

Usage: PYTHONPATH=. python scripts/honest_book.py [_full_trades.json] [days=10]
"""
import json
import os
import sys
import statistics as st
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "_full_trades.json"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 10
SPIKE_HOLD_SECS = float(os.environ.get("SPIKE_HOLD_SECS", "10"))


def fl(x):
    try:
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # position-level: frac-weighted return + spike flag from the FIRST sell leg
    pos = {}
    for t in data:
        if t.get("type") != "sell":
            continue
        p = fl(t.get("pnl_pct"))
        if p is None or not str(t.get("bot_id", "")).startswith("badday"):
            continue
        k = (t.get("bot_id"), t.get("address") or t.get("token"),
             round(fl(t.get("entry_price")) or 0, 12))
        r = pos.setdefault(k, {"ret": 0.0, "day": str(t.get("time"))[:10],
                               "tok": t.get("address") or t.get("token"),
                               "first_hold": None, "first_mae": None})
        r["ret"] += p * (fl(t.get("sell_fraction")) or 1.0)
        h = fl(t.get("hold_secs"))
        if r["first_hold"] is None or (h is not None and h < r["first_hold"]):
            r["first_hold"] = h
            r["first_mae"] = fl(t.get("mae_pct"))

    def is_spike(r):
        return (r["ret"] > 0 and r["first_hold"] is not None
                and r["first_hold"] < SPIKE_HOLD_SECS
                and r["first_mae"] is not None and r["first_mae"] >= 0)

    by_day = defaultdict(list)
    for r in pos.values():
        by_day[r["day"]].append(r)

    print("=" * 96)
    print(f"HONEST BOOK (scrub: hold<{SPIKE_HOLD_SECS:.0f}s AND mae>=0 AND "
          f"pnl>0 = unrealizable spike; per-token = deduped across mirror bots)")
    print("=" * 96)
    hdr = (f"{'day':<11}{'n':>5}{'raw_mean':>9}{'raw_sum':>9} | "
           f"{'n_scrub':>8}{'SCRUB_mean':>11}{'SCRUB_med':>10}{'SCRUB_sum':>10}"
           f"{'win%':>6} | {'spikes':>7}{'spike_pp':>9} | {'tokens':>7}"
           f"{'tok_mean':>9}")
    print(hdr)
    for day in sorted(by_day)[-DAYS:]:
        rows = by_day[day]
        raw = [r["ret"] for r in rows]
        keep = [r["ret"] for r in rows if not is_spike(r)]
        spikes = [r["ret"] for r in rows if is_spike(r)]
        toks = defaultdict(list)
        for r in rows:
            if not is_spike(r):
                toks[r["tok"]].append(r["ret"])
        tok_means = [st.mean(v) for v in toks.values()]
        win = 100 * sum(1 for x in keep if x > 0) / len(keep) if keep else 0
        print(f"{day:<11}{len(raw):>5}{st.mean(raw):>+9.2f}{sum(raw):>+9.0f} | "
              f"{len(keep):>8}"
              f"{(st.mean(keep) if keep else 0):>+11.2f}"
              f"{(st.median(keep) if keep else 0):>+10.2f}"
              f"{sum(keep):>+10.0f}{win:>6.0f} | "
              f"{len(spikes):>7}{sum(spikes):>+9.0f} | {len(toks):>7}"
              f"{(st.mean(tok_means) if tok_means else 0):>+9.2f}")
    allr = [r for rows in by_day.values() for r in rows]
    keep = [r["ret"] for r in allr if not is_spike(r)]
    spikes = [r["ret"] for r in allr if is_spike(r)]
    print("-" * 96)
    if keep:
        print(f"POOLED scrubbed: n={len(keep)} mean={st.mean(keep):+.2f} "
              f"median={st.median(keep):+.2f} "
              f"win={100*sum(1 for x in keep if x>0)/len(keep):.0f}%  |  "
              f"spikes excluded: n={len(spikes)} worth {sum(spikes):+.0f}pp")
    print("RULE: enforce/go-live decisions quote the SCRUBBED + per-token "
          "columns or they are invalid.")


if __name__ == "__main__":
    main()
