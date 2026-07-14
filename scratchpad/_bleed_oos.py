import json, statistics
d=json.load(open('_trades_cache.json'))
rows=[t for t in d if t.get('type')=='sell' and (t.get('bot_id') or '').startswith('badday_')
      and t.get('time','')>='2026-07-03' and t.get('mae_at_secs') is not None]
rows=[r for r in rows if not (r['pnl_pct']>0 and r['hold_secs']<10)]

def wr(sub): return round(sum(1 for r in sub if r['pnl_pct']>0)/len(sub),3) if sub else None
def tokmed_ex2(sub):
    if not sub: return None
    bt={}
    for r in sub: bt.setdefault(r['token'],[]).append(r['pnl_pct'])
    tm=sorted((statistics.median(v) for v in bt.values()),reverse=True)
    kept=tm[2:] if len(tm)>2 else tm
    return round(statistics.median(kept),2) if kept else None

def strong_by_T(r,T):
    s=r.get('tp1_knee_3_secs')
    return s is not None and s<=T

def cut_flag(r,T):
    # decision-time only: still making new lows at T (global low not yet reached)
    #   AND has not shown +3 strength by T
    return r['mae_at_secs']>=T and not strong_by_T(r,T)

def eval_q(sub,T):
    cut=[r for r in sub if cut_flag(r,T)]
    hold=[r for r in sub if not cut_flag(r,T)]
    cwin=[r for r in cut if r['pnl_pct']>0]
    return dict(n=len(sub),ncut=len(cut),
        winner_kill_wr=wr(cut),
        loser_save=round(1-len(cwin)/len(cut),3) if cut else None,
        cut_tokmed=tokmed_ex2(cut), hold_tokmed=tokmed_ex2(hold),
        hold_wr=wr(hold), base_wr=wr(sub), base_tokmed=tokmed_ex2(sub))

rs=sorted(rows,key=lambda r:r['time'])
mid=len(rs)//2
early,late=rs[:mid],rs[mid:]
q={'Q1_early_odd':early[0::2],'Q2_early_even':early[1::2],
   'Q3_late_odd':late[0::2],'Q4_late_even':late[1::2]}

for T in (120,90):
    print(f'\n===== FOUR-HALF OOS  cut = still-making-new-lows@{T}s & not-+3-strong-by-{T}s =====')
    for name,sub in q.items():
        r=eval_q(sub,T)
        print(f'{name}: n={r["n"]:3d} cut={r["ncut"]:3d} winner_kill_wr={r["winner_kill_wr"]} '
              f'loser_save={r["loser_save"]} | hold_wr={r["hold_wr"]} base_wr={r["base_wr"]} '
              f'| cut_tokmed={r["cut_tokmed"]} hold_tokmed={r["hold_tokmed"]} base_tokmed={r["base_tokmed"]}')

# ---- Economic magnitude on full sample: final-pnl medians (cut positions if HELD to close)
print('\n===== Economic view (FINAL pnl of cut set if held vs hold set), T=120 =====')
cut=[r for r in rows if cut_flag(r,120)]; hold=[r for r in rows if not cut_flag(r,120)]
print(f'CUT-if-held: n={len(cut)} median_final_pnl={round(statistics.median(r["pnl_pct"] for r in cut),2)} '
      f'mean_final_pnl={round(statistics.mean(r["pnl_pct"] for r in cut),2)} tokmed_ex2={tokmed_ex2(cut)}')
print(f'HOLD set:    n={len(hold)} median_final_pnl={round(statistics.median(r["pnl_pct"] for r in hold),2)} '
      f'mean_final_pnl={round(statistics.mean(r["pnl_pct"] for r in hold),2)} tokmed_ex2={tokmed_ex2(hold)}')
# among cut winners, distribution of final pnl (the forgone upside)
cw=sorted((r['pnl_pct'] for r in cut if r['pnl_pct']>0))
print(f'cut winners forgone upside: n={len(cw)} median={round(statistics.median(cw),2)} '
      f'p90={round(cw[int(0.9*len(cw))],2)} max={round(max(cw),2)}')
cl=sorted((r['pnl_pct'] for r in cut if r['pnl_pct']<=0))
print(f'cut losers final (bleed we would cap): n={len(cl)} median={round(statistics.median(cl),2)} min={round(min(cl),2)}')

# ---- PRESERVE-THE-BOUNCER direction: positions that bottomed FAST (<=60s) — winrate/tokmed
print('\n===== V-bouncer cohort (mae_at_secs<=60): HOLD-preserve candidate =====')
vb=[r for r in rows if r['mae_at_secs']<=60]
sb=[r for r in rows if r['mae_at_secs']>60]
print(f'V-bouncer (mae<=60s): n={len(vb)} wr={wr(vb)} tokmed_ex2={tokmed_ex2(vb)} median_pnl={round(statistics.median(r["pnl_pct"] for r in vb),2)}')
print(f'slow (mae>60s):       n={len(sb)} wr={wr(sb)} tokmed_ex2={tokmed_ex2(sb)} median_pnl={round(statistics.median(r["pnl_pct"] for r in sb),2)}')
