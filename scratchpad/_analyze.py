import json, statistics
from collections import defaultdict
pos=json.load(open('scratchpad/_overgating_pos.json'))
# SCRUB RULE: drop ppct>0 & hold<10s
def scrub(rows):
    return [p for p in rows if not (p['ppct'] is not None and p['ppct']>0 and p['hold'] is not None and p['hold']<10)]
pos=[p for p in pos if p['ppct'] is not None]
n0=len(pos); pos=scrub(pos); print('after scrub: %d (dropped %d)'%(len(pos),n0-len(pos)))

def extop2_median(vals):
    if len(vals)<3: return None
    s=sorted(vals, reverse=True)[2:]  # drop top 2
    return statistics.median(s)
def stats(rows):
    if not rows: return None
    v=[p['ppct'] for p in rows]
    pk=[p['peak'] for p in rows if p['peak'] is not None]
    hit6=sum(1 for x in pk if x>=6)/len(pk) if pk else None
    return dict(n=len(rows), mean=round(statistics.mean(v),2),
                med=round(statistics.median(v),2),
                extop2=(round(extop2_median(v),2) if extop2_median(v) is not None else None),
                hit6=round(hit6,3) if hit6 is not None else None,
                peak_med=round(statistics.median(pk),2) if pk else None)

def report(label, rows, splitkey):
    blk=[p for p in rows if splitkey(p)]
    pas=[p for p in rows if not splitkey(p)]
    print('\n=== %s ==='%label)
    print(' BLOCK-but-bought:', stats(blk))
    print(' PASS            :', stats(pas))

knife=lambda p: p['kv']=='BLOCK'
retr=lambda p: bool(p['rb'])

young=[p for p in pos if 'young' in p['bot'] or 'absorb' in p['bot']]
allb=pos

print('\n########## KNIFE_CATCH_PEAK ##########')
report('ALL badday', allb, knife)
report('YOUNG family', young, knife)

print('\n########## RETRACE_MICRO_AVOID ##########')
report('ALL badday', allb, retr)
report('YOUNG family', young, retr)

# per-bot within-bot for the biggest cohorts
print('\n-- within-bot RETRACE (young family bots w/ block>=10) --')
for bot in ['badday_young_absorb','badday_young_adaptsize_ab','badday_young_pump_dip_ab']:
    r=[p for p in pos if p['bot']==bot]
    report(bot, r, retr)
print('\n-- within-bot KNIFE (pump_dip bots) --')
for bot in ['badday_pump_dip_ab','badday_young_pump_dip_ab']:
    r=[p for p in pos if p['bot']==bot]
    report(bot, r, knife)
