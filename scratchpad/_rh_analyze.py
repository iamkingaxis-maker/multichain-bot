#!/usr/bin/env python3
import json, os, statistics as st

REPO = r"C:\Users\jcole\multichain-bot"
TAPE_DIR = os.path.join(REPO, "scratchpad", "robinhood_tapes")
rows = json.load(open(os.path.join(TAPE_DIR, "_recon_events.json")))

FEATS = ["sell_rate_60", "sell_traj", "cum_nf_60", "pos_subwins"]


def auc(pos, neg):
    """P(feature_pos > feature_neg): rank-based AUC, label pos=RAN."""
    if not pos or not neg:
        return None
    wins = 0.0; n = 0
    for a in pos:
        for b in neg:
            n += 1
            if a > b: wins += 1
            elif a == b: wins += 0.5
    return wins / n if n else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def summarize(label, subset):
    labeled = [r for r in subset if r["has_outcome"]]
    print(f"\n=== {label}  (events={len(subset)} labeled={len(labeled)}) ===")
    ran = [r for r in labeled if r["ran"]]
    died = [r for r in labeled if not r["ran"]]
    print(f"  RAN={len(ran)}  DIED={len(died)}  base_run_rate="
          f"{(len(ran)/len(labeled)*100 if labeled else 0):.0f}%")
    # feature availability
    for f in FEATS:
        avail = sum(1 for r in labeled if r.get(f) is not None)
        rp = [r[f] for r in ran if r.get(f) is not None]
        dp = [r[f] for r in died if r.get(f) is not None]
        a = auc(rp, dp)
        astr = f"{a:.2f}" if a is not None else "  - "
        mr = med(rp); mdd = med(dp)
        mrs = f"{mr:8.2f}" if mr is not None else "     -  "
        mds = f"{mdd:8.2f}" if mdd is not None else "     -  "
        gap = (mr - mdd) if (mr is not None and mdd is not None) else None
        gaps = f"{gap:+8.2f}" if gap is not None else "     -  "
        print(f"    {f:14s} avail={avail:3d}  medRAN={mrs}  medDIED={mds}"
              f"  gap={gaps}  AUC(RAN)={astr}")


print("REGIME KEY: 07-10 bad, 07-11 bad, 07-12 good")
# pooled
summarize("POOLED all", rows)
# per regime
for reg in ["07-10", "07-11", "07-12"]:
    summarize(f"REGIME {reg}", [r for r in rows if r["regime"] == reg])
# per session (only those with >=6 labeled events)
for s in sorted(set(r["session"] for r in rows)):
    sub = [r for r in rows if r["session"] == s]
    lab = [r for r in sub if r["has_outcome"]]
    if len(lab) >= 6:
        summarize(f"session{s} [{sub[0]['regime']}]", sub)
