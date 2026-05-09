"""Test 'stale peak' rules against historical trades.

Idea: instead of a tight trail (which cuts winners that briefly retrace),
require BOTH:
  - The peak was reached at least N minutes ago (time-since-peak >= N)
  - Current pnl is ≤ some threshold below the peak

Concept: real TP1-bound runners keep making new highs. Pre-TP1 faders
stall — peak gets stale while price drifts down.

For each closed trade, walk minute-by-minute through held window and
check if the rule would have fired BEFORE the bot's actual exit. Track:
  - Winners that would be cut (final pnl > +0.5)
  - Losers that would be caught early (final pnl < -0.5)
"""
from __future__ import annotations
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
from curl_cffi import requests as cf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from feeds.dexscreener_chart_format import parse_chart_bars

SOL = 'So11111111111111111111111111111111111111112'
SLUG_MAP = {'pumpswap': 'pumpfundex', 'pumpfun': 'pumpfundex',
            'raydium': 'solamm', 'meteora': 'meteora'}


def fetch_trades():
    return requests.get(
        'https://gracious-inspiration-production.up.railway.app/api/trades?all=1',
        timeout=60).json()


def is_dip_sell(t):
    if t.get('type') != 'sell' or t.get('pnl') is None:
        return False
    r = (t.get('reason') or '').strip()
    if not r or 'cancelled' in r.lower():
        return False
    return r.startswith('Dip ') or 'Manual sell' in r


def pair_buys_closed(trades):
    sells_by_addr = defaultdict(list)
    buys_by_addr = defaultdict(list)
    pair_lookup = {}
    for t in trades:
        addr = (t.get('address') or '').lower()
        if not addr: continue
        pa = t.get('pair_address')
        if pa and addr not in pair_lookup:
            pair_lookup[addr] = pa
        if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy':
            buys_by_addr[addr].append(t)
        elif is_dip_sell(t):
            sells_by_addr[addr].append(t)
    for k in buys_by_addr:
        buys_by_addr[k].sort(key=lambda b: b.get('time') or '')
    out = []
    for addr, buys in buys_by_addr.items():
        for i, b in enumerate(buys):
            bt = b.get('time') or ''
            next_bt = buys[i + 1].get('time') if i + 1 < len(buys) else '9999'
            cands = [s for s in sells_by_addr[addr]
                     if bt < (s.get('time') or '') < next_bt
                     and s.get('pnl') is not None]
            if not cands: continue
            pnl = sum(s.get('pnl') for s in cands)
            exit_t = min((s.get('time') or '9999') for s in cands)
            if not b.get('pair_address'):
                b = dict(b)
                b['pair_address'] = pair_lookup.get(addr)
            out.append((b, pnl, exit_t))
    return out


def resolve_dex(pair):
    try:
        d = requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair}',
                         timeout=10).json()
        pp = d.get('pairs') or ([d.get('pair')] if d.get('pair') else [])
        if pp:
            return SLUG_MAP.get(pp[0].get('dexId', 'pumpswap'), 'pumpfundex')
    except Exception: pass
    return 'pumpfundex'


def fetch_bars(pair, slug):
    url = (f'https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}'
           f'?res=1&cb=600&q={SOL}')
    try:
        r = cf.get(url, impersonate='chrome', timeout=15,
                   headers={'Origin': 'https://dexscreener.com',
                            'Referer': 'https://dexscreener.com/'})
        if r.status_code == 200:
            return parse_chart_bars(r.content)
    except Exception: pass
    return None


def simulate_stale_peak(bars, entry_ts, exit_ts, *,
                        peak_thresh_pct, mins_since_peak_min,
                        pnl_back_to_max_pct):
    """Walk minute-by-minute. Return (fired_at_min, pnl_at_fire) or
    (None, None). Rule fires when:
      - held_peak_pct >= peak_thresh_pct (peak hit at least this %)
      - mins_since_peak >= mins_since_peak_min (peak is stale)
      - current_pnl <= pnl_back_to_max_pct (price retreated)
    """
    pre = [b for b in bars if b['ts_ms'] / 1000 < entry_ts]
    held = [b for b in bars if entry_ts <= b['ts_ms'] / 1000 <= exit_ts]
    if len(held) < 5: return None, None
    series = pre + held
    entry_idx = len(pre)
    entry_close = series[entry_idx]['close']
    if entry_close <= 0: return None, None
    peak_high = entry_close
    peak_idx = entry_idx
    for i in range(entry_idx + 1, len(series)):
        if series[i]['high'] > peak_high:
            peak_high = series[i]['high']
            peak_idx = i
        peak_pct = (peak_high / entry_close - 1) * 100
        if peak_pct < peak_thresh_pct:
            continue
        mins_since_peak = i - peak_idx
        if mins_since_peak < mins_since_peak_min:
            continue
        cur_pnl_pct = (series[i]['close'] / entry_close - 1) * 100
        if cur_pnl_pct <= pnl_back_to_max_pct:
            age_min = i - entry_idx
            return age_min, cur_pnl_pct
    return None, None


def main():
    print('Fetching trades...')
    trades = fetch_trades()
    closed = pair_buys_closed(trades)
    closed.sort(key=lambda x: x[0].get('time') or '', reverse=True)
    closed = closed[:120]

    pair_to_slug = {}
    for b, _, _ in closed:
        pa = b.get('pair_address')
        if pa and pa not in pair_to_slug:
            pair_to_slug[pa] = resolve_dex(pa); time.sleep(0.25)
    pair_to_bars = {}
    for pa, slug in pair_to_slug.items():
        bars = fetch_bars(pa, slug) or []
        if bars:
            pair_to_bars[pa] = bars
        time.sleep(0.25)
    print(f'Bars: {len(pair_to_bars)}/{len(pair_to_slug)}')

    # Test combinations of (peak_thresh, mins_since_peak, pnl_back_to)
    configs = [
        (5.0, 15, 1.0),   # peak >=5, stale 15min, back to <=+1%
        (5.0, 20, 1.0),
        (5.0, 25, 1.0),
        (5.0, 15, 0.0),   # back to BE
        (5.0, 20, 0.0),
        (5.0, 30, 0.0),
        (6.0, 15, 0.0),
        (6.0, 20, 0.0),
        (6.0, 25, 0.0),
        (6.0, 20, -2.0),  # back to <=-2%
        (4.0, 25, 0.0),
        (4.0, 30, -1.0),
    ]
    for pt, msp, br in configs:
        rows = []
        for b, pnl, exit_t in closed:
            pa = b.get('pair_address')
            bars = pair_to_bars.get(pa)
            if not bars: continue
            try:
                ets = datetime.fromisoformat((b.get('time') or '').replace('Z','+00:00')).timestamp()
                xts = datetime.fromisoformat(exit_t.replace('Z','+00:00')).timestamp()
            except Exception: continue
            fire_min, fire_pnl = simulate_stale_peak(
                bars, ets, xts,
                peak_thresh_pct=pt, mins_since_peak_min=msp,
                pnl_back_to_max_pct=br)
            rows.append({'pnl': pnl, 'fire': fire_min, 'fire_pnl': fire_pnl,
                         'addr': (b.get('address') or '')[:10]})
        wins_fired = [r for r in rows if r['fire'] is not None and r['pnl'] > 0.5]
        loss_fired = [r for r in rows if r['fire'] is not None and r['pnl'] < -0.5]
        wins_total = sum(1 for r in rows if r['pnl'] > 0.5)
        loss_total = sum(1 for r in rows if r['pnl'] < -0.5)
        if not wins_fired and not loss_fired:
            continue
        # $20 sizing impact estimate
        win_cost = sum(20 * r['fire_pnl']/100 - r['pnl'] for r in wins_fired)
        loss_save = sum(r['pnl'] - 20 * r['fire_pnl']/100 for r in loss_fired)
        net = loss_save + win_cost  # win_cost is negative when filter cuts profit
        print(f'\nRULE peak>={pt}% AND mins_since_peak>={msp} AND pnl<={br}%')
        print(f'  Cohort: {wins_total}W + {loss_total}L | Fires: {len(wins_fired)}W + {len(loss_fired)}L')
        print(f'  Estimated $ impact (replace actual exit with stale-peak exit):')
        print(f'    Winners: lose ${win_cost:+.2f} of upside  ({len(wins_fired)} cut)')
        print(f'    Losers:  save ${loss_save:+.2f}            ({len(loss_fired)} caught)')
        print(f'    NET:     ${net:+.2f}')
        if 1 <= len(wins_fired) <= 6:
            for r in wins_fired:
                print(f'    [W cut]   {r["addr"]} final=${r["pnl"]:+.2f}  fire@{r["fire"]}min  pnl_at_fire={r["fire_pnl"]:+.1f}%')
        if 1 <= len(loss_fired) <= 6:
            for r in loss_fired:
                print(f'    [L catch] {r["addr"]} final=${r["pnl"]:+.2f}  fire@{r["fire"]}min  pnl_at_fire={r["fire_pnl"]:+.1f}%')


if __name__ == '__main__':
    main()
