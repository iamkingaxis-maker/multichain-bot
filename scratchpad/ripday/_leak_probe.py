# -*- coding: ascii -*-
# Demonstrate: is post60_nf>0 mechanically forced by the flow-trough (NF-min) def?
# And does ANY forward horizon give AUC~0.62 forward-only?
import json,glob,os,bisect
from datetime import datetime
RIP=os.path.dirname(os.path.abspath(__file__)); LIVE=os.path.join(RIP,"live_tapes")
D_FLOOR=400.0;D_FRAC=0.015;REB=0.35;LB=3600.0;PRE=60.0;POST=60.0
def load(p):
    seen=set();tr=[]
    for line in open(p,encoding="utf-8"):
        line=line.strip()
        if not line:continue
        try:r=json.loads(line)
        except:continue
        k=(r.get("ts"),r.get("maker"),r.get("volume_usd"),r.get("kind"))
        if k in seen:continue
        seen.add(k)
        try:ep=datetime.fromisoformat(r["ts"].replace("Z","+00:00")).timestamp()
        except:continue
        v=float(r.get("volume_usd") or 0);sv=v if r.get("kind")=="buy" else -v
        tr.append((ep,sv,v))
    tr.sort();return tr
def auc(sc,lb):
    pos=[s for s,l in zip(sc,lb) if l==1];neg=[s for s,l in zip(sc,lb) if l==0]
    if not pos or not neg:return float("nan")
    c=sum(1 if p>q else (0.5 if p==q else 0) for p in pos for q in neg)
    return c/(len(pos)*len(neg))
def run(H,causal):
    rows=[];paths=sorted(glob.glob(os.path.join(LIVE,"tape_*.jsonl")))
    for path in paths:
        tr=load(path)
        if len(tr)<20:continue
        times=[x[0] for x in tr];vols=[x[2] for x in tr]
        cum=[];s=0.0
        for x in tr:s+=x[1];cum.append(s)
        n=len(tr)
        def nfat(t):
            i=bisect.bisect_right(times,t)-1;return cum[i] if i>=0 else 0.0
        def gwin(a,b):
            i=bisect.bisect_left(times,a);j=bisect.bisect_right(times,b);return sum(vols[i:j])
        i=0;cd=-1
        while i<n:
            t=times[i]
            if t<cd:i+=1;continue
            a=bisect.bisect_left(times,t-LB)
            if i-a<5:i+=1;continue
            peak=max(cum[a:i+1]);D=max(D_FLOOR,D_FRAC*gwin(t-LB,t))
            if peak-cum[i]>=D:
                lo=cum[i];lo_i=i;j=i;fired=None
                while j<n:
                    if cum[j]<lo:lo=cum[j];lo_i=j
                    if cum[j]-lo>=REB*D:fired=j;break
                    j+=1
                if fired is None:i+=1;continue
                t0=times[lo_i]
                if causal:
                    b=bisect.bisect_right(times,t0+POST)
                    if b>lo_i and min(cum[lo_i:b])<lo-1e-6:i=lo_i+1;continue
                if times[0]>t0-PRE or times[-1]<t0+POST+H:i=lo_i+1;cd=t0+1800;continue
                pre60=nfat(t0)-nfat(t0-PRE);post60=nfat(t0+POST)-nfat(t0)
                dec=t0+POST;dNF=nfat(dec);R=max(D_FLOOR,D_FRAC*gwin(t0-LB,t0))
                aa=bisect.bisect_right(times,dec);bb=bisect.bisect_right(times,dec+H)
                out=None
                for k in range(aa,bb):
                    d=cum[k]-dNF
                    if d>=0.5*R:out=1;break
                    if d<=-0.5*R:out=0;break
                if out is None:out=1 if (bb>aa and cum[bb-1]-dNF>0) else 0
                rows.append((post60,pre60,out,os.path.basename(path)));cd=t0+1800;i=lo_i+1
            else:i+=1
    n=len(rows);frac=sum(1 for r in rows if r[0]>0)/n if n else float('nan')
    base=sum(r[2] for r in rows)/n if n else float('nan')
    a=auc([r[0] for r in rows],[r[2] for r in rows])
    p2=[r for r in rows if r[0]>0 and r[1]>-100]
    r2=sum(r[2] for r in p2)/len(p2) if p2 else float('nan')
    print("  H=%4ds %-7s n=%d tok=%d fracPost60>0=%.2f base=%.1f%% AUC=%.3f 2f n=%d CONT=%.1f%%"%(
        H,"CAUSAL" if causal else "ORACLE",n,len(set(r[3] for r in rows)),frac,100*base,a,len(p2),100*r2 if p2 else float('nan')))
print("Mechanical-leakage + horizon sweep (fracPost60>0 near 1.0 under ORACLE = trough def forces it):")
for causal in (False,True):
    for H in (120,300,600,1200):
        run(H,causal)
