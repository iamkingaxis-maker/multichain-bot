"""Filter diagnostic tool — replays the dip_buy filter stack against
historical trades to show which filters would have fired.

Usage:
  python scripts/filter_diagnostic.py                      # aggregate summary
  python scripts/filter_diagnostic.py --token SPIKE        # single-token detail
  python scripts/filter_diagnostic.py --date 2026-04-27    # single-day detail
  python scripts/filter_diagnostic.py --losers             # only show losers
  python scripts/filter_diagnostic.py --overlap            # filter overlap matrix
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')


# ---- Filter definitions (mirrors feeds/dip_scanner.py) ----------------------
def parse_buy_metrics(buy_event):
    """Extract pc_h24, pc_h1, pc_m5 from buy reason string."""
    out = {}
    r = buy_event.get('reason', '') or ''
    for m in re.finditer(r'(24h|1h|5m)=([+\-]?[\d\.]+)%', r):
        key = {'24h': 'pc_h24', '1h': 'pc_h1', '5m': 'pc_m5'}[m.group(1)]
        out[key] = float(m.group(2))
    return out


def filter_checks(buy_event, sell_event):
    """Run every filter in the dip_buy stack against this trade.

    Returns list of (filter_name, would_have_fired, detail) for each filter.
    Order mirrors feeds/dip_scanner.py.  Note: this is post-hoc — we replay
    against the entry_meta captured at buy time, so it's accurate as long
    as the underlying signal we're testing is in entry_meta.
    """
    em = (sell_event.get('entry_meta') if sell_event else None) or {}
    pc = parse_buy_metrics(buy_event)
    pc_h24 = pc.get('pc_h24')
    pc_h1 = pc.get('pc_h1')
    pc_m5 = pc.get('pc_m5')

    bs_h6 = em.get('bs_h6')
    bs_h1 = em.get('bs_h1')
    bs_m5 = em.get('bs_m5')
    peak_h24 = em.get('peak_h24_6h_pct')
    h24_ratio = em.get('h24_ratio_to_peak')
    cum_3m = em.get('1m_cum_3min_pct')
    last_close = em.get('1m_last_close_pct')
    green_in_last3 = em.get('1m_green_in_last3')

    results = []

    def add(name, fired, detail=''):
        results.append((name, fired, detail))

    add('red_h24',
        pc_h24 is not None and pc_h24 <= 0,
        f'h24={pc_h24}')

    add('trend_reversal',
        peak_h24 is not None and h24_ratio is not None
        and peak_h24 >= 100 and h24_ratio < 0.25,
        f'peak={peak_h24} ratio={h24_ratio}')

    add('top_exhaustion',
        peak_h24 is not None and pc_h1 is not None
        and 50 <= peak_h24 <= 200 and pc_h1 >= 5.0,
        f'peak={peak_h24} h1={pc_h1}')

    add('no_dip',
        pc_h1 is not None and pc_m5 is not None and pc_h1 >= 0 and pc_m5 >= 0,
        f'h1={pc_h1} m5={pc_m5}')

    add('h1_mid_dip',
        pc_h1 is not None and -10.0 <= pc_h1 < -5.0,
        f'h1={pc_h1}')

    add('m5_dip_over',
        pc_m5 is not None and 0 <= pc_m5 < 3.0,
        f'm5={pc_m5}')

    add('falling_knife',
        pc_m5 is not None and pc_h1 is not None
        and pc_m5 < -5.0 and 0 < pc_h1 < 5.0,
        f'm5={pc_m5} h1={pc_h1}')

    add('mega_pump_middle',
        pc_h24 is not None and pc_m5 is not None and pc_h1 is not None
        and pc_h24 > 5000 and pc_m5 < 0 and -15 <= pc_h1 <= 50,
        f'h24={pc_h24} h1={pc_h1} m5={pc_m5}')

    add('bs_h6',
        bs_h6 is not None and bs_h6 < 1.0,
        f'bs_h6={bs_h6}')

    add('seller_h1_red_m5',
        bs_h1 is not None and pc_m5 is not None
        and 0 < bs_h1 < 0.85 and pc_m5 < 0,
        f'bs_h1={bs_h1} m5={pc_m5}')

    add('seller_pump',
        pc_h1 is not None and pc_m5 is not None and bs_m5 is not None
        and pc_h1 > 3.0 and pc_m5 > -2.0 and 0 < bs_m5 < 1.0,
        f'h1={pc_h1} m5={pc_m5} bs_m5={bs_m5}')

    add('no_1m_reversal',
        green_in_last3 is not None and green_in_last3 == 0,
        f'green={green_in_last3}')

    add('m1_top_tick',
        last_close is not None and last_close >= 2.0,
        f'last_close={last_close}')

    add('m1_false_bounce',
        cum_3m is not None and 1.0 <= cum_3m < 3.0,
        f'cum_3m={cum_3m}')

    add('top_consolidation',
        cum_3m is not None and pc_h1 is not None
        and pc_h1 > 3.0 and abs(cum_3m) < 0.5,
        f'h1={pc_h1} cum_3m={cum_3m}')

    return results


def parse_t(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def load_trades():
    return json.load(open('C:/Users/jcole/multichain-bot/trades_today.json'))


def pair_buys_sells(data):
    """For each closed sell with non-zero pnl, find its buy."""
    buys_by_addr = {}
    for d in data:
        if d['type'] == 'buy':
            buys_by_addr.setdefault(d['address'], []).append(d)
    pairs = []
    for d in data:
        if d['type'] != 'sell' or d.get('pnl', 0) == 0:
            continue
        sell_t = parse_t(d['time'])
        valid = [b for b in buys_by_addr.get(d['address'], [])
                 if parse_t(b['time']) <= sell_t]
        if not valid:
            continue
        buy = max(valid, key=lambda b: parse_t(b['time']))
        pairs.append((buy, d))
    return pairs


def show_single(buy, sell):
    t = parse_t(sell['time']).strftime('%Y-%m-%d %H:%M:%S')
    pnl = sell.get('pnl', 0)
    pnl_pct = sell.get('pnl_pct', 0)
    rsn = (sell.get('reason', '') or '').replace('—', '-')
    rsn = ''.join(c if ord(c) < 128 else '?' for c in rsn)[:50]
    pc = parse_buy_metrics(buy)
    em = sell.get('entry_meta') or {}
    print(f'\n{"="*82}')
    print(f'{sell.get("token", "?"):10}  pnl=${pnl:+8.2f} ({pnl_pct:+.1f}%)  '
          f'sold {t}  reason={rsn}')
    print(f'  Entry: 24h={pc.get("pc_h24","?")} h1={pc.get("pc_h1","?")} '
          f'm5={pc.get("pc_m5","?")}  '
          f'bs_h6={em.get("bs_h6")} bs_h1={em.get("bs_h1")} '
          f'bs_m5={em.get("bs_m5")}')
    print(f'  1m: green={em.get("1m_green_in_last3")} '
          f'last={em.get("1m_last_close_pct")}% '
          f'cum_3m={em.get("1m_cum_3min_pct")}% '
          f'vol_spike={em.get("1m_volume_spike")}')
    print(f'  Range: peak_h24={em.get("peak_h24_6h_pct")}% '
          f'ratio_to_peak={em.get("h24_ratio_to_peak")} '
          f'pct_in_1h_range={em.get("pct_in_1h_range","new")}')
    print()
    results = filter_checks(buy, sell)
    print(f'  {"FILTER":25} {"FIRED":>6}  DETAIL')
    print(f'  {"-"*25} {"-"*6}  {"-"*40}')
    fired_any = False
    for name, fired, detail in results:
        if fired:
            fired_any = True
        flag = 'CUT' if fired else 'pass'
        marker = '!' if fired else ' '
        print(f'  {marker} {name:25} {flag:>6}  {detail}')
    if not fired_any:
        print('\n  -> No filters would have fired. Trade was eligible per current rules.')


def show_aggregate(pairs):
    """Per-filter would-cut count + win/loss split."""
    filter_counts = {}
    for buy, sell in pairs:
        pnl = sell.get('pnl', 0)
        for name, fired, _ in filter_checks(buy, sell):
            d = filter_counts.setdefault(name, {'cuts': 0, 'cut_pnl': 0,
                                                 'cut_wins': 0, 'cut_losses': 0})
            if fired:
                d['cuts'] += 1
                d['cut_pnl'] += pnl
                if pnl > 0:
                    d['cut_wins'] += 1
                else:
                    d['cut_losses'] += 1
    total_n = len(pairs)
    total_pnl = sum(s.get('pnl', 0) for _, s in pairs)
    print(f'\nTotal closed trades: {total_n}, net P&L: ${total_pnl:+.2f}\n')
    print(f'{"FILTER":25} {"WouldCut":>9} {"% all":>6} {"Cut$":>10} '
          f'{"Wins":>5} {"Losses":>7} {"avg/cut":>9}')
    print('-' * 86)
    for name, d in sorted(filter_counts.items(), key=lambda x: -x[1]['cuts']):
        if d['cuts'] == 0:
            continue
        avg = d['cut_pnl'] / d['cuts']
        pct = d['cuts'] / total_n * 100
        print(f'{name:25} {d["cuts"]:>9} {pct:>5.1f}% ${d["cut_pnl"]:>+8.2f} '
              f'{d["cut_wins"]:>5} {d["cut_losses"]:>7} ${avg:>+7.2f}')


def show_overlap(pairs):
    """Filter overlap matrix — for each pair, count co-firings."""
    fires = {}
    order = []
    for i, (buy, sell) in enumerate(pairs):
        for name, fired, _ in filter_checks(buy, sell):
            if name not in fires:
                fires[name] = set()
                order.append(name)
            if fired:
                fires[name].add(i)
    active = [n for n in order if len(fires[n]) > 0]
    print(f'\n=== FILTER OVERLAP MATRIX ({len(active)} active filters) ===\n')
    print('Cell = N trades where BOTH filters would fire\n')
    cw = 5
    hdr = f'{"":24}' + ''.join(f' {n[:cw]:>{cw}}' for n in active)
    print(hdr)
    for a in active:
        row = f'{a:24}'
        for b in active:
            n = len(fires[a] & fires[b])
            row += f' {n:>{cw}}'
        print(row)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--token', help='filter by token symbol')
    p.add_argument('--date', help='filter by date YYYY-MM-DD')
    p.add_argument('--losers', action='store_true')
    p.add_argument('--winners', action='store_true')
    p.add_argument('--overlap', action='store_true')
    p.add_argument('--max', type=int, default=20)
    args = p.parse_args()

    pairs = pair_buys_sells(load_trades())

    if args.token:
        pairs = [(b, s) for b, s in pairs
                 if (s.get('token') or '').upper() == args.token.upper()]
    if args.date:
        pairs = [(b, s) for b, s in pairs
                 if parse_t(s['time']).strftime('%Y-%m-%d') == args.date]
    if args.losers:
        pairs = [(b, s) for b, s in pairs if s.get('pnl', 0) < 0]
    if args.winners:
        pairs = [(b, s) for b, s in pairs if s.get('pnl', 0) > 0]

    if args.overlap:
        show_overlap(pairs)
        return

    if args.token or args.date or args.losers or args.winners:
        for buy, sell in pairs[:args.max]:
            show_single(buy, sell)
        if len(pairs) > args.max:
            print(f'\n... {len(pairs) - args.max} more trades (use --max N)')
    else:
        show_aggregate(pairs)


if __name__ == '__main__':
    main()
