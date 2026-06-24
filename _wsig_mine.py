import json, statistics
from collections import defaultdict

d=json.load(open(r'C:\Users\jcole\multichain-bot\_wsig_full.json'))
t=d if isinstance(d,list) else d['trades']
gp=[x for x in t if x.get('bot_id')=='pool_a_goodpond']

# sort by time ascending
def ts(x): return x['time']
gp.sort(key=ts)

buys=[x for x in gp if x['type']=='buy']
sells=[x for x in gp if x['type']=='sell']
print('buys',len(buys),'sells',len(sells))

# Build per-address sell queue (chrono)
sells_by_addr=defaultdict(list)
for s in sells:
    sells_by_addr[s['address']].append(s)
for a in sells_by_addr: sells_by_addr[a].sort(key=ts)

# join each buy to next sell same address after buy time
pairs=[]
used=defaultdict(int)
for b in sorted(buys,key=ts):
    addr=b['address']; bt=b['time']
    cand=[s for s in sells_by_addr.get(addr,[]) if s['time']>bt]
    if not cand: 
        pairs.append((b,None)); continue
    s=cand[0]
    pairs.append((b,s))

joined=[(b,s) for (b,s) in pairs if s is not None]
print('joined pairs', len(joined))

def pnl(s):
    for k in ('pnl_pct','pnl_percent','realized_pnl_pct','pct'):
        if k in s and s[k] is not None: return float(s[k])
    return None

rows=[]
for b,s in joined:
    p=pnl(s)
    print('  addr',b['address'][:8],'pnl_pct=',p,'sell_keys_has_pnl',[k for k in s.keys() if 'pnl' in k.lower() or 'pct' in k.lower()])
    rows.append((b,s,p))
