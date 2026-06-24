import json,datetime
from collections import defaultdict,Counter
d=json.load(open('_full_trades.json'))
t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
def addr(x): return (x.get('address') or x.get('token') or '').lower()
def day(x):
    return str(x.get('time'))[:10]
buys={(bot(x),addr(x)):x for x in t if x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
pos=defaultdict(list)
for s in sells:
    k=(bot(s),addr(s))
    if k in buys: pos[k].append(s)

# Build one record per position: real_pct = haircut-corrected, sell_fraction-weighted
recs=[]
for k,ss in pos.items():
    b=buys[k]; em=b.get('entry_meta') or {}
    # weighted real_pct
    tot_frac=0.0; acc=0.0
    for s in ss:
        pnl=s.get('pnl_pct'); mae=s.get('mae_pct')
        rp=pnl
        if isinstance(mae,(int,float)) and pnl<mae:
            rp=mae  # clamp up to traded low
        f=s.get('sell_fraction') or (1.0/len(ss))
        tot_frac+=f; acc+=rp*f
    real=acc/tot_frac if tot_frac else acc
    recs.append({'bot':k[0],'addr':k[1],'day':day(b),'real':real,'win':1 if real>0 else 0,'em':em,'b':b})

print('records',len(recs))
print('by day', Counter(r['day'] for r in recs))
for dd in ['2026-06-21','2026-06-22','2026-06-23']:
    rs=[r for r in recs if r['day']==dd]
    if rs:
        import statistics
        wr=sum(r['win'] for r in rs)/len(rs)
        mean=statistics.mean(r['real'] for r in rs)
        print(f"{dd}: n={len(rs)} WR={wr:.2f} mean_real={mean:.2f}%")
# overall
import statistics
print('OVERALL n=%d WR=%.2f mean_real=%.2f'%(len(recs),sum(r['win'] for r in recs)/len(recs),statistics.mean(r['real'] for r in recs)))
json.dump([{kk:r[kk] for kk in ('bot','addr','day','real','win')}|{'em':r['em']} for r in recs], open('_badday_recs.json','w'))
