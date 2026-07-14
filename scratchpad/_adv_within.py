import json, glob, math
from collections import defaultdict
from datetime import datetime
def pts(s): return datetime.fromisoformat(s).timestamp()
# reuse forward-only generator inline (drop_top=2, union-robust)
exec(open('_adv_fwd.py').read().split('def report')[0])  # defines run()
eps=run(2)
# within-token: shallow(depth<.5) vs deep(>=.5) CONT rate, tokens with >=8 each side
byt=defaultdict(lambda:{'sh':[], 'dp':[]})
for e in eps:
    (byt[e['tok']]['sh'] if e['depth']<.5 else byt[e['tok']]['dp']).append(1 if e['lab']=='CONT' else 0)
pos=neg=0; toks=0
for t,d in byt.items():
    if len(d['sh'])>=8 and len(d['dp'])>=8:
        toks+=1
        rs=sum(d['sh'])/len(d['sh']); rd=sum(d['dp'])/len(d['dp'])
        if rs>rd: pos+=1
        elif rs<rd: neg+=1
print(f"within-token (n>=8/side): {toks} tokens; shallow>deep in {pos}, deep>shallow in {neg}")
# binomial-ish sign test p (two-sided approx)
from math import comb
N=pos+neg; k=max(pos,neg)
p=sum(comb(N,i) for i in range(k,N+1))/2**N*2 if N>0 else 1
print(f"sign-test p~{p:.4f}")
# two-proportion z for pooled shallow vs deep (forward-only)
sh=[e for e in eps if e['depth']<.5]; dp=[e for e in eps if e['depth']>=.5]
def rate(s): c=sum(1 for e in s if e['lab']=='CONT'); return c,len(s),c/len(s)
c1,n1,r1=rate(sh); c2,n2,r2=rate(dp)
pp=(c1+c2)/(n1+n2); se=math.sqrt(pp*(1-pp)*(1/n1+1/n2)); z=(r1-r2)/se
print(f"pooled shallow {r1:.3f}(n={n1}) vs deep {r2:.3f}(n={n2}) z={z:.2f}")
# nf term whale check already done; also report nf edge z
nfp=[e for e in eps if e['nf']>0]; nfn=[e for e in eps if e['nf']<=0]
c1,n1,r1=rate(nfp); c2,n2,r2=rate(nfn)
pp=(c1+c2)/(n1+n2); se=math.sqrt(pp*(1-pp)*(1/n1+1/n2)); z=(r1-r2)/se
print(f"pooled nf>0 {r1:.3f}(n={n1}) vs nf<=0 {r2:.3f}(n={n2}) z={z:.2f}")
