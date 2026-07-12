import json, statistics as st
from collections import defaultdict

T = json.load(open('scratchpad/sol_selection/_trips.json'))
T = [t for t in T if t.get('ret') is not None]

# top-2 tokens by leg count (to exclude)
from collections import Counter
legc = Counter(t['token'] for t in T)
TOP2 = set(tok for tok, _ in legc.most_common(2))

def tokmed(trips, ex_top2=False):
    """median over tokens of per-token median return; returns (tokmed, n_tokens)."""
    by = defaultdict(list)
    for t in trips:
        if ex_top2 and t['token'] in TOP2:
            continue
        by[t['address']].append(t['ret'])
    per = [st.median(v) for v in by.values()]
    if not per:
        return None, 0
    return st.median(per), len(per)

def winrate(trips):
    if not trips: return None
    return 100.0 * sum(1 for t in trips if t['ret'] > 0) / len(trips)

def half_chrono(trips):
    s = sorted(trips, key=lambda t: t['sell_time'] or t['time'] or '')
    mid = len(s)//2
    return s[:mid], s[mid:]

def half_oddeven(trips):
    odd, even = [], []
    for t in trips:
        d = (t['time'] or '')[:10]
        try:
            day = int(d[-2:])
        except:
            continue
        (odd if day % 2 else even).append(t)
    return odd, even

def quintiles(trips, axis):
    vals = sorted(t[axis] for t in trips if t.get(axis) is not None)
    if len(vals) < 20:
        return None
    qs = [vals[int(len(vals)*f)] for f in (0.2,0.4,0.6,0.8)]
    return qs

def bucket_of(v, qs):
    for i, q in enumerate(qs):
        if v <= q: return i
    return len(qs)

def axis_table(trips, axis, label=None):
    present = [t for t in trips if t.get(axis) is not None]
    qs = quintiles(present, axis)
    if qs is None:
        return f"  {axis}: insufficient ({len(present)} present)\n"
    out = [f"== {label or axis} (n_trips={len(present)}) quintile edges={[round(q,3) for q in qs]}"]
    out.append(f"  {'bucket':<10}{'tokmed':>9}{'tokmed_x2':>11}{'nTok':>6}{'nTok_x2':>8}{'winrate':>9}{'meanret':>9}")
    for b in range(5):
        bt = [t for t in present if bucket_of(t[axis], qs) == b]
        tm, ntok = tokmed(bt)
        tm2, ntok2 = tokmed(bt, ex_top2=True)
        wr = winrate(bt)
        mr = st.mean(t['ret'] for t in bt) if bt else None
        rng = f"<={round(qs[b],3)}" if b < 4 else f">{round(qs[3],3)}"
        out.append(f"  Q{b+1} {rng:<6}{fmt(tm):>9}{fmt(tm2):>11}{ntok:>6}{ntok2:>8}{fmt(wr):>9}{fmt(mr):>9}")
    return "\n".join(out) + "\n"

def fmt(x):
    return f"{x:+.1f}" if isinstance(x,(int,float)) else "  -"

# ----- run -----
NEW = ['pct_off_peak','pc_h1','pc_h6','pc_h24','lifecycle_peak_h24_pct','h24_ratio_to_peak',
       'minutes_since_peak','lifecycle_age_h','entry_vol_h24','vol_5m_proj_hr','rt_buys_usd','rt_n',
       'rt_buys_n','buys_per_min','unique_buyers_n','net_flow_15s','net_flow_60s','net_flow_5m',
       'buy_sell_imbal','buy_pressure_60s','bs_m5','bs_h1','mean_buy_usd','median_buy_usd','p90_buy_usd',
       'avg_trade_h1_usd','liq','mcap_usd','hidden_supply_pct','rugcheck_score','top10_holder_pct',
       'top1_holder_pct','total_holders','chart_mtf_score','chart_score','smart_wallet_volume_pct',
       'top5_buyer_volume_pct','large_buyer_volume_pct','entry_slip_pct','unique_buyer_ratio','trades_per_sec_last60s']

import sys
sec = sys.argv[1] if len(sys.argv)>1 else 'all'
tmall, ntall = tokmed(T)
tmall2, ntall2 = tokmed(T, ex_top2=True)
print(f"BASELINE all trips: tokmed={fmt(tmall)} ({ntall} tok) | ex-top2={fmt(tmall2)} ({ntall2} tok) | winrate={fmt(winrate(T))} | n={len(T)}")
print(f"TOP2 excluded tokens: {TOP2}")
print()
for ax in NEW:
    print(axis_table(T, ax))
