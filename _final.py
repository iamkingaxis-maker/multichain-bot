import pickle, statistics
pairs,winners,losers=pickle.load(open('_cdv4_pairs.pkl','rb'))
def feat(b,k):
    em=b.get('entry_meta') or {}
    v=em.get(k)
    return float(v) if isinstance(v,(int,float)) and not isinstance(v,bool) else None
# combined view for the 5 reported separators
allp=pairs
om=statistics.mean([p[2] for p in allp])
print('n_pairs',len(allp),'overall_mean',round(om,3),
      'WR',round(100*sum(1 for p in allp if p[2]>0)/len(allp),1))
