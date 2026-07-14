#!/usr/bin/env python3
import json, os, statistics as st

REPO = r"C:\Users\jcole\multichain-bot"
TAPE_DIR = os.path.join(REPO, "scratchpad", "robinhood_tapes")
rows = json.load(open(os.path.join(TAPE_DIR, "_recon_events2.json")))

def auc(pos, neg):
    if not pos or not neg: return None
    wins = 0.0; n = 0
    for a in pos:
        for b in neg:
            n += 1
            wins += 1.0 if a > b else (0.5 if a == b else 0.0)
    return wins/n if n else None

def med(xs):
    xs=[x for x in xs if x is not None]
    return st.median(xs) if xs else None

# ---- 1. availability-as-signal ----
print("="*70)
print("(1) IS TAPE-DENSITY / AVAILABILITY ITSELF A SIGNAL?")
print("     does 'has >=3 trades in 60s at entry' predict RAN?")
for reg in ["07-10","07-11","07-12","ALL"]:
    sub=[r for r in rows if r["has_outcome"] and (reg=="ALL" or r["regime"]==reg)]
    avail=[r for r in sub if r["avail"]]
    sparse=[r for r in sub if not r["avail"]]
    def rr(g): return (sum(1 for r in g if r["ran"])/len(g)*100) if g else float('nan')
    print(f"  {reg}: avail n={len(avail):3d} run%={rr(avail):5.1f} | sparse n={len(sparse):3d} run%={rr(sparse):5.1f}")

# ---- 2. all features regime-robustness (AUC per regime) ----
FEATS=["sell_rate_60","sell_traj","cum_nf_60","pos_subwins",
       "buy_rate_60","buy_frac_usd_60","n_trades_60","vol_60"]
print("="*70)
print("(2) AUC(RAN) PER REGIME  [robust => same side of 0.5 all 3 days]")
print(f"  {'feature':16s} {'07-10':>8s} {'07-11':>8s} {'07-12':>8s} {'POOLED':>8s}  verdict")
for f in FEATS:
    aucs={}
    for reg in ["07-10","07-11","07-12","ALL"]:
        sub=[r for r in rows if r["has_outcome"] and (reg=="ALL" or r["regime"]==reg)]
        rp=[r[f] for r in sub if r["ran"] and r.get(f) is not None]
        dp=[r[f] for r in sub if not r["ran"] and r.get(f) is not None]
        aucs[reg]=auc(rp,dp)
    def s(a): return f"{a:8.2f}" if a is not None else "     -  "
    days=[aucs[d] for d in ["07-10","07-11","07-12"] if aucs[d] is not None]
    if len(days)==3:
        allhi=all(d>0.55 for d in days); alllo=all(d<0.45 for d in days)
        verdict="ROBUST" if (allhi or alllo) else "flips/flat"
    else:
        verdict="insuff"
    print(f"  {f:16s} {s(aucs['07-10'])} {s(aucs['07-11'])} {s(aucs['07-12'])} {s(aucs['ALL'])}  {verdict}")

# ---- 3. alternate outcome thresholds (is null robust to label cutoff?) ----
print("="*70)
print("(3) cum_nf_60 AUC under alternate RAN cutoffs (per regime)")
for cut in [0.0, 3.0, 6.0, 10.0]:
    line=f"  RAN>= {cut:4.0f}%: "
    for reg in ["07-10","07-11","07-12"]:
        sub=[r for r in rows if r["has_outcome"] and r["regime"]==reg and r.get("cum_nf_60") is not None]
        rp=[r["cum_nf_60"] for r in sub if (r["best_pnl"] or -99)>=cut]
        dp=[r["cum_nf_60"] for r in sub if (r["best_pnl"] or -99)<cut]
        a=auc(rp,dp)
        line+=f"{reg}={a:.2f} " if a is not None else f"{reg}=  -  "
    print(line)

# ---- 4. honest gate sweep: skip if cum_nf_60 < thresh (avail only) ----
print("="*70)
print("(4) GATE SWEEP: 'downsize/skip if cum_nf_60 < T' (avail entries only)")
print("     winner-kill = RAN wrongly skipped; loser-avoid = DIED correctly skipped")
for T in [200, 400, 800, 1500]:
    print(f"  T={T}:")
    for reg in ["07-10","07-11","07-12"]:
        sub=[r for r in rows if r["has_outcome"] and r["regime"]==reg and r.get("cum_nf_60") is not None]
        ran=[r for r in sub if r["ran"]]; died=[r for r in sub if not r["ran"]]
        wk=sum(1 for r in ran if r["cum_nf_60"]<T)
        la=sum(1 for r in died if r["cum_nf_60"]<T)
        print(f"    {reg}: winner-kill {wk}/{len(ran)} | loser-avoid {la}/{len(died)}")

# ---- 5. net-$/position sanity ----
print("="*70)
print("(5) NET-$/POSITION and win-rate sanity (per regime)")
for reg in ["07-10","07-11","07-12","ALL"]:
    sub=[r for r in rows if r["has_outcome"] and (reg=="ALL" or r["regime"]==reg)]
    allnet=[x for r in sub for x in r["pos_net_usd"]]
    wr=sum(1 for r in sub if r["ran"])/len(sub)*100 if sub else 0
    print(f"  {reg}: events={len(sub):3d} run%={wr:5.1f} positions={len(allnet):4d} "
          f"mean_net=${st.mean(allnet):+.2f} median_net=${st.median(allnet):+.2f}" if allnet else f"  {reg}: none")
