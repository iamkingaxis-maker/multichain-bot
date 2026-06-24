import json
from collections import defaultdict
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
print("n_buys",len(buys),"n_sells",len(sells))

def day(x):
    ts=x.get('timestamp') or x.get('ts') or x.get('time') or ''
    return str(ts)[:10]

def real_pct(s):
    p=s.get('pnl_pct'); m=s.get('mae_pct')
    if isinstance(m,(int,float)) and p<m:
        return max(p,m)
    return p

rows=[]
for s in sells:
    k=(bot(s),(s.get('address')or s.get('token')or'').lower())
    b=buys.get(k)
    if not b: continue
    em=b.get('entry_meta') or {}
    liq=em.get('liquidity_usd'); nf=em.get('net_flow_60s_imbalance')
    rows.append({'day':day(s),'addr':k[1],'liq':liq,'nf':nf,'real':real_pct(s),'pnl':s.get('pnl_pct')})

print("joined rows",len(rows))
# how many have both inputs
have=[r for r in rows if isinstance(r['liq'],(int,float)) and isinstance(r['nf'],(int,float))]
print("rows with liq AND nf present:",len(have),"of",len(rows))
missing_liq=sum(1 for r in rows if not isinstance(r['liq'],(int,float)))
missing_nf=sum(1 for r in rows if not isinstance(r['nf'],(int,float)))
print("missing liq:",missing_liq,"missing nf:",missing_nf)

def stats(rs):
    n=len(rs)
    if n==0: return (0,None,None)
    wr=sum(1 for r in rs if r['real']>0)/n
    mean=sum(r['real'] for r in rs)/n
    return (n,round(wr,2),round(mean,2))

BLOCK=lambda r: (r['liq']<30000) and (r['nf']<0.10)
blk=[r for r in have if BLOCK(r)]
keep=[r for r in have if not BLOCK(r)]
print("\n=== OVERALL (rows with both inputs) ===")
print("BLOCK",stats(blk))
print("KEEP ",stats(keep))

print("\n=== PER DAY ===")
for dd in sorted(set(r['day'] for r in have)):
    db=[r for r in have if r['day']==dd]
    bb=[r for r in db if BLOCK(r)]
    kk=[r for r in db if not BLOCK(r)]
    print(dd,"| BLOCK",stats(bb),"| KEEP",stats(kk))

print("\n=== TOKEN CONCENTRATION in BLOCK ===")
for dd in sorted(set(r['day'] for r in have))+['ALL']:
    rs=blk if dd=='ALL' else [r for r in blk if r['day']==dd]
    if not rs: 
        print(dd,"no block rows"); continue
    c=defaultdict(int)
    for r in rs: c[r['addr']]+=1
    top=sorted(c.values(),reverse=True)[0]
    print(dd,"blockn",len(rs),"distinct",len(c),"topshare",round(top/len(rs),2))

print("\n=== 06-22 INVERSION token breakdown (BLOCK side) ===")
b22=[r for r in blk if r['day']=='2026-06-22']
c=defaultdict(list)
for r in b22: c[r['addr']].append(r['real'])
for a,v in sorted(c.items(),key=lambda kv:-len(kv[1])):
    print(a[:12],"n",len(v),"mean",round(sum(v)/len(v),2),"wr",round(sum(1 for x in v if x>0)/len(v),2))

print("\n=== Sanity: are liq/nf actually decision-time (in entry_meta of BUY)? ===")
# confirm we pulled from buy.entry_meta not sell
sample=[b for b in buys.values()][0]
em=sample.get('entry_meta') or {}
print("buy keys present:",'entry_meta' in sample, "| em has liq:",'liquidity_usd' in em,"nf:",'net_flow_60s_imbalance' in em)
# verify no outcome fields used as input: we only used liq and nf. confirm.
print("inputs used: liquidity_usd, net_flow_60s_imbalance -> both pre-entry")

print("\n=== KEEP side positive every day? ===")
for dd in sorted(set(r['day'] for r in have)):
    kk=[r for r in have if r['day']==dd and not BLOCK(r)]
    print(dd,"KEEP mean",round(sum(r['real'] for r in kk)/len(kk),2))
