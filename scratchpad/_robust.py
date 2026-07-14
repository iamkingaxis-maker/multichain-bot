import json, statistics as st, collections
rows=json.load(open('_pos_rows.json'))
mid=[r for r in rows if r['pc_h6'] is not None and -25<r['pc_h6']<=-10]
print('MID-FLUSH cell n=%d dt=%d'%(len(mid),len(set(r['address'] for r in mid))))
# per-token aggregate
bytok=collections.defaultdict(list)
for r in mid: bytok[r['token']].append(r['rpnl'])
negtok=sum(1 for t,v in bytok.items() if st.mean(v)<0)
print('distinct tokens: %d, tokens with mean<0: %d (%.0f%%)'%(len(bytok),negtok,100*negtok/len(bytok)))
# concentration: top 3 loss tokens share
tokmean={t:sum(v) for t,v in bytok.items()}
srt=sorted(tokmean.items(),key=lambda x:x[1])
print('worst 3 tokens summed pp:',[(t,round(s,1)) for t,s in srt[:3]])
print('sum all:',round(sum(tokmean.values()),1),' worst3 share:',round(100*sum(s for _,s in srt[:3])/sum(tokmean.values()),0),'%')
# per-bot robustness
bybot=collections.defaultdict(list)
for r in mid: bybot[r['bot']].append(r['rpnl'])
print('per-bot EV in mid-flush:')
for b,v in sorted(bybot.items(),key=lambda x:-len(x[1]))[:6]:
    print('  %-28s n=%d EV=%.2f'%(b,len(v),st.mean(v)))
# compare adjacent buckets for robustness of the pattern
print()
print('pc_h6 fine grid:')
for lo,hi in [(-10,-5),(-15,-10),(-20,-15),(-25,-20),(-35,-25),(-50,-35),(-1e9,-50)]:
    sub=[r for r in rows if r['pc_h6'] is not None and lo<r['pc_h6']<=hi]
    if sub:
        p=[r['rpnl'] for r in sub]
        print('  (%d,%d] n=%d dt=%d EV=%.2f wr=%.0f'%(lo,hi,len(sub),len(set(r['address'] for r in sub)),st.mean(p),100*sum(1 for x in p if x>0)/len(p)))
