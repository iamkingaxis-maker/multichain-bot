"""trigger_capitulation_breakout — capitulation+base+breakout parallel trigger.

Fires when ALL THREE:
  1. Drop of >=5% in last 10 candles (high-low range)
  2. Last 4 candle bodies all <1.5% each (basing/consolidation)
  3. Current close > max high of last 5 bars (breakout)

The first two conditions = "deep_dump_basing" (capitulation pattern).
Condition #3 = the trend-break gate that 4-combo proved is essential
for avoiding repeat-fires during continued bleeding.

Mechanism: token had a sharp drop, sellers exhausted, price tightened
into a base, then breaks out through recent resistance. Different from
4-combo (which requires macro30 in [-15,-3] pullback zone) — this can
fire after deeper pullbacks too.

The breakout requirement ensures price has actually started its
recovery, not just consolidating mid-bleed.
"""

NAME = "trigger_capitulation_breakout"
DESCRIPTION = ("ENTER on >=5% drop + 4 small bodies (basing) + close > "
               "5-bar high (breakout)")
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 11:
        return False

    cur = recent_bars[-1]
    if cur['o'] <= 0:
        return False

    # 1. Drop >=5% in last 10 candles
    last_10 = recent_bars[-11:]
    high_10 = max(b['h'] for b in last_10)
    low_10 = min(b['l'] for b in last_10)
    if high_10 <= 0:
        return False
    drop = (low_10 / high_10 - 1) * 100
    if drop > -5:
        return False

    # 2. Last 4 bodies all <1.5%
    last_4 = recent_bars[-4:]
    bodies = [abs(b['c'] - b['o']) / b['o'] * 100
              for b in last_4 if b['o'] > 0]
    if len(bodies) != 4:
        return False
    if not all(body < 1.5 for body in bodies):
        return False

    # 3. Close above 5-bar high (trend-break confirmation)
    prior_5 = recent_bars[-6:-1]
    if len(prior_5) < 5:
        return False
    prior_5_high = max(b['h'] for b in prior_5)
    if cur['c'] <= prior_5_high:
        return False

    return True
