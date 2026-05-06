"""Reusable parallel-entry-trigger validator.

Tests new entry triggers (which OPEN positions) against the existing
clean_break trigger. Different from validate_filter.py (which tests
filters that BLOCK entries).

Two validation tiers:

  1. SIMULATION — scan multi-token chart dataset, find every bar where
     the new trigger fires, simulate bot lifecycle. Categorize entries:
       - cb_only: clean_break fires, new trigger doesn't
       - trigger_only: new trigger fires, clean_break doesn't  ← the marginal new trades
       - both: both fire (already covered)
     KEY METRIC: trigger_only cohort's avg %/trade. Must be positive
     for the marginal new trades to be net additive.

  2. RETRO — for each recent bot-traded token, fetch chart history.
     Find moments where the new trigger fires but bot didn't trade.
     Simulate bot lifecycle from those moments. Reports whether the
     missed entries would have been profitable.

Usage:
  python scripts/validate_trigger.py scripts/proposals/<trigger>.py

Trigger module spec:
  NAME = "trigger_<name>"
  DESCRIPTION = "..."
  NEEDS_OHLC = True/False  # if True, retro/sim need recent_bars

  def should_enter(o, h, l, c, v=None, em=None, recent_bars=None) -> bool:
      ...  # True means OPEN a position at this candle
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from curl_cffi import requests as cf

sys.path.insert(0, '.')
from feeds.dexscreener_chart_format import parse_chart_bars

SOL = 'So11111111111111111111111111111111111111112'
SLUG = {'pumpswap': 'pumpfundex', 'pumpfun': 'pumpfundex',
        'raydium': 'solamm', 'meteora': 'meteora'}
ROOT = Path(__file__).resolve().parent.parent
MASTER_PATH = ROOT / '.deep_token_bars_master.json'


def consec_red_at(bars, i):
    cnt = 0
    for j in range(i, -1, -1):
        if bars[j]['c'] < bars[j]['o']:
            cnt += 1
        else:
            break
    return cnt


def is_clean_break(bars, i):
    if i < 4:
        return False
    return (consec_red_at(bars, i) == 0
            and sum(1 for b in bars[max(0, i - 4):i + 1] if b['c'] < b['o']) >= 3
            and (bars[i]['c'] / bars[i]['o'] - 1) > 0)


def simulate_bot(bars, entry_i):
    entry_p = bars[entry_i]['c']
    if entry_p <= 0:
        return None
    peak = entry_p
    tp1_hit = False
    for j in range(entry_i + 1, min(len(bars), entry_i + 61)):
        h = bars[j]['h']
        l = bars[j]['l']
        if h > peak:
            peak = h
        if l / entry_p - 1 <= -0.12:
            return -12.0
        if not tp1_hit and h / entry_p - 1 >= 0.087:
            tp1_hit = True
        if h / entry_p - 1 >= 0.128:
            return 12.8
        if tp1_hit and (peak / entry_p - 1) >= 0.087:
            trail_target = peak * (1 - 0.035)
            if l <= trail_target:
                return (trail_target / entry_p - 1) * 100
    last_close = bars[min(len(bars) - 1, entry_i + 60)]['c']
    return (last_close / entry_p - 1) * 100


def load_module(path: str):
    p = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("trigger_proposal", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    required = ['NAME', 'DESCRIPTION', 'should_enter']
    missing = [r for r in required if not hasattr(mod, r)]
    if missing:
        raise SystemExit(f'Trigger module missing required attrs: {missing}')
    if not hasattr(mod, 'NEEDS_OHLC'):
        mod.NEEDS_OHLC = True
    return mod


def fetch_trades(retries: int = 3):
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(
                'https://gracious-inspiration-production.up.railway.app/api/trades',
                timeout=30,
            )
            data = r.json()
            if not isinstance(data, list):
                last_err = f'expected list, got {type(data).__name__}'
                time.sleep(2); continue
            if data and not isinstance(data[0], dict):
                last_err = 'expected list-of-dicts'
                time.sleep(2); continue
            return data
        except Exception as e:
            last_err = str(e); time.sleep(2)
    print(f'  ⚠ trades fetch failed: {last_err}')
    return None


def call_should_enter(mod, bars, i):
    """Call trigger's should_enter, passing recent_bars context."""
    cdl = bars[i]
    recent = bars[max(0, i - 60):i + 1]
    try:
        return mod.should_enter(cdl['o'], cdl['h'], cdl['l'], cdl['c'],
                                v=cdl.get('v'), em=None, recent_bars=recent)
    except TypeError:
        try:
            return mod.should_enter(cdl['o'], cdl['h'], cdl['l'], cdl['c'],
                                    v=cdl.get('v'), em=None)
        except Exception:
            return False
    except Exception:
        return False


# ── Tier 1: SIMULATION ───────────────────────────────────────────────
def validate_simulation(trigger_mod):
    print('━' * 70)
    print('VALIDATION 1: SIMULATION marginal-entry economics')
    print('━' * 70)
    if not MASTER_PATH.exists():
        print('  Skipped: master dataset not found.')
        return None
    master = json.loads(MASTER_PATH.read_text())
    tokens = master.get('tokens', {})
    print(f'  Loaded {len(tokens)} token-batches.')

    cb_only, trig_only, both = [], [], []
    for sym_key, tdat in tokens.items():
        bars = tdat.get('bars') or []
        if len(bars) < 100:
            continue
        last_ts = -9999
        for i in range(60, len(bars) - 1):
            cb = is_clean_break(bars, i)
            tr = call_should_enter(trigger_mod, bars, i)
            if not cb and not tr:
                continue
            if bars[i]['ts'] - last_ts < 300:
                continue
            last_ts = bars[i]['ts']
            r = simulate_bot(bars, i)
            if r is None:
                continue
            entry = {'pnl': r, 'outcome': 'WIN' if r > 5 else 'LOSS' if r < -5 else 'flat'}
            if cb and tr:
                both.append(entry)
            elif cb:
                cb_only.append(entry)
            else:
                trig_only.append(entry)

    def stats(group, label):
        if not group:
            print(f'  {label:<30} n=0')
            return None
        n = len(group)
        w = sum(1 for e in group if e['outcome'] == 'WIN')
        l = sum(1 for e in group if e['outcome'] == 'LOSS')
        wr = w / (w + l) * 100 if (w + l) else 0
        avg = sum(e['pnl'] for e in group) / n
        total = sum(e['pnl'] for e in group)
        print(f'  {label:<30} n={n:>5} W={w:>4} L={l:>4} WR={wr:>4.1f}% avg={avg:>+5.2f}% sum={total:>+5.0f}%')
        return {'n': n, 'w': w, 'l': l, 'wr': wr, 'avg': avg, 'sum': total}

    print()
    cb_stats = stats(cb_only, 'cb_only (current bot logic)')
    tr_stats = stats(trig_only, 'trigger_only (NEW marginal)')
    bo_stats = stats(both, 'both (already covered)')
    return {'cb_only': cb_stats, 'trigger_only': tr_stats, 'both': bo_stats}


# ── Tier 2: RETRO on recent bot-traded tokens ────────────────────────
def validate_retro(trigger_mod):
    print('━' * 70)
    print('VALIDATION 2: RETRO — would-be entries on bot-traded tokens')
    print('━' * 70)
    trades = fetch_trades()
    if trades is None:
        return None
    # Get recent bot-traded tokens
    DEPLOY = '2026-05-06T03:25:11'
    bot_buys = [t for t in trades if t.get('type') == 'buy'
                and t.get('strategy') == 'dip_buy'
                and (t.get('time') or '') >= DEPLOY]
    bot_entry_ts = defaultdict(list)
    for b in bot_buys:
        try:
            ts = datetime.fromisoformat((b.get('time') or '').replace('Z', '+00:00')).timestamp()
            bot_entry_ts[(b.get('address'), b.get('pair_address'))].append(ts)
        except Exception:
            pass

    pairs_seen = list(bot_entry_ts.keys())
    print(f'  Bot-traded pairs since deploy: {len(pairs_seen)}')

    # Resolve dex slugs and fetch bars
    pair_slug = {}
    for (addr, pair) in pairs_seen:
        try:
            r = requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair}', timeout=10)
            data = r.json().get('pair') or (r.json().get('pairs') or [{}])[0]
            dex = (data or {}).get('dexId', 'pumpswap')
            pair_slug[(addr, pair)] = SLUG.get(dex, dex)
        except Exception:
            pair_slug[(addr, pair)] = 'pumpfundex'
        time.sleep(0.4)

    pair_bars = {}
    for (addr, pair), slug in pair_slug.items():
        url = (f'https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}'
               f'?res=1&cb=600&q={SOL}')
        try:
            r = cf.get(url, impersonate='chrome', timeout=15,
                       headers={'Origin': 'https://dexscreener.com',
                                'Referer': 'https://dexscreener.com/'})
            if r.status_code == 200:
                bars = parse_chart_bars(r.content)
                if bars:
                    pair_bars[(addr, pair)] = [
                        {'ts': b['ts_ms'] / 1000, 'o': b['open'], 'h': b['high'],
                         'l': b['low'], 'c': b['close'], 'v': b['volume_usd']}
                        for b in bars
                    ]
        except Exception:
            pass
        time.sleep(0.3)
    print(f'  Got bars for {len(pair_bars)}/{len(pairs_seen)} pairs')

    # For each pair, find trigger-fires NOT within ±5min of an actual bot entry
    new_entries = []
    overlap_entries = []
    for (addr, pair), bars in pair_bars.items():
        actual_ts = bot_entry_ts.get((addr, pair), [])
        last_fire_ts = -9999
        for i in range(60, len(bars) - 1):
            if not call_should_enter(trigger_mod, bars, i):
                continue
            if bars[i]['ts'] - last_fire_ts < 300:
                continue
            last_fire_ts = bars[i]['ts']
            # Was there an actual bot entry within ±5 min?
            close_to_actual = any(abs(bars[i]['ts'] - at) < 300 for at in actual_ts)
            r = simulate_bot(bars, i)
            if r is None:
                continue
            entry = {'sym': addr[:10], 'ts': bars[i]['ts'],
                     'pnl_pct': r,
                     'outcome': 'WIN' if r > 5 else 'LOSS' if r < -5 else 'flat'}
            if close_to_actual:
                overlap_entries.append(entry)
            else:
                new_entries.append(entry)

    def show(group, label):
        if not group:
            print(f'  {label:<35} n=0')
            return None
        n = len(group); w = sum(1 for e in group if e['outcome'] == 'WIN')
        l = sum(1 for e in group if e['outcome'] == 'LOSS')
        wr = w / (w + l) * 100 if (w + l) else 0
        avg = sum(e['pnl_pct'] for e in group) / n
        total = sum(e['pnl_pct'] for e in group)
        print(f'  {label:<35} n={n:>4} W={w:>3} L={l:>3} WR={wr:>4.1f}% avg={avg:>+5.2f}% sum={total:>+4.0f}%')
        return {'n': n, 'w': w, 'l': l, 'wr': wr, 'avg': avg, 'sum': total}

    print()
    overlap_stats = show(overlap_entries, 'overlap (bot also entered)')
    new_stats = show(new_entries, 'NEW (bot did NOT enter)')

    if new_entries:
        print()
        print('  Sample NEW entries (first 10):')
        for e in new_entries[:10]:
            t = datetime.fromtimestamp(e['ts'], tz=timezone.utc).strftime('%H:%M:%S')
            print(f'    {e["sym"]:<11} {t} pct={e["pnl_pct"]:+.2f}% {e["outcome"]}')

    return {'overlap': overlap_stats, 'new': new_stats}


# ── Main ─────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/validate_trigger.py <trigger_module.py>')
        sys.exit(1)
    mod = load_module(sys.argv[1])
    print(f'\nTrigger: {mod.NAME}')
    print(f'Description: {mod.DESCRIPTION}')
    print(f'Needs OHLC: {mod.NEEDS_OHLC}\n')

    sim = validate_simulation(mod)
    print()
    retro = validate_retro(mod)

    print()
    print('━' * 70)
    print('VERDICT')
    print('━' * 70)

    sim_vote = None
    if sim and sim.get('trigger_only'):
        avg = sim['trigger_only']['avg']
        n = sim['trigger_only']['n']
        if n < 50:
            sim_vote = 'N/A'
            print(f'  Sim: trigger_only n={n} (too small to evaluate)')
        elif avg > 0:
            sim_vote = 'PASS'
            print(f'  Sim: trigger_only avg={avg:+.2f}%/trade ✓')
        else:
            sim_vote = 'FAIL'
            print(f'  Sim: trigger_only avg={avg:+.2f}%/trade ✗')
    elif sim:
        sim_vote = 'N/A'
        print('  Sim: trigger_only n=0 (trigger never fires outside cb)')

    retro_vote = None
    if retro and retro.get('new'):
        new = retro['new']
        if new['n'] < 5:
            retro_vote = 'N/A'
            print(f'  Retro: NEW n={new["n"]} (too small)')
        elif new['sum'] >= -5.0:  # tolerate small negative on small n
            retro_vote = 'PASS'
            print(f'  Retro: NEW n={new["n"]} sum={new["sum"]:+.0f}% (avg {new["avg"]:+.2f}%) ✓')
        else:
            retro_vote = 'FAIL'
            print(f'  Retro: NEW n={new["n"]} sum={new["sum"]:+.0f}% (avg {new["avg"]:+.2f}%) ✗')
    elif retro:
        retro_vote = 'N/A'
        print('  Retro: no NEW entries identified')

    print()
    fails = sum(1 for v in (sim_vote, retro_vote) if v == 'FAIL')
    passes = sum(1 for v in (sim_vote, retro_vote) if v == 'PASS')
    if fails == 0 and passes >= 1:
        print(f'  ✓ {passes} PASS, 0 FAIL — safe to ship as new entry trigger.')
    elif fails == 0:
        print('  ⚠ All tiers abstained.')
    else:
        print(f'  ✗ DO NOT SHIP — {fails} FAIL.')


if __name__ == '__main__':
    main()
