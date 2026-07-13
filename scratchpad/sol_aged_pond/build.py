import json, os
from collections import defaultdict, Counter

FILES = ['scratchpad/sol_aged_pond_raw2.json', 'scratchpad/_full_trades.json',
         'scratchpad/sol_aged_pond_raw.json', 'scratchpad/sol_selection/_trades_full.json']

# bots of interest: adolescent + absorb + aged/pond/pool_a  (keep young_absorb for age-band control)
def keep_bot(b):
    if not b: return False
    return any(k in b for k in ('adolescent', 'absorb', 'aged', 'pond', 'pool_a'))

def num(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None

def load():
    recs = {}
    for f in FILES:
        if not os.path.exists(f):
            continue
        for r in json.load(open(f)):
            b = r.get('bot_id') or ''
            if not keep_bot(b):
                continue
            key = (b, r.get('token'), r.get('address'), r.get('type'), r.get('time'),
                   round(float(r.get('entry_price') or 0), 12), round(float(r.get('exit_price') or 0), 12))
            recs[key] = r
    return list(recs.values())

def main():
    recs = load()
    buys = [r for r in recs if r.get('type') == 'buy']
    sells = [r for r in recs if r.get('type') == 'sell']
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
            if (b.get('time') or '') > st:
                continue
            bp = num(b.get('entry_price'))
            if ep and bp and abs(bp - ep) / ep < 0.02:
                best = b
        if best is None:
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
        # SCRUB RULE
        if ret is not None and hold is not None and ret > 0 and hold < 10:
            continue
        def g(k):
            return num(em.get(k))
        # hour of day from buy time
        bt = best.get('time') or ''
        hour = None
        try:
            hour = int(bt[11:13])
        except Exception:
            pass
        trips.append({
            'bot': s.get('bot_id'), 'token': s.get('token'), 'address': s.get('address'),
            'time': best.get('time'), 'sell_time': s.get('time'),
            'ret': ret, 'hold': hold, 'hour': hour,
            'peak': num(s.get('peak_pnl_pct')), 'mae': num(s.get('mae_pct')),
            'amount_usd': num(best.get('amount_usd')),
            # age
            'lifecycle_age_h': g('lifecycle_age_hours'),
            'hours_since_grad': g('hours_since_graduation'),
            'grad_age_bucket': em.get('graduation_age_bucket'),
            'lifecycle_stage': em.get('lifecycle_stage'),
            # dip depth / arc
            'pc_m5': g('pc_m5'), 'pc_h1': g('pc_h1'), 'pc_h6': g('pc_h6'), 'pc_h24': g('pc_h24'),
            'pct_off_peak': g('pct_off_peak'), 'minutes_since_peak': g('minutes_since_peak'),
            'lifecycle_peak_h24_pct': g('lifecycle_peak_h24_pct'),
            'h24_ratio_to_peak': g('lifecycle_h24_ratio') or g('h24_ratio_to_peak'),
            # liq / vol / mcap
            'liq': g('liquidity_usd'), 'entry_vol_h24': g('entry_volume_h24_usd'),
            'vol_5m_proj_hr': g('vol_5m_proj_hr_usd'), 'mcap_usd': g('mcap_usd'),
            'turnover_h24': g('turnover_h24_ratio'),
            # DEMAND / ABSORPTION gate axes
            'nf15_imbal': g('net_flow_15s_imbalance'), 'nf60_imbal': g('net_flow_60s_imbalance'),
            'nf5m_imbal': g('net_flow_5m_imbalance'),
            'nf15_usd': g('net_flow_15s_usd'), 'nf60_usd': g('net_flow_60s_usd'), 'nf5m_usd': g('net_flow_5m_usd'),
            'buy_sell_imbal': g('buy_sell_volume_imbalance'),
            'unique_buyers_n': g('unique_buyers_n'), 'unique_buyer_ratio': g('unique_buyer_ratio'),
            'buy_pressure_60s': g('buy_pressure_60s'),
            'buys_per_min': g('buys_per_min_recent'), 'sells_per_min': g('sells_per_min_recent'),
            'bs_m5': g('bs_m5'), 'bs_h1': g('bs_h1'), 'bs_h6': g('bs_h6'),
            'freq_accel': g('freq_acceleration'),
            'consec_buys_end': g('n_consecutive_buys_at_end'),
            # buyer composition
            'mean_buy_usd': g('mean_buy_size_usd'), 'median_buy_usd': g('median_buy_size_usd'),
            'p90_buy_usd': g('p90_buy_size_usd'),
            'top5_buyer_vol_pct': g('top5_buyer_volume_pct'), 'large_buyer_vol_pct': g('large_buyer_volume_pct'),
            'whale_max_buy_usd': g('whale_max_buy_usd'),
            'smart_wallet_count': g('smart_wallet_count_60s'),
            # rug/supply
            'hidden_supply_pct': g('hidden_supply_share_pct'), 'rugcheck_score': g('rugcheck_score'),
            'top10_holder_pct': g('top10_holder_pct'), 'top1_holder_pct': g('top1_holder_pct'),
            'lp_locked_pct': g('lp_locked_pct'), 'total_holders': g('total_holders'),
            'dev_pct_remaining': g('dev_pct_remaining'), 'dev_pct_dumped': g('dev_pct_dumped'),
            # structure
            'rsi_5m': g('rsi_5m'), 'rsi_15m': g('rsi_15m'),
            'bb_pos_5m': g('bb_pos_5m'), 'bb_pos_15m': g('bb_pos_15m'),
            'chart_score': g('chart_score'), 'chart_mtf_score': g('chart_mtf_score'),
            'lower_wick_ratio_5m': g('lower_wick_ratio_5m'),
            'pct_above_support': g('pct_above_support'),
            'regime': em.get('regime'),
            'has_em': bool(em),
        })
    os.makedirs('scratchpad/sol_aged_pond', exist_ok=True)
    with open('scratchpad/sol_aged_pond/_trips.json', 'w', encoding='utf-8') as f:
        json.dump(trips, f)
    wr = [t for t in trips if t['ret'] is not None]
    print('sells:', len(sells), 'unmatched:', unmatched, 'trips(post-scrub):', len(trips))
    print('with ret:', len(wr), 'green:', sum(1 for t in wr if t['ret'] > 0), 'distinct tok:', len(set(t['address'] for t in wr)))
    print('by bot:')
    for b, n in Counter(t['bot'] for t in wr).most_common():
        toks = len(set(t['address'] for t in wr if t['bot'] == b))
        print(f'  {n:4d} legs {toks:3d} tok  {b}')

if __name__ == '__main__':
    main()
