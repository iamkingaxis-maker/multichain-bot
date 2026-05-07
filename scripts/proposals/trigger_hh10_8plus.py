"""trigger_hh10_8plus — 8+ higher-highs in last 10 1m bars + green.

Mined from cohort exploration (round-3c). Pattern fires on tokens making
strong stepwise climbs without requiring vol explosion. Distinct from
trigger_hh10_strict_vol which requires HH>=7 AND vol>=1.5x.

Validation across 3 fast-mover cohort definitions:
  +15%/60min cohort:  WR=61.0%, +$1.10/trade, n=2135, Stop=20.7%
  +20%/90min cohort:  WR=62.0%, +$1.21/trade, n=2090, Stop=20.8%
  +12%/30min cohort:  WR=60.6%, +$1.03/trade, n=2180, Stop=20.6%

Lowest stop rate of any candidate tested. Pure price-action strength.
"""
NAME = "trigger_hh10_8plus"
DESCRIPTION = "ENTER on 8+ higher-highs in last 10 1m bars + cur green"
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 10:
        return False
    cur = recent_bars[-1]
    if cur['o'] <= 0 or cur['c'] <= cur['o']:
        return False
    last10 = recent_bars[-10:]
    hh = sum(1 for j in range(1, 10) if last10[j]['h'] > last10[j-1]['h'])
    return hh >= 8
