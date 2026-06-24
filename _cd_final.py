import statistics, pickle
pairs=pickle.load(open('_cd_pairs.pkl','rb'))
def sub(pred):
    k=[(b,s,pp) for b,s,pp in pairs if pred(b)]
    if not k: return None
    pp=[x[2] for x in k]
    return statistics.mean(pp), 100*sum(1 for p in pp if p>0)/len(k), len(k), statistics.median(pp)

g=lambda b,key: b.get('entry_meta',{}).get(key)

print('OVERALL', sub(lambda b: True))
print()
print('breakdown==True (KEEP):', sub(lambda b: g(b,"chart_trendline_5m_breakdown")==True))
print('breakdown==False (DROP):', sub(lambda b: g(b,"chart_trendline_5m_breakdown")==False))
print('breakdown==None:', sub(lambda b: g(b,"chart_trendline_5m_breakdown") is None))
print()
# combo: breakdown AND vp below poc
print('breakdown=T AND vp_above_poc=F:',
      sub(lambda b: g(b,"chart_trendline_5m_breakdown")==True and g(b,"chart_vp_above_poc")==False))
print('breakdown=T AND bb_pos_15m<=0.45:',
      sub(lambda b: g(b,"chart_trendline_5m_breakdown")==True and (g(b,"bb_pos_15m") or 1)<=0.45))
