import json, collections
d=json.load(open('_full_trades.json'))
# join sells to prior buy of same token
last_buy={}
rows=[]
for t in d:
    tok=t.get('token')
    if t.get('type')=='buy':
        last_buy[tok]=t
    elif t.get('type')=='sell':
        b=last_buy.get(tok)
        if not b: continue
        em=b.get('entry_meta') or {}
        pnl=t.get('pnl_pct')
        if pnl is None: continue
        hold=t.get('hold_secs')
        # SCRUB trivial round-trips
        if pnl>0 and hold is not None and hold<10: 
            continue
        rows.append({
            'token':tok,'address':b.get('address'),
            'pnl':pnl,'hold':hold,'kind':t.get('kind'),
            'buy_time':b.get('time'),'bot':b.get('bot_id'),
            'pc_h6':em.get('pc_h6'),'pc_h1':em.get('pc_h1'),'pc_h24':em.get('pc_h24'),
            'sol_pc_h6':em.get('sol_pc_h6'),'sol_pc_h1':em.get('sol_pc_h1'),'sol_pc_h24':em.get('sol_pc_h24'),
            'regime':em.get('regime'),
            'med_buy':em.get('median_buy_size_usd'),'uniq':em.get('unique_buyers_n'),
            'buy_trend':em.get('buy_size_mean_trend'),'vol':em.get('token_volatility_h24_pct'),
            'liq':em.get('liquidity_usd'),'rsi15':em.get('rsi_15m'),
            'top5':em.get('top5_buyer_volume_pct'),'uratio':em.get('unique_buyer_ratio'),
        })
print('joined sell rows (scrubbed):',len(rows))
distinct=len(set(r['address'] for r in rows))
print('distinct tokens:',distinct)
json.dump(rows,open('_regime_rows.json','w'))
# overall
import statistics as st
p=[r['pnl'] for r in rows]
print('overall avg pnl_pct',round(sum(p)/len(p),3),'median',round(st.median(p),3),'wr',round(100*sum(1 for x in p if x>0)/len(p),1))
