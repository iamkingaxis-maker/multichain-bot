"""Pull 1m bars for May 8-9 session entries and count lower highs in
the 30-45 min before each entry. Tests whether "n_lower_highs_pre_entry"
discriminates Pattern A losers (DATA/UFO/mama) from winners with
similar entry_meta (SETI/GAYTES-2/HANTA).
"""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from curl_cffi import requests as cf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from feeds.dexscreener_chart_format import parse_chart_bars

SOL = 'So11111111111111111111111111111111111111112'
SLUG_MAP = {'pumpswap': 'pumpfundex', 'pumpfun': 'pumpfundex',
            'raydium': 'solamm', 'meteora': 'meteora'}

sess = json.loads((ROOT / '.session_trades.json').read_text())


def resolve_dex(pair_addr: str) -> str:
    try:
        r = requests.get(
            f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair_addr}',
            timeout=10,
        )
        d = r.json()
        pairs = d.get('pairs') or [d.get('pair')] if d.get('pair') else []
        if pairs:
            return SLUG_MAP.get(pairs[0].get('dexId', 'pumpswap'), 'pumpfundex')
    except Exception:
        pass
    return 'pumpfundex'


def fetch_1m_bars(pair_addr: str, slug: str):
    url = (f'https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair_addr}'
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


def find_pivot_highs(bars, n=2):
    """Local maxima with n bars on each side strictly lower."""
    pivots = []
    for i in range(n, len(bars) - n):
        h = bars[i]['high']
        if all(bars[j]['high'] < h for j in range(i - n, i)) and \
           all(bars[j]['high'] < h for j in range(i + 1, i + n + 1)):
            pivots.append((i, h, bars[i]['ts_ms']))
    return pivots


def count_lower_highs_window(bars, entry_ts_sec, window_min=30, pivot_n=2):
    """Count consecutive lower-highs in the last `window_min` minutes
    before entry_ts_sec."""
    window_start_ms = (entry_ts_sec - window_min * 60) * 1000
    entry_ms = entry_ts_sec * 1000
    in_window = [b for b in bars if window_start_ms <= b['ts_ms'] <= entry_ms]
    if len(in_window) < pivot_n * 2 + 2:
        return None, len(in_window), 0
    pivots = find_pivot_highs(in_window, n=pivot_n)
    if not pivots:
        return 0, len(in_window), 0
    lh_count = 0
    for i in range(1, len(pivots)):
        if pivots[i][1] < pivots[i - 1][1]:
            lh_count += 1
        else:
            lh_count = 0  # broken sequence
    return lh_count, len(in_window), len(pivots)


def main():
    pair_to_dex = {}
    print(f'Resolving DEX slugs for {len(sess)} pairs...')
    for t in sess:
        pa = t['pair']
        if pa not in pair_to_dex:
            pair_to_dex[pa] = resolve_dex(pa)
            time.sleep(0.3)

    pair_to_bars = {}
    print(f'Fetching bars for {len(pair_to_dex)} pairs...')
    for pa, slug in pair_to_dex.items():
        bars = fetch_1m_bars(pa, slug)
        pair_to_bars[pa] = bars or []
        time.sleep(0.3)

    # For each session trade, compute lh_count for 30 min and 45 min windows
    print(f'\n{"sym":<10}{"pnl":<7}{"peak%":<8}{"max_over_entry":<15}{"chg_90m":<11}'
          f'{"rt_score":<11}{"max_age":<10}{"trigger":<28}')
    print('-' * 90)
    rows = []
    for t in sess:
        bars = pair_to_bars.get(t['pair']) or []
        if not bars:
            print(f"{t['tok']:<10}{t['pnl']:+.2f}{'':<2}{t['peak_pnl_pct']:+.1f}%{'':<2}"
                  f"{'NO_BARS':<8}{'':<24}")
            continue
        try:
            ets = datetime.fromisoformat(t['time']).timestamp()
        except Exception:
            continue
        cutoff_90 = (ets - 90 * 60) * 1000
        cutoff_60 = (ets - 60 * 60) * 1000
        in_w90 = [b for b in bars if cutoff_90 <= b['ts_ms'] <= ets * 1000]
        in_w60 = [b for b in bars if cutoff_60 <= b['ts_ms'] <= ets * 1000]
        if len(in_w90) < 10:
            rows.append({'tok': t['tok'], 'pnl': t['pnl'], 'rt_score': None})
            continue
        entry_p = in_w90[-1]['close']
        max_90 = max(b['high'] for b in in_w90)
        min_90 = min(b['low'] for b in in_w90)
        max_over_entry_pct = (max_90 / entry_p - 1) * 100  # how much higher 90m max was vs entry
        chg_90m = (entry_p / in_w90[0]['close'] - 1) * 100
        # Round-trip score: high pump (max_over_entry > 20%) AND small net move (-10%<chg<+10%)
        # Real pullback shape: high pump_over_entry but big net negative move
        rt_score = max_over_entry_pct - abs(chg_90m)  # large = round trip; small = real pullback
        # Time of max within window (minutes ago from entry)
        max_idx = max(range(len(in_w90)), key=lambda i: in_w90[i]['high'])
        mins_since_max = (ets * 1000 - in_w90[max_idx]['ts_ms']) / 60_000

        rows.append({'tok': t['tok'], 'pnl': t['pnl'], 'peak': t['peak_pnl_pct'],
                     'max_over': max_over_entry_pct, 'chg_90m': chg_90m,
                     'rt_score': rt_score, 'mins_since_max': mins_since_max,
                     'trig': t['trigger']})
        print(f"{t['tok']:<10}{t['pnl']:+.2f}{'':<2}{t['peak_pnl_pct']:+.1f}%{'':<2}"
              f"max+{max_over_entry_pct:>6.1f}%  chg{chg_90m:>+6.1f}%  rt={rt_score:>5.1f}  "
              f"max@{mins_since_max:>4.0f}m  {t['trigger']:<28}")

    # Summary
    wins = [r for r in rows if r['pnl'] > 0 and r.get('rt_score') is not None]
    losses = [r for r in rows if r['pnl'] < 0 and r.get('rt_score') is not None]
    print(f'\nWINNERS n={len(wins)}, LOSERS n={len(losses)}')
    print(f'  max_over    W: {sorted(round(r["max_over"],1) for r in wins)}')
    print(f'  max_over    L: {sorted(round(r["max_over"],1) for r in losses)}')
    print(f'  chg_90m     W: {sorted(round(r["chg_90m"],1) for r in wins)}')
    print(f'  chg_90m     L: {sorted(round(r["chg_90m"],1) for r in losses)}')
    print(f'  rt_score    W: {sorted(round(r["rt_score"],1) for r in wins)}')
    print(f'  rt_score    L: {sorted(round(r["rt_score"],1) for r in losses)}')

    # Round-trip detection: pump high in window AND back near entry
    print('\n=== ROUND-TRIP DETECTION ===')
    for label, cond in [
        ('rt_score>=20 (any pump_over_entry-net_chg >= 20)',
         lambda r: r['rt_score'] is not None and r['rt_score'] >= 20),
        ('max_over>=20% AND chg_90m>=-10% (pumped+round-tripped)',
         lambda r: r.get('max_over') is not None and r['max_over'] >= 20 and r['chg_90m'] >= -10),
        ('max_over>=15% AND -5%<=chg_90m<=10% (tight round-trip)',
         lambda r: r.get('max_over') is not None and r['max_over'] >= 15 and -5 <= r['chg_90m'] <= 10),
        ('max_over>=10% AND chg_90m>=-5% (mild pump roundtrip)',
         lambda r: r.get('max_over') is not None and r['max_over'] >= 10 and r['chg_90m'] >= -5),
    ]:
        block = [r for r in rows if cond(r)]
        bw = sum(1 for r in block if r['pnl'] > 0)
        bl = sum(1 for r in block if r['pnl'] < 0)
        bs = sum(r['pnl'] for r in block)
        print(f'\n{label}')
        print(f'  blocks {len(block)} ({bw}W/{bl}L) sum=${bs:+.2f}')
        for r in block:
            print(f'  {r["tok"]:<10} pnl={r["pnl"]:+.2f} max+{r["max_over"]:.1f}% chg{r["chg_90m"]:+.1f}% rt={r["rt_score"]:.1f}')


if __name__ == '__main__':
    main()
