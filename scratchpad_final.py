import json,os,collections,statistics
SP="C:/Users/jcole/AppData/Local/Temp/claude/C--Users-jcole-multichain-bot/ecbaef77-2f98-4dc5-9231-4bd9a529e92c/scratchpad"
idx=json.load(open('scratchpad_ourindex.json')); ouraddrs=set(idx.keys())
GATES=['falling_knife','post_pump_corpse','steep_fall_1m','consec_red_knife','structure_edge']
winners={'C3zP':'w_C3zP.json','Zsp75':'w_Zsp75.json','ArWird':'w_ArWird.json','jStURX':'w_jStURX.json','2tYcX':'w_2tYcX.json'}
rows=[]
for wn,fn in winners.items():
    p=os.path.join(SP,fn)
    if not os.path.exists(p): continue
    wt=json.load(open(p))
    for mint,v in wt.items():
        if mint in ouraddrs:
            rows.append((wn,mint,v.get('profit'),v.get('ret_pct'),idx[mint]['our_pnl_pct'],idx[mint]['gates']))
dd={r[1]:r for r in rows}; distinct=list(dd.values())
print(f"POOLED 5 winners | overlap instances {len(rows)} | distinct overlap tokens {len(distinct)}")
opn=[r[4] for r in distinct if r[4] is not None]
print(f"OUR realized on overlap: WR {sum(1 for p in opn if p>=0)}/{len(opn)}={sum(1 for p in opn if p>=0)/len(opn)*100:.0f}%  median {statistics.median(opn):.1f}%  mean {statistics.mean(opn):.1f}%")
wp=[r for r in distinct if r[2] is True]; wl=[r for r in distinct if r[2] is False]
print(f"WINNER realized on same tokens: profit {len(wp)} loss {len(wl)} open {sum(1 for r in distinct if r[2] is None)}")
print()
print(f"{'gate':18s} {'blkAll':>8s} {'blkWinProfit':>13s} {'we-LOST(saved)':>15s} {'we-WON(cost)':>13s} {'netPP':>8s}")
for g in GATES:
    valid=[r for r in distinct if r[5] and r[5].get(g) is not None]
    blk=[r for r in valid if r[5][g]]
    pv=[r for r in valid if r[2] is True]; pblk=[r for r in pv if r[5][g]]
    saved=[r for r in pblk if r[4] is not None and r[4]<0]; cost=[r for r in pblk if r[4] is not None and r[4]>=0]
    cb=[r for r in blk if r[4] is not None]
    savedpp=sum(-r[4] for r in cb if r[4]<0); costpp=sum(r[4] for r in cb if r[4]>=0)
    print(f"{g:18s} {len(blk):3d}/{len(valid):<4d} {len(pblk):3d}/{len(pv):<4d}({len(pblk)/len(pv)*100 if pv else 0:3.0f}%) {len(saved):>15d} {len(cost):>13d} {savedpp-costpp:>+8.1f}")
print()
print("=== winner-PROFIT overlap tokens our gates would BLOCK where WE realized POSITIVE (true winner-kills) ===")
for wn,mint,prof,wret,opnl,g in distinct:
    if prof is True and g and opnl is not None and opnl>=0:
        blk=[k for k in GATES if g.get(k)]
        if blk: print(f"  {mint[:12]} {wn:6s} winnerRet={round(wret,1) if wret else wret}% ourPnl=+{opnl:.1f}% killedBy={blk}")
print()
print("=== fat-tail winner tokens (winnerRet>=100%) in overlap & our gate status ===")
for wn,mint,prof,wret,opnl,g in distinct:
    if wret is not None and wret>=100:
        blk=[k for k in GATES if g and g.get(k)]
        print(f"  {mint[:12]} {wn:6s} winnerRet={wret:.0f}% ourPnl={round(opnl,1) if opnl is not None else 'open'} blockedBy={blk if blk else 'NONE(passed)'}")
