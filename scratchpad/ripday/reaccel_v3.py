import json, glob, os, math, bisect
from datetime import datetime
def ep(ts): return datetime.fromisoformat(ts).timestamp()
match=json.load(open('_match.json'))

def load_bars(fn): return json.load(open(fn))['bars']
def load_tape(fn):
    rows=[]
    for line in open(fn,encoding='utf-8'):
        line=line.strip()
        if not line: continue
        try: r=json.loads(line)
        except: continue
        rows.append((ep(r['ts']), r['kind'], float(r['volume_usd']), r['maker']))
    rows.sort(); return rows

W=8
def find_lows(bars, pump_min=0.0):
    n=len(bars); lowser=[b[3] for b in bars]; highser=[b[2] for b in bars]
    lows=[]
    for i in range(n):
        lo=lowser[i]; a=max(0,i-W); b=min(n,i+W+1)
        if lo!=min(lowser[a:b]): continue
        pa=max(0,i-30)
        if pa>=i: continue
        pmax=max(highser[pa:i])
        if lo<=0: continue
        if (pmax-lo)/lo < pump_min: continue   # genuine pump before the low
        lows.append(i)
    out=[]
    for i in lows:
        if out and (bars[i][0]-bars[out[-1]][0])<600:
            if lowser[i]<lowser[out[-1]]: out[-1]=i
            continue
        out.append(i)
    return out

edges=[(0,10),(10,20),(20,30),(30,45),(45,60)]
def close_at(bars,tt):
    idx=None
    for j,b in enumerate(bars):
        if b[0]<=tt: idx=j
        else: break
    return bars[idx][4] if idx is not None else None

def episodes_for(pair,bf,tf,forward_only=False):
    bars=load_bars(bf); tape=load_tape(tf)
    if len(tape)<20: return []
    tts=[r[0] for r in tape]
    lows=find_lows(bars)
    eps=[]
    for i in lows:
        bt=bars[i][0]; lowp=bars[i][3]; t0=bt+30
        p60=close_at(bars,t0+60); p360=close_at(bars,t0+360)
        if p60 is None or p360 is None: continue
        lo_i=bisect.bisect_left(tts,t0); hi_i=bisect.bisect_right(tts,t0+60)
        seg=tape[lo_i:hi_i]
        if not seg: continue
        buckets=[]
        for (a,b) in edges:
            buy=sell=0.0; bc=0; brs=set()
            for (t,k,v,m) in seg:
                if t0+a<=t<t0+b:
                    if k=='buy': buy+=v;bc+=1;brs.add(m)
                    else: sell+=v
            buckets.append((buy,sell,bc,brs))
        nets=[b[0]-b[1] for b in buckets]
        nonempty=sum(1 for b in buckets if (b[2]>0 or b[1]>0))
        fpos=sum(1 for x in nets if x>0)/5.0
        fpos3=sum(1 for x in nets[:3] if x>0)
        net0=1 if nets[0]>0 else 0
        early=any(x>0 for x in nets[:3]); late=any(x>0 for x in nets[3:])
        sustain=1 if (early and late) else 0
        bc_tot=sum(b[2] for b in buckets)
        brs=set().union(*[b[3] for b in buckets])
        pumphi=lowp
        for b in bars:
            if t0<=b[0]<=t0+60: pumphi=max(pumphi,b[2])
        pump=(pumphi-lowp)/lowp if lowp>0 else 0
        cont=1 if p360>p60 else 0
        # whale drop: top-abs-net wallet
        wn={}
        for (t,k,v,m) in seg:
            wn[m]=wn.get(m,0.0)+(v if k=='buy' else -v)
        topw=max(wn.items(),key=lambda x:abs(x[1]))[0] if wn else None
        nets_dw=[]
        for (a,b) in edges:
            buy=sell=0.0
            for (t,k,v,m) in seg:
                if t0+a<=t<t0+b and m!=topw:
                    if k=='buy':buy+=v
                    else: sell+=v
            nets_dw.append(buy-sell)
        fpos_dw=sum(1 for x in nets_dw if x>0)/5.0
        eps.append(dict(pair=pair,t0=t0,fpos=fpos,fpos3=fpos3,net0=net0,sustain=sustain,
            bc=bc_tot,buyers=len(brs),pump=pump,cont=cont,fpos_dw=fpos_dw,
            ntr=len(seg),nonempty=nonempty))
    return eps

allep=[]
for p,bf,tf in match:
    try: allep.extend(episodes_for(p,bf,tf))
    except: pass
json.dump(allep,open('_reaccel_v3.json','w'))
comp=[e for e in allep if e['nonempty']>=3]   # signal actually computable
print("total lows w/ trades:",len(allep),"| computable(>=3 nonempty buckets):",len(comp),
      "| tokens:",len(set(e['pair'] for e in comp)))

def rate(eps,lab='cont'):
    n=len(eps); return (sum(e[lab] for e in eps)/n if n else 0, n)
def z(a,b):
    pa,na=rate(a); pb,nb=rate(b)
    if na==0 or nb==0: return 0
    p=(pa*na+pb*nb)/(na+nb); se=math.sqrt(p*(1-p)*(1/na+1/nb))
    return (pa-pb)/se if se>0 else 0

def report(eps,cond,name):
    f=[e for e in eps if cond(e)]; nf=[e for e in eps if not cond(e)]
    b=rate(eps)
    print(f"  {name}: fire {rate(f)[0]*100:.1f}% (n={rate(f)[1]}) vs notfire {rate(nf)[0]*100:.1f}% | base {b[0]*100:.1f}% (N={b[1]}) | lift {(rate(f)[0]-b[0])*100:+.1f}pp fvsn {(rate(f)[0]-rate(nf)[0])*100:+.1f}pp z={z(f,nf):.2f}")

print("\n=== ALL COMPUTABLE EPISODES ===")
report(comp, lambda e:e['fpos']>=0.6, "fpos>=0.6 (sustain)")
report(comp, lambda e:e['fpos3']>=2, "fpos3>=2/3 (RT 30s)")
report(comp, lambda e:e['net0']==1, "net0>0 (single turn)")
report(comp, lambda e:e['sustain']==1, "sustain(early&late)")
report(comp, lambda e:e['pump']>0.10, "pump>10% (magnitude)")
report(comp, lambda e:e['fpos_dw']>=0.6, "fpos>=0.6 DROP-TOP-WALLET")

print("\n=== OUT-OF-SAMPLE: token-hash split ===")
A=[e for e in comp if hash(e['pair'])%2==0]; B=[e for e in comp if hash(e['pair'])%2==1]
print("half A tokens",len(set(e['pair'] for e in A)),"half B",len(set(e['pair'] for e in B)))
report(A, lambda e:e['fpos']>=0.6, "A fpos>=0.6")
report(B, lambda e:e['fpos']>=0.6, "B fpos>=0.6")

print("\n=== WHALE: drop top-2 tokens by episode count ===")
from collections import Counter
tc=Counter(e['pair'] for e in comp); top2=[p for p,_ in tc.most_common(2)]
print("dropping",top2, [tc[p] for p in top2])
comp2=[e for e in comp if e['pair'] not in top2]
report(comp2, lambda e:e['fpos']>=0.6, "fpos>=0.6 (top2 tok dropped)")

print("\n=== per-token directional robustness (>=4 eps) ===")
byp={}
for e in comp: byp.setdefault(e['pair'],[]).append(e)
wins=0;tot=0
for p,es in byp.items():
    if len(es)<4: continue
    f=[e for e in es if e['fpos']>=0.6]; nf=[e for e in es if e['fpos']<0.6]
    if not f or not nf: continue
    tot+=1
    if rate(f)[0]>rate(nf)[0]: wins+=1
print(f"tokens where fire>notfire: {wins}/{tot}")
