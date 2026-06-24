import json, statistics as st
from collections import Counter
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
J=[]
for s in sells:
    addr=(s.get('address')or s.get('token')or'').lower()
    em=(buys.get((bot(s),addr)) or {}).get('entry_meta') or {}
    J.append((s.get('pnl_pct'),em,addr,bot(s)))
def num(em,k):
    v=em.get(k); return v if isinstance(v,(int,float)) else None

def report(name, pred):
    blk=[(p,a,b) for p,em,a,b in J if pred(em)]
    if not blk: print(name,'EMPTY'); return
    L=sum(1 for p,_,_ in blk if p<=0); W=len(blk)-L
    dt=set(a for _,a,_ in blk)
    rem=sum(p for p,_,_ in blk)
    print(f"{name}: n={len(blk)} L={L} W={W} distinct_tokens={len(dt)} removed={rem:.1f}")
    bc=Counter(a for _,a,_ in blk)
    print('   token spread:', bc.most_common(5))
    print('   bots:', Counter(b for _,_,b in blk))

report('age<25', lambda em:(num(em,'lifecycle_age_hours') or 1e9)<25)
report('pc_h6<-22 & top10<60', lambda em:(num(em,'pc_h6') or 0)<-22 and (num(em,'top10_holder_pct') or 100)<60)
report('vol883k & liq<35k', lambda em:(num(em,'entry_volume_h24_usd') or 0)>=883000 and (num(em,'liquidity_usd') or 1e9)<35000)
report('turnover>=20.6', lambda em:(num(em,'turnover_h24_ratio') or 0)>=20.6)
report('turnover>=19', lambda em:(num(em,'turnover_h24_ratio') or 0)>=19)
report('top10<60 single', lambda em:(num(em,'top10_holder_pct') or 100)<60)
report('pc_h6<-32 single', lambda em:(num(em,'pc_h6') or 0)<-32.3)
report('entry_vol>=642k single', lambda em:(num(em,'entry_volume_h24_usd') or 0)>=642000)
