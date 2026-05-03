"""
Phase 9 of chart-reading rebuild — liquidity sweeps / stop hunts.

The single most reliable reversal pattern in crypto: price spikes
through a prior swing level (where retail stops sit), grabs the
liquidity, and immediately reverses. Smart money harvests stops to
fill orders without moving the market.

  Bullish sweep (sweep low) — a candle's LOW pierces a prior swing
    low BUT closes back above it. The wick rejected the breakdown.
    Retail stops below the swing low got hit; bots loaded long. Often
    marks the bottom of a leg.

  Bearish sweep (sweep high) — a candle's HIGH pierces a prior swing
    high BUT closes back below. The wick rejected the breakout.
    Retail stops above resistance got hit; bots loaded short. Often
    marks the top of a leg.

Quality factors:
  wick_size_pct    how far past the swept level the wick reached.
                   Bigger wick = more aggressive raid = stronger reversal.
  volume_ratio     sweep candle volume vs trailing average. Volume-
                   confirmed sweeps are the high-probability ones.
  candles_ago      how recent. Sweeps within 1-3 candles are still
                   actionable; older sweeps have already played out.
  swept_level      which prior swing was raided (most recent = strongest).

Verdict tags (used by chart_reader composite scoring):
  BULLISH_SWEEP   recent sweep low, volume-confirmed (or strong wick)
  BEARISH_SWEEP   recent sweep high, volume-confirmed
  NONE            no recent sweep

Reuses the swing classification from market_structure.analyze() so the
two phases share the same fractal vocabulary.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional

from feeds.candle_utils import Candle
from feeds.market_structure import classify_swings
from feeds.support_resistance import find_pivot_highs, find_pivot_lows


def _trailing_avg_volume(candles: List[Candle], window: int = 10) -> float:
    if not candles:
        return 0.0
    sub = candles[-window:] if len(candles) >= window else candles
    return sum(c.volume for c in sub) / max(len(sub), 1)


# ── Sweep detection ────────────────────────────────────────────────

def detect_sweep_low(
    candles: List[Candle],
    swings: List[Dict[str, Any]],
    *,
    lookback: int = 5,
    min_wick_pct: float = 0.3,
    levels_to_check: int = 3,
    avg_volume: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Most recent bullish sweep (sweep below a prior swing low) within lookback.

    Walks candles backward from the latest, returns the FIRST match (so
    "most recent"). Each candle is tested against the last
    `levels_to_check` swing lows that occurred BEFORE that candle.
    """
    if not candles:
        return None
    last_idx = len(candles) - 1
    cutoff = max(0, last_idx - lookback)
    swing_lows = [s for s in swings if s["type"] == "low"]
    if not swing_lows:
        return None

    for i in range(last_idx, cutoff - 1, -1):
        c = candles[i]
        if c.low <= 0 or c.close <= 0:
            continue
        # Lowest swing low that occurred BEFORE this candle
        prior = [s for s in swing_lows if s["index"] < i]
        if not prior:
            continue
        for sl in prior[-levels_to_check:][::-1]:  # try most-recent first
            level = sl["price"]
            if level <= 0:
                continue
            if c.low < level and c.close > level:
                wick_pct = ((level - c.low) / level * 100) if level > 0 else 0.0
                if wick_pct < min_wick_pct:
                    continue
                vol_ratio = (c.volume / avg_volume) if avg_volume > 0 else 0.0
                return {
                    "candle_index": i,
                    "candles_ago": last_idx - i,
                    "swept_level": level,
                    "swept_swing_index": sl["index"],
                    "low_reached": c.low,
                    "close": c.close,
                    "wick_size_pct": round(wick_pct, 3),
                    "volume_ratio": round(vol_ratio, 3),
                    "volume_confirmed": vol_ratio >= 1.5,
                }
    return None


def detect_sweep_high(
    candles: List[Candle],
    swings: List[Dict[str, Any]],
    *,
    lookback: int = 5,
    min_wick_pct: float = 0.3,
    levels_to_check: int = 3,
    avg_volume: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Most recent bearish sweep (sweep above a prior swing high)."""
    if not candles:
        return None
    last_idx = len(candles) - 1
    cutoff = max(0, last_idx - lookback)
    swing_highs = [s for s in swings if s["type"] == "high"]
    if not swing_highs:
        return None

    for i in range(last_idx, cutoff - 1, -1):
        c = candles[i]
        if c.high <= 0 or c.close <= 0:
            continue
        prior = [s for s in swing_highs if s["index"] < i]
        if not prior:
            continue
        for sh in prior[-levels_to_check:][::-1]:
            level = sh["price"]
            if level <= 0:
                continue
            if c.high > level and c.close < level:
                wick_pct = ((c.high - level) / level * 100) if level > 0 else 0.0
                if wick_pct < min_wick_pct:
                    continue
                vol_ratio = (c.volume / avg_volume) if avg_volume > 0 else 0.0
                return {
                    "candle_index": i,
                    "candles_ago": last_idx - i,
                    "swept_level": level,
                    "swept_swing_index": sh["index"],
                    "high_reached": c.high,
                    "close": c.close,
                    "wick_size_pct": round(wick_pct, 3),
                    "volume_ratio": round(vol_ratio, 3),
                    "volume_confirmed": vol_ratio >= 1.5,
                }
    return None


# ── Main analysis ──────────────────────────────────────────────────

def analyze(
    candles: List[Candle],
    *,
    pivot_n: int = 3,
    lookback: int = 5,
    min_wick_pct: float = 0.3,
    swings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Full liquidity-sweep analysis on a candle series.

    Args:
      candles: input series.
      pivot_n: fractal-pivot half-window (matches Phase 3 / Phase 8).
      lookback: how many recent candles to scan for sweeps.
      min_wick_pct: minimum wick depth past swept level (% of level).
      swings: optional pre-classified swings (reuse from Phase 8 to
        avoid recomputing).

    Returns:
      sweep_high                 dict or None
      sweep_low                  dict or None
      sweep_high_recent          bool — within last `lookback` candles
      sweep_low_recent           bool
      sweep_verdict              BULLISH_SWEEP / BEARISH_SWEEP / NONE
                                 (BULLISH if sweep_low present and
                                  volume-confirmed OR strong wick)
    """
    blank = {
        "sweep_high": None,
        "sweep_low": None,
        "sweep_high_recent": False,
        "sweep_low_recent": False,
        "sweep_verdict": "NONE",
    }
    if not candles or len(candles) < (2 * pivot_n + 2):
        return blank

    if swings is None:
        ph = find_pivot_highs(candles, n=pivot_n)
        pl = find_pivot_lows(candles, n=pivot_n)
        swings = classify_swings(ph, pl)
    if not swings:
        return blank

    avg_vol = _trailing_avg_volume(candles, window=10)

    sweep_low = detect_sweep_low(
        candles, swings,
        lookback=lookback,
        min_wick_pct=min_wick_pct,
        avg_volume=avg_vol,
    )
    sweep_high = detect_sweep_high(
        candles, swings,
        lookback=lookback,
        min_wick_pct=min_wick_pct,
        avg_volume=avg_vol,
    )

    sweep_low_recent = bool(sweep_low)
    sweep_high_recent = bool(sweep_high)

    # Verdict — prefer the more recent sweep if both present
    verdict = "NONE"
    if sweep_low and sweep_high:
        # Whichever is more recent wins
        if sweep_low["candles_ago"] <= sweep_high["candles_ago"]:
            chosen = "low"
        else:
            chosen = "high"
    elif sweep_low:
        chosen = "low"
    elif sweep_high:
        chosen = "high"
    else:
        chosen = None

    if chosen == "low" and sweep_low:
        # Promote BULLISH_SWEEP if volume-confirmed OR wick is large
        # (>= 1.0% past level — that's a meaningful raid)
        if sweep_low["volume_confirmed"] or sweep_low["wick_size_pct"] >= 1.0:
            verdict = "BULLISH_SWEEP"
    elif chosen == "high" and sweep_high:
        if sweep_high["volume_confirmed"] or sweep_high["wick_size_pct"] >= 1.0:
            verdict = "BEARISH_SWEEP"

    return {
        "sweep_high": sweep_high,
        "sweep_low": sweep_low,
        "sweep_high_recent": sweep_high_recent,
        "sweep_low_recent": sweep_low_recent,
        "sweep_verdict": verdict,
    }
