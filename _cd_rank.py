import json, statistics, math, pickle
pairs=pickle.load(open('_cd_pairs.pkl','rb'))
wins=[p for p in pairs if p[2]>0]
losers=[p for p in pairs if p[2]<=0]

# Holder-only feature heuristic: names with holder/_hf or top10_holder
def is_holder(k):
    kl=k.lower()
    return ('holder' in kl) or kl.endswith('_hf') or '_hf_' in kl

# gather numeric features from entry_meta across all buys
def numfeats(meta, prefix=''):
    out={}
    if not isinstance(meta,dict): return out
    for k,v in meta.items():
        if isinstance(v,bool):
            out[prefix+k]=1.0 if v else 0.0
        elif isinstance(v,(int,float)) and not isinstance(v,bool):
            if v is None: continue
            out[prefix+k]=float(v)
        elif isinstance(v,dict):
            out.update(numfeats(v, prefix+k+'.'))
    return out

# collect feature values per pair
recs=[]
for b,s,pp in pairs:
    m=b.get('entry_meta') or {}
    f=numfeats(m)
    recs.append((f,pp))

# all feature keys
allkeys=set()
for f,pp in recs: allkeys.update(f.keys())
print('total numeric feature keys', len(allkeys))

results=[]
n=len(recs)
for k in allkeys:
    wv=[f[k] for f,pp in recs if pp>0 and k in f and f[k] is not None]
    lv=[f[k] for f,pp in recs if pp<=0 and k in f and f[k] is not None]
    cov=sum(1 for f,pp in recs if k in f and f[k] is not None)/n
    if len(wv)<10 or len(lv)<10: continue
    wmed=statistics.median(wv); lmed=statistics.median(lv)
    allv=wv+lv
    sd=statistics.pstdev(allv) if len(allv)>1 else 0
    if sd==0: continue
    sep=abs(wmed-lmed)/sd
    results.append((sep,k,wmed,lmed,cov,len(wv),len(lv)))

results.sort(reverse=True)
print('\nTOP 35 by separation:')
print('%-42s %10s %10s %6s %5s' % ('feature','wmed','lmed','sep','cov%'))
for sep,k,wmed,lmed,cov,nw,nl in results[:35]:
    flag='H' if is_holder(k) else ' '
    print('%s%-41s %10.4f %10.4f %6.3f %5.0f' % (flag,k[:41],wmed,lmed,sep,cov*100))
pickle.dump((results,recs), open('_cd_results.pkl','wb'))
