import json
from collections import defaultdict
import statistics as st

d = json.load(open('_df_full.json.gz'))
buys = [x for x in d if x.get('type')=='buy']
sells = [x for x in d if x.get('type')=='sell']

# Join sells to buys by (address, entry_price). Fraction-weighted realized pnl_pct.
# Group sell legs by key
sell_by_key = defaultdict(list)
for s in sells:
    key = (s.get('address'), round(s.get('entry_price'),18) if s.get('entry_price') else None)
    sell_by_key[key].append(s)

def realized_pnl(buy):
    key = (buy.get('address'), round(buy.get('entry_price'),18) if buy.get('entry_price') else None)
    legs = sell_by_key.get(key)
    if not legs:
        return None
    num=0.0; den=0.0
    for s in legs:
        f = s.get('sell_fraction')
        p = s.get('pnl_pct')
        if f is None or p is None: continue
        num += f*p; den += f
    if den<=0: return None
    return num/den

def stats(pnls):
    n=len(pnls)
    if n==0: return dict(n=0)
    mean=sum(pnls)/n
    med=st.median(pnls)
    wr=sum(1 for x in pnls if x>0)/n*100
    never_green=sum(1 for x in pnls if x<=0)/n*100  # approx; using realized<=0
    return dict(n=n, mean=round(mean,3), median=round(med,3), WR=round(wr,1), loss_rate=round(never_green,1))

# Build positions with verdicts + realized pnl
positions=[]
for b in buys:
    em=b.get('entry_meta')
    if not isinstance(em,dict): continue
    pnl=realized_pnl(b)
    if pnl is None: continue
    positions.append(dict(
        mtf_verdict=em.get('filter_mtf_strong_downtrend_verdict'),
        fk_verdict=em.get('filter_falling_knife_verdict'),
        consec=em.get('1m_consec_red'),
        mtf_score=em.get('chart_mtf_score'),
        last_close=em.get('1m_last_close_pct'),
        pc_h1=em.get('pc_h1'),
        chart_score=em.get('chart_score'),
        liq=em.get('liquidity_usd'),
        pnl=pnl,
    ))

print("total positions with realized pnl:", len(positions))

def is_still_falling(p):
    fk = p['fk_verdict']=='BLOCK'
    cr = (p['consec'] is not None and p['consec']>=3)
    return fk or cr

# Overall mtf cohorts
mtf_block=[p for p in positions if p['mtf_verdict']=='BLOCK']
mtf_pass=[p for p in positions if p['mtf_verdict']=='PASS']
print("\n=== mtf_strong_downtrend overall ===")
print("BLOCK", stats([p['pnl'] for p in mtf_block]))
print("PASS ", stats([p['pnl'] for p in mtf_pass]))
print("verdict values:", set(p['mtf_verdict'] for p in positions))

# Among mtf BLOCK: cross-tab by still-falling
redundant=[p for p in mtf_block if is_still_falling(p)]      # (a)
incremental=[p for p in mtf_block if not is_still_falling(p)] # (b)
print("\n=== mtf BLOCK cross-tab vs still-falling (fk BLOCK OR consec>=3) ===")
print("(a) mtf-block AND still-falling (REDUNDANT):", stats([p['pnl'] for p in redundant]))
print("(b) mtf-block NOT still-falling (INCREMENTAL-ONLY):", stats([p['pnl'] for p in incremental]))

# Break down the OR components
fk_only=[p for p in mtf_block if p['fk_verdict']=='BLOCK']
cr_only=[p for p in mtf_block if (p['consec'] is not None and p['consec']>=3)]
both=[p for p in mtf_block if p['fk_verdict']=='BLOCK' and (p['consec'] is not None and p['consec']>=3)]
print("\n--- components within mtf BLOCK ---")
print("fk==BLOCK:", stats([p['pnl'] for p in fk_only]))
print("consec>=3:", stats([p['pnl'] for p in cr_only]))
print("BOTH fk&consec:", stats([p['pnl'] for p in both]))

# Incremental-only: what about just falling_knife (no consec)? i.e. incremental beyond fk alone
inc_vs_fk=[p for p in mtf_block if p['fk_verdict']!='BLOCK']  # mtf blocks but fk doesn't
print("\n--- incremental beyond falling_knife ALONE (fk!=BLOCK, regardless consec) ---")
print(stats([p['pnl'] for p in inc_vs_fk]))
# of those, split by consec
inc_vs_fk_cr=[p for p in inc_vs_fk if (p['consec'] is not None and p['consec']>=3)]
inc_vs_fk_nocr=[p for p in inc_vs_fk if not (p['consec'] is not None and p['consec']>=3)]
print("   ...but consec>=3 catches:", stats([p['pnl'] for p in inc_vs_fk_cr]))
print("   ...and neither catches (PURE incremental):", stats([p['pnl'] for p in inc_vs_fk_nocr]))
