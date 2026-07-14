import json
from collections import defaultdict, Counter
from datetime import datetime

def load(path):
    rows=[]
    with open(path) as f:
        for line in f:
            line=line.strip()
            if not line: continue
            try: r=json.loads(line)
            except: continue
            if int(str(r.get('ts',''))[:4] or 0)>=2026:
                rows.append(r)
    return rows

def pe(ts):
    return datetime.fromisoformat(ts.replace('+00:00','+00:00')).timestamp()

rows=load('scratchpad/robinhood_tapes/rh_paper_trades.jsonl')
buys=[r for r in rows if r['ev']=='buy']
sells=[r for r in rows if r['ev']=='sell']
rug=[r for r in rows if r['ev']=='rug_signals']

# Build trips per (bot_id, pool): walk chronologically, open on buy, accumulate sells until fully
bykey=defaultdict(list)
for r in rows:
    if r['ev'] in ('buy','sell'):
        bykey[(r.get('bot_id'), r['pool'])].append(r)

trips=[]
for key, evs in bykey.items():
    evs.sort(key=lambda r: pe(r['ts']))
    cur=None
    for r in evs:
        if r['ev']=='buy':
            if cur is not None:
                trips.append(cur)  # unclosed prior
            cur={'bot_id':key[0],'pool':key[1],'sym':r.get('sym'),'token':r.get('token'),
                 'entry_ts':pe(r['ts']),'entry_usd':r.get('usd',25.0),'dip':r.get('dip_pct'),
                 'liq':r.get('liq'),'age_h':r.get('age_h'),'sells':[],'closed':False}
        elif r['ev']=='sell' and cur is not None:
            cur['sells'].append(r)
            if r.get('fully'):
                cur['closed']=True
                trips.append(cur); cur=None
    if cur is not None:
        trips.append(cur)

# realized return per trip
for t in trips:
    pnl=sum(s.get('pnl_usd',0.0) for s in t['sells'])
    t['pnl_usd']=pnl
    t['ret_pct']=pnl/t['entry_usd']*100.0 if t['entry_usd'] else 0.0
    # time to full close
    if t['sells']:
        t['dur_s']=pe(t['sells'][-1]['ts'])-t['entry_ts']
        t['worst_slice_pct']=min(s.get('pnl_pct',0.0) for s in t['sells'])
    else:
        t['dur_s']=None; t['worst_slice_pct']=None

closed=[t for t in trips if t['closed']]
print('trips total', len(trips), 'closed', len(closed), 'open/unclosed', len(trips)-len(closed))

# distribution of realized return (closed trips)
rets=sorted(t['ret_pct'] for t in closed)
import statistics as st
def pct(a,p):
    if not a: return None
    i=min(len(a)-1,int(p/100*len(a)))
    return round(a[i],1)
print('CLOSED ret_pct dist: min',round(rets[0],1),'p10',pct(rets,10),'p25',pct(rets,25),'p50',pct(rets,50),'p75',pct(rets,75),'p90',pct(rets,90),'max',round(rets[-1],1))
print('mean ret', round(st.mean(rets),2))

# Violent-loss definition: realized ret <= -30% (deep) and fast (dur < 30 min)
for thr in [-20,-30,-40,-50]:
    vl=[t for t in closed if t['ret_pct']<=thr]
    ntok=len(set(t['token'] for t in vl))
    print('ret<=%d%%: n=%d  (%.1f%% of closed trips)  distinct tokens=%d'%(thr,len(vl),len(vl)/len(closed)*100,ntok))

# fast violent: <=-30 and dur<=1800s
fastvl=[t for t in closed if t['ret_pct']<=-30 and (t['dur_s'] or 1e9)<=1800]
print('fast violent (ret<=-30 & dur<=30min):', len(fastvl))

# per-token worst realized (union across bots) -> token-level rug label
tok_worst=defaultdict(lambda:1e9)
tok_sym={}
for t in closed:
    tok_worst[t['token']]=min(tok_worst[t['token']], t['ret_pct'])
    tok_sym[t['token']]=t['sym']
rugtoks=[(tok_sym[k],k,round(v,1)) for k,v in tok_worst.items() if v<=-30]
print('\nDISTINCT TOKENS with a trip <=-30%:', len(rugtoks), 'of', len(tok_worst), 'closed-traded tokens')
for s,k,v in sorted(rugtoks,key=lambda x:x[2]):
    print(f'  {s:12} {v:7.1f}%  {k}')
