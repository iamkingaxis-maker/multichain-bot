import json, statistics as st
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
def blocked(h6,h1):
    return isinstance(h6,(int,float)) and isinstance(h1,(int,float)) and h6 < -1.0 and h1 < 0
# distinct TOKENS in blocked cohort (the 4 bots mirror the same tokens -> n=48 is really ~12 token-events)
toks={}
for s in sells:
    key=(bot(s),(s.get('address')or s.get('token')or'').lower())
    em=(buys.get(key) or {}).get('entry_meta') or {}
    if blocked(em.get('sol_pc_h6'),em.get('sol_pc_h1')):
        addr=(s.get('address')or s.get('token')or'').lower()
        toks.setdefault(addr,[]).append(s['pnl_pct'])
print("distinct tokens in blocked cohort:",len(toks))
for a,v in sorted(toks.items(),key=lambda kv:sum(kv[1])):
    print(" ",a[:8],"copies=",len(v),"pnls=",[round(x,1) for x in v])
# de-dup to one record per token (mean across bots)
ded=[st.mean(v) for v in toks.values()]
print("\nde-duped blocked token-events:",len(ded))
print("token-level removedPnl(mean):",round(sum(ded),1)," L=",sum(1 for p in ded if p<=0)," W=",sum(1 for p in ded if p>0))
