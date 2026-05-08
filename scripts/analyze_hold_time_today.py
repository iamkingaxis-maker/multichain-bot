"""Analyze today's trades by hold time — find the long-hold problem."""
import json
import urllib.request
from collections import defaultdict
from datetime import datetime


tr = json.loads(urllib.request.urlopen(
    'https://gracious-inspiration-production.up.railway.app/api/trades').read())
trades = tr if isinstance(tr, list) else tr.get('trades', [])

buys = [t for t in trades if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy']
sells = [t for t in trades if t.get('type') == 'sell']
sell_idx = defaultdict(list)
for s in sells:
    sell_idx[(s.get('address'), s.get('pair_address'))].append(s)


def parse_iso(t):
    return datetime.fromisoformat(t.replace('Z', '+00:00'))


TODAY_CUT = '2026-05-07T00:00:00'
paired = []
for b in buys:
    bt = b.get('time') or ''
    if bt < TODAY_CUT:
        continue
    rel = sorted(
        [s for s in sell_idx.get((b.get('address'), b.get('pair_address')), [])
         if (s.get('time') or '') > bt],
        key=lambda s: s.get('time') or ''
    )
    if not rel:
        continue
    pnl = sum(float(s.get('pnl') or 0) for s in rel)
    last = rel[-1]
    if abs(pnl) < 0.01 and 'cancel' in (last.get('reason') or '').lower():
        continue
    hold_secs = (parse_iso(last.get('time')) - parse_iso(bt)).total_seconds()
    paired.append({
        'tok': b.get('token'),
        'pnl': pnl,
        'hold_secs': hold_secs,
        'hold_min': hold_secs / 60,
        'reason': (last.get('reason') or '').split('[')[0].strip(),
        'trigger': (b.get('entry_meta') or {}).get('trigger_source', 'unknown'),
    })

print(f'Closed trades today: {len(paired)}')
print()

# Hold time buckets
buckets = [
    ('0-5 min',   0,    300),
    ('5-15 min',  300,  900),
    ('15-30 min', 900,  1800),
    ('30-60 min', 1800, 3600),
    ('1-2 hr',    3600, 7200),
    ('2-4 hr',    7200, 14400),
    ('4-9 hr',    14400, 32400),
    ('9+ hr',     32400, 99999999),
]
print(f'{"bucket":<12} {"n":>3} {"wins":>5} {"losses":>7} {"WR%":>5} {"avg$":>7} {"total$":>8}')
print('-' * 65)
for name, lo, hi in buckets:
    grp = [p for p in paired if lo <= p['hold_secs'] < hi]
    if not grp:
        continue
    wins = sum(1 for p in grp if p['pnl'] > 0)
    losses = sum(1 for p in grp if p['pnl'] <= 0)
    avg = sum(p['pnl'] for p in grp) / len(grp)
    total = sum(p['pnl'] for p in grp)
    wr = wins / len(grp) * 100
    print(f'{name:<12} {len(grp):>3} {wins:>5} {losses:>7} {wr:>4.0f}% ${avg:>+5.2f} ${total:>+7.2f}')

print()
print('=== ALL trades held >= 30 min ===')
long_holds = sorted([p for p in paired if p['hold_secs'] >= 1800],
                    key=lambda p: -p['hold_secs'])
print(f'{"hold":>8} {"pnl":>7} {"trigger":<22} {"tok":<14} {"exit":<35}')
for p in long_holds:
    h = p['hold_min']
    if h < 60:
        hstr = f'{h:.0f}m'
    else:
        hstr = f'{h/60:.1f}h'
    print(f'{hstr:>8} ${p["pnl"]:>+5.2f} {p["trigger"]:<22} {p["tok"]:<14} {p["reason"][:33]}')

print()
print('=== Long hold (>=2hr) summary ===')
ll = [p for p in paired if p['hold_secs'] >= 7200]
sl = [p for p in paired if p['hold_secs'] < 7200]
if ll:
    print(f'Long hold (>=2hr):  n={len(ll)}, wins={sum(1 for p in ll if p["pnl"]>0)}, '
          f'total=${sum(p["pnl"] for p in ll):+.2f}, '
          f'avg=${sum(p["pnl"] for p in ll)/len(ll):+.2f}')
if sl:
    print(f'Short hold (<2hr):  n={len(sl)}, wins={sum(1 for p in sl if p["pnl"]>0)}, '
          f'total=${sum(p["pnl"] for p in sl):+.2f}, '
          f'avg=${sum(p["pnl"] for p in sl)/len(sl):+.2f}')

print()
print('=== Exit reason breakdown for long holds (>=30min) ===')
reasons = defaultdict(list)
for p in long_holds:
    r = p['reason']
    if r.startswith('Dip TP1'):
        r = 'Dip TP1'
    elif r.startswith('Dip TP2'):
        r = 'Dip TP2'
    elif r.startswith('Dip stop'):
        r = 'Dip stop'
    elif r.startswith('Volume'):
        r = 'Volume death'
    elif 'Manual' in r:
        r = 'Manual sell'
    elif 'cancel' in r.lower():
        r = 'cancel on restart'
    reasons[r].append(p)
for r, lst in sorted(reasons.items(), key=lambda x: -len(x[1])):
    avg = sum(p['pnl'] for p in lst) / len(lst)
    total = sum(p['pnl'] for p in lst)
    print(f'  {r:<30} n={len(lst):>2} avg=${avg:+5.2f} total=${total:+5.2f}')
