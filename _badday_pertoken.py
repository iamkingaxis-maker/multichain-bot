import json
from collections import defaultdict
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
# per-token: avg pnl across all bot-fills, entry_meta from any
tok=defaultdict(lambda:{'pnls':[],'em':None})
for s in sells:
    a=(s.get('address')or s.get('token')or'').lower()
    em=(buys.get((bot(s),a)) or {}).get('entry_meta') or {}
    tok[a]['pnls'].append(s.get('pnl_pct'))
    if em and tok[a]['em'] is None: tok[a]['em']=em
T=[(sum(v['pnls'])/len(v['pnls']), v['em'] or {}, a, len(v['pnls'])) for a,v in tok.items()]
N=len(T); base=sum(p for p,_,_,_ in T)/N
print(f"DISTINCT TOKENS N={N} mean_pertoken={base:.2f} winrate={sum(1 for p,_,_,_ in T if p>0)/N:.3f}")
def num(em,k):
    v=em.get(k); return v if isinstance(v,(int,float)) else None
def rep(name,pred):
    blk=[(p,a,n) for p,em,a,n in T if pred(em)]
    kept=[p for p,em,a,n in T if not pred(em)]
    if not blk: print(name,'empty'); return
    L=sum(1 for p,_,_ in blk if p<=0); W=len(blk)-L
    rem=sum(p for p,_,_ in blk)
    ka=sum(kept)/len(kept) if kept else 0
    fills=sum(n for _,_,n in blk)
    print(f"{name}: tokens={len(blk)} L={L} W={W} fills={fills} removed_pertok={rem:.1f} kept_mean_after={ka:.2f} (base {base:.2f})")
rep('age<25', lambda em:(num(em,'lifecycle_age_hours') or 1e9)<25)
rep('age<40', lambda em:(num(em,'lifecycle_age_hours') or 1e9)<40)
rep('turnover>=19', lambda em:(num(em,'turnover_h24_ratio') or 0)>=19)
rep('turnover>=20.6', lambda em:(num(em,'turnover_h24_ratio') or 0)>=20.6)
rep('entry_vol>=642k', lambda em:(num(em,'entry_volume_h24_usd') or 0)>=642000)
rep('entry_vol>=883k', lambda em:(num(em,'entry_volume_h24_usd') or 0)>=883000)
rep('top10<60', lambda em:(num(em,'top10_holder_pct') or 100)<60)
rep('pc_h6<-22', lambda em:(num(em,'pc_h6') or 0)<-22)
rep('pc_h6<-32', lambda em:(num(em,'pc_h6') or 0)<-32)
rep('pc_h6<-22 & top10<60', lambda em:(num(em,'pc_h6') or 0)<-22 and (num(em,'top10_holder_pct') or 100)<60)
rep('vol883k & liq<35k', lambda em:(num(em,'entry_volume_h24_usd') or 0)>=883000 and (num(em,'liquidity_usd') or 1e9)<35000)
rep('turnover>=19 & age<40', lambda em:(num(em,'turnover_h24_ratio') or 0)>=19 and (num(em,'lifecycle_age_hours') or 1e9)<40)
rep('entry_vol>=642k & liq<35k', lambda em:(num(em,'entry_volume_h24_usd') or 0)>=642000 and (num(em,'liquidity_usd') or 1e9)<35000)
