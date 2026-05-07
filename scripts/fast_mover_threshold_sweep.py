"""Threshold sweep on candidate fast-mover triggers — find variants
that hit WR >= 60% on the fast-mover cohort.

User criterion: WR < 60% is unacceptable.

Candidates:
  1. explosive_break-style: range_exp + cs5_vol + cs5_consec_green
  2. range_expansion: range_exp + vol confirmation
  3. momentum-tightened: build a tighter mc variant
  4. range_expansion + cum_3min momentum

Sweep multiple thresholds on each, only report variants with:
  - WR >= 60%
  - n_fires >= 50 (meaningful sample)
  - avg/trade > 0
"""
import json
import sys
from collections import defaultdict


def aggregate_5m(bars_1m):
    if not bars_1m:
        return []
    out = []
    grp = []
    grp_anchor = None
    for b in bars_1m:
        ts = int(b.get('ts', 0))
        anchor = ts - (ts % 300)
        if grp_anchor is None or anchor != grp_anchor:
            if grp:
                out.append({
                    'ts': grp_anchor,
                    'o': grp[0]['o'], 'c': grp[-1]['c'],
                    'h': max(x['h'] for x in grp),
                    'l': min(x['l'] for x in grp),
                    'v': sum(x.get('v') or 0 for x in grp),
                })
            grp = [b]
            grp_anchor = anchor
        else:
            grp.append(b)
    if grp:
        out.append({
            'ts': grp_anchor,
            'o': grp[0]['o'], 'c': grp[-1]['c'],
            'h': max(x['h'] for x in grp),
            'l': min(x['l'] for x in grp),
            'v': sum(x.get('v') or 0 for x in grp),
        })
    return out


def is_fast_mover_token(bars, fast_pct=10.0, fast_window=20, min_events=1):
    events = 0
    for i in range(len(bars) - fast_window):
        entry_p = bars[i]['c']
        if entry_p <= 0: continue
        max_gain = max((bars[j]['h']/entry_p-1)*100 for j in range(i+1, i+1+fast_window))
        if max_gain >= fast_pct:
            events += 1
            if events >= min_events: return True
    return False


def simulate_lifecycle(bars, entry_i, tp_pct=8.0, stop_pct=12.0, max_hold=60):
    entry_p = bars[entry_i].get('c', 0)
    if entry_p <= 0: return None
    horizon = bars[entry_i+1:min(len(bars), entry_i+1+max_hold)]
    if len(horizon) < 5: return None
    for b in horizon:
        h_pct = (b['h']/entry_p-1)*100
        l_pct = (b['l']/entry_p-1)*100
        if l_pct <= -stop_pct: return -stop_pct
        if h_pct >= tp_pct: return tp_pct
    last_close = horizon[-1]['c']
    return (last_close/entry_p-1)*100


def evaluate_predicate(fast_tokens, predicate):
    """Run predicate against fast tokens, return aggregate stats."""
    results = []
    for addr, info in fast_tokens.items():
        bars = info.get('bars') or []
        for i in range(35, len(bars) - 65):
            cur = bars[i]
            if cur['o'] <= 0: continue
            recent_bars = bars[max(0, i-60):i+1]
            try:
                if predicate(cur, recent_bars):
                    pnl = simulate_lifecycle(bars, i)
                    if pnl is not None:
                        results.append(pnl)
            except Exception:
                continue
    if not results:
        return None
    n = len(results)
    avg = sum(results) / n
    wins = sum(1 for r in results if r > 0)
    tps = sum(1 for r in results if r >= 7.9)
    stops = sum(1 for r in results if r <= -11.9)
    return {
        'n': n,
        'avg': avg,
        'wr': wins / n * 100,
        'tp_rate': tps / n * 100,
        'stop_rate': stops / n * 100,
    }


# ── Predicates ────────────────────────────────────────────────────

def make_explosive_break(range_thr, cs5_vol_thr, cs5_green_thr,
                          cum3_thr=None):
    def pred(cur, recent_bars):
        if len(recent_bars) < 60:
            return False
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        # Range expansion
        cur_range_pct = (cur['h']-cur['l'])/cur['o']*100
        last5_ranges = [(b['h']-b['l'])/b['o']*100 for b in recent_bars[-6:-1] if b['o']>0]
        if len(last5_ranges) < 5: return False
        avg5 = sum(last5_ranges)/5
        if avg5 <= 0 or cur_range_pct/avg5 < range_thr:
            return False
        # 5m TF
        cs5 = aggregate_5m(recent_bars[-90:])
        if len(cs5) < 6: return False
        cur5 = cs5[-1]
        if cur5['o'] <= 0 or cur5['c'] <= cur5['o']:
            return False
        # consec green 5m
        cgrn = 0
        for b in reversed(cs5[-5:]):
            if b['c'] > b['o']: cgrn += 1
            else: break
        if cgrn < cs5_green_thr:
            return False
        # 5m vol spike
        prior_vols = [b.get('v',0) for b in cs5[-6:-1]]
        if len(prior_vols) < 5: return False
        avg5v = sum(prior_vols)/5
        if avg5v <= 0 or cur5.get('v',0)/avg5v < cs5_vol_thr:
            return False
        # Optional momentum confirm
        if cum3_thr is not None:
            if recent_bars[-4]['c'] <= 0: return False
            cum3 = (cur['c']/recent_bars[-4]['c']-1)*100
            if cum3 < cum3_thr:
                return False
        return True
    return pred


def make_range_expansion(range_thr, vol_thr, vol_window=30,
                          cum3_thr=None, hh_thr=None):
    def pred(cur, recent_bars):
        if len(recent_bars) < 35: return False
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        cur_range_pct = (cur['h']-cur['l'])/cur['o']*100
        last5_ranges = [(b['h']-b['l'])/b['o']*100 for b in recent_bars[-6:-1] if b['o']>0]
        if len(last5_ranges) < 5: return False
        avg5 = sum(last5_ranges)/5
        if avg5 <= 0 or cur_range_pct/avg5 < range_thr:
            return False
        # vol spike vs N-window avg
        prior_vols = [b.get('v',0) for b in recent_bars[-(vol_window+1):-1]]
        if not prior_vols: return False
        avgv = sum(prior_vols)/len(prior_vols)
        if avgv <= 0 or cur.get('v',0)/avgv < vol_thr:
            return False
        if cum3_thr is not None:
            if recent_bars[-4]['c'] <= 0: return False
            cum3 = (cur['c']/recent_bars[-4]['c']-1)*100
            if cum3 < cum3_thr:
                return False
        if hh_thr is not None:
            last5 = recent_bars[-5:]
            hh = sum(1 for j in range(1,5) if last5[j]['h'] > last5[j-1]['h'])
            if hh < hh_thr:
                return False
        return True
    return pred


def make_mc_variant(consec_green_thr, vol_thr, vol_window=30,
                    body_thr=None, cum3_thr=None):
    """Tighter momentum_continuation variants."""
    def pred(cur, recent_bars):
        if len(recent_bars) < 35: return False
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        # consec green
        for k in range(1, consec_green_thr+1):
            b = recent_bars[-k]
            if b['o'] <= 0 or b['c'] <= b['o']:
                return False
        # vol
        prior_vols = [b.get('v',0) for b in recent_bars[-(vol_window+1):-1]]
        if not prior_vols: return False
        avgv = sum(prior_vols)/len(prior_vols)
        if avgv <= 0 or cur.get('v',0)/avgv < vol_thr:
            return False
        # Optional body threshold
        if body_thr is not None:
            body_pct = (cur['c']-cur['o'])/cur['o']*100
            if body_pct < body_thr:
                return False
        if cum3_thr is not None:
            if recent_bars[-4]['c'] <= 0: return False
            cum3 = (cur['c']/recent_bars[-4]['c']-1)*100
            if cum3 < cum3_thr:
                return False
        return True
    return pred


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})
    fast_tokens = {addr: info for addr, info in tokens.items()
                   if (info.get('bars') and len(info['bars']) >= 100
                       and is_fast_mover_token(info['bars']))}
    print(f"Fast-mover cohort: {len(fast_tokens)} tokens")
    print()

    # Sweep variants
    variants = []

    # explosive_break sweep
    for r in (2.5, 3.0, 4.0, 5.0):
        for v5 in (2.5, 3.0, 4.0):
            for cg5 in (2, 3):
                for cum3 in (None, 0.0, 1.0):
                    label = f"exp(r>={r},v5>={v5},cg5>={cg5},cum3>={cum3})"
                    variants.append((label, make_explosive_break(r, v5, cg5, cum3)))

    # range_expansion sweep
    for r in (3.0, 4.0, 5.0, 6.0):
        for v in (1.5, 2.0, 3.0):
            for cum3 in (None, 0.5, 1.0):
                for hh in (None, 2, 3):
                    label = f"re(r>={r},v>={v},cum3>={cum3},hh>={hh})"
                    variants.append((label, make_range_expansion(r, v, 30, cum3, hh)))

    # mc tightened variants
    for cg in (4, 5, 6):
        for v in (1.5, 2.0, 3.0):
            for body in (None, 1.0, 2.0):
                label = f"mc(cg>={cg},v>={v},body>={body})"
                variants.append((label, make_mc_variant(cg, v, 30, body)))

    print(f"Sweeping {len(variants)} variants...")
    print()

    results = []
    for label, pred in variants:
        stats = evaluate_predicate(fast_tokens, pred)
        if stats and stats['n'] >= 30:
            results.append((label, stats))

    # Filter to WR >= 60%
    print(f"=== Variants with WR >= 60% AND n >= 50 (sorted by avg/trade) ===")
    above60 = [(l, s) for l, s in results if s['wr'] >= 60 and s['n'] >= 50 and s['avg'] > 0]
    above60.sort(key=lambda x: -x[1]['avg'])
    print(f"\n{'variant':<55} {'n':>5} {'avg%':>6} {'WR%':>5} {'TP%':>5} {'Stop%':>6}")
    for label, s in above60[:30]:
        print(f"{label:<55} {s['n']:>5} {s['avg']:>+5.2f} {s['wr']:>4.1f} {s['tp_rate']:>4.1f} {s['stop_rate']:>5.1f}")

    print()
    print(f"=== Top by WR alone (n >= 50, all candidates) ===")
    by_wr = sorted([(l, s) for l, s in results if s['n'] >= 50],
                   key=lambda x: -x[1]['wr'])
    print(f"\n{'variant':<55} {'n':>5} {'avg%':>6} {'WR%':>5}")
    for label, s in by_wr[:20]:
        print(f"{label:<55} {s['n']:>5} {s['avg']:>+5.2f} {s['wr']:>4.1f}")


if __name__ == "__main__":
    main()
