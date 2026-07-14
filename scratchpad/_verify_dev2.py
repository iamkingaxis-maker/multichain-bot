import json
from collections import defaultdict
t=json.load(open('scratchpad/_full_trades.json'))
t2=sorted(t,key=lambda x:x.get('time') or 0)
pending=defaultdict(list); joined=[]
for x in t2:
    key=(x.get('bot_id'),x.get('token'))
    if x.get('type')=='buy': pending[key].append(x)
    elif x.get('type')=='sell' and pending[key]:
        b=pending[key].pop(0); em=b.get('entry_meta') or {}
        joined.append({'bot':x.get('bot_id'),'token':x.get('token'),'t':x.get('time'),
            'pnl_pct':x.get('pnl_pct'),'hold':x.get('hold_secs'),'dev':em.get('dev_pct_remaining')})
J=[r for r in joined if r['pnl_pct'] is not None and not (r['hold'] is not None and r['pnl_pct']>0 and r['hold']<10)]
flush={'badday_flush','badday_flush_nf15','badday_flush_peel_ab','badday_flush_wickride_ab','badday_flush_wideexit_ab','badday_pump_dip_ab'}
def token_mean(rows):
    byt=defaultdict(list)
    for r in rows: byt[r['token']].append(r['pnl_pct'])
    tm=[sum(v)/len(v) for v in byt.values()]
    return (sum(tm)/len(tm) if tm else None, len(tm))
FF=[r for r in J if r['bot'] in flush and r['dev'] is not None]
FF.sort(key=lambda r:r['t'] or 0)
mid=FF[len(FF)//2]['t']
for lbl,half in [('EARLY',[r for r in FF if (r['t'] or 0)<mid]),('LATE',[r for r in FF if (r['t'] or 0)>=mid])]:
    blk=[r for r in half if r['dev']<20]; pas=[r for r in half if r['dev']>=20]
    bm,bn=token_mean(blk); pm,pn=token_mean(pas)
    print(f'{lbl}: blk_dt={bn} blkTM={bm and round(bm,2)} | pas_dt={pn} pasTM={pm and round(pm,2)} | gain={pm and bm and round(pm-bm,2)}')

# jackknife the flush aggregate gain by dropping top token contributor
print('\nFlush passed cohort top/bottom token contributors (token-mean sensitivity):')
pas=[r for r in FF if r['dev']>=20]
byt=defaultdict(list)
for r in pas: byt[r['token']].append(r['pnl_pct'])
tm=sorted(((sum(v)/len(v),k,len(v)) for k,v in byt.items()))
print(' worst3:',[(round(x[0],1),x[1]) for x in tm[:3]])
print(' best3:',[(round(x[0],1),x[1]) for x in tm[-3:]])
allpas=[sum(v)/len(v) for v in byt.values()]
print(' passed token-mean',round(sum(allpas)/len(allpas),2),'n_distinct',len(allpas))
import statistics
print(' passed token-mean MEDIAN',round(statistics.median(allpas),2))
