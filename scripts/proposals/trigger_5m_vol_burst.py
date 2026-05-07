"""trigger_5m_vol_burst — 5m volume burst + 2+ consec green 5m.

Mined feature: cs5_vol_spike >= 3.25 + cs5_consec_green >= 2 = 38-42%
fast-win share. Catches 5m-timeframe momentum where the latest 5m bar
has 3x volume vs prior 5 5m bars AND we have 2+ consecutive green 5m.

Mechanism: this is fundamentally a 5m TF trigger. While 1m might look
choppy, the 5m structure shows clear momentum with volume confirming.

Different from momentum_continuation (1m TF) and range_expansion (single
1m candle).
"""
NAME = "trigger_5m_vol_burst"
DESCRIPTION = "ENTER on 5m vol_spike >= 2.5x + 2+ consec green 5m bars"
NEEDS_OHLC = True


def _aggregate_5m(bars_1m):
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


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 60:
        return False
    cs5 = _aggregate_5m(recent_bars[-90:])
    if len(cs5) < 6:
        return False
    cur5 = cs5[-1]
    if cur5['o'] <= 0:
        return False
    # Current 5m must be green
    if cur5['c'] <= cur5['o']:
        return False
    # 2+ consec green 5m
    if cs5[-2]['o'] <= 0 or cs5[-2]['c'] <= cs5[-2]['o']:
        return False
    # Vol spike: cur 5m vol >= 2.5x avg of prior 5 5m bars
    prior_5m_vols = [b.get('v', 0) for b in cs5[-6:-1]]
    if len(prior_5m_vols) < 5:
        return False
    avg5_5m = sum(prior_5m_vols) / 5
    if avg5_5m <= 0:
        return False
    if cur5.get('v', 0) / avg5_5m < 2.5:
        return False
    return True
