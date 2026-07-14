#!/usr/bin/env python3
"""Validate proposed runner_score on labeled runs (cov>=0.5, nD>=20)."""
import json, statistics as st
from collections import defaultdict

def clip01(x): return max(0.0, min(1.0, x))

def runner_score(f):
    """Pure function: features dict -> (score 0-1, reasons list)."""
    subs = {}
    if f.get("net_ratio_D") is not None:
        subs["flow"] = clip01(f["net_ratio_D"] / 0.2)
    if f.get("bpm_accel") is not None:
        subs["accel"] = clip01((f["bpm_accel"] - 0.6) / 0.6)
    if f.get("med_buy_rel") is not None:
        subs["size"] = clip01((f["med_buy_rel"] - 1.0) / 1.0)
    if f.get("new_maker_frac") is not None:
        subs["fresh"] = clip01((f["new_maker_frac"] - 0.35) / 0.3)
    if not subs:
        return None, []
    score = sum(subs.values()) / len(subs)
    reasons = [f"{k}={v:.2f}" for k, v in subs.items() if v >= 0.5]
    return round(score, 3), reasons

rows = json.load(open("scratchpad/_runner_features.json"))
use = [r for r in rows if r["label"] in ("monster", "regular")
       and not r.get("thin", True) and (r.get("tape_coverage") or 0) >= 0.5
       and r.get("n_D", 0) >= 20]
for r in use:
    r["score"], r["why"] = runner_score(r)
use = [r for r in use if r["score"] is not None]

mon = [r for r in use if r["label"] == "monster"]
reg = [r for r in use if r["label"] == "regular"]
print(f"runs scored: monster={len(mon)} regular={len(reg)}")
print(f"score median: mon={st.median([r['score'] for r in mon]):.3f} reg={st.median([r['score'] for r in reg]):.3f}")
for th in (0.4, 0.5, 0.6):
    tp = sum(1 for r in mon if r["score"] >= th) / len(mon)
    fp = sum(1 for r in reg if r["score"] >= th) / len(reg)
    print(f"  thr={th}: monster hit-rate={tp:.2f}  regular false-fire={fp:.2f}")
pairs = [(a["score"], b["score"]) for a in mon for b in reg]
auc = (sum(1 for a, b in pairs if a > b) + 0.5 * sum(1 for a, b in pairs if a == b)) / len(pairs)
print(f"  combined AUC (runs) = {auc:.2f}")

# per-token table
print("\n== per-token (median score across its runs) ==")
by = defaultdict(list)
for r in use: by[(r["label"], r["sym"], r["net"])].append(r["score"])
tok = sorted(((lab, sym, net, st.median(s), len(s)) for (lab, sym, net), s in by.items()),
             key=lambda x: (x[0], -x[3]))
mt = [t for t in tok if t[0] == "monster"]; rt = [t for t in tok if t[0] == "regular"]
print(f"token-level AUC = ", end="")
p2 = [(a[3], b[3]) for a in mt for b in rt]
print(f"{(sum(1 for a,b in p2 if a>b)+0.5*sum(1 for a,b in p2 if a==b))/len(p2):.2f}")
for lab, sym, net, s, n in tok:
    print(f"{lab:<8} {net[:3]} {str(sym)[:14]:<14} score={s:.2f} (runs={n})")
