"""trigger_momentum_continuation — 4+ consec green 1m bars + vol_spike >= 1.5x.

Mined from FAST_WIN vs LOSER analysis on 295-token master dataset.
The combo "4+ consec green + vol_spike_30 >= 1.5" produced the highest
fast-win share (39.8%) among 2-feature combinations tested, with sample
n=1230 (489 fast / 741 loser).

Mechanism: continuation pattern. After 4 consecutive green 1m bars with
volume rising vs 30-bar avg, the momentum is real (not just noise) and
often runs another 8%+ in 20 minutes.

Different from existing triggers:
  - clean_break: first green AFTER reds (consec_green=1)
  - deep_breakout: close above 10-bar high (different mechanism)
  - This: 4+ consec greens, momentum continuation (consec_green>=4)
"""
NAME = "trigger_momentum_continuation"
DESCRIPTION = "ENTER on 4+ consec green 1m bars + vol > 1.5x avg of last 30"
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 30:
        return False
    cur = recent_bars[-1]
    if cur['o'] <= 0:
        return False
    # Current candle must be green
    if cur['c'] <= cur['o']:
        return False
    # 4+ consec green (current + 3 prior all green)
    for k in (1, 2, 3, 4):
        b = recent_bars[-k]
        if b['o'] <= 0 or b['c'] <= b['o']:
            return False
    # Volume spike: current vol > 1.5x avg of bars [-30 .. -1]
    if v is None:
        return False
    prior_30 = recent_bars[-31:-1]
    vols = [b.get('v', 0) for b in prior_30 if b.get('v') is not None]
    if not vols or len(vols) < 20:
        return False
    avg30 = sum(vols) / len(vols)
    if avg30 <= 0:
        return False
    if v / avg30 < 1.5:
        return False
    return True
