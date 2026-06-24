#!/usr/bin/env python
"""Combine euphoria entry gates + robustness:
 (a) per-close raw
 (b) per-token equal-weight (so GLOOP n=46 can't carry it)
 (c) drop the 3 most-traded tokens (decluster) and re-test
 (d) combined-gate scan -> the recommended euphoria entry FILTER
"""
from __future__ import annotations
import json, statistics as st
from collections import defaultdict

rows = json.load(open("_euphoria_traits.json"))


def num(r, k):
    v = r.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# per-close expansion
CL = []
for r in rows:
    for pnl in r["closes"]:
        d = dict(r); d["__pnl"] = pnl; d["__win"] = pnl > 0
        CL.append(d)


def evalg(name, pred, pool=CL, unit="close"):
    g = [c for c in pool if pred(c)]
    if not g:
        print(f"{name:48s}  n=0"); return
    if unit == "close":
        w = sum(1 for c in g if c["__win"])
        net = sum(c["__pnl"] for c in g)
        print(f"{name:48s}  n={len(g):3d}  WR={100*w/len(g):3.0f}%  "
              f"$/cl={net/len(g):+6.2f}  net={net:+8.1f}  toks={len(set(c['addr'] for c in g))}")
    else:  # token equal-weight: avg of per-token $/close, and token WR
        bytok = defaultdict(list)
        for c in g:
            bytok[c["addr"]].append(c["__pnl"])
        tok_dpc = [sum(v) / len(v) for v in bytok.values()]
        tok_win = sum(1 for v in bytok.values() if sum(v) > 0)
        print(f"{name:48s}  toks={len(bytok):3d}  tokWR={100*tok_win/len(bytok):3.0f}%  "
              f"avg$/cl={st.mean(tok_dpc):+6.2f}  median$/cl={st.median(tok_dpc):+6.2f}")


print("=" * 100)
print("BASELINE")
evalg("ALL (per-close)", lambda c: True)
evalg("ALL (per-token equal-wt)", lambda c: True, unit="token")

# the 3 most-traded tokens
top3 = sorted(rows, key=lambda r: -r["n"])[:3]
top3a = {r["addr"] for r in top3}
print("decluster drops:", [r["tok"] for r in top3])
CL_dc = [c for c in CL if c["addr"] not in top3a]
evalg("ALL minus top-3-traded (per-close)", lambda c: True, pool=CL_dc)

print("\n" + "=" * 100)
print("SINGLE GATES (per-close, then declustered, then per-token)")
gates = {
    "pc_h24 >= 100":            lambda c: (num(c, "pc_h24") or -1e9) >= 100,
    "pc_h6  >= 50":             lambda c: (num(c, "pc_h6") or -1e9) >= 50,
    "pc_h6  >= 0":              lambda c: (num(c, "pc_h6") or -1e9) >= 0,
    "mcap   >= 400k":           lambda c: (num(c, "mcap") or 0) >= 400e3,
    "liq    >= 20k":            lambda c: (num(c, "liq") or 0) >= 20e3,
    "vol_liq_ratio_h24 < 4":    lambda c: (num(c, "vol_liq_ratio_h24") or 1e9) < 4,
}
for nm, p in gates.items():
    evalg(nm, p)
    evalg("   (declustered)", p, pool=CL_dc)
    evalg("   (per-token)", p, unit="token")
    print()

print("=" * 100)
print("COMBINED EUPHORIA FILTER candidates")
# the anti-rug core: positive 6h momentum + real liquidity + not-churned
combos = {
    "A: pc_h6>=0 & liq>=20k":
        lambda c: (num(c, "pc_h6") or -1e9) >= 0 and (num(c, "liq") or 0) >= 20e3,
    "B: pc_h24>=60 & liq>=20k & vlr<8":
        lambda c: (num(c, "pc_h24") or -1e9) >= 60 and (num(c, "liq") or 0) >= 20e3 and (num(c, "vol_liq_ratio_h24") or 1e9) < 8,
    "C: pc_h6>=0 & mcap>=200k & vlr<8":
        lambda c: (num(c, "pc_h6") or -1e9) >= 0 and (num(c, "mcap") or 0) >= 200e3 and (num(c, "vol_liq_ratio_h24") or 1e9) < 8,
    "D: pc_h6>=20 & liq>=30k":
        lambda c: (num(c, "pc_h6") or -1e9) >= 20 and (num(c, "liq") or 0) >= 30e3,
    "E: pc_h24>=100 & pc_h6>=0 & liq>=25k":
        lambda c: (num(c, "pc_h24") or -1e9) >= 100 and (num(c, "pc_h6") or -1e9) >= 0 and (num(c, "liq") or 0) >= 25e3,
    "F: ANTI-RUG: mcap>=100k & liq>=20k & pc_h24>=0 & vlr<10":
        lambda c: (num(c, "mcap") or 0) >= 100e3 and (num(c, "liq") or 0) >= 20e3 and (num(c, "pc_h24") or -1e9) >= 0 and (num(c, "vol_liq_ratio_h24") or 1e9) < 10,
}
for nm, p in combos.items():
    evalg(nm, p)
    evalg("   (declustered)", p, pool=CL_dc)
    evalg("   (per-token)", p, unit="token")
    # what it rejects
    rej = [c for c in CL if not p(c)]
    if rej:
        w = sum(1 for c in rej if c["__win"])
        print(f"   {'REJECTED pool':45s}  n={len(rej):3d}  WR={100*w/len(rej):3.0f}%  net={sum(c['__pnl'] for c in rej):+8.1f}")
    print()
