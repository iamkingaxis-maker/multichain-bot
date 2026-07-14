"""Composition drill-down: WHY are our own young 09-13 fills bad when the universe says
09-13 is the best young window? never-green rate, liq mix, day/token concentration, churn."""
import json, os, statistics as st
from collections import defaultdict

ROOT = r"C:\Users\jcole\multichain-bot\scratchpad\sol_young_regime"
rows = [json.loads(l) for l in open(os.path.join(ROOT, "positions.jsonl"), encoding="utf-8")]
young = [r for r in rows if r["age_h"] is not None and r["age_h"] < 6]
for r in young:
    r["in0913"] = 9 <= r["utc_hour"] < 13

def side(sel): return [r for r in young if r["in0913"] == sel]

print("== never-green rate (peak <= 0) and shallow-peak (<2%) ==")
for lab, sel in (("09-13", True), ("rest", False)):
    rr = side(sel)
    pk = [r.get("peak") for r in rr if r.get("peak") is not None]
    ng = sum(1 for p in pk if p <= 0) / len(pk) * 100
    sh = sum(1 for p in pk if p < 2) / len(pk) * 100
    print(f"{lab:6s} n={len(pk):4d} never-green={ng:4.1f}%  peak<2%={sh:4.1f}%")

print("\n== liq bucket mix + wr by bucket ==")
def bucket(l):
    if l is None: return "?"
    if l < 25000: return "<25k"
    if l < 50000: return "25-50k"
    if l < 100000: return "50-100k"
    return ">=100k"
for lab, sel in (("09-13", True), ("rest", False)):
    rr = side(sel)
    bk = defaultdict(list)
    for r in rr: bk[bucket(r.get("liq"))].append(r["pnl_pct"])
    tot = len(rr)
    out = []
    for b in ("<25k", "25-50k", "50-100k", ">=100k", "?"):
        v = bk.get(b)
        if v:
            out.append(f"{b}: {len(v)/tot*100:4.1f}% wr={sum(1 for p in v if p>0)/len(v)*100:4.1f}% med={st.median(v):+5.1f}")
    print(f"{lab:6s} " + " | ".join(out))

print("\n== own young 09-13: per-day n / mean pnl / sum pnl_usd ==")
pd = defaultdict(list)
for r in side(True): pd[r["day"]].append(r)
for d in sorted(pd):
    v = pd[d]
    print(f"{d} n={len(v):3d} tok={len({r['address'] for r in v}):2d} wr={sum(1 for r in v if r['pnl_pct']>0)/len(v)*100:4.0f}% "
          f"medpnl={st.median([r['pnl_pct'] for r in v]):+6.2f} sum_usd={sum(r['pnl_usd'] or 0 for r in v):+7.2f}")

print("\n== token concentration in 09-13 (fills per token; churn check) ==")
tk = defaultdict(list)
for r in side(True): tk[r["address"]].append(r)
top = sorted(tk.items(), key=lambda kv: -len(kv[1]))[:10]
for a, v in top:
    print(f"{v[0]['token'][:12]:12s} fills={len(v):3d} med={st.median([r['pnl_pct'] for r in v]):+6.2f} "
          f"sum_usd={sum(r['pnl_usd'] or 0 for r in v):+7.2f} days={sorted({r['day'] for r in v})}")
fills = sorted((len(v) for v in tk.values()), reverse=True)
print(f"tokens={len(tk)} fills={sum(fills)}; top2 tokens carry {sum(fills[:2])} fills "
      f"({sum(fills[:2])/sum(fills)*100:.0f}%)")

print("\n== rest-side token churn for comparison ==")
tk2 = defaultdict(int)
for r in side(False): tk2[r["address"]] += 1
f2 = sorted(tk2.values(), reverse=True)
print(f"tokens={len(tk2)} fills={sum(f2)}; top2 carry {sum(f2[:2])/sum(f2)*100:.0f}%; "
      f"mean fills/token 0913={st.fmean(fills):.2f} rest={st.fmean(f2):.2f}")

print("\n== young-LANE bots only (badday_young_*): 09-13 composition ==")
lane = [r for r in young if (r.get("bot_id") or "").startswith(("badday_young", "young"))]
for lab, sel in (("09-13", True), ("rest", False)):
    rr = [r for r in lane if r["in0913"] == sel]
    if not rr: continue
    pk = [r.get("peak") for r in rr if r.get("peak") is not None]
    ng = sum(1 for p in pk if p <= 0) / len(pk) * 100 if pk else float("nan")
    liqm = st.median([r["liq"] for r in rr if r.get("liq") is not None])
    print(f"{lab:6s} n={len(rr):4d} wr={sum(1 for r in rr if r['pnl_pct']>0)/len(rr)*100:4.1f}% "
          f"med={st.median([r['pnl_pct'] for r in rr]):+6.2f} never-green={ng:4.1f}% liq_med={liqm:8.0f} "
          f"hold_med={st.median([r['hold_secs'] for r in rr]):5.0f}s")

print("\n== excl top-2 loss days (07-09, 07-11): own young 09-13 vs rest ==")
excl = {"2026-07-09", "2026-07-11"}
for lab, sel in (("09-13", True), ("rest", False)):
    rr = [r for r in side(sel) if r["day"] not in excl]
    if rr:
        toks = defaultdict(list)
        for r in rr: toks[r["address"]].append(r["pnl_pct"])
        tm = st.median([st.median(v) for v in toks.values()])
        print(f"{lab:6s} n={len(rr):4d} tok={len(toks):3d} wr={sum(1 for r in rr if r['pnl_pct']>0)/len(rr)*100:4.1f}% "
              f"med={st.median([r['pnl_pct'] for r in rr]):+6.2f} tokmed={tm:+6.2f}")
