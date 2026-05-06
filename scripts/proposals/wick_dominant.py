"""filter_wick_dominant — BLOCK if entry candle's lower wick > body size.

Hypothesis from multi-token simulation: lower-wick-dominant clean_break
candles are failed-bounce patterns (price dumped, partially recovered,
then dumps again). Sim said -3.4pp lift, n=526, sum -390%.

NOTE: retro check on first 10 real bot trades showed this filter would
have BLOCKED 2 winners and PASSED all 3 losses, costing $-3.94. NOT
SHIPPED. Kept here as a template + cautionary tale.
"""

NAME = "filter_wick_dominant"
DESCRIPTION = "BLOCK if entry candle lower_wick_pct > body_size_pct"
NEEDS_OHLC = True


def should_block(o, h, l, c, v=None, em=None):
    if o is None or o <= 0:
        return False  # fail-open
    body = abs(c - o) / o * 100
    body_bot = min(c, o)
    lower_wick = (body_bot - l) / o * 100
    return lower_wick > body and lower_wick > 0
