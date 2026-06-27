"""Patient-sleeve A/B: compare patient_sleeve vs the time-box fleet on SHARED tokens.

The clean paired comparison from the winner study: both arms take the same
winner-selected entries; the sleeve HOLDS (-22 stop / 240min / partial-TP-ride), the
badday_* fleet TIME-BOXES (~5.6min). On tokens BOTH arms closed, compare realized mean,
median, and tail-capture (share realizing > +25%). Judge on MEAN + tail (fat-tail:
median stays negative). PAPER over-states patient holds (deep stops gap through live) —
this validates the THESIS; a live probe is the real test.

Usage: python scripts/patient_sleeve_ab.py            # reads _full_trades.json
"""
from __future__ import annotations
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PATIENT_BOT = "patient_sleeve"
TAIL_PCT = 25.0


def _blended_by_token(records, bot_pred):
    """{token_address: [blended_pct per POSITION, ...]} for bots matching bot_pred.

    A position is (bot_id, address, entry_price); its blended realized is the
    FRACTION-WEIGHTED sum of all its sell legs `sum(pnl_pct_i * sell_fraction_i)`
    — NOT the single fully_closed leg (which would drop every partial-TP gain and
    falsely score a partial-TP-then-stop trade as a full -22 loss). This is the
    sum-ALL-sell-legs methodology (memory: final-leg-only falsely condemned conviction_x2).
    Single full closes with no sell_fraction fall back to that leg's pnl_pct."""
    from collections import defaultdict
    groups: dict[tuple, list] = defaultdict(list)
    for r in records:
        bid = r.get("bot_id") or ""
        if not bot_pred(bid):
            continue
        if not isinstance(r.get("pnl_pct"), (int, float)):
            continue
        addr = r.get("address")
        if not addr:
            continue
        groups[(bid, addr, r.get("entry_price"))].append(r)

    out: dict[str, list[float]] = defaultdict(list)
    for (bid, addr, _ep), legs in groups.items():
        fracs = [(float(l["pnl_pct"]), float(l["sell_fraction"]))
                 for l in legs if isinstance(l.get("sell_fraction"), (int, float))]
        total_frac = sum(f for _p, f in fracs)
        if fracs and 0.8 <= total_frac <= 1.2:
            blended = sum(p * f for p, f in fracs)          # fully realized, fraction-weighted
        else:
            closed = [float(l["pnl_pct"]) for l in legs if l.get("fully_closed")]
            if not closed:
                continue                                     # position not fully exited -> skip
            blended = sum(closed) / len(closed)             # single/fallback full close
        out[addr].append(blended)
    return out


def compare_arms(records):
    """Pure A/B over a list of trade-leg records. Pairs tokens where BOTH the
    patient sleeve and a time-box (badday_*) bot have a fully-closed leg; per token
    each arm uses its mean realized pnl_pct. Returns summary dict."""
    patient = _blended_by_token(records, lambda b: b == PATIENT_BOT)
    timebox = _blended_by_token(records, lambda b: b.startswith("badday_"))
    shared = sorted(set(patient) & set(timebox))

    p_vals = [st.mean(patient[t]) for t in shared]   # per-token mean (avoids re-entry weighting)
    t_vals = [st.mean(timebox[t]) for t in shared]

    def _tail_rate(vals):
        return (sum(1 for v in vals if v > TAIL_PCT) / len(vals)) if vals else 0.0

    return {
        "paired_tokens": len(shared),
        "patient_mean": st.mean(p_vals) if p_vals else 0.0,
        "timebox_mean": st.mean(t_vals) if t_vals else 0.0,
        "patient_median": st.median(p_vals) if p_vals else 0.0,
        "timebox_median": st.median(t_vals) if t_vals else 0.0,
        "patient_tail_rate": _tail_rate(p_vals),
        "timebox_tail_rate": _tail_rate(t_vals),
        "n_distinct": len(shared),
        "tokens": shared,
    }


def main():
    path = "_full_trades.json"
    if not os.path.exists(path):
        print(f"{path} not found — run scripts/pull_full_trades.py first.")
        sys.exit(1)
    recs = json.load(open(path))
    out = compare_arms(recs)
    print(f"=== Patient sleeve A/B (paired tokens, paper) — n={out['paired_tokens']} ===")
    if out["paired_tokens"] == 0:
        print("No paired tokens yet (sleeve needs to accrue winner-selected closes vs the fleet).")
        return
    print(f"  patient : mean {out['patient_mean']:+.2f}%  median {out['patient_median']:+.2f}%  "
          f"tail>+25% {out['patient_tail_rate']:.0%}")
    print(f"  timebox : mean {out['timebox_mean']:+.2f}%  median {out['timebox_median']:+.2f}%  "
          f"tail>+25% {out['timebox_tail_rate']:.0%}")
    print(f"  EDGE (mean): {out['patient_mean'] - out['timebox_mean']:+.2f}pp  "
          f"(bar: patient mean beats timebox by > ~1.5% haircut, n>=30, distinct>=10)")
    print("  NOTE: paper over-states patient holds (deep-stop gap-through) — live probe is the real test.")


if __name__ == "__main__":
    main()
