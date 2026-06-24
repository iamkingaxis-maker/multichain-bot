"""Regime-flip entry-feature miner: PRE-PUMP vs PUMP (2026-06-14).

Derives entry GEOMETRY from 1m DexScreener bars (regime-neutral, not bot labels):
  dip depth off 90m/30m high, momentum (5/15/30/60m returns), position-in-range,
  realized vol, green-run, volume burst. Splits winners/losers per window and
  reports which features FLIP sign between pre-pump and pump.
"""
from __future__ import annotations
import json, pickle, sys, time, statistics as st
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import requests
from curl_cffi import requests as cf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from feeds.dexscreener_chart_format import parse_chart_bars

SOL = 'So11111111111111111111111111111111111111112'
SLUG_MAP = {'pumpswap': 'pumpfundex', 'pumpfun': 'pumpfundex',
            'raydium': 'solamm', 'meteora': 'meteora'}
SLUG_TRY = ['pumpfundex', 'solamm', 'meteora']


def resolve_dex(pair):
    try:
        d = requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair}',
                         timeout=10, headers={'User-Agent': 'Mozilla/5.0'}).json()
        pp = d.get('pairs') or ([d.get('pair')] if d.get('pair') else [])
        if pp:
            return SLUG_MAP.get(pp[0].get('dexId', 'pumpswap'), 'pumpfundex')
    except Exception:
        pass
    return None


def fetch_bars(pair, slug):
    url = (f'https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}'
           f'?res=1&cb=900&q={SOL}')
    try:
        r = cf.get(url, impersonate='chrome', timeout=20,
                   headers={'Origin': 'https://dexscreener.com',
                            'Referer': 'https://dexscreener.com/'})
        if r.status_code == 200:
            b = parse_chart_bars(r.content)
            if b:
                return b
    except Exception:
        pass
    return None


def fetch_bars_any(pair, slug_hint):
    order = ([slug_hint] if slug_hint else []) + [s for s in SLUG_TRY if s != slug_hint]
    for s in order:
        b = fetch_bars(pair, s)
        if b and len(b) >= 10:
            return b
    return None


def feats(bars, ets):
    """Compute entry geometry from bars up to entry ts (seconds)."""
    pre = [x for x in bars if x['ts_ms'] / 1000.0 <= ets]
    if len(pre) < 15:
        return None
    closes = [x['close'] for x in pre]
    highs = [x['high'] for x in pre]
    lows = [x['low'] for x in pre]
    vols = [x.get('volume_usd', 0) or 0 for x in pre]
    p = closes[-1]
    if p <= 0:
        return None

    def ret_n(n):
        if len(closes) <= n:
            return None
        base = closes[-1 - n]
        return 100.0 * (p / base - 1.0) if base > 0 else None

    def hi_n(n):
        w = highs[-n:] if len(highs) >= n else highs
        return max(w) if w else p

    def lo_n(n):
        w = lows[-n:] if len(lows) >= n else lows
        return min(w) if w else p

    h90 = hi_n(90); h30 = hi_n(30); h60 = hi_n(60)
    l90 = lo_n(90); l30 = lo_n(30)
    dip90 = 100.0 * (p / h90 - 1.0) if h90 > 0 else None       # <=0; deeper = more negative
    dip30 = 100.0 * (p / h30 - 1.0) if h30 > 0 else None
    rng90 = (h90 - l90)
    pos90 = (p - l90) / rng90 if rng90 > 0 else 0.5            # 0=at low,1=at high
    rng30 = (h30 - l30)
    pos30 = (p - l30) / rng30 if rng30 > 0 else 0.5

    # realized vol: stdev of last-15 1m log-ish returns (pct)
    rr = []
    for i in range(max(1, len(closes) - 15), len(closes)):
        a, b = closes[i - 1], closes[i]
        if a > 0:
            rr.append(100.0 * (b / a - 1.0))
    vol15 = st.pstdev(rr) if len(rr) >= 2 else None

    # green run: consecutive trailing bars with close>open
    gr = 0
    for x in reversed(pre):
        if x['close'] > x['open']:
            gr += 1
        else:
            break
    # volume burst: last-5 mean vol / prior-30 mean vol
    last5 = vols[-5:]
    prior = vols[-35:-5] if len(vols) >= 10 else vols[:-5]
    vb = (st.mean(last5) / st.mean(prior)) if prior and st.mean(prior) > 0 and last5 else None

    # uptrend slope: ret over last 30 normalized
    r5, r15, r30, r60 = ret_n(5), ret_n(15), ret_n(30), ret_n(60)
    return {
        'dip90': dip90, 'dip30': dip30,
        'mom5': r5, 'mom15': r15, 'mom30': r30, 'mom60': r60,
        'pos90': pos90, 'pos30': pos30,
        'vol15': vol15, 'green_run': float(gr),
        'vol_burst': vb,
        'n_bars_pre': float(len(pre)),
    }


def main():
    with open(ROOT / '_regime_pairs.pkl', 'rb') as f:
        D = pickle.load(f)
    pre, pump = D['pre'], D['pump']

    # unique pairs across both windows
    pairmap = {}
    for p in pre + pump:
        if p['pair']:
            pairmap.setdefault(p['pair'], None)
    print(f'resolving + fetching {len(pairmap)} unique pairs ...', flush=True)

    bars_cache = {}
    for i, pa in enumerate(pairmap):
        slug = resolve_dex(pa)
        time.sleep(0.25)
        b = fetch_bars_any(pa, slug)
        if b:
            bars_cache[pa] = b
        if (i + 1) % 10 == 0:
            print(f'  {i+1}/{len(pairmap)} fetched, {len(bars_cache)} with bars', flush=True)
        time.sleep(0.35)
    print(f'got bars for {len(bars_cache)}/{len(pairmap)} pairs', flush=True)

    def enrich(ps):
        out = []
        for p in ps:
            bars = bars_cache.get(p['pair'])
            if not bars:
                continue
            try:
                ets = datetime.fromisoformat(p['time'].replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            f = feats(bars, ets)
            if not f:
                continue
            row = dict(p)
            row['f'] = f
            # also fold in on-record mcap/age/vol if present
            for k in ('mcap', 'age', 'volh1'):
                if p.get(k) is not None:
                    row['f'][k] = float(p[k])
            out.append(row)
        return out

    PRE = enrich(pre)
    PUMP = enrich(pump)
    with open(ROOT / '_regime_enriched.pkl', 'wb') as f:
        pickle.dump({'PRE': PRE, 'PUMP': PUMP}, f)
    print(f'enriched: PRE={len(PRE)} PUMP={len(PUMP)} (with bars-derived features)')


if __name__ == '__main__':
    main()
