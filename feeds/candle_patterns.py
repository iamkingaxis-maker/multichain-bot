"""
Phase 1 of chart-reading rebuild — candlestick pattern detection.

Classifies individual candles and pairs of candles into the standard
patterns chart traders read at a glance:

Single-candle patterns (computed from one Candle):
  - doji              indecision; very small body relative to total range
  - hammer            bullish reversal; small body at top, long lower wick
  - shooting_star     bearish reversal; small body at bottom, long upper wick
  - bullish_pin_bar   like hammer but broader rejection criterion
  - bearish_pin_bar   like shooting_star but broader rejection criterion
  - bullish_marubozu  strong green candle with minimal wicks (continuation)
  - bearish_marubozu  strong red candle with minimal wicks (continuation)

Two-candle patterns (computed from two consecutive Candles):
  - bullish_engulfing  current green candle's body fully covers prior red
  - bearish_engulfing  current red candle's body fully covers prior green
  - bullish_harami     small green inside prior larger red (potential reversal)
  - bearish_harami     small red inside prior larger green

Higher-level summary (computed across last N candles):
  - bullish_count, bearish_count, neutral_count
  - latest_pattern (most recent classification)

Pattern definitions follow widely-used industry rules. Thresholds are
conservative — designed to surface clear, unambiguous patterns rather
than borderline ones. Validation phase will tune thresholds against
the Winner Regression Set before any pattern becomes an enforced filter.
"""
from __future__ import annotations

from typing import List, Optional, Dict, Any

from feeds.candle_utils import Candle


# ── Single-candle classification ──────────────────────────────────────

def _body_size(c: Candle) -> float:
    return abs(c.close - c.open)


def _range_size(c: Candle) -> float:
    return c.high - c.low


def _upper_wick(c: Candle) -> float:
    return c.high - max(c.open, c.close)


def _lower_wick(c: Candle) -> float:
    return min(c.open, c.close) - c.low


def _is_green(c: Candle) -> bool:
    return c.close > c.open


def _is_red(c: Candle) -> bool:
    return c.close < c.open


def classify_single(c: Candle) -> Optional[str]:
    """Return single-candle pattern name or None.

    Order of checks matters — most specific first. A candle that
    qualifies as marubozu (no wicks) shouldn't also be flagged as
    engulfing-component logic; that's handled in classify_pair.

    Thresholds:
      doji:           body < 10% of range
      marubozu:       wicks combined < 10% of range, body >= 80% of range
      hammer:         lower_wick >= 2× body, upper_wick <= body, body in
                      upper third of range
      shooting_star:  upper_wick >= 2× body, lower_wick <= body, body in
                      lower third of range
      pin_bar:        one wick >= 2× total of (body + other wick) —
                      a strong rejection; less strict than hammer
                      because it doesn't constrain body color
    """
    rng = _range_size(c)
    if rng <= 0:
        return None  # zero-range candle (no movement)
    body = _body_size(c)
    upper = _upper_wick(c)
    lower = _lower_wick(c)

    body_top = max(c.open, c.close)
    body_bottom = min(c.open, c.close)
    body_pos_high = (body_top - c.low) / rng
    body_pos_low = (c.high - body_bottom) / rng

    # Marubozu — almost no wicks. Check first because it can otherwise
    # overlap with high-body candles.
    if (upper + lower) / rng < 0.10 and body / rng >= 0.80:
        return "bullish_marubozu" if _is_green(c) else "bearish_marubozu"

    # Hammer — long lower wick, body up high. Bullish reversal signal.
    # Checked BEFORE doji because hammers have small bodies; the wick
    # asymmetry (lower >> upper) is what distinguishes them.
    if lower >= 2 * body and upper <= body and body_pos_high >= 0.66:
        return "hammer"

    # Shooting star — long upper wick, body down low. Bearish reversal.
    # Same reasoning: check before doji since wick asymmetry is the key.
    if upper >= 2 * body and lower <= body and body_pos_low >= 0.66:
        return "shooting_star"

    # Pin bar — strong rejection on either side, body color agnostic.
    # Looser than hammer/shooting_star (which constrain body location).
    if lower >= 2 * (body + upper):
        return "bullish_pin_bar"
    if upper >= 2 * (body + lower):
        return "bearish_pin_bar"

    # Doji — symmetric small body relative to range. Comes after the
    # asymmetric-wick patterns so a hammer-shaped candle isn't
    # mis-tagged as doji.
    if body / rng < 0.10:
        return "doji"

    return None


# ── Two-candle (sequence) classification ──────────────────────────────

def classify_pair(prev: Candle, curr: Candle) -> Optional[str]:
    """Return two-candle pattern name or None. Caller passes prev=older
    and curr=newer."""
    prev_body = _body_size(prev)
    curr_body = _body_size(curr)
    if prev_body <= 0 or curr_body <= 0:
        return None

    prev_top = max(prev.open, prev.close)
    prev_bot = min(prev.open, prev.close)
    curr_top = max(curr.open, curr.close)
    curr_bot = min(curr.open, curr.close)

    # Bullish engulfing — prev red, curr green, curr body fully covers
    # prev body (curr_open <= prev_close AND curr_close >= prev_open).
    if _is_red(prev) and _is_green(curr):
        if curr_top >= prev_top and curr_bot <= prev_bot:
            return "bullish_engulfing"

    # Bearish engulfing — prev green, curr red, curr body fully covers
    # prev body.
    if _is_green(prev) and _is_red(curr):
        if curr_top >= prev_top and curr_bot <= prev_bot:
            return "bearish_engulfing"

    # Harami — current candle entirely inside prior body, opposite
    # color. Consolidation/indecision after a directional move.
    if _is_red(prev) and _is_green(curr):
        if curr_top <= prev_top and curr_bot >= prev_bot:
            return "bullish_harami"
    if _is_green(prev) and _is_red(curr):
        if curr_top <= prev_top and curr_bot >= prev_bot:
            return "bearish_harami"

    return None


# ── Pattern direction tagging ─────────────────────────────────────────

_BULLISH = {
    "hammer", "bullish_pin_bar", "bullish_marubozu",
    "bullish_engulfing", "bullish_harami",
}
_BEARISH = {
    "shooting_star", "bearish_pin_bar", "bearish_marubozu",
    "bearish_engulfing", "bearish_harami",
}
_NEUTRAL = {"doji"}


def pattern_direction(name: Optional[str]) -> str:
    if name in _BULLISH: return "bullish"
    if name in _BEARISH: return "bearish"
    if name in _NEUTRAL: return "neutral"
    return "none"


# ── Series-level summary ──────────────────────────────────────────────

def analyze_series(candles: List[Candle], lookback: int = 5) -> Dict[str, Any]:
    """Return a feature dict summarizing the last `lookback` candles.

    Keys:
      latest_pattern        most-recent single-candle pattern (or None)
      latest_pair_pattern   most-recent two-candle pattern (or None)
      latest_direction      bullish/bearish/neutral/none for latest pattern
      bullish_count         bullish patterns in lookback window
      bearish_count         bearish patterns in lookback window
      neutral_count         neutral patterns in lookback window
      patterns              list of (single, pair) tuples per candle in
                            window (oldest-first), for downstream
                            confluence checks
    """
    if not candles:
        return {
            "latest_pattern": None,
            "latest_pair_pattern": None,
            "latest_direction": "none",
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "patterns": [],
        }

    window = candles[-lookback:] if len(candles) >= lookback else candles
    singles: List[Optional[str]] = [classify_single(c) for c in window]
    pairs: List[Optional[str]] = []
    for i, c in enumerate(window):
        if i == 0:
            # First candle in window; look back to candle BEFORE the
            # window if it exists, so we don't lose pair-detection at
            # window boundary.
            window_start_idx = len(candles) - len(window)
            if window_start_idx > 0:
                pairs.append(classify_pair(candles[window_start_idx - 1], c))
            else:
                pairs.append(None)
        else:
            pairs.append(classify_pair(window[i - 1], c))

    bullish = sum(
        1 for s, p in zip(singles, pairs)
        if pattern_direction(s) == "bullish" or pattern_direction(p) == "bullish"
    )
    bearish = sum(
        1 for s, p in zip(singles, pairs)
        if pattern_direction(s) == "bearish" or pattern_direction(p) == "bearish"
    )
    neutral = sum(
        1 for s, p in zip(singles, pairs)
        if (s and pattern_direction(s) == "neutral")
        or (p and pattern_direction(p) == "neutral")
    )

    latest_single = singles[-1]
    latest_pair = pairs[-1]
    if pattern_direction(latest_pair) != "none":
        latest_direction = pattern_direction(latest_pair)
    else:
        latest_direction = pattern_direction(latest_single)

    return {
        "latest_pattern": latest_single,
        "latest_pair_pattern": latest_pair,
        "latest_direction": latest_direction,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "patterns": list(zip(singles, pairs)),
    }


# ── Multi-timeframe confluence ────────────────────────────────────────

def confluence(
    summary_5m: Dict[str, Any],
    summary_15m: Dict[str, Any],
) -> str:
    """Combine 5m and 15m series summaries into a single confluence tag.

    Returns one of:
      strong_bullish — both timeframes latest direction = bullish
      bullish        — 5m bullish, 15m not bearish
      mixed          — one bullish, one bearish (chart in conflict)
      bearish        — 5m bearish, 15m not bullish
      strong_bearish — both bearish
      none           — neither timeframe has a clear pattern
    """
    d5 = summary_5m.get("latest_direction", "none")
    d15 = summary_15m.get("latest_direction", "none")
    if d5 == "bullish" and d15 == "bullish": return "strong_bullish"
    if d5 == "bearish" and d15 == "bearish": return "strong_bearish"
    if d5 == "bullish" and d15 == "bearish": return "mixed"
    if d5 == "bearish" and d15 == "bullish": return "mixed"
    if d5 == "bullish": return "bullish"
    if d5 == "bearish": return "bearish"
    if d15 == "bullish": return "bullish"
    if d15 == "bearish": return "bearish"
    return "none"
