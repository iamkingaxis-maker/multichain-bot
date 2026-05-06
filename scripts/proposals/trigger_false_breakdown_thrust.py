"""trigger_false_breakdown_thrust — stop-hunt reversal parallel trigger.

Fires when BOTH:
  1. false_breakdown_recovery: current low broke below the lows of the
     last 3 candles, but current close is BACK ABOVE that broken level
     (stop-hunt wick that gets bought)
  2. vol_thrust: current volume > 3x avg of last 10 bars AND green close

Different mechanism from clean_break (first green after sustained red)
and 4-combo (pullback + breakout + HL). Captures the "stop-hunt reversal"
pattern — price wicked below recent support to grab stops, then strong
volume bought it back.

Pattern miner v3 results on MARGINAL cohort (NOT cb AND NOT 4combo):
  - n=107, WR=72.5%, avg=+1.61%/trade, sum=+173%
  - Highest WR 2-combo found among 14 shapes tested.

The double-AND makes it specific enough to avoid the repetitive-fire
trap that killed deep_dump_basing.
"""

NAME = "trigger_false_breakdown_thrust"
DESCRIPTION = ("ENTER on stop-hunt reversal: low broke 3-bar lows but "
               "close back above + 3x volume + green close")
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

    # 1. False breakdown recovery
    prior_3 = recent_bars[-4:-1]  # last 3 bars before current
    if len(prior_3) < 3:
        return False
    min_prior_3 = min(b['l'] for b in prior_3)
    if cur['l'] >= min_prior_3:
        return False  # didn't break below prior lows
    if cur['c'] < min_prior_3:
        return False  # didn't recover back above

    # 2. Volume thrust (3x avg of last 10)
    prior_10 = recent_bars[-11:-1]
    if len(prior_10) < 10:
        return False
    avg_vol = sum(b['v'] for b in prior_10) / 10
    if avg_vol <= 0:
        return False
    if cur['v'] / avg_vol <= 3.0:
        return False

    return True
