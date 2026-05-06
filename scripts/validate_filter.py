"""Reusable filter-proposal validator.

Runs three checks for any filter proposal:
  1. SIMULATION lift — apply filter to clean_break entries on multi-token chart
     dataset (.deep_token_bars_master.json). Reports WR, avg/trade, lift.
  2. RETRO check — fetch recent bot trades, find entry candle for each,
     compute filter verdict, report which winners/losers it would have
     blocked. Catches simulation-vs-real divergence.
  3. LIFETIME held-out (optional) — if filter only depends on entry_meta
     fields, run on lifetime $20-era trades with 70/30 train/test split.

Usage:
  python scripts/validate_filter.py scripts/proposals/wick_dominant.py

Filter module spec — define a Python file with:
  NAME = "filter_<short_name>"
  DESCRIPTION = "what it does"
  NEEDS_OHLC = True/False  # if True, lifetime check is skipped

  def should_block(o, h, l, c, v=None, em=None) -> bool:
      \"\"\"Return True to BLOCK, False to PASS. o/h/l/c are the entry
      candle's OHLC. v is volume, em is entry_meta dict (may be None for
      simulation context). When NEEDS_OHLC is False, this may be called
      with o=h=l=c=None and em fully populated.\"\"\"
      ...
"""
from __future__ import annotations

import importlib.util
import json
import statistics
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


# ── Helpers ──────────────────────────────────────────────────────────────
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
    """Replicate bot's $20 dip_buy lifecycle. TP1 +8.7%, TP2 +12.8%, stop -12%, trail -3.5%."""
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


def load_filter_module(path: str):
    p = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("filter_proposal", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    required = ['NAME', 'DESCRIPTION', 'should_block']
    missing = [r for r in required if not hasattr(mod, r)]
    if missing:
        raise SystemExit(f'Filter module missing required attrs: {missing}')
    if not hasattr(mod, 'NEEDS_OHLC'):
        mod.NEEDS_OHLC = True
    return mod


# ── Validation 1: SIMULATION ─────────────────────────────────────────────
def validate_simulation(filter_mod):
    print('━' * 70)
    print(f'VALIDATION 1: SIMULATION on multi-token chart dataset')
    print('━' * 70)
    if not MASTER_PATH.exists():
        print(f'Skipped: {MASTER_PATH} not found.')
        return None

    master = json.loads(MASTER_PATH.read_text())
    tokens = master.get('tokens', {})
    print(f'  Loaded {len(tokens)} token-batches from master.')

    all_entries = []
    blocked_entries = []
    for sym_key, tdat in tokens.items():
        bars = tdat.get('bars') or []
        if len(bars) < 100:
            continue
        last_ts = -9999
        for i in range(60, len(bars) - 1):
            if not is_clean_break(bars, i):
                continue
            if bars[i]['ts'] - last_ts < 300:
                continue
            last_ts = bars[i]['ts']
            cdl = bars[i]
            blk = False
            try:
                blk = filter_mod.should_block(cdl['o'], cdl['h'], cdl['l'], cdl['c'],
                                              v=cdl.get('v'), em=None)
            except Exception:
                blk = False
            r = simulate_bot(bars, i)
            if r is None:
                continue
            entry = {'pnl': r, 'blocked': blk,
                     'outcome': 'WIN' if r > 5 else 'LOSS' if r < -5 else 'flat'}
            all_entries.append(entry)
            if blk:
                blocked_entries.append(entry)

    n = len(all_entries)
    if n == 0:
        print('  No simulated entries — skipped.')
        return None

    # Baseline (all entries, no filter)
    base_w = sum(1 for e in all_entries if e['outcome'] == 'WIN')
    base_l = sum(1 for e in all_entries if e['outcome'] == 'LOSS')
    base_wr = base_w / (base_w + base_l) * 100 if (base_w + base_l) else 0
    base_sum = sum(e['pnl'] for e in all_entries)
    base_avg = base_sum / n

    # Pass cohort (filter PASSED)
    pass_set = [e for e in all_entries if not e['blocked']]
    bp_w = sum(1 for e in pass_set if e['outcome'] == 'WIN')
    bp_l = sum(1 for e in pass_set if e['outcome'] == 'LOSS')
    pass_wr = bp_w / (bp_w + bp_l) * 100 if (bp_w + bp_l) else 0
    pass_sum = sum(e['pnl'] for e in pass_set)
    pass_avg = pass_sum / len(pass_set) if pass_set else 0

    # Block cohort
    block_w = sum(1 for e in blocked_entries if e['outcome'] == 'WIN')
    block_l = sum(1 for e in blocked_entries if e['outcome'] == 'LOSS')
    block_wr = block_w / (block_w + block_l) * 100 if (block_w + block_l) else 0
    block_sum = sum(e['pnl'] for e in blocked_entries)
    block_avg = block_sum / len(blocked_entries) if blocked_entries else 0

    print(f'  Total entries: {n}')
    print(f'  Baseline (no filter): WR={base_wr:.1f}% avg={base_avg:+.3f}% sum={base_sum:+.0f}%')
    print(f'  PASS cohort: n={len(pass_set)} WR={pass_wr:.1f}% avg={pass_avg:+.3f}% sum={pass_sum:+.0f}%')
    print(f'  BLOCK cohort: n={len(blocked_entries)} WR={block_wr:.1f}% avg={block_avg:+.3f}% sum={block_sum:+.0f}%')
    print(f'  Lift on PASS cohort: WR {pass_wr - base_wr:+.1f}pp, avg {pass_avg - base_avg:+.3f}%/trade')
    return {
        'n_total': n, 'n_pass': len(pass_set), 'n_block': len(blocked_entries),
        'base_wr': base_wr, 'pass_wr': pass_wr, 'block_wr': block_wr,
        'base_avg': base_avg, 'pass_avg': pass_avg, 'block_avg': block_avg,
        'pass_lift_pp': pass_wr - base_wr,
        'pass_lift_pct_per_trade': pass_avg - base_avg,
    }


# ── Validation 2: RETRO on recent bot trades ─────────────────────────────
def validate_retro(filter_mod):
    print('━' * 70)
    print(f'VALIDATION 2: RETRO on recent bot trades (chart data still available)')
    print('━' * 70)

    trades = requests.get(
        'https://gracious-inspiration-production.up.railway.app/api/trades',
        timeout=30,
    ).json()

    DEPLOY = '2026-05-06T03:25:11'
    buys = [t for t in trades if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy'
            and (t.get('time') or '') >= DEPLOY]
    sells = [t for t in trades if t.get('type') == 'sell' and t.get('pnl') is not None]

    all_buys_by_key = defaultdict(list)
    for t in trades:
        if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy':
            all_buys_by_key[(t.get('address'), t.get('pair_address'))].append(t.get('time') or '')
    for k in all_buys_by_key:
        all_buys_by_key[k].sort()
    sidx = defaultdict(list)
    for s in sells:
        sidx[(s.get('address'), s.get('pair_address'))].append(s)

    closed = []
    for b in buys:
        bt = b.get('time') or ''
        key = (b.get('address'), b.get('pair_address'))
        next_bt = '9999'
        for cbt in all_buys_by_key.get(key, []):
            if cbt > bt:
                next_bt = cbt
                break
        cands = [s for s in sidx[key]
                 if bt < (s.get('time') or '') < next_bt and s.get('pnl') is not None]
        if not cands:
            continue
        if any('cancelled' in (s.get('reason') or '').lower() for s in cands):
            continue
        pnl = sum(s.get('pnl') for s in cands)
        closed.append((b, pnl))

    print(f'  Closed clean_break-era trades: {len(closed)}')

    pairs_seen = {}
    for b, _ in closed:
        pairs_seen[(b.get('address'), b.get('pair_address'))] = None

    # Resolve dex slugs
    for (addr, pair) in list(pairs_seen.keys()):
        try:
            r = requests.get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair}', timeout=10)
            data = r.json().get('pair') or (r.json().get('pairs') or [{}])[0]
            dex = (data or {}).get('dexId', 'pumpswap')
            pairs_seen[(addr, pair)] = SLUG.get(dex, dex)
        except Exception:
            pairs_seen[(addr, pair)] = 'pumpfundex'
        time.sleep(0.4)

    # Fetch bars
    pair_bars = {}
    for (addr, pair), slug in pairs_seen.items():
        url = (f'https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}'
               f'?res=1&cb=600&q={SOL}')
        try:
            r = cf.get(url, impersonate='chrome', timeout=15,
                       headers={'Origin': 'https://dexscreener.com', 'Referer': 'https://dexscreener.com/'})
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

    blocked = []
    passed = []
    no_data = []
    for b, pnl in closed:
        bt_str = b.get('time') or ''
        try:
            bt_ts = datetime.fromisoformat(bt_str.replace('Z', '+00:00')).timestamp()
        except Exception:
            no_data.append((b, pnl))
            continue
        key = (b.get('address'), b.get('pair_address'))
        bars = pair_bars.get(key)
        if not bars:
            no_data.append((b, pnl))
            continue
        cdl = None
        for x in bars:
            if x['ts'] <= bt_ts:
                cdl = x
            else:
                break
        if not cdl or cdl['o'] <= 0:
            no_data.append((b, pnl))
            continue
        em = b.get('entry_meta') or {}
        try:
            blk = filter_mod.should_block(cdl['o'], cdl['h'], cdl['l'], cdl['c'],
                                          v=cdl.get('v'), em=em)
        except Exception:
            blk = False
        sym = (em.get('token_symbol') or b.get('address', '?'))[:10]
        rec = (sym, pnl, 'WIN' if pnl > 0.5 else 'LOSS' if pnl < -0.5 else 'flat')
        if blk:
            blocked.append(rec)
        else:
            passed.append(rec)

    print(f'  WOULD BLOCK ({len(blocked)} trades):')
    for sym, pnl, out in blocked:
        print(f'    {sym:<10} ${pnl:+.2f} {out}')
    if blocked:
        bs = sum(p for _, p, _ in blocked)
        bw = sum(1 for _, _, o in blocked if o == 'WIN')
        bl = sum(1 for _, _, o in blocked if o == 'LOSS')
        print(f'    Sum: ${bs:+.2f}, {bw}W/{bl}L')
    print(f'  WOULD PASS ({len(passed)} trades): {sum(1 for _, _, o in passed if o == "WIN")}W'
          f' / {sum(1 for _, _, o in passed if o == "LOSS")}L'
          f' sum=${sum(p for _, p, _ in passed):+.2f}')
    if no_data:
        print(f'  NO DATA ({len(no_data)} trades — chart history expired/missing)')

    real_total = sum(p for _, p, _ in blocked) + sum(p for _, p, _ in passed)
    filtered_total = sum(p for _, p, _ in passed)
    print(f'  Real total (without filter): ${real_total:+.2f}')
    print(f'  With filter applied:         ${filtered_total:+.2f}')
    print(f'  Delta:                       ${filtered_total - real_total:+.2f}')
    return {
        'n_blocked': len(blocked), 'n_passed': len(passed), 'n_no_data': len(no_data),
        'blocked_pnl': sum(p for _, p, _ in blocked),
        'passed_pnl': sum(p for _, p, _ in passed),
        'real_total': real_total,
        'with_filter_total': filtered_total,
        'delta': filtered_total - real_total,
        'blocked_winners': sum(1 for _, _, o in blocked if o == 'WIN'),
        'blocked_losers': sum(1 for _, _, o in blocked if o == 'LOSS'),
    }


# ── Validation 3: LIFETIME held-out (only if filter doesn't need OHLC) ───
def validate_lifetime_em(filter_mod):
    print('━' * 70)
    print(f'VALIDATION 3: LIFETIME held-out via entry_meta')
    print('━' * 70)
    if filter_mod.NEEDS_OHLC:
        print('  Skipped — filter needs OHLC; entry_meta does not store raw OHLC.')
        return None

    trades = requests.get(
        'https://gracious-inspiration-production.up.railway.app/api/trades',
        timeout=30,
    ).json()
    buys = [t for t in trades if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy'
            and (t.get('amount_usd') or 1000) <= 30]
    sells = [t for t in trades if t.get('type') == 'sell' and t.get('pnl') is not None]

    all_buys_by_key = defaultdict(list)
    for t in trades:
        if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy':
            all_buys_by_key[(t.get('address'), t.get('pair_address'))].append(t.get('time') or '')
    for k in all_buys_by_key:
        all_buys_by_key[k].sort()
    sidx = defaultdict(list)
    for s in sells:
        sidx[(s.get('address'), s.get('pair_address'))].append(s)

    paired = []
    for b in buys:
        bt = b.get('time') or ''
        key = (b.get('address'), b.get('pair_address'))
        next_bt = '9999'
        for cbt in all_buys_by_key.get(key, []):
            if cbt > bt:
                next_bt = cbt
                break
        cands = [s for s in sidx[key]
                 if bt < (s.get('time') or '') < next_bt and s.get('pnl') is not None]
        if cands and not any('cancelled' in (s.get('reason') or '').lower() for s in cands):
            paired.append((b, sum(s.get('pnl') for s in cands)))

    paired.sort(key=lambda x: x[0].get('time') or '')
    split = int(len(paired) * 0.7)
    train, test = paired[:split], paired[split:]

    def evaluate(cohort):
        pass_set, block_set = [], []
        for b, p in cohort:
            em = b.get('entry_meta') or {}
            try:
                blk = filter_mod.should_block(None, None, None, None, v=None, em=em)
            except Exception:
                blk = False
            (block_set if blk else pass_set).append((b, p))
        wins_p = sum(1 for _, p in pass_set if p > 0.5)
        losses_p = sum(1 for _, p in pass_set if p < -0.5)
        full_avg = sum(p for _, p in cohort) / len(cohort) if cohort else 0
        pass_avg = sum(p for _, p in pass_set) / len(pass_set) if pass_set else 0
        return {
            'n': len(cohort), 'n_pass': len(pass_set), 'n_block': len(block_set),
            'pass_pnl': sum(p for _, p in pass_set),
            'block_pnl': sum(p for _, p in block_set),
            'full_pnl': sum(p for _, p in cohort),
            'pass_wr': wins_p / (wins_p + losses_p) * 100 if (wins_p + losses_p) else 0,
            'pass_avg': pass_avg,
            'full_avg': full_avg,
            'avg_lift': pass_avg - full_avg,
        }

    tr = evaluate(train)
    te = evaluate(test)
    print(f'  TRAIN n={tr["n"]} | PASS n={tr["n_pass"]} sum=${tr["pass_pnl"]:+.2f} | '
          f'BLOCK n={tr["n_block"]} sum=${tr["block_pnl"]:+.2f} | lift ${tr["avg_lift"]:+.3f}/trade')
    print(f'  TEST  n={te["n"]} | PASS n={te["n_pass"]} sum=${te["pass_pnl"]:+.2f} | '
          f'BLOCK n={te["n_block"]} sum=${te["block_pnl"]:+.2f} | lift ${te["avg_lift"]:+.3f}/trade')
    return {'train': tr, 'test': te}


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/validate_filter.py <filter_module.py>')
        sys.exit(1)
    mod = load_filter_module(sys.argv[1])
    print(f'\nFilter: {mod.NAME}')
    print(f'Description: {mod.DESCRIPTION}')
    print(f'Needs OHLC: {mod.NEEDS_OHLC}')
    print()

    sim = validate_simulation(mod)
    print()
    retro = validate_retro(mod)
    print()
    lifetime = validate_lifetime_em(mod)

    print()
    print('━' * 70)
    print('VERDICT')
    print('━' * 70)
    print(f'  Sim PASS-cohort lift: {sim["pass_lift_pp"]:+.1f}pp WR, '
          f'{sim["pass_lift_pct_per_trade"]:+.3f}%/trade' if sim else '  Sim: skipped')
    if retro:
        if retro['n_blocked'] == 0:
            print(f'  Retro: filter would not have fired on any of '
                  f'{retro["n_passed"]} recent trades')
        else:
            print(f'  Retro on n={retro["n_blocked"]} blocks: '
                  f'{retro["blocked_winners"]}W/{retro["blocked_losers"]}L, '
                  f'delta ${retro["delta"]:+.2f}')
    if lifetime:
        print(f'  Lifetime TEST (held-out): n={lifetime["test"]["n_pass"]} PASS, '
              f'WR={lifetime["test"]["pass_wr"]:.0f}%, total=${lifetime["test"]["pass_pnl"]:+.2f}')

    # Decision rule. Each tier votes — filter ships if all applicable tiers
    # agree it improves outcomes. Tiers may abstain (e.g., sim BLOCK n=0
    # for em-only filters → sim is N/A, not negative).
    print()
    sim_vote = None
    if sim is not None:
        if sim['n_block'] == 0:
            sim_vote = 'N/A'  # filter never fires in sim — abstains
        elif sim['pass_lift_pct_per_trade'] > 0:
            sim_vote = 'PASS'
        else:
            sim_vote = 'FAIL'
    retro_vote = None
    if retro is not None:
        if retro['n_blocked'] == 0:
            retro_vote = 'N/A'
        elif retro['delta'] >= -0.5:
            retro_vote = 'PASS'
        else:
            retro_vote = 'FAIL'
    lifetime_vote = None
    if lifetime is not None:
        # Filter ships if PASS-cohort avg is BETTER than full-cohort avg
        # (i.e., filter improves the cohort by removing bad trades), AND
        # the BLOCK cohort is at-or-below full-cohort avg (sanity).
        te = lifetime['test']
        if te['n_block'] == 0:
            lifetime_vote = 'N/A'
        elif te['avg_lift'] > 0:
            lifetime_vote = 'PASS'
        else:
            lifetime_vote = 'FAIL'

    print(f'  Sim vote:      {sim_vote}')
    print(f'  Retro vote:    {retro_vote}')
    print(f'  Lifetime vote: {lifetime_vote}')

    fails = sum(1 for v in (sim_vote, retro_vote, lifetime_vote) if v == 'FAIL')
    passes = sum(1 for v in (sim_vote, retro_vote, lifetime_vote) if v == 'PASS')
    print()
    if fails == 0 and passes >= 1:
        print(f'  ✓ {passes} PASS, 0 FAIL — safe to ship as shadow.')
    elif fails == 0:
        print('  ⚠ All tiers abstained (N/A). Need fresh data before deciding.')
    else:
        print(f'  ✗ DO NOT SHIP — {fails} FAIL.')


if __name__ == '__main__':
    main()
