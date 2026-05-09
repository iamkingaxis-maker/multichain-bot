"""Compute the new chart_shape_features on the May 8-9 session entries
to verify the feature outputs reasonable values and to see the W/L
separation for the new metrics."""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from curl_cffi import requests as cf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from feeds.dexscreener_chart_format import parse_chart_bars
from feeds.candle_utils import Candle
from feeds.chart_shape_features import compute_chart_shape

SOL = 'So11111111111111111111111111111111111111112'
SLUG_MAP = {'pumpswap': 'pumpfundex', 'pumpfun': 'pumpfundex',
            'raydium': 'solamm', 'meteora': 'meteora'}

sess = json.loads((ROOT / '.session_trades.json').read_text())


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


def to_candle(b):
    return Candle(open_time=int(b['ts_ms']), open=b['open'], high=b['high'],
                  low=b['low'], close=b['close'], volume=b['volume_usd'],
                  close_time=int(b['ts_ms']) + 60_000)


def main():
    cache = {}
    rows = []
    for t in sess:
        pa = t['pair']
        if pa not in cache:
            slug = resolve_dex(pa)
            time.sleep(0.3)
            bars = fetch_bars(pa, slug) or []
            cache[pa] = bars
            time.sleep(0.3)
        bars = cache[pa]
        if not bars:
            rows.append({'tok': t['tok'], 'pnl': t['pnl'], 'shape': None})
            continue
        ets = datetime.fromisoformat(t['time']).timestamp()
        # Slice to bars STRICTLY before/at entry ts
        before = [b for b in bars if b['ts_ms'] / 1000 <= ets]
        candles = [to_candle(b) for b in before[-120:]]
        shape = compute_chart_shape(candles)
        rows.append({'tok': t['tok'], 'pnl': t['pnl'],
                     'peak': t['peak_pnl_pct'], 'shape': shape})

    print(f"\n{'sym':<10}{'pnl':<7}{'30m_max+':<10}{'30m_chg':<9}{'30m_lh':<8}"
          f"{'90m_max+':<10}{'90m_chg':<9}{'90m_pb':<8}{'90m_lh':<8}{'90m_dd':<8}")
    print('-' * 95)
    for r in rows:
        if not r['shape']:
            print(f"{r['tok']:<10}{r['pnl']:+.2f}{'':<2}NO_BARS")
            continue
        s = r['shape']
        def f(k, fmt='.1f'):
            v = s.get(k)
            return format(v, fmt) if v is not None else '-'
        print(f"{r['tok']:<10}{r['pnl']:+.2f}{'':<2}"
              f"{f('shape_30m_max_over_entry_pct'):<10}"
              f"{f('shape_30m_chg_pct'):<9}"
              f"{str(s.get('shape_30m_lh_count','-')):<8}"
              f"{f('shape_90m_max_over_entry_pct'):<10}"
              f"{f('shape_90m_chg_pct'):<9}"
              f"{f('shape_90m_pump_bleed_score'):<8}"
              f"{str(s.get('shape_90m_lh_count','-')):<8}"
              f"{f('shape_90m_drawdown_from_max_pct'):<8}")

    # Compute summary distributions
    win_rows = [r for r in rows if r['shape'] and r['pnl'] > 0]
    los_rows = [r for r in rows if r['shape'] and r['pnl'] < 0]
    print(f"\nWINNERS n={len(win_rows)}, LOSERS n={len(los_rows)}")
    for k in ['shape_30m_lh_count', 'shape_60m_lh_count', 'shape_90m_lh_count',
             'shape_30m_pump_bleed_score', 'shape_90m_pump_bleed_score',
             'shape_90m_max_over_entry_pct', 'shape_90m_chg_pct',
             'shape_90m_mins_since_max', 'shape_90m_drawdown_from_max_pct']:
        wv = sorted(r['shape'].get(k) for r in win_rows if r['shape'].get(k) is not None)
        lv = sorted(r['shape'].get(k) for r in los_rows if r['shape'].get(k) is not None)
        if wv and lv:
            wm = sum(wv) / len(wv)
            lm = sum(lv) / len(lv)
            print(f'  {k:<35}  W mean={wm:+.2f}  L mean={lm:+.2f}  diff={wm-lm:+.2f}')


if __name__ == '__main__':
    main()
