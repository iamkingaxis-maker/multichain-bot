"""Test buy/sell imbalance signals at the slow-bleed pre-condition tick.

For each closed trade:
- Walk minute-by-minute through the held window (capped at actual exit).
- Find the FIRST tick where age >= 30min AND pnl <= -3% (the slow-bleed
  pre-condition). Skip trades that never hit it.
- At that tick, compute several bar-derived imbalance metrics looking
  back 5/15/30/60 min.
- Compare distributions for winners vs losers.

Imbalance metrics (1m bars):
  red_pct_5m         red-bar ratio in last 5 bars
  red_pct_30m        red-bar ratio in last 30 bars
  weighted_dir_30m   sum(volume * sign(c-o)) / sum(volume) over last 30
  cum_30m_pct        net price change over last 30 bars
  cum_5m_pct         net price change over last 5 bars
  vol_red_ratio_30m  red-volume / total-volume over last 30 bars
  consec_red_now     consecutive red bars ending at this tick
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
        if not addr:
            continue
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
            if not cands:
                continue
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
    except Exception:
        pass
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
    except Exception:
        pass
    return None


def compute_imbalance(series, idx, entry_idx):
    """Compute many candidate signals at bar index `idx`. Includes
    bleed-tick-local metrics AND trajectory-since-entry metrics."""
    out = {}
    def slice_back(n):
        return series[max(0, idx - n + 1):idx + 1]

    s5 = slice_back(5)
    s15 = slice_back(15)
    s30 = slice_back(30)
    s60 = slice_back(60)
    s90 = slice_back(90)
    held_so_far = series[entry_idx:idx + 1]
    pre_entry_30 = series[max(0, entry_idx - 30):entry_idx]

    if len(s30) < 30 or len(held_so_far) < 5:
        return {}

    def red_ratio(s):
        return sum(1 for b in s if b['close'] < b['open']) / max(len(s), 1)

    def cum_pct(s):
        if not s or s[0]['open'] <= 0: return None
        return (s[-1]['close'] / s[0]['open'] - 1) * 100

    def linear_slope(s):
        """Slope of close prices in % per bar (normalized)."""
        if len(s) < 5 or s[0]['close'] <= 0: return None
        n = len(s)
        xs = list(range(n))
        ys = [b['close'] / s[0]['close'] - 1 for b in s]  # % from start
        x_mean = sum(xs)/n; y_mean = sum(ys)/n
        num = sum((xs[i]-x_mean)*(ys[i]-y_mean) for i in range(n))
        den = sum((xs[i]-x_mean)**2 for i in range(n))
        return (num/den)*100 if den > 0 else None  # % per bar

    consec_red = 0
    for b in reversed(s30):
        if b['close'] < b['open']: consec_red += 1
        else: break

    # 60-min range position
    h60 = max(b['high'] for b in s60)
    l60 = min(b['low'] for b in s60)
    cur = series[idx]['close']
    pct_in_60m_range = (cur - l60) / (h60 - l60) if h60 > l60 else 0.5

    # Held-window peak/floor (since entry)
    held_max = max(b['high'] for b in held_so_far)
    held_min = min(b['low'] for b in held_so_far)
    held_peak_pct = (held_max / series[entry_idx]['close'] - 1) * 100
    held_dd_pct = (held_min / series[entry_idx]['close'] - 1) * 100
    # Time since the held-window peak
    max_idx_local = max(range(len(held_so_far)), key=lambda i: held_so_far[i]['high'])
    mins_since_held_max = (len(held_so_far) - 1 - max_idx_local)  # 1m bars

    # Time-below-water: how many minutes since entry has price been below entry close?
    entry_close = series[entry_idx]['close']
    below_water = sum(1 for b in held_so_far if b['close'] < entry_close)
    below_ratio = below_water / len(held_so_far)

    # Pre-entry 30m trajectory (was the entry into a downtrend?)
    pre_entry_chg_pct = None
    if len(pre_entry_30) >= 10 and pre_entry_30[0]['open'] > 0:
        pre_entry_chg_pct = (pre_entry_30[-1]['close'] / pre_entry_30[0]['open'] - 1) * 100

    # Recent low touch — distance from current price to the last 30m low
    low_30 = min(b['low'] for b in s30)
    dist_from_low_30_pct = (cur / low_30 - 1) * 100 if low_30 > 0 else None

    # Slope of last 30 bars — % per bar (very negative = active free-fall)
    slope_30 = linear_slope(s30)
    slope_60 = linear_slope(s60)
    slope_held = linear_slope(held_so_far) if len(held_so_far) >= 5 else None

    out['cum_5m_pct'] = round(cum_pct(s5), 2) if cum_pct(s5) is not None else None
    out['cum_30m_pct'] = round(cum_pct(s30), 2) if cum_pct(s30) is not None else None
    out['cum_60m_pct'] = round(cum_pct(s60), 2) if cum_pct(s60) is not None else None
    out['red_pct_30m'] = round(red_ratio(s30), 2)
    out['consec_red'] = consec_red
    out['slope_30_pct_per_bar'] = round(slope_30, 3) if slope_30 is not None else None
    out['slope_60_pct_per_bar'] = round(slope_60, 3) if slope_60 is not None else None
    out['slope_held_pct_per_bar'] = round(slope_held, 3) if slope_held is not None else None
    out['pct_in_60m_range'] = round(pct_in_60m_range, 2)
    out['dist_from_low_30_pct'] = round(dist_from_low_30_pct, 2) if dist_from_low_30_pct is not None else None
    out['held_peak_pct'] = round(held_peak_pct, 2)
    out['held_dd_pct'] = round(held_dd_pct, 2)
    out['mins_since_held_max'] = mins_since_held_max
    out['below_water_ratio'] = round(below_ratio, 2)
    out['pre_entry_30m_chg_pct'] = round(pre_entry_chg_pct, 2) if pre_entry_chg_pct is not None else None
    return out


def find_bleed_tick(bars, entry_ts, exit_ts):
    """First minute in the held window where age>=30 AND pnl<=-3%.
    pnl computed against the entry-time bar's close (so units cancel —
    bar prices may be in SOL or USD; we just want the % change)."""
    pre = [b for b in bars if b['ts_ms'] / 1000 < entry_ts]
    held = [b for b in bars if entry_ts <= b['ts_ms'] / 1000 <= exit_ts]
    series = pre + held
    entry_idx = len(pre)
    if len(held) < 30:
        return None, None, None
    entry_close = series[entry_idx]['close']
    if entry_close <= 0:
        return None, None, None
    for i in range(entry_idx + 30, len(series)):
        age = (series[i]['ts_ms'] - series[entry_idx]['ts_ms']) / 60_000
        if age < 30:
            continue
        pnl = (series[i]['close'] / entry_close - 1) * 100
        if pnl <= -3.0:
            return series, i, entry_idx
    return series, None, entry_idx


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
            pair_to_slug[pa] = resolve_dex(pa)
            time.sleep(0.25)
    pair_to_bars = {}
    for pa, slug in pair_to_slug.items():
        bars = fetch_bars(pa, slug) or []
        if bars:
            pair_to_bars[pa] = bars
        time.sleep(0.25)
    print(f'Bars: {len(pair_to_bars)}/{len(pair_to_slug)} pairs\n')

    rows = []
    for b, pnl, exit_t in closed:
        pa = b.get('pair_address')
        bars = pair_to_bars.get(pa)
        if not bars:
            continue
        try:
            ets = datetime.fromisoformat((b.get('time') or '').replace('Z', '+00:00')).timestamp()
            xts = datetime.fromisoformat(exit_t.replace('Z', '+00:00')).timestamp()
        except Exception:
            continue
        series, tick, entry_idx = find_bleed_tick(bars, ets, xts)
        if tick is None:
            continue
        ib = compute_imbalance(series, tick, entry_idx)
        if not ib:
            continue
        entry_close = series[entry_idx]['close']
        rows.append({
            'addr': (b.get('address') or '')[:10],
            'pair': pa,
            'pnl': pnl,
            'tick_pnl_pct': (series[tick]['close'] / entry_close - 1) * 100,
            'tick_age_min': (series[tick]['ts_ms'] - series[entry_idx]['ts_ms']) / 60000,
            **ib,
        })

    wins = [r for r in rows if r['pnl'] > 0.5]
    losses = [r for r in rows if r['pnl'] < -0.5]
    print(f'At slow-bleed pre-condition tick: {len(wins)}W + {len(losses)}L')
    print(f'(Trades that ever hit age>=30 AND pnl<=-3 within their hold window.)\n')
    print(f'{"sym":<12}{"pair_pre":<11}{"final":<7}{"tick%":<7}{"age":<5}'
          f'{"cum30":<8}{"slope30":<9}{"slopeH":<9}{"hldPk":<7}{"hldDD":<7}'
          f'{"sinceMx":<8}{"belowR":<7}{"preEnt":<8}{"pct60r":<7}')
    for r in sorted(rows, key=lambda r: r['pnl']):
        s = lambda v: '?' if v is None else (f'{v:.1f}' if isinstance(v,float) else str(v))
        print(f"{r['addr']:<12}{r['pair'][:10]:<11}{r['pnl']:+.2f}{'':<1}"
              f"{r['tick_pnl_pct']:+.1f}{'':<1}{r['tick_age_min']:.0f}m{'':<2}"
              f"{s(r['cum_30m_pct']):<8}{s(r['slope_30_pct_per_bar']):<9}"
              f"{s(r['slope_held_pct_per_bar']):<9}{s(r['held_peak_pct']):<7}"
              f"{s(r['held_dd_pct']):<7}{r['mins_since_held_max']!s:<8}"
              f"{s(r['below_water_ratio']):<7}{s(r['pre_entry_30m_chg_pct']):<8}"
              f"{s(r['pct_in_60m_range']):<7}")

    print('\n=== Mean comparison (W vs L) ===')
    keys = ['cum_5m_pct','cum_30m_pct','cum_60m_pct',
            'slope_30_pct_per_bar','slope_60_pct_per_bar','slope_held_pct_per_bar',
            'pct_in_60m_range','dist_from_low_30_pct',
            'held_peak_pct','held_dd_pct','mins_since_held_max','below_water_ratio',
            'pre_entry_30m_chg_pct','red_pct_30m','consec_red']
    for k in keys:
        wv = [r[k] for r in wins if r.get(k) is not None]
        lv = [r[k] for r in losses if r.get(k) is not None]
        if wv and lv:
            wm = sum(wv) / len(wv)
            lm = sum(lv) / len(lv)
            print(f'  {k:<22}  W={wm:+.3f}  L={lm:+.3f}  diff={wm-lm:+.3f}')


if __name__ == '__main__':
    main()
