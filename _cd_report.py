import statistics, pickle
results,recs=pickle.load(open('_cd_results.pkl','rb'))
# pull specific feature win/loser medians + coverage
def stat(key):
    wv=[f[key] for f,pp in recs if pp>0 and key in f and f[key] is not None]
    lv=[f[key] for f,pp in recs if pp<=0 and key in f and f[key] is not None]
    cov=sum(1 for f,pp in recs if key in f and f[key] is not None)/len(recs)*100
    allv=wv+lv; sd=statistics.pstdev(allv)
    return statistics.median(wv),statistics.median(lv),abs(statistics.median(wv)-statistics.median(lv))/sd, cov
for k in ['chart_trendline_5m_breakdown','chart_vp_above_poc','bb_pos_15m','trend_30m_r_squared','net_flow_15s_imbalance']:
    print(k, [round(x,4) for x in stat(k)])
