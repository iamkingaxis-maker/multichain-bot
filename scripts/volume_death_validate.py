"""Validate proposed loosened volume_death threshold against winners.

Question: at the 30-90min mark of past trades, when pnl was <=-3%,
how does the proposed condition (v_m5 < 25% of avg-5m-of-hour) score
on winners vs losers? If many winners pass through that condition
en-route to a successful exit, loosening the threshold cuts winners.

Approach: fetch 1m bars for recent closed trades. Walk the hold
minute-by-minute. At each tick where age >= 30min AND pnl <= -3%,
compute v_m5 (last 5 bars) and v_h1 (last 60 bars). If
v_m5 < (v_h1/12) * 0.25 AND v_h1 < v_h24/48, the proposed filter
would fire. Track first-fire time per trade and the trade's final
outcome.
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
            # Earliest sell timestamp = effective exit
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


def simulate_volume_death(bars, entry_ts, entry_price, exit_ts, threshold_pct):
    """Walk the hold minute-by-minute, capped at the actual exit time.
    Return (fired_at_min, pnl_at_fire) if the proposed filter would
    have fired before the bot actually exited, else (None, None)."""
    if not bars or entry_price <= 0:
        return None, None
    pre_bars = [b for b in bars if b['ts_ms'] / 1000 < entry_ts]
    held_bars = [b for b in bars if entry_ts <= b['ts_ms'] / 1000 <= exit_ts]
    if len(held_bars) < 30:  # need at least 30 min of hold to fire
        return None, None
    series = pre_bars + held_bars
    entry_idx = len(pre_bars)
    end_idx = len(series)  # walk through the held window only
    for i in range(entry_idx, end_idx):
        age_min = (series[i]['ts_ms'] - series[entry_idx]['ts_ms']) / 60_000
        if age_min < 30:
            continue
        cur_price = series[i]['close']
        pnl_pct = (cur_price / entry_price - 1) * 100
        if pnl_pct > -3.0:
            continue
        v_m5 = sum(b['volume_usd'] for b in series[max(0, i - 4):i + 1])
        v_h1 = sum(b['volume_usd'] for b in series[max(0, i - 59):i + 1])
        v_h24 = sum(b['volume_usd'] for b in series[max(0, i - 1439):i + 1])
        if v_h1 <= 0 or v_h24 <= 0:
            continue
        decay_threshold = v_h24 / 48
        v_m5_expected = v_h1 / 12
        if (v_m5 == 0 or v_m5 < v_m5_expected * threshold_pct) and v_h1 < decay_threshold:
            return age_min, pnl_pct
    return None, None


def main():
    print('Fetching trades...')
    trades = fetch_trades()
    closed = pair_buys_closed(trades)
    closed.sort(key=lambda x: x[0].get('time') or '', reverse=True)
    closed = closed[:120]
    print(f'Closed: {len(closed)}')

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
    print(f'Got bars for {len(pair_to_bars)}/{len(pair_to_slug)} pairs')

    # Diagnostic — how many trades hit the {age>=30, pnl<=-3} pre-conditions at all?
    diag_eligible = 0
    diag_v_h1_ok = 0
    diag_total_with_bars = 0
    for b, pnl, exit_t in closed:
        pa = b.get('pair_address')
        bars = pair_to_bars.get(pa)
        if not bars:
            continue
        try:
            ets = datetime.fromisoformat((b.get('time') or '').replace('Z','+00:00')).timestamp()
            xts = datetime.fromisoformat(exit_t.replace('Z','+00:00')).timestamp()
        except Exception:
            continue
        ep = float(b.get('entry_price') or 0)
        if ep <= 0:
            continue
        diag_total_with_bars += 1
        held_bars = [bb for bb in bars if ets <= bb['ts_ms']/1000 <= xts]
        pre_bars = [bb for bb in bars if bb['ts_ms']/1000 < ets]
        series = pre_bars + held_bars
        entry_idx = len(pre_bars)
        if len(held_bars) < 30:
            continue
        for i in range(entry_idx + 30, len(series)):
            age_min = (series[i]['ts_ms'] - series[entry_idx]['ts_ms']) / 60_000
            if age_min < 30: continue
            pnl_pct = (series[i]['close'] / ep - 1) * 100
            if pnl_pct > -3.0: continue
            diag_eligible += 1
            v_h1 = sum(bb['volume_usd'] for bb in series[max(0,i-59):i+1])
            v_h24 = sum(bb['volume_usd'] for bb in series[max(0,i-1439):i+1])
            if v_h1 > 0 and v_h24 > 0 and v_h1 < v_h24/48:
                diag_v_h1_ok += 1
            break
    print(f'Diag: {diag_total_with_bars} trades w/ bars; {diag_eligible} hit age>=30 AND pnl<=-3; '
          f'{diag_v_h1_ok} of those also had v_h1 < v_h24/48')

    # Test multiple thresholds
    for thr_pct in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]:
        print(f'\n=== Threshold: v_m5 < {thr_pct*100:.0f}% of v_m5_expected ===')
        rows = []
        for b, pnl, exit_t in closed:
            pa = b.get('pair_address')
            bars = pair_to_bars.get(pa)
            if not bars:
                continue
            try:
                ets = datetime.fromisoformat(
                    (b.get('time') or '').replace('Z', '+00:00')).timestamp()
                xts = datetime.fromisoformat(
                    exit_t.replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            entry_p = float(b.get('entry_price') or 0)
            if entry_p <= 0:
                continue
            fire_min, fire_pnl = simulate_volume_death(bars, ets, entry_p, xts, thr_pct)
            rows.append({'pnl': pnl, 'fire_min': fire_min, 'fire_pnl': fire_pnl,
                         'addr': (b.get('address') or '')[:10],
                         'hold_min': (xts - ets) / 60})
        wins_fired = [r for r in rows if r['fire_min'] is not None and r['pnl'] > 0.5]
        loss_fired = [r for r in rows if r['fire_min'] is not None and r['pnl'] < -0.5]
        n_total_w = sum(1 for r in rows if r['pnl'] > 0.5)
        n_total_l = sum(1 for r in rows if r['pnl'] < -0.5)
        wins_pnl = sum(r['pnl'] for r in wins_fired)
        # Estimate $20 sizing impact: $20 * fire_pnl/100 if exited at fire
        est_loss_save = sum(r['pnl'] - 20 * r['fire_pnl']/100 for r in loss_fired)
        est_win_cost = sum(20 * r['fire_pnl']/100 - r['pnl'] for r in wins_fired)
        print(f'  Total: {n_total_w}W + {n_total_l}L | Fires: {len(wins_fired)}W cut + {len(loss_fired)}L caught')
        print(f'  Winners cut sum (final pnl): ${wins_pnl:+.2f}')
        print(f'  Est exit-at-fire impact:')
        print(f'    Winners: lose ${est_win_cost:+.2f} of upside (final {wins_pnl:+.2f} -> early {sum(20*r["fire_pnl"]/100 for r in wins_fired):+.2f})')
        print(f'    Losers:  save ${est_loss_save:+.2f} (final {sum(r["pnl"] for r in loss_fired):+.2f} -> early {sum(20*r["fire_pnl"]/100 for r in loss_fired):+.2f})')
        for r in wins_fired:
            print(f'    [WIN cut]  addr={r["addr"]} final=${r["pnl"]:+.2f} hold={r["hold_min"]:.0f}m | '
                  f'fired@{r["fire_min"]:.0f}min pnl_at_fire={r["fire_pnl"]:+.1f}%')
        for r in loss_fired:
            print(f'    [LOSS catch] addr={r["addr"]} final=${r["pnl"]:+.2f} hold={r["hold_min"]:.0f}m | '
                  f'fired@{r["fire_min"]:.0f}min pnl_at_fire={r["fire_pnl"]:+.1f}%')


if __name__ == '__main__':
    main()
