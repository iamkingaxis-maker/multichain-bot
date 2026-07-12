import json, sys
from collections import defaultdict

FILES = ['scratchpad/_full_trades.json', 'scratchpad/sol_selection/_trades_full.json']

def load():
    recs = {}
    for f in FILES:
        for r in json.load(open(f)):
            b = r.get('bot_id') or ''
            if 'young' not in b:
                continue
            key = (b, r.get('token'), r.get('address'), r.get('type'), r.get('time'),
                   round(float(r.get('entry_price') or 0), 12), round(float(r.get('exit_price') or 0), 12))
            recs[key] = r  # dedupe
    return list(recs.values())

def num(x):
    try:
        if x is None: return None
        return float(x)
    except: return None

def main():
    recs = load()
    buys = [r for r in recs if r.get('type') == 'buy']
    sells = [r for r in recs if r.get('type') == 'sell']
    # index buys by (bot,address)
    bidx = defaultdict(list)
    for r in buys:
        bidx[(r.get('bot_id'), r.get('address'))].append(r)
    for lst in bidx.values():
        lst.sort(key=lambda r: r.get('time') or '')

    trips = []
    unmatched = 0
    for s in sells:
        key = (s.get('bot_id'), s.get('address'))
        cands = bidx.get(key, [])
        ep = num(s.get('entry_price'))
        st = s.get('time') or ''
        best = None
        for b in cands:
            bt = b.get('time') or ''
            if bt > st:  # buy after sell -> skip
                continue
            bp = num(b.get('entry_price'))
            if ep and bp and abs(bp - ep) / ep < 0.02:
                best = b  # latest matching buy before sell
        if best is None:
            # fallback: closest entry_price match ignoring time
            for b in cands:
                bp = num(b.get('entry_price'))
                if ep and bp and abs(bp - ep) / ep < 0.02:
                    best = b
        if best is None:
            unmatched += 1
            continue
        em = best.get('entry_meta') or {}
        ret = num(s.get('pnl_pct'))
        hold = num(s.get('hold_secs'))
        # SCRUB RULE: drop ret>0 AND hold<10s (phantom spikes)
        if ret is not None and hold is not None and ret > 0 and hold < 10:
            continue
        trips.append({
            'bot': s.get('bot_id'), 'token': s.get('token'), 'address': s.get('address'),
            'time': best.get('time'), 'sell_time': s.get('time'),
            'ret': ret, 'hold': hold,
            'peak': num(s.get('peak_pnl_pct')), 'mae': num(s.get('mae_pct')),
            'amount_usd': num(best.get('amount_usd')),
            'entry_slip_pct': num(best.get('entry_slip_pct')),
            'entry_price': num(best.get('entry_price')), 'entry_mid': num(best.get('entry_mid_price')),
            # ---- AXES from entry_meta ----
            'pct_off_peak': num(em.get('pct_off_peak')),
            'minutes_since_peak': num(em.get('minutes_since_peak')),
            'entry_vol_h24': num(em.get('entry_volume_h24_usd')),
            'vol_5m_proj_hr': num(em.get('vol_5m_proj_hr_usd')),
            'liq': num(em.get('liquidity_usd')),
            'lifecycle_age_h': num(em.get('lifecycle_age_hours')),
            'hours_since_grad': num(em.get('hours_since_graduation')),
            'lifecycle_peak_h24_pct': num(em.get('lifecycle_peak_h24_pct')),
            'h24_ratio_to_peak': num(em.get('h24_ratio_to_peak')),
            'pc_m5': num(em.get('pc_m5')), 'pc_h1': num(em.get('pc_h1')),
            'pc_h6': num(em.get('pc_h6')), 'pc_h24': num(em.get('pc_h24')),
            'mcap_usd': num(em.get('mcap_usd')),
            # demand composition
            'net_flow_15s': num(em.get('net_flow_15s_usd')),
            'net_flow_60s': num(em.get('net_flow_60s_usd')),
            'net_flow_5m': num(em.get('net_flow_5m_usd')),
            'buy_sell_imbal': num(em.get('buy_sell_volume_imbalance')),
            'unique_buyers_n': num(em.get('unique_buyers_n')),
            'unique_buyer_ratio': num(em.get('unique_buyer_ratio')),
            'buy_pressure_60s': num(em.get('buy_pressure_60s')),
            'bs_m5': num(em.get('bs_m5')), 'bs_h1': num(em.get('bs_h1')),
            'buys_h1': num(em.get('buys_h1')), 'sells_h1': num(em.get('sells_h1')),
            'buys_per_min': num(em.get('buys_per_min_recent')),
            'rt_n': num(em.get('rt_n')), 'rt_buys_usd': num(em.get('rt_buys_usd')),
            'rt_buys_n': num(em.get('rt_buys_n')), 'rt_dollar_imbal': num(em.get('rt_dollar_imbalance')),
            'mean_buy_usd': num(em.get('mean_buy_size_usd')),
            'median_buy_usd': num(em.get('median_buy_size_usd')),
            'p90_buy_usd': num(em.get('p90_buy_size_usd')),
            'avg_trade_h1_usd': num(em.get('avg_trade_size_h1_usd')),
            # rug / supply
            'hidden_supply_pct': num(em.get('hidden_supply_share_pct')),
            'rugcheck_score': num(em.get('rugcheck_score')),
            'top10_holder_pct': num(em.get('top10_holder_pct')),
            'top1_holder_pct': num(em.get('top1_holder_pct')),
            'lp_locked_pct': num(em.get('lp_locked_pct')),
            'lp_burned': em.get('lp_burned'),
            'total_holders': num(em.get('total_holders')),
            # structure / mtf (baseline)
            'chart_mtf_score': num(em.get('chart_mtf_score')),
            'chart_mtf_align': em.get('chart_mtf_alignment'),
            'chart_score': num(em.get('chart_score')),
            'regime': em.get('regime'),
            'smart_wallet_count_60s': num(em.get('smart_wallet_count_60s')),
            'smart_wallet_volume_pct': num(em.get('smart_wallet_volume_pct')),
            'top5_buyer_volume_pct': num(em.get('top5_buyer_volume_pct')),
            'large_buyer_volume_pct': num(em.get('large_buyer_volume_pct')),
            'trades_per_sec_last60s': num(em.get('trades_per_sec_last60s')),
            'has_em': bool(em),
        })
    with open('scratchpad/sol_selection/_trips.json', 'w') as f:
        json.dump(trips, f)
    withret = [t for t in trips if t['ret'] is not None]
    green = [t for t in withret if t['ret'] > 0]
    print('sells:', len(sells), 'unmatched:', unmatched, 'trips(post-scrub):', len(trips))
    print('trips with ret:', len(withret), 'green:', len(green), 'red:', len(withret)-len(green))
    print('distinct tokens:', len(set(t['address'] for t in withret)))
    # em coverage
    print('trips with entry_meta:', sum(1 for t in trips if t['has_em']))
    from collections import Counter
    print('by bot:', dict(Counter(t['bot'] for t in withret)))

if __name__ == '__main__':
    main()
