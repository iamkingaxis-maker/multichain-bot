"""trigger_explosive_break — top 3-feature combo from miner.

Combines all three top discriminators:
  - range_expansion_5 >= 2.5 (current 1m range 2.5x avg of last 5)
  - cs5_vol_spike >= 2.5 (5m vol 2.5x avg of prior 5)
  - cs5_consec_green >= 2 (2+ consec green 5m bars)

The miner showed this 3-way combo at 48.7% fast-win share, lift 2.39x.
Sample n=115 — small but distinctive.

Mechanism: simultaneous expansion across timeframes. 1m range explodes,
5m vol confirms, 5m structure trending. The most "everything is green"
signal in the data.
"""
NAME = "trigger_explosive_break"
DESCRIPTION = "ENTER on 1m range>=2.5x + 5m vol>=2.5x + 2+ consec green 5m"
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

    # Current 1m must be green
    cur = recent_bars[-1]
    if cur['o'] <= 0 or cur['c'] <= cur['o']:
        return False

    # 1m range expansion: >= 2.5x avg of last 5 bars
    cur_range_pct = (cur['h'] - cur['l']) / cur['o'] * 100
    last5_ranges = [
        (b['h'] - b['l']) / b['o'] * 100
        for b in recent_bars[-6:-1] if b['o'] > 0
    ]
    if len(last5_ranges) < 5:
        return False
    avg5_range = sum(last5_ranges) / 5
    if avg5_range <= 0:
        return False
    if cur_range_pct / avg5_range < 2.5:
        return False

    # 5m TF: 2+ consec green + vol spike
    cs5 = _aggregate_5m(recent_bars[-90:])
    if len(cs5) < 6:
        return False
    cur5 = cs5[-1]
    if cur5['o'] <= 0 or cur5['c'] <= cur5['o']:
        return False
    if cs5[-2]['o'] <= 0 or cs5[-2]['c'] <= cs5[-2]['o']:
        return False

    # 5m vol >= 2.5x avg of prior 5 5m bars
    prior_5m_vols = [b.get('v', 0) for b in cs5[-6:-1]]
    if len(prior_5m_vols) < 5:
        return False
    avg5_5m_vol = sum(prior_5m_vols) / 5
    if avg5_5m_vol <= 0:
        return False
    if cur5.get('v', 0) / avg5_5m_vol < 2.5:
        return False

    return True
