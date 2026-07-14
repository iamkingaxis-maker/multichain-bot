#!/usr/bin/env python3
"""Separation stats: monster vs regular runs, from _runner_features.json.
Filters: not thin, tape_coverage >= COV_MIN, n_D >= 20 trades.
Reports per-run and token-deduped (median per token) stats.
"""
import json, sys, statistics as st
from collections import defaultdict

COV_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
rows = json.load(open("scratchpad/_runner_features.json"))

FEATS = ["makers_per_1k", "new_maker_frac", "pos_windows_10", "max_streak",
         "net_ratio_D", "net_ratio_early", "net_ratio_late", "med_buy",
         "buy_share_ge100", "buy_share_ge500", "med_buy_rel", "bpm_early", "bpm_late", "bpm_accel",
         "bpm_vs_ref", "seller_top3_share", "n_buyers_D", "buy_vol_D"]

def usable(r):
    return (r["label"] in ("monster", "regular") and not r.get("thin", True)
            and (r.get("tape_coverage") or 0) >= COV_MIN and r.get("n_D", 0) >= 20)

use = [r for r in rows if usable(r)]
mon = [r for r in use if r["label"] == "monster"]
reg = [r for r in use if r["label"] == "regular"]
print(f"cov>={COV_MIN} nD>=20 -> usable runs: {len(use)} (monster={len(mon)} regular={len(reg)})")
print(f"  monster tokens: {len({r['pair'] for r in mon})}  regular tokens: {len({r['pair'] for r in reg})}")
print(f"  by net: sol mon={sum(1 for r in mon if r['net']=='solana')} reg={sum(1 for r in reg if r['net']=='solana')}"
      f" | rh mon={sum(1 for r in mon if r['net']=='robinhood')} reg={sum(1 for r in reg if r['net']=='robinhood')}")

def med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else None

def q(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals: return None
    i = min(int(p * len(vals)), len(vals) - 1)
    return vals[i]

print(f"\n{'feature':<20}{'mon_med':>9}{'reg_med':>9}{'mon_q25':>9}{'reg_q75':>9}  overlap_note")
sep = {}
for f in FEATS:
    mv = [r.get(f) for r in mon]; rv = [r.get(f) for r in reg]
    mm, rm = med(mv), med(rv)
    if mm is None or rm is None: continue
    m25, m75 = q(mv, 0.25), q(mv, 0.75)
    r25, r75 = q(rv, 0.25), q(rv, 0.75)
    # separation score: does monster q25 clear regular q75 (or reverse)?
    if mm >= rm:
        clean = m25 is not None and r75 is not None and m25 > r75
        note = "CLEAN mon_q25>reg_q75" if clean else ""
    else:
        clean = m75 is not None and r25 is not None and m75 < r25
        note = "CLEAN mon_q75<reg_q25" if clean else ""
    # AUC-ish: fraction of (mon, reg) pairs where mon > reg
    import itertools
    pairs = [(a, b) for a in mv for b in rv if a is not None and b is not None]
    auc = (sum(1 for a, b in pairs if a > b) + 0.5 * sum(1 for a, b in pairs if a == b)) / max(len(pairs), 1)
    sep[f] = auc
    print(f"{f:<20}{mm:>9.3g}{rm:>9.3g}{m25 if m25 is not None else float('nan'):>9.3g}"
          f"{r75 if r75 is not None else float('nan'):>9.3g}  auc={auc:.2f} {note}")

# token-deduped: median feature per token, then compare
print("\n== token-deduped (median per token) ==")
def tok_med(rows_):
    by = defaultdict(list)
    for r in rows_: by[r["pair"]].append(r)
    return by
mb, rb = tok_med(mon), tok_med(reg)
for f in FEATS:
    mv = [med([r.get(f) for r in rs]) for rs in mb.values()]
    rv = [med([r.get(f) for r in rs]) for rs in rb.values()]
    mm, rm = med(mv), med(rv)
    if mm is None or rm is None: continue
    pairs = [(a, b) for a in mv for b in rv if a is not None and b is not None]
    auc = (sum(1 for a, b in pairs if a > b) + 0.5 * sum(1 for a, b in pairs if a == b)) / max(len(pairs), 1)
    print(f"{f:<20} mon={mm:>8.3g} reg={rm:>8.3g} auc={auc:.2f} (n_tok {len(mv)}/{len(rv)})")
