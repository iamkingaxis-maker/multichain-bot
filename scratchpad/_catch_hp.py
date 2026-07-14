import json, statistics as st
rows=json.load(open('_catch_rows.json'))
rows=[r for r in rows if not (r['pnl'] is not None and r['pnl']>0 and r['hold'] is not None and r['hold']<10)]
rows=[r for r in rows if r['pnl'] is not None and r['liq'] is not None]
# coverage
print('mae cov', sum(1 for r in rows if r['mae'] is not None),'/',len(rows))
print('ws60 cov', sum(1 for r in rows if r['ws60'] is not None))
print('peak cov', sum(1 for r in rows if r['peak'] is not None))
# LOSER severity by pump class: among losers pnl<0, median loss + gap-through rate
def losersev(sub):
    los=[r['pnl'] for r in sub if r['pnl']<0]
    if not los: return None
    gap=sum(1 for p in los if p<=-15)/len(los)
    return dict(nlos=len(los),medloss=round(st.median(los),2),p10loss=round(sorted(los)[max(0,int(0.1*len(los)))],2),gaprate=round(gap,3))
def band(lo,hi,key='pc_h6'):
    return [r for r in rows if r.get(key) is not None and (lo is None or r[key]>=lo) and (hi is None or r[key]<hi)]
print('\nLOSER SEVERITY by PC_H6')
for lo,hi in [(None,0),(0,100),(100,300),(300,None)]:
    sub=band(lo,hi)
    print(' pc_h6[%s,%s)'%(lo,hi), 'n',len(sub),'npair',len(set(r['addr'] for r in sub)),losersev(sub))

# THE FAT-TAIL ZONE: pc_h6>=100. Within it find separators.
hp=[r for r in rows if r['pc_h6'] is not None and r['pc_h6']>=100]
print('\nHIGH-PUMP cohort (pc_h6>=100): n',len(hp),'pairs',len(set(r['addr'] for r in hp)))
def out(sub):
    n=len(sub); 
    if not n: return 'empty'
    pnls=[r['pnl'] for r in sub]
    return 'n=%d pairs=%d win8=%.2f gap15=%.3f med=%.2f'%(n,len(set(r['addr'] for r in sub)),sum(p>=8 for p in pnls)/n,sum(p<=-15 for p in pnls)/n,st.median(pnls))
for key,edges in [('liq',[40000,60000,90000]),('age',[3,12,48]),('ws60',[-5,0,5]),('medbuy',[15,40]),('ubuyers',[20,50])]:
    print(' -- split by',key)
    es=[None]+edges+[None]
    for i in range(len(edges)+1):
        lo=edges[i-1] if i>0 else None; hi=edges[i] if i<len(edges) else None
        sub=[r for r in hp if r.get(key) is not None and (lo is None or r[key]>=lo) and (hi is None or r[key]<hi)]
        print('    [%s,%s)'%(lo,hi),out(sub))
