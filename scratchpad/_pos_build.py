import json, collections, statistics as st
d=json.load(open('_full_trades.json'))
last_buy={}
pos=collections.OrderedDict()  # key=(address,buy_time)-> dict
for t in d:
    tok=t.get('token')
    if t.get('type')=='buy':
        last_buy[tok]=t
    elif t.get('type')=='sell':
        b=last_buy.get(tok)
        if not b: continue
        pnl=t.get('pnl_pct')
        if pnl is None: continue
        em=b.get('entry_meta') or {}
        key=(b.get('address'),b.get('time'))
        if key not in pos:
            pos[key]={'legs':[],'em':em,'b':b}
        pos[key]['legs'].append((pnl,t.get('sell_fraction'),t.get('hold_secs')))

rows=[]
for key,v in pos.items():
    legs=v['legs']
    # weighted realized pnl by sell_fraction; if missing, equal weight
    fracs=[f for _,f,_ in legs]
    if all(f is not None for f in fracs) and sum(fracs)>0:
        tot=sum(f for _,f,_ in legs)
        rpnl=sum(p*f for p,f,_ in legs)/tot
    else:
        rpnl=sum(p for p,_,_ in legs)/len(legs)
    # SCRUB: drop trivial round trip (all legs pnl>0 and max hold<10)
    holds=[h for _,_,h in legs if h is not None]
    maxhold=max(holds) if holds else None
    if rpnl>0 and maxhold is not None and maxhold<10:
        continue
    em=v['em']; b=v['b']
    rows.append({
        'address':b.get('address'),'token':b.get('token'),'buy_time':b.get('time'),'bot':b.get('bot_id'),
        'rpnl':rpnl,'nlegs':len(legs),'maxhold':maxhold,
        'pc_h6':em.get('pc_h6'),'pc_h1':em.get('pc_h1'),'pc_h24':em.get('pc_h24'),
        'sol_pc_h6':em.get('sol_pc_h6'),'sol_pc_h1':em.get('sol_pc_h1'),'sol_pc_h24':em.get('sol_pc_h24'),
        'regime':em.get('regime'),
        'med_buy':em.get('median_buy_size_usd'),'uniq':em.get('unique_buyers_n'),
        'buy_trend':em.get('buy_size_mean_trend'),'vol':em.get('token_volatility_h24_pct'),
        'liq':em.get('liquidity_usd'),'rsi15':em.get('rsi_15m'),
        'top5':em.get('top5_buyer_volume_pct'),'uratio':em.get('unique_buyer_ratio'),
    })
json.dump(rows,open('_pos_rows.json','w'))
print('positions (scrubbed):',len(rows))
print('distinct tokens:',len(set(r['address'] for r in rows)))
p=[r['rpnl'] for r in rows]
print('overall avg realized pnl_pct',round(sum(p)/len(p),3),'median',round(st.median(p),3),'wr',round(100*sum(1 for x in p if x>0)/len(p),1))
# how many have pc_h6 not None
print('with pc_h6:',sum(1 for r in rows if r['pc_h6'] is not None))
print('with sol_pc_h6:',sum(1 for r in rows if r['sol_pc_h6'] is not None))
print('with buy_time:',sum(1 for r in rows if r['buy_time']))
