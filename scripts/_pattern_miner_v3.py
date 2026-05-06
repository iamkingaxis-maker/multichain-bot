"""Pattern miner v3 — scan for entry triggers OTHER than clean_break and 4-combo.

Tests 25+ shape primitives at EVERY bar (not just clean_break entries),
finds combos with:
  - WR >= 60% on standalone
  - n >= 100 entries
  - Low overlap with existing triggers (clean_break, 4-combo)

Goal: find a third independent reversal pattern to add as parallel trigger.
"""
import json
from itertools import combinations

master = json.loads(open('.deep_token_bars_master.json').read())


def consec_red_at(bars, i):
    cnt = 0
    for j in range(i, -1, -1):
        if bars[j]['c'] < bars[j]['o']:
            cnt += 1
        else:
            break
    return cnt


def is_clean_break(bars, i):
    if i < 4: return False
    return (consec_red_at(bars, i) == 0
            and sum(1 for b in bars[max(0, i-4):i+1] if b['c'] < b['o']) >= 3
            and (bars[i]['c'] / bars[i]['o'] - 1) > 0)


def is_4combo(bars, i):
    if i < 30 or bars[i-30]['c'] <= 0 or bars[i]['o'] <= 0: return False
    m30 = (bars[i]['c'] / bars[i-30]['c'] - 1) * 100
    if not (-15 <= m30 <= -3): return False
    prior = [b['v'] for b in bars[i-5:i]]
    avg = sum(prior) / len(prior) if prior else 0
    if avg <= 0 or bars[i]['v'] / avg <= 1.5: return False
    if i < 5: return False
    if bars[i]['c'] <= max(b['h'] for b in bars[i-5:i]): return False
    if i < 1 or bars[i]['l'] <= bars[i-1]['l']: return False
    return True


def simulate_bot(bars, entry_i):
    entry_p = bars[entry_i]['c']
    if entry_p <= 0: return None
    peak = entry_p
    tp1_hit = False
    for j in range(entry_i + 1, min(len(bars), entry_i + 61)):
        h = bars[j]['h']; l = bars[j]['l']
        if h > peak: peak = h
        if l / entry_p - 1 <= -0.12: return -12.0
        if not tp1_hit and h / entry_p - 1 >= 0.087: tp1_hit = True
        if h / entry_p - 1 >= 0.128: return 12.8
        if tp1_hit and (peak / entry_p - 1) >= 0.087:
            trail_target = peak * (1 - 0.035)
            if l <= trail_target:
                return (trail_target / entry_p - 1) * 100
    last_close = bars[min(len(bars)-1, entry_i + 60)]['c']
    return (last_close / entry_p - 1) * 100


# ── New shape primitives ──────────────────────────────────────────
def false_breakdown_recovery(bars, i):
    """Current low broke below prior 3-bar lows, but close is back above."""
    if i < 3 or bars[i]['o'] <= 0: return False
    prior_lows = [b['l'] for b in bars[i-3:i]]
    min_prior = min(prior_lows)
    cur = bars[i]
    return cur['l'] < min_prior and cur['c'] >= min_prior

def secondary_test_higher_low(bars, i):
    """Second visit to a prior low forms a higher low (double-bottom-like)."""
    if i < 10: return False
    cur_low = bars[i]['l']
    # Find a prior local low in last 4-10 candles
    found = False
    prior_low_val = None
    for j in range(i-10, i-3):
        if j < 1 or j >= len(bars) - 1: continue
        if bars[j]['l'] < bars[j-1]['l'] and bars[j]['l'] < bars[j+1]['l']:
            found = True
            prior_low_val = bars[j]['l']
            break
    if not found: return False
    # cur is within 3% of prior_low but slightly higher (HL on retest)
    if prior_low_val <= 0: return False
    diff_pct = (cur_low - prior_low_val) / prior_low_val * 100
    return 0 < diff_pct < 3 and bars[i]['c'] > bars[i]['o']

def vol_divergence_red(bars, i):
    """Price down over last 3 candles but vol decreasing — sellers tiring."""
    if i < 3: return False
    if bars[i]['c'] >= bars[i-3]['c']: return False
    return bars[i-3]['v'] > bars[i-2]['v'] > bars[i-1]['v']

def consolidation_after_drop(bars, i):
    """Drop of >=5% in last 10 candles, then last 4 candles small bodies."""
    if i < 10: return False
    high_10 = max(b['h'] for b in bars[i-10:i+1])
    low_10 = min(b['l'] for b in bars[i-10:i+1])
    drop = (low_10 / high_10 - 1) * 100 if high_10 > 0 else 0
    if drop > -5: return False
    last4 = bars[i-3:i+1]
    bodies = [abs(b['c']-b['o'])/b['o']*100 for b in last4 if b['o'] > 0]
    return all(b < 1.5 for b in bodies) and len(bodies) == 4

def inside_bar_break(bars, i):
    """Prior candle was inside-bar (range within bar -2's range), current breaks above."""
    if i < 2: return False
    a, p, c = bars[i-2], bars[i-1], bars[i]
    inside = (p['h'] <= a['h'] and p['l'] >= a['l'])
    return inside and c['c'] > p['h']

def green_after_doji(bars, i):
    """Prior was doji-like (body<0.5%), current is large green (>1.5%)."""
    if i < 1 or bars[i]['o'] <= 0 or bars[i-1]['o'] <= 0: return False
    p_body = abs(bars[i-1]['c']-bars[i-1]['o'])/bars[i-1]['o']*100
    c_body = abs(bars[i]['c']-bars[i]['o'])/bars[i]['o']*100
    return p_body < 0.5 and c_body > 1.5 and bars[i]['c'] > bars[i]['o']

def support_bounce(bars, i):
    """Current low touched/near 60-bar low, then closed in upper third of range."""
    if i < 60: return False
    low_60 = min(b['l'] for b in bars[i-60:i+1])
    cur = bars[i]
    if cur['o'] <= 0: return False
    near_low = abs((cur['l'] - low_60) / low_60 * 100) < 2
    rng = cur['h'] - cur['l']
    if rng <= 0: return False
    upper_third = (cur['c'] - cur['l']) / rng > 0.66
    return near_low and upper_third

def vol_thrust(bars, i):
    """Current vol > 3x avg of last 10, AND green close."""
    if i < 10: return False
    prior = [b['v'] for b in bars[i-10:i]]
    avg = sum(prior) / len(prior) if prior else 0
    return avg > 0 and bars[i]['v']/avg > 3.0 and bars[i]['c'] > bars[i]['o']

def hammer_reversal(bars, i):
    """Hammer-shape: long lower wick (>=2x body), small body, close near high."""
    if i < 0 or bars[i]['o'] <= 0: return False
    cur = bars[i]
    body = abs(cur['c']-cur['o'])/cur['o']*100
    body_bot = min(cur['c'], cur['o'])
    body_top = max(cur['c'], cur['o'])
    lw = (body_bot - cur['l'])/cur['o']*100
    uw = (cur['h'] - body_top)/cur['o']*100
    return lw >= body * 2 and lw >= 1.0 and uw < body and cur['c'] > cur['o']

def momentum_continuation(bars, i):
    """Last 2 bars all green with rising closes; current also green."""
    if i < 2: return False
    return (bars[i-2]['c'] > bars[i-2]['o']
            and bars[i-1]['c'] > bars[i-1]['o']
            and bars[i]['c'] > bars[i]['o']
            and bars[i]['c'] > bars[i-1]['c'] > bars[i-2]['c'])

def macro30_recovering(bars, i):
    """macro30 < 0 but price recovered from low: close > min low of last 30."""
    if i < 30: return False
    if bars[i-30]['c'] <= 0: return False
    m30 = (bars[i]['c']/bars[i-30]['c'] - 1) * 100
    if m30 >= 0: return False
    low_30 = min(b['l'] for b in bars[i-30:i+1])
    return bars[i]['c'] > low_30 * 1.02  # 2%+ above min

def vol_quiet_then_pop(bars, i):
    """Last 3 had small vol (each < avg of prior 10), current has large vol (> 2x avg)."""
    if i < 13: return False
    avg10 = sum(b['v'] for b in bars[i-13:i-3]) / 10
    if avg10 <= 0: return False
    last3_vols = [b['v'] for b in bars[i-3:i]]
    quiet = all(v < avg10 * 0.8 for v in last3_vols)
    pop = bars[i]['v'] > avg10 * 2.0
    return quiet and pop and bars[i]['c'] > bars[i]['o']

def deep_dump_basing(bars, i):
    """macro15 < -8% (sharp recent drop) AND last 3 bars consolidated within 1.5% range."""
    if i < 15: return False
    if bars[i-15]['c'] <= 0: return False
    m15 = (bars[i]['c']/bars[i-15]['c'] - 1) * 100
    if m15 > -8: return False
    last3 = bars[i-2:i+1]
    high3 = max(b['h'] for b in last3)
    low3 = min(b['l'] for b in last3)
    if low3 <= 0: return False
    range_pct = (high3/low3 - 1) * 100
    return range_pct < 1.5

def macro_v_shape(bars, i):
    """Drop then recovery: macro30 < 0 AND macro15 > macro30 (recovery underway)."""
    if i < 30: return False
    if bars[i-30]['c'] <= 0 or bars[i-15]['c'] <= 0: return False
    m30 = (bars[i]['c']/bars[i-30]['c'] - 1) * 100
    m15 = (bars[i]['c']/bars[i-15]['c'] - 1) * 100
    return m30 < -3 and m15 > m30 + 3  # m15 at least 3pp better than m30


SHAPES = {
    'false_breakdown_recovery': false_breakdown_recovery,
    'secondary_test_higher_low': secondary_test_higher_low,
    'vol_divergence_red': vol_divergence_red,
    'consolidation_after_drop': consolidation_after_drop,
    'inside_bar_break': inside_bar_break,
    'green_after_doji': green_after_doji,
    'support_bounce': support_bounce,
    'vol_thrust': vol_thrust,
    'hammer_reversal': hammer_reversal,
    'momentum_continuation': momentum_continuation,
    'macro30_recovering': macro30_recovering,
    'vol_quiet_then_pop': vol_quiet_then_pop,
    'deep_dump_basing': deep_dump_basing,
    'macro_v_shape': macro_v_shape,
}


# Build dataset — scan ALL bars (not just clean_break)
all_entries = []
for sym, tdat in master['tokens'].items():
    bars = tdat['bars']
    if len(bars) < 100: continue
    last_ts_per_shape = {name: -9999 for name in SHAPES}
    last_ts_any = -9999
    for i in range(60, len(bars) - 1):
        # Compute clean_break / 4combo coverage flags
        cb = is_clean_break(bars, i)
        c4 = is_4combo(bars, i)
        # Compute all shapes
        shapes = {name: fn(bars, i) for name, fn in SHAPES.items()}
        if not any(shapes.values()):
            continue
        # Cooldown across all shapes (5 min)
        if bars[i]['ts'] - last_ts_any < 300:
            continue
        last_ts_any = bars[i]['ts']
        result = simulate_bot(bars, i)
        if result is None: continue
        all_entries.append({
            'pnl': result, 'shapes': shapes, 'cb': cb, 'c4': c4,
            'outcome': 'WIN' if result > 5 else 'LOSS' if result < -5 else 'flat',
        })

n = len(all_entries)
print(f'Total entries (any shape fires): {n}')
print()


def evaluate(predicate):
    sub = [e for e in all_entries if predicate(e)]
    if not sub: return None
    sw = sum(1 for e in sub if e['outcome']=='WIN')
    sl = sum(1 for e in sub if e['outcome']=='LOSS')
    wr = sw/(sw+sl)*100 if (sw+sl) else 0
    return {'n': len(sub), 'w': sw, 'l': sl, 'wr': wr,
            'avg': sum(e['pnl'] for e in sub)/len(sub),
            'sum': sum(e['pnl'] for e in sub)}

def evaluate_marginal(predicate):
    """Stats for entries matching predicate AND NOT clean_break AND NOT 4combo."""
    sub = [e for e in all_entries if predicate(e) and not e['cb'] and not e['c4']]
    if not sub: return None
    sw = sum(1 for e in sub if e['outcome']=='WIN')
    sl = sum(1 for e in sub if e['outcome']=='LOSS')
    wr = sw/(sw+sl)*100 if (sw+sl) else 0
    return {'n': len(sub), 'w': sw, 'l': sl, 'wr': wr,
            'avg': sum(e['pnl'] for e in sub)/len(sub),
            'sum': sum(e['pnl'] for e in sub)}


# Single-shape evaluations
print('=== SINGLE SHAPES — standalone (any fires) ===')
print(f'{"shape":<28} {"n":>5} {"WR%":>5} {"avg%":>7} {"sum%":>7}')
print('-' * 65)
single = []
for name in SHAPES:
    res = evaluate(lambda e, n=name: e['shapes'][n])
    if not res or res['n'] < 50: continue
    single.append((name, res))
single.sort(key=lambda x: -x[1]['wr'])
for name, res in single:
    print(f'{name:<28} {res["n"]:>5} {res["wr"]:>4.1f}% {res["avg"]:>+5.2f}% {res["sum"]:>+5.0f}%')

print()
print('=== SINGLE SHAPES — MARGINAL (NOT cb AND NOT 4combo) ===')
print(f'{"shape":<28} {"n":>5} {"WR%":>5} {"avg%":>7} {"sum%":>7}')
print('-' * 65)
marginal = []
for name in SHAPES:
    res = evaluate_marginal(lambda e, n=name: e['shapes'][n])
    if not res or res['n'] < 50: continue
    marginal.append((name, res))
marginal.sort(key=lambda x: -x[1]['wr'])
for name, res in marginal:
    print(f'{name:<28} {res["n"]:>5} {res["wr"]:>4.1f}% {res["avg"]:>+5.2f}% {res["sum"]:>+5.0f}%')

# 2-combo on marginal
print()
print('=== TOP 2-COMBOS — MARGINAL (NOT cb AND NOT 4combo, n>=80, WR>=60%) ===')
print(f'{"combo":<55} {"n":>5} {"WR%":>5} {"avg%":>7} {"sum%":>7}')
print('-' * 90)
combo_marginal = []
for a, b in combinations(SHAPES.keys(), 2):
    res = evaluate_marginal(lambda e, x=a, y=b: e['shapes'][x] and e['shapes'][y])
    if not res or res['n'] < 80 or res['wr'] < 60: continue
    combo_marginal.append((f'{a} + {b}', res))
combo_marginal.sort(key=lambda x: -x[1]['wr'])
for tag, res in combo_marginal[:25]:
    print(f'{tag:<55} {res["n"]:>5} {res["wr"]:>4.1f}% {res["avg"]:>+5.2f}% {res["sum"]:>+5.0f}%')

# 3-combo on marginal
print()
print('=== TOP 3-COMBOS — MARGINAL (n>=50, WR>=65%) ===')
top_marg_shapes = [n for n, _ in marginal[:8]]
triple = []
for a, b, c in combinations(top_marg_shapes, 3):
    res = evaluate_marginal(lambda e, x=a, y=b, z=c: e['shapes'][x] and e['shapes'][y] and e['shapes'][z])
    if not res or res['n'] < 50 or res['wr'] < 65: continue
    triple.append((f'{a} + {b} + {c}', res))
triple.sort(key=lambda x: -x[1]['wr'])
print(f'{"combo":<70} {"n":>5} {"WR%":>5} {"avg%":>7} {"sum%":>7}')
print('-' * 105)
for tag, res in triple[:15]:
    print(f'{tag:<70} {res["n"]:>5} {res["wr"]:>4.1f}% {res["avg"]:>+5.2f}% {res["sum"]:>+5.0f}%')
