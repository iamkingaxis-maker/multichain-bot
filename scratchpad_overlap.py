import json,glob,os,collections
SP="C:/Users/jcole/AppData/Local/Temp/claude/C--Users-jcole-multichain-bot/ecbaef77-2f98-4dc5-9231-4bd9a529e92c/scratchpad"
idx=json.load(open('scratchpad_ourindex.json'))
ouraddrs=set(idx.keys())
GATES=['falling_knife','post_pump_corpse','steep_fall_1m','consec_red_knife','structure_edge']
winners={'C3zP':'w_C3zP.json','Zsp75':'w_Zsp75.json','ArWird':'w_ArWird.json','jStURX':'w_jStURX.json'}

# aggregate across all winners (pooled) + per winner
overlap_rows=[]  # (winner, mint, winner_profit, winner_ret, our_pnl, gates)
for wn,fn in winners.items():
    wt=json.load(open(os.path.join(SP,fn)))
    for mint,v in wt.items():
        if mint in ouraddrs:
            rec=idx[mint]
            overlap_rows.append((wn,mint,v.get('profit'),v.get('ret_pct'),rec['our_pnl_pct'],rec['gates'],rec['nclosed']))

print("=== OVERLAP TOKENS (winner-traded AND we-traded), matched by full address ===")
print(f"total overlap legs/tokens matched: {len(overlap_rows)}")
permint=collections.Counter(r[1] for r in overlap_rows)
print(f"distinct overlap tokens: {len(permint)}")
print()

# Per-gate block analysis on winner-PROFITABLE overlap tokens
def analyze(rows,label):
    print(f"--- {label}: n={len(rows)} overlap tokens ---")
    # winner profitable subset
    prof=[r for r in rows if r[2] is True]
    loss=[r for r in rows if r[2] is False]
    openw=[r for r in rows if r[2] is None]
    print(f"  winner outcome: profitable={len(prof)} loss={len(loss)} open/unknown={len(openw)}")
    for g in GATES:
        # among tokens where we have a gate value
        valid=[r for r in rows if r[5] and r[5].get(g) is not None]
        blocked=[r for r in valid if r[5][g]]
        # winner-profitable & blocked
        pv=[r for r in valid if r[2] is True]
        pblk=[r for r in pv if r[5][g]]
        # of blocked winner-profitable tokens, our realized pnl
        saved=[r for r in pblk if r[4] is not None and r[4]<0]   # blocking SAVED us (we lost)
        cost=[r for r in pblk if r[4] is not None and r[4]>=0]   # blocking COST us (we won)
        wkrate=(len(pblk)/len(pv)*100) if pv else 0
        print(f"  {g:18s} block {len(blocked)}/{len(valid)} ({len(blocked)/len(valid)*100 if valid else 0:4.0f}%) | "
              f"winner-PROFIT-overlap block {len(pblk)}/{len(pv)} ({wkrate:4.0f}%) | "
              f"of those: we-LOST(saved)={len(saved)} we-WON(cost)={len(cost)}")

analyze(overlap_rows,"POOLED (4 full-map winners)")
print()
for wn in winners:
    rows=[r for r in overlap_rows if r[0]==wn]
    if rows: analyze(rows,wn)
    print()

# Detail: winner-profitable overlap tokens that ANY gate would block, with our pnl
print("=== WINNER-PROFITABLE overlap tokens BLOCKED by >=1 gate (winner-kill candidates) ===")
seen=set()
for wn,mint,prof,wret,opnl,g,ncl in overlap_rows:
    if prof is True and g:
        blk=[k for k in GATES if g.get(k)]
        if blk and mint not in seen:
            seen.add(mint)
            print(f"  {mint[:12]} {wn:7s} winnerRet={wret if wret is None else round(wret,1)}% ourPnl={opnl if opnl is None else round(opnl,1)}% closed={ncl} blockedBy={blk}")
