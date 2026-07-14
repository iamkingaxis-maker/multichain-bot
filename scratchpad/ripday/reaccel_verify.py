import json, glob, os, math
from datetime import datetime

def ep(ts):
    return datetime.fromisoformat(ts).timestamp()

match=json.load(open('_match.json'))
print("pairs:",len(match))

def load_bars(fn):
    d=json.load(open(fn)); return d['bars']

def load_tape(fn):
    rows=[]
    for line in open(fn,encoding='utf-8'):
        line=line.strip()
        if not line: continue
        try: r=json.loads(line)
        except: continue
        rows.append((ep(r['ts']), r['kind'], float(r['volume_usd']), r['maker']))
    rows.sort()
    return rows

# ---- retrace-low detection (price, forward-only-flaggable) ----
W=8  # local min half-window (bars)
def find_lows(bars):
    # bars: [ts,o,h,l,c,v]; 60s
    n=len(bars); lows=[]
    lowser=[b[3] for b in bars]; highser=[b[2] for b in bars]
    for i in range(n):
        lo=lowser[i]
        a=max(0,i-W); b=min(n,i+W+1)
        if lo!=min(lowser[a:b]): continue
        # prior pump: max high in preceding 30 bars
        pa=max(0,i-30)
        if pa>=i: continue
        pmax=max(highser[pa:i]) if i>pa else lo
        decl=(pmax-lo)/pmax if pmax>0 else 0
        if decl<0.08: continue
        lows.append(i)
    # dedupe within 10 bars keep-lower
    out=[]
    for i in lows:
        if out and (bars[i][0]-bars[out[-1]][0])<600:
            if lowser[i]<lowser[out[-1]]: out[-1]=i
            continue
        out.append(i)
    return out

def netbuckets(tape, t0, edges):
    # edges list of (a,b) seconds relative to t0
    res=[]
    for (a,b) in edges:
        buy=0.0; sell=0.0; bc=0; buyers=set()
        for (t,k,v,m) in tape:
            if t< t0+a: continue
            if t>=t0+b: break_=False
            if t>=t0+b: continue
            if t< t0+a or t>=t0+b: continue
            if k=='buy': buy+=v; bc+=1; buyers.add(m)
            else: sell+=v
        res.append((buy,sell,bc,len(buyers)))
    return res

# faster: index tape once per episode via bisect
import bisect
def build_episodes(pair,bf,tf):
    bars=load_bars(bf); tape=load_tape(tf)
    if len(tape)<20: return []
    tts=[r[0] for r in tape]
    lows=find_lows(bars)
    eps=[]
    for i in lows:
        bt=bars[i][0]; lowp=bars[i][3]
        t0=bt+30  # pure-price low epoch (mid-bar)
        # need forward price for label: find bar close ~ t0+360 and price ~t0+60
        def price_at(tt):
            # last bar with ts<=tt -> its close
            idx=None
            for j,b in enumerate(bars):
                if b[0]<=tt: idx=j
                else: break
            return bars[idx][3+1] if idx is not None else None  # close index 4
        p60=price_at(t0+60); p360=price_at(t0+360); p600=price_at(t0+600)
        if p60 is None or p360 is None: continue
        # signal buckets over 0-60
        edges=[(0,10),(10,20),(20,30),(30,45),(45,60)]
        lo_i=bisect.bisect_left(tts,t0)
        hi_i=bisect.bisect_right(tts,t0+60)
        seg=tape[lo_i:hi_i]
        buckets=[]
        for (a,b) in edges:
            buy=sell=0.0; bc=0; brs=set()
            for (t,k,v,m) in seg:
                if t0+a<=t<t0+b:
                    if k=='buy': buy+=v;bc+=1;brs.add(m)
                    else: sell+=v
            buckets.append((buy,sell,bc,len(brs)))
        nets=[b[0]-b[1] for b in buckets]
        fpos=sum(1 for x in nets if x>0)/5.0
        fpos3=sum(1 for x in nets[:3] if x>0)  # 0..30s count (RT)
        net0=1 if nets[0]>0 else 0
        early=any(x>0 for x in nets[:3]); late=any(x>0 for x in nets[3:])
        sustain=1 if (early and late) else 0
        bc_tot=sum(b[2] for b in buckets)
        buyers_tot=len(set().union(*[set() for _ in buckets])) # placeholder
        # distinct buyers over 0-60
        brs=set(m for (t,k,v,m) in seg if k=='buy')
        # pump magnitude: max high in 0-60 vs lowp
        pumphi=lowp
        for b in bars:
            if t0<=b[0]<=t0+60: pumphi=max(pumphi,b[2])
        pump=(pumphi-lowp)/lowp if lowp>0 else 0
        # labels
        cont = 1 if (p360> p60) else 0            # rises AFTER signal window
        cont_up3 = 1 if (p360>= lowp*1.03) else 0
        # top wallet net contribution for whale test: identify max single-wallet buy net
        wallet_net={}
        for (t,k,v,m) in seg:
            wallet_net[m]=wallet_net.get(m,0.0)+(v if k=='buy' else -v)
        top_w=max(wallet_net.items(), key=lambda x:abs(x[1]))[0] if wallet_net else None
        # recompute nets dropping top wallet
        nets_dw=[]
        for (a,b) in edges:
            buy=sell=0.0
            for (t,k,v,m) in seg:
                if t0+a<=t<t0+b and m!=top_w:
                    if k=='buy':buy+=v
                    else: sell+=v
            nets_dw.append(buy-sell)
        fpos_dw=sum(1 for x in nets_dw if x>0)/5.0
        eps.append(dict(pair=pair,t0=t0,fpos=fpos,fpos3=fpos3,net0=net0,sustain=sustain,
                        bc=bc_tot,buyers=len(brs),pump=pump,cont=cont,cont_up3=cont_up3,
                        fpos_dw=fpos_dw,ntr=len(seg)))
    return eps

all_eps=[]
for p,bf,tf in match:
    try:
        all_eps.extend(build_episodes(p,bf,tf))
    except Exception as e:
        pass
print("episodes:",len(all_eps),"tokens:",len(set(e['pair'] for e in all_eps)))
json.dump(all_eps, open('_reaccel_eps.json','w'))

def prec(eps,label='cont'):
    if not eps: return (0,0)
    n=len(eps); c=sum(e[label] for e in eps)
    return (c/n, n)

base=prec(all_eps)
fired=[e for e in all_eps if e['fpos']>=0.6]
notf=[e for e in all_eps if e['fpos']<0.6]
print("=== LABEL cont (p360>p60) ===")
print("base",round(base[0]*100,1),base[1])
print("fpos>=0.6",round(prec(fired)[0]*100,1),prec(fired)[1])
print("not",round(prec(notf)[0]*100,1),prec(notf)[1])
# z test
def ztest(a,b):
    pa,na=prec(a); pb,nb=prec(b)
    if na==0 or nb==0: return 0
    p=(pa*na+pb*nb)/(na+nb)
    se=math.sqrt(p*(1-p)*(1/na+1/nb))
    return (pa-pb)/se if se>0 else 0
print("z fired vs not",round(ztest(fired,notf),2))
print("lift",round((prec(fired)[0]-base[0])*100,1),"pp; fired-vs-notfired",round((prec(fired)[0]-prec(notf)[0])*100,1),"pp")
