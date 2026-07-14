import pickle
from collections import Counter,defaultdict
scr=pickle.load(open('scratchpad/_trips.pkl','rb'))
n=len(scr)
# continuous numeric feature coverage
num=Counter(); cat=Counter()
for c in scr:
    for k,v in c['em'].items():
        if isinstance(v,bool):
            cat[k]+=1
        elif isinstance(v,(int,float)):
            num[k]+=1
        elif isinstance(v,str) and (v in ('PASS','BLOCK') or 'verdict' in k):
            cat[k+'|'+v]+=1
print('=== NUMERIC features (coverage>=80%) ===')
for k,v in num.most_common():
    if v>=0.8*n: print(f'  {k}: {v}')
