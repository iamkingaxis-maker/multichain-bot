"""Round-3 fast-mover pattern miner.

Tests 12+ candidate patterns orthogonal to the 5 already-shipped
fast-mover triggers. Reports WR, avg, TP, stop% for each on
fast-mover cohort with TP=8% / SL=12% lifecycle.
"""
import json


def is_fast(bars, fast_pct=10, fast_window=20):
    for i in range(len(bars) - fast_window):
        ep = bars[i]['c']
        if ep <= 0:
            continue
        mg = max((bars[j]['h']/ep - 1) * 100
                 for j in range(i + 1, i + 1 + fast_window))
        if mg >= fast_pct:
            return True
    return False


def sim_lifecycle(bars, i, tp=8, sl=12, mh=60):
    ep = bars[i].get('c', 0)
    if ep <= 0:
        return None
    horizon = bars[i+1:min(len(bars), i+1+mh)]
    if len(horizon) < 5:
        return None
    for b in horizon:
        if (b['l']/ep - 1) * 100 <= -sl:
            return -sl
        if (b['h']/ep - 1) * 100 >= tp:
            return tp
    return (horizon[-1]['c']/ep - 1) * 100


def agg_5m(bars):
    out, grp, anchor = [], [], None
    for b in bars:
        ts = int(b.get('ts', 0))
        a = ts - (ts % 300)
        if anchor is None or a != anchor:
            if grp:
                out.append({
                    'ts': anchor, 'o': grp[0]['o'], 'c': grp[-1]['c'],
                    'h': max(x['h'] for x in grp),
                    'l': min(x['l'] for x in grp),
                    'v': sum(x.get('v') or 0 for x in grp),
                })
            grp = [b]
            anchor = a
        else:
            grp.append(b)
    if grp:
        out.append({
            'ts': anchor, 'o': grp[0]['o'], 'c': grp[-1]['c'],
            'h': max(x['h'] for x in grp),
            'l': min(x['l'] for x in grp),
            'v': sum(x.get('v') or 0 for x in grp),
        })
    return out


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})
    fast = {a: i for a, i in tokens.items()
            if i.get('bars') and len(i['bars']) >= 100 and is_fast(i['bars'])}
    print(f'Fast cohort: {len(fast)}')
    print()

    candidates = []

    # r1: 3 bars increasing vol + green + body >= 1%
    def r1(bars, i, recent):
        if i < 3:
            return False
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        body_pct = (cur['c']-cur['o'])/cur['o']*100
        if body_pct < 1.0:
            return False
        v0 = bars[i-2].get('v', 0)
        v1 = bars[i-1].get('v', 0)
        v2 = cur.get('v', 0)
        if not (v2 > v1 > v0):
            return False
        if v0 == 0 or v2/v0 < 2.0:
            return False
        return True
    candidates.append(('vol_3bar_accel_2x_body1pct', r1))

    # r2: 5m breakout — cur 5m close > max high of prior 5 5m bars
    def r2(bars, i, recent):
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        cs5 = agg_5m(recent[-90:])
        if len(cs5) < 6:
            return False
        cur5 = cs5[-1]
        if cur5['o'] <= 0 or cur5['c'] <= cur5['o']:
            return False
        prior5 = [b['h'] for b in cs5[-6:-1]]
        if cur5['c'] <= max(prior5):
            return False
        return True
    candidates.append(('5m_breakout_5bar_high', r2))

    # r3: Multi-TF align — 1m green + 5m_consec_green >= 2 + vol >= 2x
    def r3(bars, i, recent):
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        cs5 = agg_5m(recent[-90:])
        if len(cs5) < 3:
            return False
        if cs5[-1]['c'] <= cs5[-1]['o']:
            return False
        if cs5[-2]['o'] <= 0 or cs5[-2]['c'] <= cs5[-2]['o']:
            return False
        if len(recent) < 31:
            return False
        p30 = recent[-31:-1]
        vols = [b.get('v', 0) for b in p30 if b.get('v') is not None]
        if not vols:
            return False
        av = sum(vols)/len(vols)
        if av <= 0 or cur.get('v', 0)/av < 2.0:
            return False
        return True
    candidates.append(('1m_5m_align_vol2x', r3))

    # r4: Big body green no upper wick
    def r4(bars, i, recent):
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        body = cur['c'] - cur['o']
        upper_wick = cur['h'] - cur['c']
        if upper_wick > body * 0.2:
            return False
        if (body/cur['o'])*100 < 2.0:
            return False
        if len(recent) < 31:
            return False
        p30 = recent[-31:-1]
        vols = [b.get('v', 0) for b in p30 if b.get('v') is not None]
        if not vols:
            return False
        av = sum(vols)/len(vols)
        if av <= 0 or cur.get('v', 0)/av < 1.5:
            return False
        return True
    candidates.append(('clean_body_no_upper_wick', r4))

    # r5: 30-bar high break + green
    def r5(bars, i, recent):
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        if len(recent) < 31:
            return False
        if cur['c'] <= max(b['h'] for b in recent[-31:-1]):
            return False
        return True
    candidates.append(('30bar_high_break', r5))

    # r6: 30-bar break + vol
    def r6(bars, i, recent):
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        if len(recent) < 31:
            return False
        if cur['c'] <= max(b['h'] for b in recent[-31:-1]):
            return False
        vols = [b.get('v', 0) for b in recent[-31:-1] if b.get('v') is not None]
        if not vols:
            return False
        av = sum(vols)/len(vols)
        if av <= 0 or cur.get('v', 0)/av < 1.5:
            return False
        return True
    candidates.append(('30bar_break_vol1.5x', r6))

    # r7: Greens-progression: green count last5 > prior5
    def r7(bars, i, recent):
        if len(recent) < 11:
            return False
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        last5 = recent[-5:]
        prior5 = recent[-10:-5]
        g_l = sum(1 for b in last5 if b['o']>0 and b['c']>b['o'])
        g_p = sum(1 for b in prior5 if b['o']>0 and b['c']>b['o'])
        if g_l < 4 or g_l <= g_p:
            return False
        p30 = recent[-31:-1]
        vols = [b.get('v', 0) for b in p30 if b.get('v') is not None]
        if not vols:
            return False
        av = sum(vols)/len(vols)
        if av <= 0 or cur.get('v', 0)/av < 1.3:
            return False
        return True
    candidates.append(('green_progression_4plus', r7))

    # r8: Coil-break: prior 4 tight, current 3x expansion
    def r8(bars, i, recent):
        if len(recent) < 6:
            return False
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        cur_r = (cur['h']-cur['l'])/cur['o']*100
        if cur_r < 2.0:
            return False
        last4 = recent[-5:-1]
        last4_ranges = [(b['h']-b['l'])/b['o']*100 for b in last4 if b['o']>0]
        if len(last4_ranges) < 4:
            return False
        avg4 = sum(last4_ranges)/4
        if avg4 >= 1.0:
            return False
        if cur_r < 3 * avg4:
            return False
        return True
    candidates.append(('coil_break_tight4_3x', r8))

    # r9: 5m strong + vol velocity
    def r9(bars, i, recent):
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        cs5 = agg_5m(recent[-90:])
        if len(cs5) < 2:
            return False
        cur5 = cs5[-1]
        if cur5['o'] <= 0 or cur5['c'] <= cur5['o']:
            return False
        body5 = (cur5['c']-cur5['o'])/cur5['o']*100
        if body5 < 3.0:
            return False
        if i < 3:
            return False
        v0 = bars[i-2].get('v', 0)
        v1 = bars[i-1].get('v', 0)
        v2 = cur.get('v', 0)
        if not (v2 > v1 > v0):
            return False
        return True
    candidates.append(('5m_3pct_vol_velocity', r9))

    # r10: Lower-wick reversal
    def r10(bars, i, recent):
        if len(recent) < 2:
            return False
        cur = recent[-1]
        p1 = recent[-2]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        if p1['o'] <= 0 or p1['c'] >= p1['o']:
            return False
        p1_body = p1['o'] - p1['c']
        p1_lw = p1['c'] - p1['l']
        if p1_lw < p1_body * 1.5:
            return False
        if len(recent) < 31:
            return False
        p30 = recent[-31:-1]
        vols = [b.get('v', 0) for b in p30 if b.get('v') is not None]
        if not vols:
            return False
        av = sum(vols)/len(vols)
        if av <= 0 or cur.get('v', 0)/av < 1.5:
            return False
        return True
    candidates.append(('lower_wick_reversal_vol', r10))

    # r11: 3 consec green each body >= 1%
    def r11(bars, i, recent):
        if len(recent) < 4:
            return False
        for k in (1, 2, 3):
            b = recent[-k]
            if b['o'] <= 0 or b['c'] <= b['o']:
                return False
            if (b['c']-b['o'])/b['o']*100 < 1.0:
                return False
        p30 = recent[-31:-1]
        vols = [b.get('v', 0) for b in p30 if b.get('v') is not None]
        if not vols:
            return False
        av = sum(vols)/len(vols)
        cur = recent[-1]
        if av <= 0 or cur.get('v', 0)/av < 1.5:
            return False
        return True
    candidates.append(('3green_body1pct_each_vol', r11))

    # r12: cum_5min strong + 5m green
    def r12(bars, i, recent):
        cur = recent[-1]
        if cur['o'] <= 0 or cur['c'] <= cur['o']:
            return False
        if len(recent) < 6:
            return False
        if recent[-6]['c'] <= 0:
            return False
        cum5 = (cur['c']/recent[-6]['c'] - 1) * 100
        if cum5 < 3.0:
            return False
        cs5 = agg_5m(recent[-90:])
        if len(cs5) < 2:
            return False
        if cs5[-1]['c'] <= cs5[-1]['o']:
            return False
        return True
    candidates.append(('cum5_3pct_5m_green', r12))

    print(f'Testing {len(candidates)} candidates on n={len(fast)} fast tokens...')
    print()
    print(f"{'name':<32} {'n':>5} {'WR%':>5} {'avg%':>6} {'TP%':>5} {'Stop%':>6} {'verdict':>4}")
    for name, pred in candidates:
        results = []
        for addr, info in fast.items():
            bars = info.get('bars') or []
            for i in range(35, len(bars) - 65):
                cur = bars[i]
                rb = bars[max(0, i-60):i+1]
                try:
                    if pred(bars, i, rb):
                        pnl = sim_lifecycle(bars, i)
                        if pnl is not None:
                            results.append(pnl)
                except Exception:
                    pass
        if results:
            n = len(results)
            avg = sum(results)/n
            wr = sum(1 for r in results if r > 0)/n*100
            tp_h = sum(1 for r in results if r >= 7.9)/n*100
            st = sum(1 for r in results if r <= -11.9)/n*100
            marker = 'PASS' if wr >= 60 else 'FAIL'
            print(f"{name:<32} {n:>5} {wr:>4.1f}% {avg:>+5.2f}% {tp_h:>4.1f}% {st:>5.1f}% {marker:>4}")
        else:
            print(f"{name:<32} 0 fires")


if __name__ == "__main__":
    main()
