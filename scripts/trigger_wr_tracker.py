#!/usr/bin/env python3
"""Track real-fire WR per trigger source vs phantom-projected.

Reads trades from production API (or .all_trades_fresh.json) and computes:
  - Fires per trigger family (alpha_buyperscold, beta_retailfresh, etc.)
  - WR (pnl > 0), avg pnl, peak avg, hold time
  - For multi-trigger entries (e.g. "clean_break_alpha_buyperscold"),
    counts as fire for EACH constituent trigger so we see the full reach

Phantom projections (for comparison):
  clean_break       73% fires of recent 300 (~73% baseline)
  high_regime       27% fires
  alpha_buyperscold 72% WR projected, 21/day
  beta_retailfresh  78% WR projected, 19/day
  delta_microcap    73% WR, +6.5%/tr projected, 10/day
  seller_exhaustion 76% WR, 6/day
  deep_dip_bottom   63% WR, 6/day
  patient_bottom    78% WR, 20/wk
  informed_cluster  70% WR, 25-30/wk
  grad_window_dip   79% WR, 12-15/wk

Run:  python scripts/trigger_wr_tracker.py [--since 2026-05-12T05:18:00]
"""
import json
import sys
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict


PHANTOM_PROJECTIONS = {
    'clean_break':       {'wr': None,  'note': 'primary path'},
    'high_regime':       {'wr': None,  'note': 'primary parallel'},
    'alpha_buyperscold': {'wr': 0.72,  'fires_per_day': 21.0},
    'beta_retailfresh':  {'wr': 0.78,  'fires_per_day': 18.7},
    'delta_microcap':    {'wr': 0.73,  'fires_per_day': 9.6,  'note': '+6.5%/tr'},
    'seller_exhaustion': {'wr': 0.76,  'fires_per_day': 6.0},
    'deep_dip_bottom':   {'wr': 0.63,  'fires_per_day': 6.0},
    'patient_bottom':    {'wr': 0.78,  'fires_per_day': 2.9},
    'informed_cluster':  {'wr': 0.70,  'fires_per_day': 3.9},
    'grad_window_dip':   {'wr': 0.79,  'fires_per_day': 2.0},
}


def fetch_trades(use_local=False):
    if use_local:
        with open('.all_trades_fresh.json') as f:
            return json.load(f)
    try:
        import urllib.request
        url = 'https://gracious-inspiration-production.up.railway.app/api/trades?limit=5000'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'API fetch failed ({e}); falling back to .all_trades_fresh.json')
        with open('.all_trades_fresh.json') as f:
            return json.load(f)


def ts(t):
    try:
        return datetime.fromisoformat(t.get('time', '').replace('Z', '+00:00'))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-05-12T05:18:00',
                    help='UTC cutoff. Default = deploy of 8 new triggers.')
    ap.add_argument('--local', action='store_true', help='Use .all_trades_fresh.json')
    args = ap.parse_args()

    cutoff = datetime.fromisoformat(args.since.replace('Z', '+00:00'))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    trades = fetch_trades(use_local=args.local)
    print(f'Total trades: {len(trades)}')

    # Pair buys with their sells via address
    sells_by_addr = defaultdict(list)
    for t in trades:
        if t.get('type') == 'sell':
            sells_by_addr[t.get('address')].append(t)

    paired = []
    for b in trades:
        if b.get('type') != 'buy':
            continue
        bts = ts(b)
        if bts is None or bts < cutoff:
            continue
        addr = b.get('address')
        sells = sells_by_addr.get(addr, [])
        sell = None
        for s in sells:
            sts = ts(s)
            if sts and sts > bts:
                sell = s
                break
        em = b.get('entry_meta') or {}
        triggers = em.get('triggers_fired') or []
        if not triggers:
            # Fallback for legacy entries: trigger_source is a single string
            # joined by underscore for multi-trigger entries. We can't reliably
            # split (underscore is also in names like "clean_break") so we
            # just keep the full string as one bucket.
            ts_field = em.get('trigger_source')
            if ts_field:
                triggers = [ts_field]
        paired.append({
            'buy': b, 'sell': sell, 'triggers': triggers,
            'pnl': (sell or {}).get('pnl'),
            'pnl_pct': (sell or {}).get('pnl_pct'),
            'peak': (sell or {}).get('peak_pnl_pct'),
        })

    closed = [p for p in paired if p['sell']]
    open_ = [p for p in paired if not p['sell']]
    print(f'Post-cutoff buys: {len(paired)}  closed: {len(closed)}  open: {len(open_)}\n')

    if not paired:
        print('No trades since cutoff — bot may not have fired any new triggers yet.')
        return

    # Aggregate per trigger
    stats = defaultdict(lambda: {'fires': 0, 'closed': 0, 'wins': 0,
                                  'pnl_total': 0.0, 'peak_total': 0.0})
    for p in paired:
        for trig in p['triggers']:
            stats[trig]['fires'] += 1
            if p['sell']:
                stats[trig]['closed'] += 1
                pnl = p['pnl'] or 0
                stats[trig]['pnl_total'] += pnl
                if pnl > 0:
                    stats[trig]['wins'] += 1
                stats[trig]['peak_total'] += p['peak'] or 0

    print(f'=== TRIGGER FIRES SINCE {cutoff.isoformat()} ===')
    print(f'{"trigger":<22} {"fires":>5} {"closed":>6} {"WR":>5} {"$/tr":>7} {"peak":>7}  phantom_WR')
    print('-' * 80)
    for trig in sorted(stats, key=lambda k: -stats[k]['fires']):
        s = stats[trig]
        n_closed = s['closed']
        wr_str = f'{s["wins"]/n_closed:.0%}' if n_closed else '  --'
        avg_pnl = f'${s["pnl_total"]/n_closed:+.2f}' if n_closed else '   --'
        peak = f'{s["peak_total"]/n_closed:+.1f}%' if n_closed else '   --'
        proj = PHANTOM_PROJECTIONS.get(trig, {})
        proj_wr = f'{proj.get("wr", 0):.0%}' if proj.get('wr') else proj.get('note', '?')
        print(f'  {trig:<20} {s["fires"]:>5} {n_closed:>6} {wr_str:>5} {avg_pnl:>7} {peak:>7}  {proj_wr}')

    # Overall
    if closed:
        total = sum(c['pnl'] or 0 for c in closed)
        wins = sum(1 for c in closed if (c['pnl'] or 0) > 0)
        print(f'\nOverall closed: {len(closed)}  WR={wins/len(closed):.0%}  total=${total:+.2f}')

    # New trigger fires specifically
    new_trigs = {'alpha_buyperscold', 'beta_retailfresh', 'delta_microcap',
                 'seller_exhaustion', 'deep_dip_bottom', 'patient_bottom',
                 'informed_cluster', 'grad_window_dip'}
    new_fires = sum(stats[t]['fires'] for t in new_trigs if t in stats)
    new_closed = sum(stats[t]['closed'] for t in new_trigs if t in stats)
    print(f'\n=== NEW TRIGGERS (8 shipped 2026-05-12) ===')
    print(f'Total fires: {new_fires}  closed: {new_closed}')
    if new_closed == 0 and new_fires == 0:
        print('No fires yet — wait for more scan cycles or check whether trigger features')
        print('are available on candidates (1s coverage = 10%, etc.).')


if __name__ == '__main__':
    main()
