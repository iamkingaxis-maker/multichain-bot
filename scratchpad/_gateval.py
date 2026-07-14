import json, statistics as st
rows=json.load(open('_pos_rows.json'))
def addr(r): return r['address']
tot_ev=sum(r['rpnl'] for r in rows)/len(rows)
N=len(rows)
dtN=len(set(addr(r) for r in rows))
print('BASELINE book: n=%d dt=%d EV=%.3fpp sum=%.1fpp\n'%(N,dtN,tot_ev,sum(r['rpnl'] for r in rows)))

def ev_gate(pred,label):
    blocked=[r for r in rows if pred(r)]
    kept=[r for r in rows if not pred(r)]
    if not blocked: print(label+': blocks 0\n'); return
    win_killed=[r['rpnl'] for r in blocked if r['rpnl']>0]
    loss_removed=[r['rpnl'] for r in blocked if r['rpnl']<=0]
    kept_ev=sum(r['rpnl'] for r in kept)/len(kept) if kept else 0
    dtb=len(set(addr(r) for r in blocked))
    bev=sum(r['rpnl'] for r in blocked)/len(blocked)
    print(label)
    print('  blocks n=%d dt=%d  blockedEV=%.2f'%(len(blocked),dtb,bev))
    print('  winners killed: %d pos, +%.1fpp (avg win %.1f)'%(len(win_killed),sum(win_killed),st.mean(win_killed) if win_killed else 0))
    print('  losses removed: %d pos, %.1fpp'%(len(loss_removed),sum(loss_removed)))
    print('  book EV: %.3f -> %.3f  (delta %+.3f pp/trade; kept n=%d)'%(tot_ev,kept_ev,kept_ev-tot_ev,len(kept)))
    print()

ev_gate(lambda r: r['pc_h6'] is not None and -25<r['pc_h6']<=-10, 'BLOCK mid-flush (-25<pc_h6<=-10)')
ev_gate(lambda r: r['vol'] is not None and r['vol']>=300, 'BLOCK vol>=300%')
ev_gate(lambda r: r['rsi15'] is not None and 55<=r['rsi15']<70, 'BLOCK rsi_15m 55-70')
ev_gate(lambda r: r['uniq'] is not None and r['uniq']<30, 'BLOCK unique_buyers<30')
ev_gate(lambda r: (r['pc_h6'] is not None and -25<r['pc_h6']<=-10) or (r['vol'] is not None and r['vol']>=300), 'BLOCK mid-flush OR vol>=300')
ev_gate(lambda r: r['pc_h6'] is not None and -25<r['pc_h6']<=-10 and (r['uniq'] is None or r['uniq']<50), 'BLOCK mid-flush & uniq<50')
