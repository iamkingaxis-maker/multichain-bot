import pickle, statistics as st
from collections import defaultdict,Counter
scr=pickle.load(open('scratchpad/_trips.pkl','rb'))
dc=Counter(c['t'][:10] for c in scr)
for day in sorted(dc): 
    ntok=len(set(c['tok'] for c in scr if c['t'][:10]==day))
    print(day, 'trips',dc[day],'tok',ntok)
print('total days',len(dc))
