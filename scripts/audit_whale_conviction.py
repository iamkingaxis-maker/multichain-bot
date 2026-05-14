"""Audit whale_conviction trigger by mcap and age buckets.

Pulls trades from /api/trades, finds whale_conviction buys, pairs with
sells, and buckets by entry_market_cap_usd and entry_age_hours.
"""
import json
from collections import defaultdict
from pathlib import Path


def main():
    trades = json.load(open('.audit_trades.json'))
    trades.sort(key=lambda t: t.get('time', ''))

    wc_buys = []
    for t in trades:
        if t.get('type') != 'buy':
            continue
        em = t.get('entry_meta') or {}
        if isinstance(em, str):
            try:
                em = json.loads(em)
            except Exception:
                em = {}
        triggers = em.get('triggers_fired') or []
        src = em.get('trigger_source')
        if 'whale_conviction' in triggers or src == 'whale_conviction':
            wc_buys.append({
                'time': t['time'],
                'token': t['token'],
                'address': t['address'],
                'mcap': float(t.get('entry_market_cap_usd') or 0),
                'age_h': float(t.get('entry_age_hours') or 0),
                'amount_usd': float(t.get('amount_usd') or 0),
                'src': src,
                'trigs': triggers,
            })

    sells_by_addr = defaultdict(list)
    for t in trades:
        if t.get('type') != 'sell':
            continue
        sells_by_addr[t['address']].append(t)

    print(f"whale_conviction buys: {len(wc_buys)}")
    print(f"Time window: {trades[0]['time'][:10]} -> {trades[-1]['time'][:10]}")
    print()
    print(f"{'time':16s} {'token':12s} {'mcap':>10s} {'age_h':>7s} {'pnl':>8s} {'src':6s} status")
    print("-" * 90)

    audit = []
    for buy in wc_buys:
        sells = [s for s in sells_by_addr.get(buy['address'], []) if s['time'] > buy['time']]
        if not sells:
            pnl = None
            status = 'OPEN'
        else:
            pnl = sum(float(s.get('pnl') or 0) for s in sells)
            status = sells[-1].get('reason', '')[:20]
        audit.append({**buy, 'pnl': pnl, 'status': status})
        mcap_lbl = f"${buy['mcap']/1e6:.1f}M" if buy['mcap'] else 'n/a'
        pnl_lbl = f"${pnl:+.2f}" if pnl is not None else 'OPEN'
        solo = "SOLO" if len(buy['trigs']) == 1 else f"+{len(buy['trigs'])-1}"
        print(f"{buy['time'][:16]} {buy['token'][:12]:12s} {mcap_lbl:>10s} "
              f"{buy['age_h']:>6.0f}h {pnl_lbl:>8s} {solo:6s} {status[:20]}")

    closed = [a for a in audit if a['pnl'] is not None]
    solo_closed = [a for a in closed if len(a['trigs']) == 1]
    print()
    print("=== SUMMARY ===")
    if closed:
        wr = sum(1 for a in closed if a['pnl'] > 0) / len(closed) * 100
        print(f"All closed: n={len(closed)} WR={wr:.1f}% total=${sum(a['pnl'] for a in closed):+.2f}")
    if solo_closed:
        wr = sum(1 for a in solo_closed if a['pnl'] > 0) / len(solo_closed) * 100
        print(f"Solo fires: n={len(solo_closed)} WR={wr:.1f}% total=${sum(a['pnl'] for a in solo_closed):+.2f}")

    print()
    print("=== BY MCAP ===")
    mcap_buckets = [(0, 1e6, '<$1M'), (1e6, 2e6, '$1M-2M'), (2e6, 5e6, '$2M-5M'),
                    (5e6, 10e6, '$5M-10M'), (10e6, 50e6, '$10M-50M'),
                    (50e6, 9e9, '>=$50M')]
    for lo, hi, lbl in mcap_buckets:
        g = [a for a in closed if lo <= a['mcap'] < hi]
        if not g:
            continue
        wins = sum(1 for a in g if a['pnl'] > 0)
        pnls = [a['pnl'] for a in g]
        print(f"  {lbl:12s} n={len(g):>3} WR={wins/len(g)*100:5.1f}% "
              f"avg=${sum(pnls)/len(g):+6.2f} total=${sum(pnls):+7.2f}")

    print()
    print("=== BY AGE ===")
    age_buckets = [(0, 6, '0-6h'), (6, 24, '6-24h'), (24, 168, '1-7d'),
                   (168, 720, '7-30d'), (720, 9999999, '>30d')]
    for lo, hi, lbl in age_buckets:
        g = [a for a in closed if lo <= a['age_h'] < hi]
        if not g:
            continue
        wins = sum(1 for a in g if a['pnl'] > 0)
        pnls = [a['pnl'] for a in g]
        print(f"  {lbl:8s} n={len(g):>3} WR={wins/len(g)*100:5.1f}% "
              f"avg=${sum(pnls)/len(g):+6.2f} total=${sum(pnls):+7.2f}")

    print()
    print("=== 2D: MCAP x AGE ===")
    age_2d = [(0, 24, '<24h'), (24, 168, '24h-7d'), (168, 9999999, '>7d')]
    print(f"  {'mcap':12s}", " | ".join(f"{a[2]:>14s}" for a in age_2d))
    for lo, hi, mlbl in mcap_buckets:
        cells = []
        for alo, ahi, _ in age_2d:
            g = [a for a in closed if lo <= a['mcap'] < hi and alo <= a['age_h'] < ahi]
            if g:
                wins = sum(1 for a in g if a['pnl'] > 0)
                pnls = [a['pnl'] for a in g]
                cells.append(f"n={len(g)} {wins/len(g)*100:4.0f}% ${sum(pnls):+5.1f}")
            else:
                cells.append('--')
        if any(c != '--' for c in cells):
            print(f"  {mlbl:12s}", " | ".join(f"{c:>14s}" for c in cells))


if __name__ == '__main__':
    main()
