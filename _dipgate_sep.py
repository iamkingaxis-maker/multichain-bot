import json, statistics, math
d=json.load(open('./_dipgate_meta.json'))
win=d['buys_win']; lose=d['buys_lose']; allb=d['all_buys']
Nall=len(allb)

def numeric_keys(metas):
    ks=set()
    for m in metas:
        for k,v in m.items():
            if isinstance(v,bool): continue
            if isinstance(v,(int,float)): ks.add(k)
    return ks
keys=numeric_keys(allb)

def vals(metas,k):
    out=[]
    for m in metas:
        v=m.get(k)
        if isinstance(v,bool): continue
        if isinstance(v,(int,float)) and not (isinstance(v,float) and math.isnan(v)):
            out.append(float(v))
    return out

rows=[]
for k in keys:
    wv=vals(win,k); lv=vals(lose,k)
    if len(wv)<5 or len(lv)<5: continue
    cov=100*len([1 for m in allb if isinstance(m.get(k),(int,float)) and not isinstance(m.get(k),bool)])/Nall
    wmed=statistics.median(wv); lmed=statistics.median(lv)
    pooled=wv+lv
    sd=statistics.pstdev(pooled) if len(set(pooled))>1 else 0
    if sd==0: continue
    sep=abs(wmed-lmed)/sd
    rows.append((sep,k,wmed,lmed,cov,len(wv),len(lv)))

rows.sort(reverse=True)
hdr="%6s %-40s %12s %12s %6s %s" % ("sep","feature","wmed","lmed","cov%","nw nl")
print(hdr)
for sep,k,wmed,lmed,cov,nw,nl in rows[:45]:
    print("%6.2f %-40.40s %12.4g %12.4g %6.1f %d %d" % (sep,k,wmed,lmed,cov,nw,nl))
