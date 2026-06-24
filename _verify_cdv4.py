import pickle, statistics
pairs,winners,losers=pickle.load(open('_cdv4_pairs.pkl','rb'))
def feat(b,k):
    em=b.get('entry_meta') or {}
    v=em.get(k)
    return float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) else None

# look at the 7 losers and what gates exclude them
print('LOSERS (pnl_pct, trend_60m_r_squared, shape_90m_chg_pct, chart_stop_cluster_5m_density, bb_pos_5m):')
for b,s,p in sorted(losers,key=lambda x:x[2]):
    print(f"  pnl={p:8.2f}  r2={feat(b,'trend_60m_r_squared')}  shp90={feat(b,'shape_90m_chg_pct')}  cluster={feat(b,'chart_stop_cluster_5m_density')}  bb={feat(b,'bb_pos_5m')}")

# distribution of r2 across all
vals=[(p,feat(b,'trend_60m_r_squared')) for b,s,p in pairs]
print('\nr2 values present for all:', all(v is not None for _,v in vals))
# how many losers excluded by r2>=0.2
exc=sum(1 for b,s,p in losers if (feat(b,'trend_60m_r_squared') or 0)<0.2)
print(f'losers excluded by r2>=0.2: {exc}/{len(losers)}')
inc=sum(1 for b,s,p in winners if (feat(b,'trend_60m_r_squared') or 0)>=0.2)
print(f'winners kept by r2>=0.2: {inc}/{len(winners)}')
