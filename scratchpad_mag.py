import json,os
SP="C:/Users/jcole/AppData/Local/Temp/claude/C--Users-jcole-multichain-bot/ecbaef77-2f98-4dc5-9231-4bd9a529e92c/scratchpad"
idx=json.load(open('scratchpad_ourindex.json'))
ouraddrs=set(idx.keys())
GATES=['falling_knife','post_pump_corpse','steep_fall_1m','consec_red_knife','structure_edge']
winners={'C3zP':'w_C3zP.json','Zsp75':'w_Zsp75.json','ArWird':'w_ArWird.json','jStURX':'w_jStURX.json'}
rows=[]
for wn,fn in winners.items():
    wt=json.load(open(os.path.join(SP,fn)))
    for mint,v in wt.items():
        if mint in ouraddrs:
            rows.append((wn,mint,v.get('profit'),v.get('ret_pct'),idx[mint]['our_pnl_pct'],idx[mint]['gates']))
print("=== Magnitude of blocking on ALL overlap (our realized pnl_pct sum) ===")
for g in GATES:
    blk=[r for r in rows if r[5] and r[5].get(g) and r[4] is not None]
    saved=sum(-r[4] for r in blk if r[4]<0)  # loss avoided (positive)
    cost=sum(r[4] for r in blk if r[4]>=0)   # win forfeited
    net=saved-cost
    print(f"  {g:18s} blocks {len(blk)} closed-overlap | our loss-avoided +{saved:6.1f}pp | our win-forfeited -{cost:5.1f}pp | NET {net:+6.1f}pp")
print()
# dedup distinct tokens with our pnl
dd={}
for r in rows:
    dd[r[1]]=r
distinct=list(dd.values())
print(f"distinct overlap tokens with our realized pnl: {sum(1 for r in distinct if r[4] is not None)}")
our_overlap_pnls=[r[4] for r in distinct if r[4] is not None]
import statistics
print(f"our median pnl_pct on overlap tokens: {statistics.median(our_overlap_pnls):.1f}%  mean {statistics.mean(our_overlap_pnls):.1f}%")
wins=[p for p in our_overlap_pnls if p>=0]
print(f"our WR on overlap tokens: {len(wins)}/{len(our_overlap_pnls)} = {len(wins)/len(our_overlap_pnls)*100:.0f}%")
