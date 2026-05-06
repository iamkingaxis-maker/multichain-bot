"""trigger_volume_breakout — explosive-volume breakout parallel trigger.

Fires when BOTH:
  1. vol_thrust: current vol > 3x avg of last 10 AND green close
  2. close_above_5bar_high: current close > max high of last 5 bars

Same trend-break confirmation (close > 5bar high) as 4-combo, but
captures a different mechanism: instead of moderate-pullback recovery,
this is "explosive breakout on big volume." The 5-bar high gate filters
out fires during continued bleeding (the trap that killed
deep_dump_basing and false_breakdown_thrust).

Hypothesis: this catches setups where a token has been quiet then
surges (volume explosion + price breaking recent resistance). Different
from 4-combo (which requires moderate macro30 pullback). This trigger
fires regardless of macro context — purely volume + breakout-driven.
"""

NAME = "trigger_volume_breakout"
DESCRIPTION = ("ENTER on volume explosion + 5-bar breakout: vol > 3x "
               "last-10-avg AND green close AND close > 5-bar high")
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 11:
        return False

    cur = recent_bars[-1]
    if cur['o'] <= 0:
        return False

    # Must be green close
    if cur['c'] <= cur['o']:
        return False

    # 1. Volume thrust (3x avg of last 10)
    prior_10 = recent_bars[-11:-1]
    if len(prior_10) < 10:
        return False
    avg_vol = sum(b['v'] for b in prior_10) / 10
    if avg_vol <= 0:
        return False
    if cur['v'] / avg_vol <= 3.0:
        return False

    # 2. Close above 5-bar high (trend-break confirmation)
    prior_5 = recent_bars[-6:-1]
    if len(prior_5) < 5:
        return False
    prior_5_high = max(b['h'] for b in prior_5)
    if cur['c'] <= prior_5_high:
        return False

    return True
