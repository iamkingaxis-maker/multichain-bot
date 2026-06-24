#!/usr/bin/env python
"""Robustness checks for the euphoric-window entry findings:
 1) token concentration in the worst/best buckets (cluster confound)
 2) token-deduped winner/loser medians for the top features
 3) a combined entry GATE: pc_h1 in [-24,-2] AND sol_pc_h1>0 AND mcap>143k AND
    5m_vol_decay>=1  -> WR / $/tr, n, distinct tokens
 4) leave-one-token-out sensitivity on the headline gate
"""
from __future__ import annotations
import json, statistics as st, math
from collections import defaultdict, Counter

SRC = "_euphoric_mine.json"
WINDOW_START = "2026-06-14T16:00"
trades = json.load(open(SRC))
trades.sort(key=lambda t: t.get("time") or "")
open_pos = defaultdict(list)
closes = []
for t in trades:
    bot = t.get("bot_id"); addr = t.get("address") or t.get("token")
    ty = (t.get("type") or "").lower()
    if not bot or not addr:
        continue
    k = (bot, addr)
    if ty == "buy":
        open_pos[k].append(t)
    elif ty == "sell":
        buy = open_pos[k].pop(0) if open_pos[k] else None
        if t.get("pnl") is None or (t.get("time") or "")[:16] < WINDOW_START or buy is None:
            continue
        em = dict(buy.get("entry_meta") or {})
        psych = em.get("mcap_nearest_psych_level_usd"); dp = em.get("mcap_distance_to_psych_pct")
        if psych and dp is not None:
            em["mcap_derived_usd"] = psych * (1 + dp / 100.0)
        em["__pnl"] = float(t["pnl"]); em["__win"] = float(t["pnl"]) > 0
        em["__addr"] = addr; em["__tok"] = t.get("token"); em["__bot"] = bot
        closes.append(em)

n = len(closes)
print(f"n={n}  W={sum(c['__win'] for c in closes)}  tokens={len(set(c['__addr'] for c in closes))}\n")

# 1) token concentration of the big-loss buckets
print("=== TOKEN CONCENTRATION in pc_h1<=-24 (the deep-dip trap bucket) ===")
deep = [c for c in closes if isinstance(c.get("pc_h1"), (int, float)) and c["pc_h1"] <= -24]
cc = Counter(c["__tok"] for c in deep)
for tok, k in cc.most_common(10):
    sub = [c for c in deep if c["__tok"] == tok]
    print(f"   {str(tok):16s} n={k:3d} net=${sum(c['__pnl'] for c in sub):+8.2f} WR={100*sum(x['__win'] for x in sub)/k:.0f}%")
print(f"   -> {len(deep)} trades across {len(cc)} tokens; top token = {100*cc.most_common(1)[0][1]/len(deep):.0f}% of bucket")

print("\n=== TOKEN CONCENTRATION in mcap<143k (micro trap) ===")
micro = [c for c in closes if isinstance(c.get("mcap_derived_usd"), (int, float)) and c["mcap_derived_usd"] <= 143000]
cc2 = Counter(c["__tok"] for c in micro)
for tok, k in cc2.most_common(10):
    sub = [c for c in micro if c["__tok"] == tok]
    print(f"   {str(tok):16s} n={k:3d} net=${sum(c['__pnl'] for c in sub):+8.2f} WR={100*sum(x['__win'] for x in sub)/k:.0f}%")
print(f"   -> {len(micro)} trades across {len(cc2)} tokens; top token = {100*cc2.most_common(1)[0][1]/len(micro):.0f}% of bucket")

# 2) TOKEN-DEDUPED winner/loser medians (one row per token = median feature, median pnl)
print("\n=== TOKEN-DEDUPED winner/loser medians (kills cluster inflation) ===")
bytok = defaultdict(list)
for c in closes:
    bytok[c["__addr"]].append(c)
tok_rows = []
for addr, cs in bytok.items():
    pnl_med = st.median([c["__pnl"] for c in cs])
    rep = {"__win": pnl_med > 0, "__n": len(cs), "__tok": cs[0]["__tok"]}
    for f in ["pc_h1", "sol_pc_h1", "mcap_derived_usd", "5m_vol_decay", "net_flow_60s_usd",
              "vol_5m_burst_vs_h1", "bs_m5", "shape_90m_drawdown_from_max_pct", "lifecycle_age_hours"]:
        fv = [c[f] for c in cs if isinstance(c.get(f), (int, float)) and not isinstance(c.get(f), bool)]
        rep[f] = st.median(fv) if fv else None
    tok_rows.append(rep)
TW = [r for r in tok_rows if r["__win"]]; TL = [r for r in tok_rows if not r["__win"]]
print(f"   token-level: {len(TW)} win-tokens / {len(TL)} loss-tokens  (tokWR={100*len(TW)/len(tok_rows):.0f}%)")
print(f"   {'feature':36s}{'winTok_med':>13}{'lossTok_med':>13}")
for f in ["pc_h1", "sol_pc_h1", "mcap_derived_usd", "5m_vol_decay", "net_flow_60s_usd",
          "vol_5m_burst_vs_h1", "bs_m5", "shape_90m_drawdown_from_max_pct", "lifecycle_age_hours"]:
    wv = [r[f] for r in TW if r[f] is not None]; lv = [r[f] for r in TL if r[f] is not None]
    if len(wv) >= 3 and len(lv) >= 3:
        print(f"   {f:36s}{st.median(wv):>13.4g}{st.median(lv):>13.4g}")

# 3) COMBINED GATE
def gate(c):
    return (isinstance(c.get("pc_h1"), (int, float)) and -24 <= c["pc_h1"] <= -2
            and isinstance(c.get("sol_pc_h1"), (int, float)) and c["sol_pc_h1"] > 0
            and isinstance(c.get("mcap_derived_usd"), (int, float)) and c["mcap_derived_usd"] >= 143000
            and isinstance(c.get("5m_vol_decay"), (int, float)) and c["5m_vol_decay"] >= 1.0)

print("\n=== COMBINED EUPHORIC ENTRY GATE ===")
print("   pc_h1 in [-24,-2]  AND  sol_pc_h1>0  AND  mcap>=143k  AND  5m_vol_decay>=1")
g = [c for c in closes if gate(c)]
ng = [c for c in closes if not gate(c)]
def summ(rows, lab):
    if not rows:
        print(f"   {lab}: EMPTY"); return
    w = sum(c["__win"] for c in rows)
    print(f"   {lab:14s} n={len(rows):3d}  toks={len(set(c['__addr'] for c in rows)):2d}  "
          f"WR={100*w/len(rows):3.0f}%  $/tr={sum(c['__pnl'] for c in rows)/len(rows):+6.2f}  net={sum(c['__pnl'] for c in rows):+8.2f}")
summ(g, "GATE PASS")
summ(ng, "GATE FAIL")

# 4) leave-one-token-out on the gate-pass set
print("\n=== leave-one-token-out sensitivity (gate-pass $/tr) ===")
if g:
    by = defaultdict(list)
    for c in g:
        by[c["__tok"]].append(c)
    base = sum(c["__pnl"] for c in g) / len(g)
    print(f"   full gate-pass $/tr = {base:+.2f} (n={len(g)}, toks={len(by)})")
    worst = []
    for tok in by:
        rest = [c for c in g if c["__tok"] != tok]
        if rest:
            worst.append((sum(c["__pnl"] for c in rest) / len(rest), tok, len(by[tok])))
    worst.sort()
    print("   most fragile (drop this token -> $/tr):")
    for d, tok, k in worst[:5]:
        print(f"      drop {str(tok):16s} (n={k}) -> $/tr={d:+.2f}")
    print("   most load-bearing (drop -> biggest drop):")
    for d, tok, k in worst[-3:]:
        print(f"      drop {str(tok):16s} (n={k}) -> $/tr={d:+.2f}")
