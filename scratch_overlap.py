# -*- coding: utf-8 -*-
import json, sys, io
sys.stdout=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
rows=json.load(open('scratch_rows.json'))
def bm(r,f,t,d):
    v=r['em'].get(f)
    return None if v is None else (v>=t if d=='ge' else v<t)
defs={'C1':('macro30_pct',-4.83,'ge'),'C2':('trend_ma50_dist_pct',-5.463,'ge'),
      'C4':('pct_in_1h_range',0.198,'ge'),'C7':('pc_m5',0.27,'ge')}
# pairwise jaccard on blocked sets
sets={}
for k,(f,t,d) in defs.items():
    sets[k]=set(i for i,r in enumerate(rows) if bm(r,f,t,d)==True)
keys=list(sets)
print("Blocked-set overlap (Jaccard):")
for i in range(len(keys)):
    for j in range(i+1,len(keys)):
        a,b=sets[keys[i]],sets[keys[j]]
        jac=len(a&b)/len(a|b) if a|b else 0
        print(f"  {keys[i]} vs {keys[j]}: jaccard={jac:.2f} |a|={len(a)} |b|={len(b)} overlap={len(a&b)}")
# combined union rule C1 OR C2 (the tight extended-token gate)
union=sets['C1']|sets['C2']
ng=sum(1 for i in union if rows[i]['lab']=='NG'); b=sum(1 for i in union if rows[i]['lab']=='B')
inside=[i for i in range(len(rows)) if i not in union]
ngi=sum(1 for i in inside if rows[i]['lab']=='NG')
print(f"\nUNION C1|C2: blocked n={len(union)} NG={ng} B={b} wk={b/ng:.2f} ; inside n={len(inside)} NG%={100*ngi/len(inside):.1f}")
