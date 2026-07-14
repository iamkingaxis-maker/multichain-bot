import json
import numpy as np
rows=json.load(open('scratch_rows.json'))
feats=json.load(open('scratch_feats.json'))
N=len(rows)
results=[]
for f in feats:
    allv=[(r['em'].get(f), r['lab'], r['token'], r['time']) for r in rows if r['em'].get(f) is not None]
    if len(allv) < 0.5*N: continue
    arr=np.array([v[0] for v in allv],dtype=float)
    cand_t=sorted(set(float(np.percentile(arr,q)) for q in [10,20,30,40,50,60,70,80,90]))
    best=None
    for t in cand_t:
        for direction in ('ge','lt'):
            if direction=='ge':
                blocked=[v for v in allv if v[0]>=t]; inside=[v for v in allv if v[0]<t]
            else:
                blocked=[v for v in allv if v[0]<t]; inside=[v for v in allv if v[0]>=t]
            if len(blocked)<20 or len(inside)<20: continue
            ng_in=sum(1 for v in inside if v[1]=='NG')/len(inside)
            ng_bl=sum(1 for v in blocked if v[1]=='NG')/len(blocked)
            gap=ng_bl-ng_in
            b_blocked=sum(1 for v in blocked if v[1]=='B')
            ng_blocked=sum(1 for v in blocked if v[1]=='NG')
            wk = b_blocked/ng_blocked if ng_blocked>0 else 999
            if best is None or gap>best['gap']:
                best={'f':f,'t':t,'dir':direction,'gap':gap,'ng_in':ng_in,'ng_bl':ng_bl,
                      'n_in':len(inside),'n_bl':len(blocked),'wk':wk,'b_bl':b_blocked,'ng_bl_n':ng_blocked}
    if best: results.append(best)
results.sort(key=lambda x:-x['gap'])
hdr="%-40s %3s %10s  %5s  %5s  %5s  %4s %4s  %4s" % ("feature","dir","thr","gap","ngBl","ngIn","nBl","nIn","wk")
print(hdr)
for b in results[:30]:
    print("%-40s %3s %10.4g  %5.1f  %5.1f  %5.1f  %4d %4d  %.2f" % (
        b['f'],b['dir'],b['t'],b['gap']*100,b['ng_bl']*100,b['ng_in']*100,b['n_bl'],b['n_in'],b['wk']))
json.dump(results,open('scratch_results.json','w'))
