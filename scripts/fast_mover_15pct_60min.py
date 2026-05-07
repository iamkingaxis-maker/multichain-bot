"""Mine patterns on a +15%-in-60min outcome cohort.

Different fast-mover definition than rounds 1-3 (which used +10% in 20min).
Bigger but slower moves. Surfaces patterns characteristic of tokens that
make sustained meaningful runs vs quick bounces.

Lifecycle simulation still uses bot's actual TP1=8% / SL=12% (since that's
what bot trades). Only the COHORT (which tokens we test on) changes.
"""
import json


def is_big_mover(bars, fast_pct=15.0, fast_window=60):
    """Token had >= 1 instance of fast_pct% gain within fast_window bars."""
    for i in range(len(bars) - fast_window):
        ep = bars[i]['c']
        if ep <= 0: continue
        mg = max((bars[j]['h']/ep - 1) * 100
                 for j in range(i + 1, i + 1 + fast_window))
        if mg >= fast_pct:
            return True
    return False


def sim_lifecycle(bars, i, tp=8, sl=12, mh=60):
    ep = bars[i].get('c', 0)
    if ep <= 0: return None
    horizon = bars[i+1:min(len(bars), i+1+mh)]
    if len(horizon) < 5: return None
    for b in horizon:
        if (b['l']/ep-1)*100 <= -sl: return -sl
        if (b['h']/ep-1)*100 >= tp: return tp
    return (horizon[-1]['c']/ep-1)*100


def agg_5m(bars):
    out, grp, anchor = [], [], None
    for b in bars:
        ts = int(b.get('ts', 0))
        a = ts - (ts % 300)
        if anchor is None or a != anchor:
            if grp:
                out.append({'ts': anchor, 'o': grp[0]['o'], 'c': grp[-1]['c'],
                            'h': max(x['h'] for x in grp), 'l': min(x['l'] for x in grp),
                            'v': sum(x.get('v') or 0 for x in grp)})
            grp = [b]; anchor = a
        else:
            grp.append(b)
    if grp:
        out.append({'ts': anchor, 'o': grp[0]['o'], 'c': grp[-1]['c'],
                    'h': max(x['h'] for x in grp), 'l': min(x['l'] for x in grp),
                    'v': sum(x.get('v') or 0 for x in grp)})
    return out


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})

    # Try multiple cohort definitions
    cohorts = [
        ("15pct_60min", lambda b: is_big_mover(b, 15, 60)),
        ("20pct_90min", lambda b: is_big_mover(b, 20, 90)),
        ("12pct_30min", lambda b: is_big_mover(b, 12, 30)),
    ]

    for cohort_name, cohort_fn in cohorts:
        print()
        print("=" * 90)
        print(f"COHORT: {cohort_name}")
        print("=" * 90)

        cohort_tokens = {a: i for a, i in tokens.items()
                         if i.get('bars') and len(i['bars']) >= 100 and cohort_fn(i['bars'])}
        print(f"Tokens in cohort: {len(cohort_tokens)}")
        if len(cohort_tokens) < 30:
            print("Cohort too small, skipping.")
            continue

        # Run a battery of candidate patterns from prior rounds
        # plus a few new angles
        candidates = []

        # Already shipped (verify they work on this cohort)
        def shipped_mc(bars, i, r):
            if len(r) < 31: return False
            for k in (1,2,3,4):
                b = r[-k]
                if b['o']<=0 or b['c']<=b['o']: return False
            cur = r[-1]
            p30 = r[-31:-1]
            vols = [b.get('v',0) for b in p30 if b.get('v') is not None]
            if not vols: return False
            av = sum(vols)/len(vols)
            if av <= 0 or cur.get('v',0)/av < 1.5: return False
            return True
        candidates.append(('SHIPPED_mc', shipped_mc))

        def shipped_6of7(bars, i, r):
            if len(r) < 31: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            last7 = r[-7:]
            g = sum(1 for b in last7 if b['o']>0 and b['c']>b['o'])
            if g < 6: return False
            p30 = r[-31:-1]
            vols = [b.get('v',0) for b in p30 if b.get('v') is not None]
            if not vols: return False
            av = sum(vols)/len(vols)
            if av <= 0 or cur.get('v',0)/av < 1.5: return False
            return True
        candidates.append(('SHIPPED_6of7', shipped_6of7))

        # NEW candidates targeting bigger moves

        # b1: Strong cum_10min momentum + green
        def b1(bars, i, r):
            if len(r) < 11: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            if r[-11]['c']<=0: return False
            if (cur['c']/r[-11]['c']-1)*100 < 5.0: return False
            return True
        candidates.append(('cum10_>=5_green', b1))

        def b2(bars, i, r):
            if len(r) < 11: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            if r[-11]['c']<=0: return False
            if (cur['c']/r[-11]['c']-1)*100 < 8.0: return False
            return True
        candidates.append(('cum10_>=8_green', b2))

        # b3: 5m strong + 1m alignment
        def b3(bars, i, r):
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            cs5 = agg_5m(r[-90:])
            if len(cs5) < 4: return False
            cgrn = 0
            for b in reversed(cs5[-5:]):
                if b['c']>b['o']: cgrn += 1
                else: break
            if cgrn < 3: return False
            if len(r) < 31: return False
            p30 = r[-31:-1]
            vols = [b.get('v',0) for b in p30 if b.get('v') is not None]
            if not vols: return False
            av = sum(vols)/len(vols)
            if av <= 0 or cur.get('v',0)/av < 1.5: return False
            return True
        candidates.append(('5m_3green_vol1.5', b3))

        # b4: Strong body + vol velocity
        def b4(bars, i, r):
            if i < 3: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            body_pct = (cur['c']-cur['o'])/cur['o']*100
            if body_pct < 2.0: return False
            v0 = bars[i-2].get('v',0)
            v1 = bars[i-1].get('v',0)
            v2 = cur.get('v',0)
            if not (v2 > v1 > v0): return False
            return True
        candidates.append(('body2pct_vol_accel', b4))

        # b5: Big cum_30min recovery
        def b5(bars, i, r):
            if len(r) < 31: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            if r[-31]['c']<=0: return False
            if (cur['c']/r[-31]['c']-1)*100 < 10.0: return False
            return True
        candidates.append(('cum30_>=10_green', b5))

        # b6: HH 8+ in last 10 (very strong trend)
        def b6(bars, i, r):
            if len(r) < 10: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            last10 = r[-10:]
            hh = sum(1 for j in range(1,10) if last10[j]['h']>last10[j-1]['h'])
            if hh < 8: return False
            return True
        candidates.append(('hh10_>=8', b6))

        # b7: 5m close > 10-bar 5m high (5m breakout) + 1m green
        def b7(bars, i, r):
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            cs5 = agg_5m(r[-90:])
            if len(cs5) < 11: return False
            cur5 = cs5[-1]
            if cur5['o']<=0 or cur5['c']<=cur5['o']: return False
            if cur5['c'] <= max(b['h'] for b in cs5[-11:-1]): return False
            return True
        candidates.append(('5m_break_10bar_high', b7))

        # b8: cum_5min strong + body >= 1%
        def b8(bars, i, r):
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            body_pct = (cur['c']-cur['o'])/cur['o']*100
            if body_pct < 1.0: return False
            if len(r) < 6: return False
            if r[-6]['c']<=0: return False
            cum5 = (cur['c']/r[-6]['c']-1)*100
            if cum5 < 4.0: return False
            return True
        candidates.append(('cum5_>=4_body1pct', b8))

        # b9: 5+ green in 7 + body >= 2%
        def b9(bars, i, r):
            if len(r) < 7: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            body_pct = (cur['c']-cur['o'])/cur['o']*100
            if body_pct < 2.0: return False
            last7 = r[-7:]
            g = sum(1 for b in last7 if b['o']>0 and b['c']>b['o'])
            if g < 5: return False
            return True
        candidates.append(('5green_in_7_body2pct', b9))

        # b10: range expansion 3x + cum_5min >= 2%
        def b10(bars, i, r):
            if len(r) < 6: return False
            cur = r[-1]
            if cur['o']<=0 or cur['c']<=cur['o']: return False
            cur_r = (cur['h']-cur['l'])/cur['o']*100
            last5_r = [(b['h']-b['l'])/b['o']*100 for b in r[-6:-1] if b['o']>0]
            if len(last5_r) < 5: return False
            avg5_r = sum(last5_r)/5
            if avg5_r <= 0 or cur_r/avg5_r < 3.0: return False
            if r[-6]['c']<=0: return False
            cum5 = (cur['c']/r[-6]['c']-1)*100
            if cum5 < 2.0: return False
            return True
        candidates.append(('range3x_cum5_>=2', b10))

        print(f"\nTesting {len(candidates)} patterns...")
        print(f"{'name':<28} {'n':>5} {'WR%':>5} {'avg%':>6} {'TP%':>5} {'Stop%':>6} {'verdict':>5}")
        for name, pred in candidates:
            results = []
            for addr, info in cohort_tokens.items():
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
                marker = 'PASS' if wr >= 60 else 'fail'
                print(f"{name:<28} {n:>5} {wr:>4.1f}% {avg:>+5.2f}% {tp_h:>4.1f}% {st:>5.1f}% {marker:>5}")


if __name__ == "__main__":
    main()
