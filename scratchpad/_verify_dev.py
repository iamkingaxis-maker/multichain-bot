import json
from collections import defaultdict, Counter
t=json.load(open('scratchpad/_full_trades.json'))
# sort by time
def tm(x): return x.get('time') or 0
t2=sorted(t,key=tm)
# join sells to prior buy same (bot_id,token)
pending=defaultdict(list)  # key-> list of buys
joined=[]
for x in t2:
    key=(x.get('bot_id'),x.get('token'))
    if x.get('type')=='buy':
        pending[key].append(x)
    elif x.get('type')=='sell':
        if pending[key]:
            b=pending[key].pop(0)  # FIFO
            em=b.get('entry_meta') or {}
            joined.append({'bot':x.get('bot_id'),'token':x.get('token'),
                'pnl_pct':x.get('pnl_pct'),'hold':x.get('hold_secs'),
                'dev':em.get('dev_pct_remaining'),'rsi':em.get('rsi_15m'),
                'pc_h6':em.get('pc_h6'),'em':em})
print('joined sells:',len(joined))
# scrub trivial round-trips: drop ret>0 & hold<10s
def scrub(r):
    p=r['pnl_pct']; h=r['hold']
    if p is None: return False
    if h is not None and p>0 and h<10: return False
    return True
J=[r for r in joined if scrub(r)]
print('after scrub:',len(J))
print('distinct tokens:',len(set(r['token'] for r in J)))

flush_family={'badday_flush','badday_flush_nf15','badday_flush_peel_ab','badday_flush_wickride_ab','badday_flush_wideexit_ab','badday_pump_dip_ab',
  'badday_flush_conviction','badday_flush_nf15_dense','badday_flush_runner_ab','badday_flush_patient_slot_ab','badday_flush_hlconfirm_ab'}

def stats(rows):
    import statistics
    pn=[r['pnl_pct'] for r in rows]
    dt=len(set(r['token'] for r in rows))
    return len(rows),dt,(sum(pn)/len(pn) if pn else None)

# dev_not_dumped: dev<20 = would BLOCK
def has_dev(r): return r['dev'] is not None
D=[r for r in J if has_dev(r)]
print('\n=== FLEET-WIDE (dev known) ===')
blk=[r for r in D if r['dev']<20]
pas=[r for r in D if r['dev']>=20]
print('blocked(dev<20):',stats(blk),'passed(dev>=20):',stats(pas))
print('token-mean blocked vs passed:')
# token-mean: mean over distinct tokens (avg per token first)
def token_mean(rows):
    byt=defaultdict(list)
    for r in rows: byt[r['token']].append(r['pnl_pct'])
    tmeans=[sum(v)/len(v) for v in byt.values()]
    return sum(tmeans)/len(tmeans) if tmeans else None, len(tmeans)
print(' blocked token-mean',token_mean(blk),' passed token-mean',token_mean(pas))

print('\n=== PER-BOT dev-dump(blocked) vs dev-held(passed) [token-mean, distinct-n] ===')
byb=defaultdict(list)
for r in D: byb[r['bot']].append(r)
rows_out=[]
for bot,rows in sorted(byb.items()):
    blk=[r for r in rows if r['dev']<20]; pas=[r for r in rows if r['dev']>=20]
    if len(rows)<15: continue
    bm,bn=token_mean(blk); pm,pn=token_mean(pas)
    diff = (pm-bm) if (bm is not None and pm is not None) else None
    rows_out.append((bot,bn,bm,pn,pm,diff))
for bot,bn,bm,pn,pm,diff in sorted(rows_out,key=lambda z:-(z[5] or -99)):
    fam='FLUSH' if bot in flush_family else ''
    print(f'{bot:38s} blk_dt={bn} blkTM={bm and round(bm,2)} | pas_dt={pn} pasTM={pm and round(pm,2)} | gate_gain(pas-blk)={diff and round(diff,2)} {fam}')

print('\n=== FLUSH-FAMILY AGGREGATE ===')
FF=[r for r in D if r['bot'] in flush_family]
blk=[r for r in FF if r['dev']<20]; pas=[r for r in FF if r['dev']>=20]
print('flush blocked(dev<20):',stats(blk),' token-mean',token_mean(blk))
print('flush passed(dev>=20):',stats(pas),' token-mean',token_mean(pas))
