import json,collections
rows=json.load(open('_full_trades.json'))
# group legs by address
by_addr=collections.defaultdict(list)
for r in rows:
    a=r.get('address')
    if a: by_addr[a].append(r)

def gates_from_em(em):
    g={}
    # recorded verdicts (populated)
    g['falling_knife']= em.get('filter_falling_knife_verdict')=='BLOCK'
    g['post_pump_corpse']= em.get('filter_post_pump_corpse_verdict')=='BLOCK'
    g['steep_fall_1m']= em.get('filter_1m_steep_fall_verdict')=='BLOCK'
    # computed (verdict fields unpopulated)
    cr=em.get('1m_consec_red')
    g['consec_red_knife']= (isinstance(cr,(int,float)) and cr>=3)
    pch6=em.get('pc_h6'); liq=em.get('liquidity_usd')
    if pch6 is None and liq is None:
        g['structure_edge']=None
    else:
        inside=( (isinstance(pch6,(int,float)) and pch6>=0) or (isinstance(liq,(int,float)) and liq>=48000) )
        g['structure_edge']= (not inside)
    return g

idx={}
for a,legs in by_addr.items():
    # entry leg with gate meta
    em=None
    for l in legs:
        m=l.get('entry_meta')
        if isinstance(m,dict) and 'filter_falling_knife_verdict' in m:
            em=m; break
    # realized fraction-weighted pnl_pct over fully_closed legs
    num=den=0.0; npos=0; nclosed=0
    for l in legs:
        if l.get('fully_closed'):
            f=l.get('sell_fraction') or 1.0; p=l.get('pnl_pct')
            if p is not None:
                num+=p*f; den+=f; nclosed+=1
    our_pnl=(num/den) if den>0 else None
    rec={'gates':gates_from_em(em) if em else None,'our_pnl_pct':our_pnl,'nclosed':nclosed,'nlegs':len(legs)}
    idx[a]=rec
json.dump(idx,open('scratchpad_ourindex.json','w'))
withgates=sum(1 for v in idx.values() if v['gates'])
print('addresses',len(idx),'with_gates',withgates)
