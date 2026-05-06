"""trigger_quiet_pop_breakout — calm-then-explosion parallel trigger.

Fires when ALL THREE:
  1. Last 3 bars had small volume (each < 0.8x avg of prior 10) — calm
  2. Current vol > 2x avg of prior 10 — pop
  3. Current close > max high of last 5 bars (breakout)
  4. Green close

Different mechanism from existing triggers:
  - clean_break: first green after sustained red
  - 4-combo: pullback + 1.5x vol + breakout + higher_low
  - capitulation_breakout: drop + 4 small bodies + breakout
  - THIS: 3 quiet bars + 2x vol pop + breakout (consolidation + accumulation)
"""

NAME = "trigger_quiet_pop_breakout"
DESCRIPTION = ("ENTER on 3 quiet bars + 2x vol pop + close > 5bar high "
               "+ green close (consolidation breakout)")
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 14:
        return False

    cur = recent_bars[-1]
    if cur['o'] <= 0:
        return False

    # Must be green close
    if cur['c'] <= cur['o']:
        return False

    # 1. Last 3 bars all had small volume relative to prior 10
    avg_10 = sum(b['v'] for b in recent_bars[-13:-3]) / 10
    if avg_10 <= 0:
        return False
    last_3_vols = [b['v'] for b in recent_bars[-4:-1]]
    if not all(vv < avg_10 * 0.8 for vv in last_3_vols):
        return False

    # 2. Current vol > 2x avg of prior 10
    if cur['v'] / avg_10 <= 2.0:
        return False

    # 3. Close above 5-bar high
    prior_5 = recent_bars[-6:-1]
    if len(prior_5) < 5:
        return False
    prior_5_high = max(b['h'] for b in prior_5)
    if cur['c'] <= prior_5_high:
        return False

    return True
