"""trigger_range_expansion — explosive 1m candle 3x avg range.

Mined feature: range_expansion_5 >= 2.93 (top 5%) shows 34.8% fast-win
share. Adding any vol confirmation pushes to 38-40%. This module uses
a moderate threshold + vol filter to catch volatility expansion entries.

Mechanism: when current 1m range is 2.5x the avg of last 5 bars AND
5m volume is ramping, the token has likely just broken from a coil
and is in active expansion mode.
"""
NAME = "trigger_range_expansion"
DESCRIPTION = "ENTER on 1m range >= 2.5x avg of last 5 + green + 5m vol >= 2x avg"
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 30:
        return False
    cur = recent_bars[-1]
    if cur['o'] <= 0 or cur['c'] <= 0:
        return False
    # Must be green
    if cur['c'] <= cur['o']:
        return False

    # Range expansion: cur range >= 2.5x avg of prior 5 bars
    cur_range_pct = (cur['h'] - cur['l']) / cur['o'] * 100
    last5_ranges = [
        (b['h'] - b['l']) / b['o'] * 100
        for b in recent_bars[-6:-1] if b['o'] > 0
    ]
    if len(last5_ranges) < 5:
        return False
    avg5 = sum(last5_ranges) / 5
    if avg5 <= 0:
        return False
    range_expansion = cur_range_pct / avg5
    if range_expansion < 2.5:
        return False

    # 5m vol confirmation: aggregate last 6 1m bars (current 5m candle in progress)
    # Use the last 5 1m bars to estimate "5m vol now" vs prior 5m windows
    if v is None:
        return False
    last5_1m_vols = [b.get('v', 0) for b in recent_bars[-5:]]
    cur5m_vol = sum(last5_1m_vols)
    # Prior 25 bars = 5x prior 5m windows
    prior_25 = recent_bars[-30:-5]
    prior_25_vols = [b.get('v', 0) for b in prior_25]
    if not prior_25_vols:
        return False
    avg_5m_window = sum(prior_25_vols) / 5  # average per-5m-window total
    if avg_5m_window <= 0:
        return False
    if cur5m_vol / avg_5m_window < 2.0:
        return False

    return True
