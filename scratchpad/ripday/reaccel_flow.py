import json, glob, os, math, bisect
from datetime import datetime
def ep(ts): return datetime.fromisoformat(ts).timestamp()

def load_tape(fn):
    rows=[]
    for line in open(fn,encoding='utf-8'):
        line=line.strip()
        if not line: continue
        try: r=json.loads(line)
        except: continue
        rows.append((ep(r['ts']), r['kind'], float(r['volume_usd']), r['maker']))
    rows.sort(); return rows

# gather densest tapes from both dirs, dedupe by prefix keep-larger
cands={}
for d in ['.','live_tapes']:
    for fn in glob.glob(os.path.join(d,'tape_*.jsonl')):
        k=os.path.basename(fn)[5:13]
        n=sum(1 for _ in open(fn,encoding='utf-8'))
        if k not in cands or n>cands[k][1]: cands[k]=(fn,n)
tapes=[fn for fn,n in cands.values() if n>=150]  # dense enough
print("dense tapes (>=150 trades):",len(tapes))

edges=[(0,10),(10,20),(20,30),(30,45),(45,60)]
def episodes(fn):
    tape=load_tape(fn)
    if len(tape)<150: return []
    t0s=tape[0][0]; tts=[r[0] for r in tape]
    # build per-second cumulative net-USD, find local drawdown minima (flow trough)
    # bucket into 10s grid
    T0=tts[0]; T1=tts[-1]
    # cumulative net
    cum=0.0; series=[]  # (t,cum)
    for (t,k,v,m) in tape:
        cum+= v if k=='buy' else -v
        series.append((t,cum))
    # candidate low = local min of cum over +-90s window, with prior rise >=X
    lows=[]
    st=[c for _,c in series]
    ts=[t for t,_ in series]
    n=len(series)
    for i in range(n):
        a=bisect.bisect_left(ts,ts[i]-90); b=bisect.bisect_right(ts,ts[i]+90)
        if st[i]!=min(st[a:b]): continue
        # prior rise: max cum in [ts[i]-600, ts[i]) minus st[i]
        pa=bisect.bisect_left(ts,ts[i]-600)
        if pa>=i: continue
        priormax=max(st[pa:i])
        if priormax-st[i] < 0: continue
        lows.append(ts[i])
    # dedupe within 120s
    out=[]
    for t in lows:
        if out and t-out[-1]<120: continue
        out.append(t)
    eps=[]
    for t0 in out:
        # need forward coverage to t0+300 and 60s signal
        if t0+300 > T1: continue
        lo_i=bisect.bisect_left(tts,t0); hi_i=bisect.bisect_right(tts,t0+60)
        seg=tape[lo_i:hi_i]
        buckets=[]
        for (a,b) in edges:
            buy=sell=0.0; bc=0; brs=set()
            for (tt,k,v,m) in seg:
                if t0+a<=tt<t0+b:
                    if k=='buy': buy+=v;bc+=1;brs.add(m)
                    else: sell+=v
            buckets.append((buy,sell,bc,brs))
        nonempty=sum(1 for bk in buckets if (bk[2]>0 or bk[1]>0))
        if nonempty<3: continue
        nets=[bk[0]-bk[1] for bk in buckets]
        fpos=sum(1 for x in nets if x>0)/5.0
        fpos3=sum(1 for x in nets[:3] if x>0)
        net0=1 if nets[0]>0 else 0
        early=any(x>0 for x in nets[:3]); late=any(x>0 for x in nets[3:])
        sustain=1 if (early and late) else 0
        # LABEL: forward net-USD over [60,300] strictly after signal window
        f_i=bisect.bisect_left(tts,t0+60); g_i=bisect.bisect_right(tts,t0+300)
        fwd=tape[f_i:g_i]
        fbuy=sum(v for (tt,k,v,m) in fwd if k=='buy'); fsell=sum(v for (tt,k,v,m) in fwd if k=='sell')
        cont=1 if (fbuy-fsell)>0 else 0
        # whale drop top wallet in signal window
        wn={}
        for (tt,k,v,m) in seg: wn[m]=wn.get(m,0.0)+(v if k=='buy' else -v)
        topw=max(wn.items(),key=lambda x:abs(x[1]))[0] if wn else None
        nets_dw=[]
        for (a,b) in edges:
            bu=se=0.0
            for (tt,k,v,m) in seg:
                if t0+a<=tt<t0+b and m!=topw:
                    if k=='buy':bu+=v
                    else: se+=v
            nets_dw.append(bu-se)
        fpos_dw=sum(1 for x in nets_dw if x>0)/5.0
        # whale drop in LABEL too (fair): drop same/any top forward wallet
        wnf={}
        for (tt,k,v,m) in fwd: wnf[m]=wnf.get(m,0.0)+(v if k=='buy' else -v)
        topwf=max(wnf.items(),key=lambda x:abs(x[1]))[0] if wnf else None
        fbuy2=sum(v for (tt,k,v,m) in fwd if k=='buy' and m!=topwf)
        fsell2=sum(v for (tt,k,v,m) in fwd if k=='sell' and m!=topwf)
        cont_dw=1 if (fbuy2-fsell2)>0 else 0
        eps.append(dict(pair=os.path.basename(fn)[5:13],t0=t0,fpos=fpos,fpos3=fpos3,net0=net0,
            sustain=sustain,cont=cont,cont_dw=cont_dw,fpos_dw=fpos_dw,nonempty=nonempty))
    return eps

allep=[]
for fn in tapes:
    try: allep.extend(episodes(fn))
    except Exception as e: pass
print("flow episodes:",len(allep),"tokens:",len(set(e['pair'] for e in allep)))
json.dump(allep,open('_reaccel_flow.json','w'))

def rate(eps,lab='cont'):
    n=len(eps); return (sum(e[lab] for e in eps)/n if n else 0,n)
def z(a,b,lab='cont'):
    pa,na=rate(a,lab); pb,nb=rate(b,lab)
    if na==0 or nb==0: return 0
    p=(pa*na+pb*nb)/(na+nb); se=math.sqrt(p*(1-p)*(1/na+1/nb))
    return (pa-pb)/se if se>0 else 0
def rep(eps,cond,name,lab='cont'):
    f=[e for e in eps if cond(e)]; nf=[e for e in eps if not cond(e)]; b=rate(eps,lab)
    print(f"  {name}: fire {rate(f,lab)[0]*100:.1f}%(n={rate(f,lab)[1]}) notfire {rate(nf,lab)[0]*100:.1f}% base {b[0]*100:.1f}%(N={b[1]}) lift {(rate(f,lab)[0]-b[0])*100:+.1f}pp fvsn {(rate(f,lab)[0]-rate(nf,lab)[0])*100:+.1f}pp z={z(f,nf,lab):.2f}")

print("\n=== ALL (label=forward net-USD[60,300]>0) ===")
rep(allep, lambda e:e['fpos']>=0.6, "fpos>=0.6 sustain")
rep(allep, lambda e:e['fpos3']>=2, "fpos3>=2/3 RT")
rep(allep, lambda e:e['net0']==1, "net0 single-turn")
rep(allep, lambda e:e['sustain']==1, "sustain early&late")
print("\n=== WHALE: drop top wallet BOTH sides ===")
rep(allep, lambda e:e['fpos_dw']>=0.6, "fpos_dw>=0.6", lab='cont_dw')
print("\n=== OOS token split ===")
A=[e for e in allep if hash(e['pair'])%2==0]; B=[e for e in allep if hash(e['pair'])%2==1]
rep(A, lambda e:e['fpos']>=0.6, "A fpos>=0.6")
rep(B, lambda e:e['fpos']>=0.6, "B fpos>=0.6")
print("\n=== drop top-3 tokens by episode count ===")
from collections import Counter
tc=Counter(e['pair'] for e in allep); top3=[p for p,_ in tc.most_common(3)]
print("drop",top3,[tc[p] for p in top3])
rep([e for e in allep if e['pair'] not in top3], lambda e:e['fpos']>=0.6, "fpos>=0.6 -top3tok")
print("\n=== per-token directional (>=6 eps) ===")
byp={}
for e in allep: byp.setdefault(e['pair'],[]).append(e)
w=t=0
for p,es in byp.items():
    if len(es)<6: continue
    f=[e for e in es if e['fpos']>=0.6]; nf=[e for e in es if e['fpos']<0.6]
    if not f or not nf: continue
    t+=1; w+= 1 if rate(f)[0]>rate(nf)[0] else 0
print(f"fire>notfire in {w}/{t} tokens")
