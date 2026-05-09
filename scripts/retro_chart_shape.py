"""Retro-validate chart_shape_features on recent bot trades.

Approach: pull recent trades from /api/trades, fetch 1m bars via
DexScreener internal API for each pair, slice to entry timestamp,
compute the shape features, then scan candidate thresholds.

Constraint: DS retention is ~24h on 1m bars. Trades older than that
will return NO_BARS and are excluded from the cohort.

Usage:
  python scripts/retro_chart_shape.py
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
from feeds.candle_utils import Candle
from feeds.chart_shape_features import compute_chart_shape

SOL = 'So11111111111111111111111111111111111111112'
SLUG_MAP = {'pumpswap': 'pumpfundex', 'pumpfun': 'pumpfundex',
            'raydium': 'solamm', 'meteora': 'meteora'}


def fetch_trades():
    r = requests.get(
        'https://gracious-inspiration-production.up.railway.app/api/trades?all=1',
        timeout=60)
    return r.json()


def _is_dip_sell(t):
    """A real dip_buy exit. The tracker mis-tags sell strategy as
    'scanner' because reasons don't contain SCALP/COPY/PUMP — so we
    match by reason pattern instead."""
    if t.get('type') != 'sell' or t.get('pnl') is None:
        return False
    reason = (t.get('reason') or '').strip()
    if not reason or 'cancelled' in reason.lower():
        return False
    return reason.startswith('Dip ') or 'Manual sell' in reason


def pair_buys_closed(trades):
    """Pair each dip_buy 'buy' with the dip-style sells that close it.
    Returns list of (buy, total_pnl). Pair address is looked up from
    any non-null pair_address record for the same address."""
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
        elif _is_dip_sell(t):
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
            # Inject pair_address from lookup if missing on this record
            if not b.get('pair_address'):
                b = dict(b)
                b['pair_address'] = pair_lookup.get(addr)
            out.append((b, pnl))
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


def to_candle(b):
    return Candle(open_time=int(b['ts_ms']), open=b['open'], high=b['high'],
                  low=b['low'], close=b['close'], volume=b['volume_usd'],
                  close_time=int(b['ts_ms']) + 60_000)


def main():
    print('Fetching trades...')
    trades = fetch_trades()
    closed = pair_buys_closed(trades)
    closed.sort(key=lambda x: x[0].get('time') or '', reverse=True)
    closed = closed[:100]  # last 100 closed trades — DS bars only retain ~24h
    print(f'Got {len(closed)} closed trades (most recent 100).')

    # Resolve and fetch bars
    pair_to_slug = {}
    for b, _ in closed:
        pa = b.get('pair_address')
        if pa and pa not in pair_to_slug:
            pair_to_slug[pa] = resolve_dex(pa)
            time.sleep(0.3)
    print(f'Resolved {len(pair_to_slug)} unique pairs.')

    pair_to_bars = {}
    for pa, slug in pair_to_slug.items():
        bars = fetch_bars(pa, slug) or []
        if bars:
            pair_to_bars[pa] = bars
        time.sleep(0.3)
    print(f'Got bars for {len(pair_to_bars)}/{len(pair_to_slug)} pairs.')

    # Compute shape features per trade
    rows = []
    for b, pnl in closed:
        pa = b.get('pair_address')
        bars = pair_to_bars.get(pa)
        if not bars:
            rows.append({'pnl': pnl, 'shape': None, 'reason': 'no_bars',
                         'time': b.get('time')})
            continue
        try:
            ets = datetime.fromisoformat(
                (b.get('time') or '').replace('Z', '+00:00')).timestamp()
        except Exception:
            rows.append({'pnl': pnl, 'shape': None, 'reason': 'bad_ts'})
            continue
        before = [x for x in bars if x['ts_ms'] / 1000 <= ets]
        if len(before) < 30:
            rows.append({'pnl': pnl, 'shape': None,
                         'reason': f'only_{len(before)}_bars_pre_entry'})
            continue
        candles = [to_candle(x) for x in before[-120:]]
        shape = compute_chart_shape(candles)
        if not shape:
            rows.append({'pnl': pnl, 'shape': None, 'reason': 'shape_empty'})
            continue
        rows.append({'pnl': pnl, 'shape': shape,
                     'time': b.get('time'),
                     'addr': (b.get('address') or '')[:10]})

    usable = [r for r in rows if r['shape']]
    print(f'\nUsable: {len(usable)} of {len(rows)}')
    skips = defaultdict(int)
    for r in rows:
        if not r['shape']:
            skips[r.get('reason', '?')] += 1
    print(f'Skip reasons: {dict(skips)}')

    # Distribution overview
    wins = [r for r in usable if r['pnl'] > 0.5]
    losses = [r for r in usable if r['pnl'] < -0.5]
    print(f'\nWINNERS n={len(wins)} sum=${sum(r["pnl"] for r in wins):+.2f}')
    print(f'LOSSES  n={len(losses)} sum=${sum(r["pnl"] for r in losses):+.2f}')
    print(f'Real total: ${sum(r["pnl"] for r in usable):+.2f}')

    print('\n=== Mean comparison ===')
    keys = ['shape_30m_lh_count', 'shape_60m_lh_count', 'shape_90m_lh_count',
            'shape_30m_chg_pct', 'shape_60m_chg_pct', 'shape_90m_chg_pct',
            'shape_30m_max_over_entry_pct', 'shape_60m_max_over_entry_pct',
            'shape_90m_max_over_entry_pct',
            'shape_30m_pump_bleed_score', 'shape_60m_pump_bleed_score',
            'shape_90m_pump_bleed_score',
            'shape_30m_drawdown_from_max_pct', 'shape_60m_drawdown_from_max_pct',
            'shape_90m_drawdown_from_max_pct',
            'shape_30m_mins_since_max', 'shape_60m_mins_since_max',
            'shape_90m_mins_since_max']
    for k in keys:
        wv = [r['shape'].get(k) for r in wins if r['shape'].get(k) is not None]
        lv = [r['shape'].get(k) for r in losses if r['shape'].get(k) is not None]
        if not wv or not lv:
            continue
        wm = sum(wv) / len(wv)
        lm = sum(lv) / len(lv)
        print(f'  {k:<40}  W={wm:+8.2f}  L={lm:+8.2f}  diff={wm-lm:+8.2f}')

    # Threshold scan
    print('\n=== Threshold scan ===')
    candidates = [
        # Original wide rules — cut too many winners
        ('chg_90>=0 AND dd_90<-15',
         lambda s: (s.get('shape_90m_chg_pct') or -999) >= 0 and
                   (s.get('shape_90m_drawdown_from_max_pct') or 0) < -15),
        ('chg_90>=5 AND max_over_90>=20',
         lambda s: (s.get('shape_90m_chg_pct') or -999) >= 5 and
                   (s.get('shape_90m_max_over_entry_pct') or 0) >= 20),
        # Narrow rules — add mins_since_max + dd to exclude active runners
        ('NARROW: chg_90>=4 AND max_over_90>=25 AND mins_since_max>=25',
         lambda s: (s.get('shape_90m_chg_pct') or -999) >= 4 and
                   (s.get('shape_90m_max_over_entry_pct') or 0) >= 25 and
                   (s.get('shape_90m_mins_since_max') or 0) >= 25),
        ('NARROW2: + dd_90<=-22',
         lambda s: (s.get('shape_90m_chg_pct') or -999) >= 4 and
                   (s.get('shape_90m_max_over_entry_pct') or 0) >= 25 and
                   (s.get('shape_90m_mins_since_max') or 0) >= 25 and
                   (s.get('shape_90m_drawdown_from_max_pct') or 0) <= -22),
        ('NARROW3: + chg_30<=-3',
         lambda s: (s.get('shape_90m_chg_pct') or -999) >= 4 and
                   (s.get('shape_90m_max_over_entry_pct') or 0) >= 25 and
                   (s.get('shape_90m_mins_since_max') or 0) >= 25 and
                   (s.get('shape_90m_drawdown_from_max_pct') or 0) <= -22 and
                   (s.get('shape_30m_chg_pct') or 999) <= -3),
        ('NARROW4: chg_90>=4 AND mins_since_max>=30 AND dd_90<=-22',
         lambda s: (s.get('shape_90m_chg_pct') or -999) >= 4 and
                   (s.get('shape_90m_mins_since_max') or 0) >= 30 and
                   (s.get('shape_90m_drawdown_from_max_pct') or 0) <= -22),
    ]
    for name, fn in candidates:
        block = [r for r in usable if fn(r['shape'])]
        bw = sum(1 for r in block if r['pnl'] > 0.5)
        bl = sum(1 for r in block if r['pnl'] < -0.5)
        bs = sum(r['pnl'] for r in block)
        passed = [r for r in usable if not fn(r['shape'])]
        ps = sum(r['pnl'] for r in passed)
        delta = ps - sum(r['pnl'] for r in usable)
        print(f'\n{name}')
        print(f'  blocks {len(block)} ({bw}W / {bl}L) sum=${bs:+.2f}')
        print(f'  delta vs no-filter: ${delta:+.2f}')
        if 1 <= len(block) <= 12:
            for r in block:
                s = r['shape']
                addr = r.get('addr', '?')
                tag = 'WIN ' if r['pnl'] > 0.5 else 'LOSS' if r['pnl'] < -0.5 else 'flat'
                print(f"    [{tag}] addr={addr} pnl=${r['pnl']:+.2f}  "
                      f"chg30={s.get('shape_30m_chg_pct',0):+.1f} "
                      f"chg60={s.get('shape_60m_chg_pct',0):+.1f} "
                      f"chg90={s.get('shape_90m_chg_pct',0):+.1f} | "
                      f"max+30={s.get('shape_30m_max_over_entry_pct',0):.1f} "
                      f"max+90={s.get('shape_90m_max_over_entry_pct',0):.1f} | "
                      f"dd90={s.get('shape_90m_drawdown_from_max_pct',0):.1f} | "
                      f"lh90={s.get('shape_90m_lh_count',0)} "
                      f"pivots90={s.get('shape_90m_distinct_pivots',0)} | "
                      f"pb90={s.get('shape_90m_pump_bleed_score',0):.1f} | "
                      f"max@{s.get('shape_90m_mins_since_max',0)}m")


if __name__ == '__main__':
    main()
