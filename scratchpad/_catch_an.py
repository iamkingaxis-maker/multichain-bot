import json, statistics as st
rows=json.load(open('_catch_rows.json'))
rows=[r for r in rows if not (r['pnl'] is not None and r['pnl']>0 and r['hold'] is not None and r['hold']<10)]
rows=[r for r in rows if r['pnl'] is not None and r['liq'] is not None and r['pc_h6'] is not None]
print('N episodes',len(rows),' distinct pairs',len(set(r['addr'] for r in rows)))
def outcomes(sub):
    n=len(sub)
    if n==0: return None
    pnls=[r['pnl'] for r in sub]
    win=sum(1 for p in pnls if p>=8)/n
    biglose=sum(1 for p in pnls if p<=-15)/n
    med=st.median(pnls)
    npair=len(set(r['addr'] for r in sub))
    return dict(n=n,npair=npair,win=round(win,3),biglose=round(biglose,3),med=round(med,2))
print('BASE',outcomes(rows))
def binreport(name,keyfn,edges):
    print('=== %s ===' % name)
    labels=[]
    for i in range(len(edges)+1):
        lo=edges[i-1] if i>0 else None
        hi=edges[i] if i<len(edges) else None
        labels.append((lo,hi))
    for lo,hi in labels:
        sub=[r for r in rows if keyfn(r) is not None and (lo is None or keyfn(r)>=lo) and (hi is None or keyfn(r)<hi)]
        o=outcomes(sub)
        if o: print('  [%s,%s): n=%4d pairs=%3d win=%.3f biglose=%.3f med=%s'%(lo,hi,o['n'],o['npair'],o['win'],o['biglose'],o['med']))
binreport('LIQUIDITY_USD', lambda r:r['liq'], [15000,30000,50000,80000,150000])
binreport('PC_H6', lambda r:r['pc_h6'], [-30,0,30,100,300,700])
binreport('PC_H24', lambda r:r['pc_h24'] if r['pc_h24'] is not None else None, [0,50,150,400,900])
binreport('AGE_HOURS', lambda r:r['age'], [1,3,6,12,24,72])
binreport('MEDBUY_USD', lambda r:r['medbuy'] if r['medbuy'] is not None else None,[10,25,50,100])
