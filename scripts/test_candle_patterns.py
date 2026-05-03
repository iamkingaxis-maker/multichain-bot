"""Quick unit tests for candle_patterns.py — verify pattern detection
against hand-crafted candles before live integration."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from feeds.candle_utils import Candle
from feeds.candle_patterns import classify_single, classify_pair, analyze_series, confluence

def C(o, h, l, c, v=100):
    return Candle(open_time=0, open=o, high=h, low=l, close=c, volume=v, close_time=0)

# --- Single-candle tests ---

# Doji: open ~= close, decent range
doji = C(100, 105, 95, 100.2)
assert classify_single(doji) == "doji", f"expected doji, got {classify_single(doji)}"

# Hammer: small green body up high, long lower wick
hammer = C(100, 102, 90, 101)
# body=1, range=12, lower=10, upper=1, body_pos_high=(102-90)/12=1.0
# lower (10) >= 2*body (2) ✓, upper (1) <= body (1) ✓, body_pos_high 1.0 >= 0.66 ✓
assert classify_single(hammer) == "hammer", f"expected hammer, got {classify_single(hammer)}"

# Shooting star: small body down low, long upper wick
ss = C(100, 110, 99, 99.5)
# body=0.5, range=11, upper=10, lower=0
# upper (10) >= 2*body (1) ✓, lower (0) <= body (0.5) ✓
# Wait: body_pos_low = (high-body_bottom)/range = (110-99.5)/11 = 0.95 ≥ 0.66 ✓
assert classify_single(ss) == "shooting_star", f"expected shooting_star, got {classify_single(ss)}"

# Bullish marubozu: green, almost no wicks
mb_g = C(100, 110.05, 99.95, 110)
assert classify_single(mb_g) == "bullish_marubozu", f"got {classify_single(mb_g)}"

# Normal candle — no pattern
normal = C(100, 103, 99, 102)
# body=2, range=4, body/rng=0.5 (not doji), upper=1, lower=1
# wicks/range=0.5 (not marubozu). Lower (1) < 2*body(4)=4 ✓ but no other checks pass
print(f'normal: {classify_single(normal)}')  # likely None

# --- Pair tests ---

# Bullish engulfing: prev red, curr green covering prev body
prev_red = C(100, 102, 95, 96)  # red, body 96-100
curr_green = C(95, 102, 94, 101)  # green, body 95-101 (covers 96-100)
pair = classify_pair(prev_red, curr_green)
assert pair == "bullish_engulfing", f"expected bullish_engulfing, got {pair}"

# Bearish engulfing
prev_green = C(100, 105, 99, 104)  # green, body 100-104
curr_red = C(105, 106, 98, 99)  # red, body 99-105 (covers 100-104)
pair = classify_pair(prev_green, curr_red)
assert pair == "bearish_engulfing", f"expected bearish_engulfing, got {pair}"

# --- Series test ---
candles = [
    C(100, 102, 99, 101),    # green
    C(101, 103, 100, 102),    # green
    C(102, 105, 99, 99.5),    # shooting_star
    C(99.5, 100, 90, 99),     # hammer (small body high, long lower wick)
    C(99, 105, 98, 104),      # green, no specific pattern
]
summary = analyze_series(candles, lookback=5)
print(f'Series summary: latest={summary["latest_pattern"]}, dir={summary["latest_direction"]}, B={summary["bullish_count"]} S={summary["bearish_count"]} N={summary["neutral_count"]}')

# Confluence test
s5 = {"latest_direction": "bullish"}
s15 = {"latest_direction": "bullish"}
assert confluence(s5, s15) == "strong_bullish"

s5 = {"latest_direction": "bearish"}
s15 = {"latest_direction": "bullish"}
assert confluence(s5, s15) == "mixed"

print('\nAll tests passed.')
