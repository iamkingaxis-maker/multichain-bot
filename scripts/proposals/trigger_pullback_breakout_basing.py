"""trigger_pullback_breakout_basing — parallel entry trigger.

Fires when ALL FOUR conditions match at the current 1m candle:

  1. macro30_pct in [-15%, -3%]      (moderate pullback zone)
  2. current vol > 1.5x avg of last 5 (real buying)
  3. current close > max high of last 5 (5-bar breakout)
  4. current low > previous low      (basing — first higher low)

From pattern miner v2: standalone (no clean_break gate) gives n=85
with 79% WR / +4.47%/trade in simulation. As parallel trigger to
clean_break: 401 NEW marginal entries (NOT in clean_break), 57.3% WR /
+0.45%/trade — net positive marginal contribution.

Different shape from clean_break (first green after sustained red).
This catches "pullback + breakout + basing" — a textbook reversal
pattern that doesn't always coincide with the green-after-red trigger.
"""

NAME = "trigger_pullback_breakout_basing"
DESCRIPTION = ("ENTER when macro30 in [-15%,-3%] AND vol_spike>1.5x "
               "AND close > 5bar_high AND higher_low")
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 31:
        return False
    cur = recent_bars[-1]
    if cur['o'] <= 0:
        return False

    # 1. macro30 in zone
    bar_30_ago = recent_bars[-31]
    if bar_30_ago['c'] <= 0:
        return False
    macro30 = (cur['c'] / bar_30_ago['c'] - 1) * 100
    if not (-15 <= macro30 <= -3):
        return False

    # 2. vol_confirms
    prior = [b['v'] for b in recent_bars[-6:-1]]
    if not prior:
        return False
    avg_vol = sum(prior) / len(prior)
    if avg_vol <= 0 or cur['v'] / avg_vol <= 1.5:
        return False

    # 3. close above 5-bar high
    if len(recent_bars) < 6:
        return False
    prior_high = max(b['h'] for b in recent_bars[-6:-1])
    if cur['c'] <= prior_high:
        return False

    # 4. higher_low
    if cur['l'] <= recent_bars[-2]['l']:
        return False

    return True
