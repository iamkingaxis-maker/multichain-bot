"""Scrubbed stacked-cohort read — the Day-5 gate question, answered early.
Stack layers (each measured pre-scrub; re-verify SCRUBBED + per-token):
  L0 baseline (all badday, honest era)
  L1 SOL-red   (sol_pc_h24 <= 0 at entry)
  L2 + full_thesis (pc_h6 <= 0 AND median_buy_size_usd >= 34.3)
  L3 + oversold_held (rsi_15m <= 44 AND dev_pct_remaining >= 10)
Also: green_day-gate-pass version (the Matrix B live filter as shipped).
Spike scrub: first-sell hold<10s AND mae>=0 AND ret>0 -> excluded.
"""
import json, statistics as st
from collections import defaultdict

def fl(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None

d = json.load(open("_full_trades.json"))
buys = defaultdict(list)
for t in d:
    if t.get("type") == "buy" and isinstance(t.get("entry_meta"), dict):
        buys[t.get("address") or t.get("token")].append((str(t.get("time", "")), t["entry_meta"]))
for a in buys:
    buys[a].sort(key=lambda x: x[0])

def em_for(addr, stime):
    l = buys.get(addr)
    if not l:
        return {}
    s = str(stime or "")
    c = [e for tm, e in l if tm <= s]
    return c[-1] if c else l[0][1]

pos = {}
for t in d:
    if t.get("type") != "sell":
        continue
    p = fl(t.get("pnl_pct"))
    if p is None or not str(t.get("bot_id", "")).startswith("badday"):
        continue
    addr = t.get("address") or t.get("token")
    k = (t.get("bot_id"), addr, round(fl(t.get("entry_price")) or 0, 12))
    r = pos.setdefault(k, {"ret": 0.0, "tok": addr, "day": str(t.get("time"))[:10],
                           "hold": None, "mae": None, "em": None, "t": t.get("time")})
    r["ret"] += p * (fl(t.get("sell_fraction")) or 1.0)
    h = fl(t.get("hold_secs"))
    if r["hold"] is None or (h is not None and h < r["hold"]):
        r["hold"] = h
        r["mae"] = fl(t.get("mae_pct"))
    if r["em"] is None:
        r["em"] = em_for(addr, t.get("time"))

rows = [r for r in pos.values() if r["em"] is not None]
scrub = [r for r in rows
         if not (r["ret"] > 0 and r["hold"] is not None and r["hold"] < 10
                 and r["mae"] is not None and r["mae"] >= 0)]

def L1(r):
    v = fl(r["em"].get("sol_pc_h24")); return v is not None and v <= 0
def L2(r):
    p6 = fl(r["em"].get("pc_h6")); mb = fl(r["em"].get("median_buy_size_usd"))
    return p6 is not None and p6 <= 0 and mb is not None and mb >= 34.3
def L3(r):
    rsi = fl(r["em"].get("rsi_15m")); dev = fl(r["em"].get("dev_pct_remaining"))
    return rsi is not None and rsi <= 44 and dev is not None and dev >= 10
def GD(r):
    s6 = fl(r["em"].get("sol_pc_h6")); s1 = fl(r["em"].get("sol_pc_h1"))
    p6 = fl(r["em"].get("pc_h6"))
    if s1 is not None and s1 > 1: return False
    if s6 is None or s6 <= 0: return True
    if s6 <= 1.5: return p6 is not None and p6 <= -25
    return L3(r)

def rep(name, rr):
    if not rr:
        print(f"{name:<38} EMPTY"); return
    rets = [r["ret"] for r in rr]
    toks = defaultdict(list)
    for r in rr:
        toks[r["tok"]].append(r["ret"])
    tokm = [st.mean(v) for v in toks.values()]
    days = len(set(r["day"] for r in rr))
    print(f"{name:<38} n={len(rets):4} mean={st.mean(rets):+6.2f} med={st.median(rets):+6.2f} "
          f"win={100*sum(1 for x in rets if x>0)/len(rets):3.0f}% | tok={len(toks):3} "
          f"tokmean={st.mean(tokm):+6.2f} days={days}")

print("=== SCRUBBED STACK READ (honest era >=06-26; spike-scrubbed; per-token alongside) ===")
era = [r for r in scrub if r["day"] >= "2026-06-26"]
rep("L0 baseline (scrubbed)", era)
l1 = [r for r in era if L1(r)];               rep("L1 SOL-red (sol_h24<=0)", l1)
l2 = [r for r in l1 if L2(r)];                rep("L2 +full_thesis", l2)
l3 = [r for r in l2 if L3(r)];                rep("L3 +oversold_held (FULL STACK)", l3)
gd = [r for r in era if GD(r)];               rep("GreenDayGate-pass only", gd)
gdl2 = [r for r in gd if L2(r)];              rep("GDpass +full_thesis", gdl2)
gdl3 = [r for r in gdl2 if L3(r)];            rep("GDpass +full_thesis +osh", gdl3)
print()
print("=== same stacks, WHOLE window 06-23+ (more n, mixed regimes) ===")
rep("L0 baseline (scrubbed)", scrub)
w1 = [r for r in scrub if L1(r)];             rep("L1 SOL-red", w1)
w2 = [r for r in w1 if L2(r)];                rep("L2 +full_thesis", w2)
w3 = [r for r in w2 if L3(r)];                rep("L3 +osh (FULL STACK)", w3)
wg = [r for r in scrub if GD(r)];             rep("GreenDayGate-pass", wg)
wg2 = [r for r in wg if L2(r)];               rep("GDpass +full_thesis", wg2)
wg3 = [r for r in wg2 if L3(r)];              rep("GDpass +ft +osh", wg3)
print()
print("BAR: live candidate needs mean >= +2.0pp scrubbed (per-token mean is the honest n).")
