"""Mine smart-money WINNERS for entry patterns we haven't used.

Pivot from following the wallets (they all bag 60-90%) to using their WINNING tokens
as a labeled positive set. For each token, extract the structure of how the smart
wallets bought it, then find what separates WINNERS (in any wallet's winners list)
from LOSERS (smart-bought but never a winner). Local-only on the discovered dataset.

Features per token (from smart-wallet buys only):
  n_buyers      distinct smart wallets that bought it
  n_elite       distinct ELITE buyers (n_winners >= 50)
  span_min      minutes between first and last smart buy (clustering tightness)
  peak10        max smart buyers in any 10-min window (consensus burst)
  velocity      buyers per minute over the active span
  tot_vol       total smart $ in
  med_vol       median per-buy $
"""
from __future__ import annotations
import json, collections, statistics
from datetime import datetime

def _epoch(ts):
    try:
        return datetime.strptime(ts.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").timestamp()
    except Exception:
        return None

rec = json.load(open("_prune_mine/discovered_wallets.json"))
buys = json.load(open("_prune_mine/discovered_buys.json"))
winners = set()
nwin = {}
for w, v in rec.items():
    nwin[w] = v.get("n_winners", 0)
    winners |= set(v.get("winners", []))
ELITE = {w for w, n in nwin.items() if n >= 50}
print(f"wallets={len(rec)} | elite(n_winners>=50)={len(ELITE)} | winner-token universe={len(winners)}")

# group buys by token
bytok = collections.defaultdict(list)
for b in buys:
    t = b.get("token"); e = _epoch(b.get("ts", ""));
    try: vol = float(b.get("vol") or 0)
    except Exception: vol = 0.0
    if t and e: bytok[t].append({"w": b.get("wallet"), "e": e, "vol": vol})

def feats(es):
    ws = {x["w"] for x in es}
    elite = {x["w"] for x in es if x["w"] in ELITE}
    times = sorted(x["e"] for x in es)
    span = (times[-1] - times[0]) / 60.0 if len(times) > 1 else 0.0
    # peak buyers in any 10-min window (by distinct wallet)
    peak = 0
    for i, t0 in enumerate(times):
        wn = {es[j]["w"] for j in range(len(times)) if 0 <= times[j] - t0 <= 600}
        peak = max(peak, len(wn))
    vel = len(ws) / max(span, 1.0) if span > 0 else len(ws)
    vols = [x["vol"] for x in es]
    return {"n_buyers": len(ws), "n_elite": len(elite), "span_min": span,
            "peak10": peak, "velocity": vel, "tot_vol": sum(vols),
            "med_vol": statistics.median(vols) if vols else 0}

W, L = [], []
for t, es in bytok.items():
    f = feats(es)
    (W if t in winners else L).append(f)
print(f"tokens with smart buys: {len(W)+len(L)} | winners={len(W)} losers={len(L)}\n")

keys = ["n_buyers", "n_elite", "peak10", "span_min", "velocity", "tot_vol", "med_vol"]
if L:
    print(f"{'feature':10s} {'WINNER med':>11s} {'LOSER med':>10s} {'W/L ratio':>10s} {'sep':>6s}")
    for k in keys:
        wv = [f[k] for f in W]; lv = [f[k] for f in L]
        wm = statistics.median(wv); lm = statistics.median(lv)
        ratio = (wm / lm) if lm else float("inf")
        above = sum(1 for x in wv if x > lm) / len(wv) if wv else 0
        print(f"  {k:8s} {wm:11.2f} {lm:10.2f} {ratio:10.2f} {above*100:5.0f}%")
else:
    print("NO LOSER tokens in dataset — discovered_buys is winners-only (pre-filtered).")
    print("Can't discriminate; descriptive WINNER profile only (need bag/loser tokens to mine separation):")
    for k in keys:
        wv = [f[k] for f in W]
        print(f"  {k:8s} median {statistics.median(wv):8.2f}  p25 {sorted(wv)[len(wv)//4]:.2f}  p75 {sorted(wv)[3*len(wv)//4]:.2f}")

# LEAD-WALLET: who is FIRST into winners most often (the alpha)
print("\nLEAD wallets — first smart buyer of WINNING tokens (alpha candidates):")
lead = collections.Counter()
for t, es in bytok.items():
    if t in winners and es:
        first = min(es, key=lambda x: x["e"])
        lead[first["w"]] += 1
for w, c in lead.most_common(10):
    print(f"  {w[:14]:14s} first-into {c} winners  (n_winners={nwin.get(w,0)}, elite={'Y' if w in ELITE else 'n'})")
