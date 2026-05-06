"""trigger_deep_dump_basing — capitulation + stabilization parallel trigger.

Fires when:
  1. Drop of >=5% in last 10 candles (high-low range), AND
  2. Last 4 candles all have small bodies (<1.5% each) — basing

Different mechanism from clean_break (first green after red) and 4-combo
(pullback + breakout + HL). Captures the "exhaustion floor" pattern —
token just had a sharp drop, sellers finished, now consolidating.

Pattern miner v3 results (marginal cohort, NOT cb AND NOT 4combo):
  - n=236 entries, WR=63.6%, avg=+1.62%/trade, sum=+382%
  - Top single shape by avg/trade among 14 candidates tested
"""

NAME = "trigger_deep_dump_basing"
DESCRIPTION = ("ENTER when last 10 bars had >=5% drop AND last 4 bodies "
               "are all <1.5% (capitulation + tight base)")
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 11:
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

    # 2. Last 4 candles all small bodies (<1.5%)
    last_4 = recent_bars[-4:]
    bodies = [abs(b['c'] - b['o']) / b['o'] * 100
              for b in last_4 if b['o'] > 0]
    if len(bodies) != 4:
        return False
    if not all(body < 1.5 for body in bodies):
        return False

    return True
