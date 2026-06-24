#!/usr/bin/env python
"""Token-DEDUPED quartile tables for the survivors (age, pc_h1, 5m_vol_decay,
vol_5m_burst, sol_pc_h1). One row per token (median feature, median pnl, win =
median pnl>0). This is the honest test: a bucket's WR/$/tr is over DISTINCT
tokens, so GLOOP-style clusters can't manufacture an edge.
"""
from __future__ import annotations
import json, statistics as st, math
from collections import defaultdict

SRC = "_euphoric_mine.json"; WINDOW_START = "2026-06-14T16:00"
trades = json.load(open(SRC)); trades.sort(key=lambda t: t.get("time") or "")
open_pos = defaultdict(list); closes = []
for t in trades:
    bot = t.get("bot_id"); addr = t.get("address") or t.get("token"); ty = (t.get("type") or "").lower()
    if not bot or not addr: continue
    k = (bot, addr)
    if ty == "buy": open_pos[k].append(t)
    elif ty == "sell":
        buy = open_pos[k].pop(0) if open_pos[k] else None
        if t.get("pnl") is None or (t.get("time") or "")[:16] < WINDOW_START or buy is None: continue
        em = dict(buy.get("entry_meta") or {})
        psych = em.get("mcap_nearest_psych_level_usd"); dp = em.get("mcap_distance_to_psych_pct")
        if psych and dp is not None: em["mcap_derived_usd"] = psych * (1 + dp / 100.0)
        em["__pnl"] = float(t["pnl"]); em["__addr"] = addr; em["__tok"] = t.get("token")
        closes.append(em)

# collapse to one row per token
bytok = defaultdict(list)
for c in closes: bytok[c["__addr"]].append(c)
toks = []
FEATS = ["lifecycle_age_hours", "pc_h1", "5m_vol_decay", "vol_5m_burst_vs_h1",
         "sol_pc_h1", "mcap_derived_usd", "net_flow_60s_imbalance"]
for addr, cs in bytok.items():
    r = {"__tok": cs[0]["__tok"], "__pnl": st.median([c["__pnl"] for c in cs]),
         "__n": len(cs)}
    r["__win"] = r["__pnl"] > 0
    for f in FEATS:
        fv = [c[f] for c in cs if isinstance(c.get(f), (int, float)) and not isinstance(c.get(f), bool)]
        r[f] = st.median(fv) if fv else None
    toks.append(r)

print(f"distinct tokens = {len(toks)}  win-tokens = {sum(t['__win'] for t in toks)}  "
      f"tokWR = {100*sum(t['__win'] for t in toks)/len(toks):.0f}%\n")

def table(f):
    fv = sorted(t[f] for t in toks if t[f] is not None)
    if len(fv) < 12:
        print(f"{f}: low token coverage ({len(fv)})\n"); return
    qs = [fv[int(len(fv)*p)] for p in (0.33, 0.66)]
    print(f"{f}  (token cov {len(fv)})  terciles at {[round(x,3) for x in qs]}")
    edges = [(-math.inf, qs[0], f"T1 <={qs[0]:.3g}"), (qs[0], qs[1], f"T2 {qs[0]:.3g}..{qs[1]:.3g}"), (qs[1], math.inf, f"T3 >{qs[1]:.3g}")]
    for lo, hi, lab in edges:
        sub = [t for t in toks if t[f] is not None and lo < t[f] <= hi]
        if not sub: continue
        w = sum(t["__win"] for t in sub)
        # token-weighted $/tr uses median token pnl
        med_pnl = st.median([t["__pnl"] for t in sub])
        print(f"   {lab:24s} tokens={len(sub):3d}  tokWR={100*w/len(sub):3.0f}%  median_tok_pnl={med_pnl:+6.2f}")
    print()

for f in FEATS:
    table(f)

# 2-way: YOUNG (age<=median) AND moderate-dip (pc_h1 in [-24,-3]) over distinct tokens
print("=== 2-WAY (token-deduped): age<=33h  AND  pc_h1 in [-24,-3] ===")
def cond(t):
    return (t["lifecycle_age_hours"] is not None and t["lifecycle_age_hours"] <= 33
            and t["pc_h1"] is not None and -24 <= t["pc_h1"] <= -3)
P = [t for t in toks if cond(t)]; F = [t for t in toks if not cond(t)]
for lab, rows in (("PASS", P), ("FAIL", F)):
    if rows:
        w = sum(t["__win"] for t in rows)
        print(f"   {lab}: tokens={len(rows):2d}  tokWR={100*w/len(rows):3.0f}%  median_tok_pnl={st.median([t['__pnl'] for t in rows]):+.2f}")
print("   PASS tokens:", [t["__tok"] for t in P])
