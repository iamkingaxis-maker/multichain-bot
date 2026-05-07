"""Round-3b — tighten the close-misses from round-3 to push WR above 60%."""
import json


def is_fast(bars, fast_pct=10, fast_window=20):
    for i in range(len(bars) - fast_window):
        ep = bars[i]['c']
        if ep <= 0:
            continue
        mg = max((bars[j]['h']/ep - 1) * 100 for j in range(i+1, i+1+fast_window))
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
    fast = {a: i for a, i in data.get('tokens', {}).items()
            if i.get('bars') and len(i['bars']) >= 100 and is_fast(i['bars'])}
    print(f'Fast cohort: {len(fast)}')
    print()

    candidates = []

    # 30-bar break tightened: + greens >= 4 in last 7 + body >= 1%
    def t1(bars, i, r):
        cur = r[-1]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        if (cur['c']-cur['o'])/cur['o']*100 < 1.0: return False
        if len(r)<31: return False
        if cur['c'] <= max(b['h'] for b in r[-31:-1]): return False
        last7 = r[-7:]
        if sum(1 for b in last7 if b['o']>0 and b['c']>b['o']) < 4: return False
        vols = [b.get('v',0) for b in r[-31:-1] if b.get('v') is not None]
        av = sum(vols)/max(1,len(vols))
        if av<=0 or cur.get('v',0)/av < 1.5: return False
        return True
    candidates.append(('30bar_break_4g_body1pct_vol1.5', t1))

    # 30-bar break tightened: vol >= 2x + 5m green
    def t2(bars, i, r):
        cur = r[-1]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        if len(r)<31: return False
        if cur['c'] <= max(b['h'] for b in r[-31:-1]): return False
        cs5 = agg_5m(r[-90:])
        if len(cs5)<2: return False
        if cs5[-1]['o']<=0 or cs5[-1]['c']<=cs5[-1]['o']: return False
        vols = [b.get('v',0) for b in r[-31:-1] if b.get('v') is not None]
        av = sum(vols)/max(1,len(vols))
        if av<=0 or cur.get('v',0)/av < 2.0: return False
        return True
    candidates.append(('30bar_break_5m_green_vol2x', t2))

    # 1m+5m align: stricter — 5m_consec_green >= 2 + 1m body >= 1.5% + vol 2x
    def t3(bars, i, r):
        cur = r[-1]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        if (cur['c']-cur['o'])/cur['o']*100 < 1.5: return False
        cs5 = agg_5m(r[-90:])
        if len(cs5)<3: return False
        if cs5[-1]['o']<=0 or cs5[-1]['c']<=cs5[-1]['o']: return False
        if cs5[-2]['o']<=0 or cs5[-2]['c']<=cs5[-2]['o']: return False
        if len(r)<31: return False
        vols = [b.get('v',0) for b in r[-31:-1] if b.get('v') is not None]
        av = sum(vols)/max(1,len(vols))
        if av<=0 or cur.get('v',0)/av < 2.0: return False
        return True
    candidates.append(('1m5m_align_body1.5_vol2x', t3))

    # 5m breakout + 1m cum_3min >= +1%
    def t4(bars, i, r):
        cur = r[-1]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        cs5 = agg_5m(r[-90:])
        if len(cs5)<6: return False
        cur5 = cs5[-1]
        if cur5['o']<=0 or cur5['c']<=cur5['o']: return False
        if cur5['c'] <= max(b['h'] for b in cs5[-6:-1]): return False
        if len(r)<4: return False
        if r[-4]['c']<=0: return False
        cum3 = (cur['c']/r[-4]['c']-1)*100
        if cum3 < 1.0: return False
        return True
    candidates.append(('5m_breakout_cum3_1pct', t4))

    # 5m breakout + vol_spike + 1m green
    def t5(bars, i, r):
        cur = r[-1]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        cs5 = agg_5m(r[-90:])
        if len(cs5)<6: return False
        cur5 = cs5[-1]
        if cur5['o']<=0 or cur5['c']<=cur5['o']: return False
        if cur5['c'] <= max(b['h'] for b in cs5[-6:-1]): return False
        if len(r)<31: return False
        vols = [b.get('v',0) for b in r[-31:-1] if b.get('v') is not None]
        av = sum(vols)/max(1,len(vols))
        if av<=0 or cur.get('v',0)/av < 2.0: return False
        return True
    candidates.append(('5m_breakout_vol2x_green', t5))

    # Lower wick reversal + cum_3min >= 0
    def t6(bars, i, r):
        if len(r)<4: return False
        cur = r[-1]; p1 = r[-2]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        if p1['o']<=0 or p1['c']>=p1['o']: return False
        if (p1['c'] - p1['l']) < (p1['o']-p1['c']) * 2.0: return False  # 2x lower wick
        if r[-4]['c']<=0: return False
        if (cur['c']/r[-4]['c']-1)*100 < 0.5: return False
        if len(r)<31: return False
        vols = [b.get('v',0) for b in r[-31:-1] if b.get('v') is not None]
        av = sum(vols)/max(1,len(vols))
        if av<=0 or cur.get('v',0)/av < 1.5: return False
        return True
    candidates.append(('lower_wick_2x_cum3_0.5', t6))

    # Coil break tighter: prior 6 tight (avg < 0.8%), current 4x
    def t7(bars, i, r):
        if len(r)<7: return False
        cur = r[-1]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        cur_r = (cur['h']-cur['l'])/cur['o']*100
        if cur_r < 2.0: return False
        last6 = r[-7:-1]
        last6_ranges = [(b['h']-b['l'])/b['o']*100 for b in last6 if b['o']>0]
        if len(last6_ranges)<6: return False
        avg6 = sum(last6_ranges)/6
        if avg6 >= 0.8: return False
        if cur_r < 4*avg6: return False
        # vol >= 2x
        if len(r)<31: return False
        vols = [b.get('v',0) for b in r[-31:-1] if b.get('v') is not None]
        av = sum(vols)/max(1,len(vols))
        if av<=0 or cur.get('v',0)/av < 2.0: return False
        return True
    candidates.append(('coil_6tight_break_4x_vol2x', t7))

    # Multi-feature: 5m green strong + 1m green + 5+ HH
    def t8(bars, i, r):
        cur = r[-1]
        if cur['o']<=0 or cur['c']<=cur['o']: return False
        cs5 = agg_5m(r[-90:])
        if len(cs5)<2: return False
        cur5 = cs5[-1]
        if cur5['o']<=0 or cur5['c']<=cur5['o']: return False
        body5 = (cur5['c']-cur5['o'])/cur5['o']*100
        if body5 < 2.0: return False
        if len(r)<10: return False
        last10 = r[-10:]
        hh = sum(1 for j in range(1,10) if last10[j]['h']>last10[j-1]['h'])
        if hh < 5: return False
        return True
    candidates.append(('5m_body2pct_hh5', t8))

    print(f'Testing {len(candidates)} tightened candidates...')
    print()
    print(f"{'name':<35} {'n':>5} {'WR%':>5} {'avg%':>6} {'TP%':>5} {'Stop%':>6} {'verdict':>4}")
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
            print(f"{name:<35} {n:>5} {wr:>4.1f}% {avg:>+5.2f}% {tp_h:>4.1f}% {st:>5.1f}% {marker:>4}")


if __name__ == "__main__":
    main()
