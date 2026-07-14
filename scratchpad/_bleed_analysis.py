import json, statistics
d=json.load(open('_trades_cache.json'))

def load():
    rows=[]
    for t in d:
        if t.get('type')!='sell': continue
        bid=t.get('bot_id') or ''
        if not bid.startswith('badday_'): continue
        if (t.get('time','')) < '2026-07-03': continue
        if t.get('mae_at_secs') is None: continue
        rows.append(t)
    # scrub rule: drop ret>0 & hold<10s
    rows=[r for r in rows if not (r['pnl_pct']>0 and r['hold_secs']<10)]
    return rows

rows=load()
print('N after load+scrub:', len(rows))

def token_median_ex_top2(sub):
    """ex-top-2 token-median: per-token median pnl_pct, drop the 2 tokens with
    highest per-token median (the outsized winners), then median across tokens."""
    if not sub: return None
    bytok={}
    for r in sub:
        bytok.setdefault(r['token'], []).append(r['pnl_pct'])
    tokmed=sorted((statistics.median(v) for v in bytok.values()), reverse=True)
    kept=tokmed[2:] if len(tokmed)>2 else tokmed
    return round(statistics.median(kept),3) if kept else None

def winrate(sub):
    return round(sum(1 for r in sub if r['pnl_pct']>0)/len(sub),3) if sub else None

# ---- Rule: CUT at time T if still making new lows at T (mae not yet reached),
#      never showed strength (peak_so_far<peak_thr), and on a bleeding path.
def cut_flag(r, T, peak_thr=2.0, mae_thr=-4.0):
    # decision-time proxies from summary fields:
    still_low = r['mae_at_secs'] >= T          # global low not yet reached by T => still bleeding
    weak = (r['peak_pnl_pct'] or 0.0) < peak_thr  # never peaked >= thr (final peak is upper bound on peak-so-far)
    bleeding = (r['mae_pct'] or 0.0) <= mae_thr    # path reaches at least mae_thr
    return still_low and weak and bleeding

def eval_rule(sub, T, peak_thr=2.0, mae_thr=-4.0):
    cut=[r for r in sub if cut_flag(r,T,peak_thr,mae_thr)]
    hold=[r for r in sub if not cut_flag(r,T,peak_thr,mae_thr)]
    if not cut:
        return dict(n=len(sub), ncut=0)
    cut_win=[r for r in cut if r['pnl_pct']>0]
    return dict(
        n=len(sub), ncut=len(cut),
        cut_frac=round(len(cut)/len(sub),3),
        cut_winrate=winrate(cut),          # winner-kill rate within cut set
        cut_loser_frac=round(1-len(cut_win)/len(cut),3),  # loser-save rate within cut
        cut_tokmed=token_median_ex_top2(cut),
        hold_winrate=winrate(hold),
        hold_tokmed=token_median_ex_top2(hold),
        base_winrate=winrate(sub),
        base_tokmed=token_median_ex_top2(sub),
    )

print('\n=== FULL SAMPLE rule scan ===')
for T in (90,120):
    for peak_thr in (2.0,):
        for mae_thr in (-3.0,-4.0,-5.0):
            r=eval_rule(rows,T,peak_thr,mae_thr)
            print(f'T={T} peak<{peak_thr} mae<={mae_thr}: '
                  f'cut={r.get("ncut")}({r.get("cut_frac")}) cut_wr={r.get("cut_winrate")} '
                  f'loser_save={r.get("cut_loser_frac")} cut_tokmed={r.get("cut_tokmed")} '
                  f'hold_wr={r.get("hold_winrate")} hold_tokmed={r.get("hold_tokmed")} '
                  f'| base_wr={r.get("base_winrate")} base_tokmed={r.get("base_tokmed")}')

# ---- FOUR-HALF OOS: chronological halves x odd/even
rows_sorted=sorted(rows, key=lambda r: r['time'])
mid=len(rows_sorted)//2
early=rows_sorted[:mid]; late=rows_sorted[mid:]
def oddeven(sub):
    return sub[0::2], sub[1::2]
q={}
q['Q1_early_odd'], q['Q2_early_even']=oddeven(early)
q['Q3_late_odd'], q['Q4_late_even']=oddeven(late)

print('\n=== FOUR-HALF OOS (T=120, peak<2, mae<=-4) ===')
for name,sub in q.items():
    r=eval_rule(sub,120,2.0,-4.0)
    print(f'{name}: n={r["n"]} cut={r.get("ncut")} cut_wr={r.get("cut_winrate")} '
          f'loser_save={r.get("cut_loser_frac")} cut_tokmed={r.get("cut_tokmed")} '
          f'hold_wr={r.get("hold_winrate")} base_wr={r.get("base_winrate")} '
          f'hold_tokmed={r.get("hold_tokmed")} base_tokmed={r.get("base_tokmed")}')

print('\n=== FOUR-HALF OOS (T=90, peak<2, mae<=-4) ===')
for name,sub in q.items():
    r=eval_rule(sub,90,2.0,-4.0)
    print(f'{name}: n={r["n"]} cut={r.get("ncut")} cut_wr={r.get("cut_winrate")} '
          f'loser_save={r.get("cut_loser_frac")} cut_tokmed={r.get("cut_tokmed")} '
          f'hold_wr={r.get("hold_winrate")} base_wr={r.get("base_winrate")}')
