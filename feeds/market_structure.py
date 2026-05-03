"""
Phase 8 of chart-reading rebuild — market structure (BOS / CHoCH / trend state).

Smart-Money-Concepts (SMC) framework. Goes beyond visual trend detection
into structural break analysis. The core observation: trends only end
when the structural sequence of higher-highs-and-higher-lows (uptrend)
or lower-highs-and-lower-lows (downtrend) is broken in the OPPOSITE
direction. Until then, every pullback is a continuation buy/sell.

Concepts:
  Swing classification — each pivot is tagged HH/LH (vs prior pivot
    high) or HL/LL (vs prior pivot low). The sequence of these tags
    tells you whether structure is intact or fracturing.

  Trend state — derived from the last 2 highs and last 2 lows:
    uptrend   = last 2 highs ascending AND last 2 lows ascending (HH+HL)
    downtrend = last 2 highs descending AND last 2 lows descending (LH+LL)
    ranging   = otherwise

  BOS (Break of Structure) — a candle close beyond the most recent
    swing high (bullish BOS) or below the most recent swing low
    (bearish BOS). Confirms the trend continues. Bullish BOS in an
    uptrend = "trend intact, continuation likely."

  CHoCH (Change of Character) — the FIRST opposite-direction
    structural break. When an uptrend prints its first LH-LL pair, or
    a downtrend prints its first HH-HL pair. CHoCH is the strongest
    reversal signal in this framework — it's where smart money flips
    direction.

Verdicts (used by chart_reader composite scoring):
  TREND_UP            currently uptrend, last BOS bullish
  TREND_DOWN          currently downtrend, last BOS bearish
  REVERSAL_UP         CHoCH from down → up (highest-conviction long)
  REVERSAL_DOWN       CHoCH from up → down (highest-conviction short)
  RANGING             no clean structure (mixed swings)

This module is pure logic on a list of Candle objects. Pivot detection
delegates to feeds.support_resistance.find_pivot_highs/lows so we share
the same fractal definition across the chart_reader stack.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple

from feeds.candle_utils import Candle
from feeds.support_resistance import find_pivot_highs, find_pivot_lows


# ── Swing classification ────────────────────────────────────────────

def classify_swings(
    pivot_highs: List[Tuple[int, float, float]],
    pivot_lows: List[Tuple[int, float, float]],
) -> List[Dict[str, Any]]:
    """Walk pivots in chronological order, tag each HH/LH/HL/LL.

    Returns ordered list of dicts:
      index   candle index
      type    'high' or 'low'
      price   swing price
      class   HH / LH / HL / LL / FIRST_HIGH / FIRST_LOW
    """
    merged: List[Tuple[int, str, float]] = []
    for i, p, _v in pivot_highs:
        merged.append((i, "high", p))
    for i, p, _v in pivot_lows:
        merged.append((i, "low", p))
    merged.sort(key=lambda x: x[0])

    out: List[Dict[str, Any]] = []
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    for i, t, p in merged:
        if t == "high":
            if last_high is None:
                cls = "FIRST_HIGH"
            elif p > last_high:
                cls = "HH"
            else:
                cls = "LH"
            last_high = p
        else:
            if last_low is None:
                cls = "FIRST_LOW"
            elif p > last_low:
                cls = "HL"
            else:
                cls = "LL"
            last_low = p
        out.append({"index": i, "type": t, "price": p, "class": cls})
    return out


# ── Trend state ────────────────────────────────────────────────────

def trend_state_from_swings(swings: List[Dict[str, Any]]) -> str:
    """Determine current trend from the last 2 highs and last 2 lows."""
    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return "undefined"
    last_high_classes = [h["class"] for h in highs[-2:]]
    last_low_classes = [l["class"] for l in lows[-2:]]
    high_up = any(c == "HH" for c in last_high_classes)
    low_up = any(c == "HL" for c in last_low_classes)
    high_dn = any(c == "LH" for c in last_high_classes)
    low_dn = any(c == "LL" for c in last_low_classes)
    if high_up and low_up:
        return "uptrend"
    if high_dn and low_dn:
        return "downtrend"
    return "ranging"


# ── Break of Structure ─────────────────────────────────────────────

def detect_bos(
    candles: List[Candle],
    swings: List[Dict[str, Any]],
    *,
    lookback: int = 5,
) -> Optional[Dict[str, Any]]:
    """Did any candle in the last `lookback` close beyond the most recent
    prior swing high (bullish BOS) or swing low (bearish BOS)?

    Returns:
      None — no BOS in window
      {direction, swept_level, candle_index, candles_ago} otherwise
    """
    if not candles or not swings:
        return None
    last_idx = len(candles) - 1
    cutoff = max(0, last_idx - lookback)

    # Most recent swing high BEFORE the cutoff (it's the level being broken)
    prior_highs = [s for s in swings if s["type"] == "high" and s["index"] < cutoff]
    prior_lows = [s for s in swings if s["type"] == "low" and s["index"] < cutoff]

    bullish: Optional[Dict[str, Any]] = None
    if prior_highs:
        target = prior_highs[-1]["price"]
        for i in range(cutoff, last_idx + 1):
            if candles[i].close > target:
                bullish = {
                    "direction": "bullish",
                    "swept_level": target,
                    "candle_index": i,
                    "candles_ago": last_idx - i,
                }
                break
    bearish: Optional[Dict[str, Any]] = None
    if prior_lows:
        target = prior_lows[-1]["price"]
        for i in range(cutoff, last_idx + 1):
            if candles[i].close < target:
                bearish = {
                    "direction": "bearish",
                    "swept_level": target,
                    "candle_index": i,
                    "candles_ago": last_idx - i,
                }
                break
    # Take the most recent
    if bullish and bearish:
        return bullish if bullish["candle_index"] >= bearish["candle_index"] else bearish
    return bullish or bearish


# ── Change of Character ────────────────────────────────────────────

def detect_choch(swings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The most recent CHoCH in the swing sequence.

    Walks the swings chronologically, tracking the current trend state
    after each new swing, and notes any flip from up→down (bullish to
    bearish CHoCH) or down→up (bearish to bullish CHoCH).
    """
    if len(swings) < 4:
        return None
    state = "undefined"
    most_recent: Optional[Dict[str, Any]] = None
    for i, _ in enumerate(swings):
        slice_ = swings[: i + 1]
        new_state = trend_state_from_swings(slice_)
        if state == "uptrend" and new_state == "downtrend":
            most_recent = {
                "direction": "bullish_to_bearish",
                "swing_index": swings[i]["index"],
                "swing_price": swings[i]["price"],
            }
        elif state == "downtrend" and new_state == "uptrend":
            most_recent = {
                "direction": "bearish_to_bullish",
                "swing_index": swings[i]["index"],
                "swing_price": swings[i]["price"],
            }
        if new_state != "undefined":
            state = new_state
    return most_recent


# ── Main analysis ──────────────────────────────────────────────────

def analyze(
    candles: List[Candle],
    *,
    pivot_n: int = 3,
    bos_lookback: int = 5,
    choch_lookback_swings: int = 6,
) -> Dict[str, Any]:
    """Full market-structure analysis on a candle series.

    Returns:
      current_structure       uptrend / downtrend / ranging / undefined
      swing_count             total swings detected
      hh_count, lh_count, hl_count, ll_count
      last_swing_high_price, last_swing_high_index
      last_swing_low_price, last_swing_low_index
      recent_bos              dict or None (direction/level/candles_ago)
      recent_choch            dict or None — only kept if within last
                              `choch_lookback_swings` swings (older
                              CHoCHes are stale)
      structure_verdict       TREND_UP / TREND_DOWN / REVERSAL_UP /
                              REVERSAL_DOWN / RANGING / NEUTRAL
    """
    blank = {
        "current_structure": "undefined",
        "swing_count": 0,
        "hh_count": 0, "lh_count": 0, "hl_count": 0, "ll_count": 0,
        "last_swing_high_price": None, "last_swing_high_index": None,
        "last_swing_low_price": None, "last_swing_low_index": None,
        "recent_bos": None,
        "recent_choch": None,
        "structure_verdict": "?",
    }
    if not candles or len(candles) < (2 * pivot_n + 2):
        return blank

    pivot_highs = find_pivot_highs(candles, n=pivot_n)
    pivot_lows = find_pivot_lows(candles, n=pivot_n)
    swings = classify_swings(pivot_highs, pivot_lows)
    if not swings:
        return blank

    structure = trend_state_from_swings(swings)
    bos = detect_bos(candles, swings, lookback=bos_lookback)
    choch = detect_choch(swings)

    # CHoCH is only "recent" if it's in the last N swings; otherwise
    # it's old news that's been overtaken by subsequent structure.
    if choch is not None:
        recent_swing_indices = [s["index"] for s in swings[-choch_lookback_swings:]]
        if choch["swing_index"] not in recent_swing_indices:
            choch = None

    # Counts by class
    hh = sum(1 for s in swings if s["class"] == "HH")
    lh = sum(1 for s in swings if s["class"] == "LH")
    hl = sum(1 for s in swings if s["class"] == "HL")
    ll = sum(1 for s in swings if s["class"] == "LL")

    last_high = next((s for s in reversed(swings) if s["type"] == "high"), None)
    last_low = next((s for s in reversed(swings) if s["type"] == "low"), None)

    # Verdict — CHoCH dominates BOS dominates trend state
    verdict = "NEUTRAL"
    if choch and choch["direction"] == "bearish_to_bullish":
        verdict = "REVERSAL_UP"
    elif choch and choch["direction"] == "bullish_to_bearish":
        verdict = "REVERSAL_DOWN"
    elif bos and bos["direction"] == "bullish" and structure in ("uptrend", "ranging"):
        verdict = "TREND_UP"
    elif bos and bos["direction"] == "bearish" and structure in ("downtrend", "ranging"):
        verdict = "TREND_DOWN"
    elif structure == "uptrend":
        verdict = "TREND_UP"
    elif structure == "downtrend":
        verdict = "TREND_DOWN"
    elif structure == "ranging":
        verdict = "RANGING"

    return {
        "current_structure": structure,
        "swing_count": len(swings),
        "hh_count": hh, "lh_count": lh, "hl_count": hl, "ll_count": ll,
        "last_swing_high_price": last_high["price"] if last_high else None,
        "last_swing_high_index": last_high["index"] if last_high else None,
        "last_swing_low_price": last_low["price"] if last_low else None,
        "last_swing_low_index": last_low["index"] if last_low else None,
        "recent_bos": bos,
        "recent_choch": choch,
        "structure_verdict": verdict,
        # Expose classified swings for downstream phases (liquidity_sweeps).
        "_swings": swings,
    }
