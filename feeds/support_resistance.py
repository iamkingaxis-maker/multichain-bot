"""
Phase 3 of chart-reading rebuild — support / resistance level detection.

The chart feature an experienced trader looks at first. Pure rule-based
detection (no ML), validated against the data we already track.

Algorithm:
  1. Find swing pivots (fractal highs and lows) on each input timeframe
  2. Cluster nearby pivots into "levels" (a level = a price band that
     has been touched multiple times)
  3. Score each level by:
       - touch_count (how many pivots fall in the band)
       - max_volume (highest volume of any candle that touched it)
       - recency (more recent touches weighted higher)
  4. For the current price, surface:
       - nearest_support_pct_below   (distance to nearest level below)
       - nearest_resistance_pct_above (distance to nearest level above)
       - support_strength            (score of the nearest support)
       - resistance_strength         (score of the nearest resistance)
       - at_support (bool)           (within 1% of a strong support)
       - at_resistance (bool)        (within 1% of a strong resistance)
       - levels_below                (list of all levels below current)
       - levels_above                (list of all levels above current)

Pivot detection (industry-standard "fractal" method):
  A pivot HIGH at index i = candle.high[i] strictly greater than
  candle.high[i-N..i-1] AND candle.high[i+1..i+N].
  Same logic for pivot LOW with candle.low.

Choice of N:
  N=2 means "high (or low) than 2 candles on each side." Small N
  produces many small pivots (noisy). N=3 or 5 produces fewer, more
  significant pivots. We use N=3 by default — a balance between
  catching real reversals and filtering noise.

Clustering:
  Pivots within +/-1% of each other are grouped into a single level.
  Threshold tunable via tolerance_pct param.

This module is pure logic — input is a list of Candle objects, output
is the level analysis dict. No I/O, no caching. Caller (chart_reader)
runs it on whichever timeframe(s) are useful for the decision at hand.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple

from feeds.candle_utils import Candle


# ── Pivot detection ──────────────────────────────────────────────────

def find_pivot_highs(candles: List[Candle], n: int = 3) -> List[Tuple[int, float, float]]:
    """Return [(index, price, volume)] for each pivot high.

    A pivot high at index i means candles[i].high is strictly greater
    than candles[i-n .. i-1].high AND candles[i+1 .. i+n].high.
    """
    out: List[Tuple[int, float, float]] = []
    if len(candles) < 2 * n + 1:
        return out
    for i in range(n, len(candles) - n):
        ch = candles[i].high
        is_pivot = (
            all(ch > candles[j].high for j in range(i - n, i))
            and all(ch > candles[j].high for j in range(i + 1, i + n + 1))
        )
        if is_pivot:
            out.append((i, ch, candles[i].volume))
    return out


def find_pivot_lows(candles: List[Candle], n: int = 3) -> List[Tuple[int, float, float]]:
    """Return [(index, price, volume)] for each pivot low."""
    out: List[Tuple[int, float, float]] = []
    if len(candles) < 2 * n + 1:
        return out
    for i in range(n, len(candles) - n):
        cl = candles[i].low
        is_pivot = (
            all(cl < candles[j].low for j in range(i - n, i))
            and all(cl < candles[j].low for j in range(i + 1, i + n + 1))
        )
        if is_pivot:
            out.append((i, cl, candles[i].volume))
    return out


# ── Level clustering ─────────────────────────────────────────────────

def cluster_levels(
    pivots: List[Tuple[int, float, float]],
    tolerance_pct: float = 1.0,
) -> List[Dict[str, Any]]:
    """Group pivots within +/- tolerance_pct% into levels.

    Returns list of level dicts:
      price            average price of pivots in cluster
      touch_count      number of pivots in cluster
      max_volume       largest candle volume of any touch
      latest_index     highest index — most-recent touch
      indices          all pivot indices in cluster
    """
    if not pivots:
        return []
    # Sort by price ascending so we can sweep and cluster
    sorted_pivots = sorted(pivots, key=lambda x: x[1])

    clusters: List[List[Tuple[int, float, float]]] = []
    current_cluster: List[Tuple[int, float, float]] = [sorted_pivots[0]]
    for piv in sorted_pivots[1:]:
        cluster_avg = sum(p[1] for p in current_cluster) / len(current_cluster)
        if cluster_avg > 0 and abs(piv[1] - cluster_avg) / cluster_avg * 100 <= tolerance_pct:
            current_cluster.append(piv)
        else:
            clusters.append(current_cluster)
            current_cluster = [piv]
    clusters.append(current_cluster)

    out: List[Dict[str, Any]] = []
    for cl in clusters:
        prices = [p[1] for p in cl]
        out.append({
            "price": sum(prices) / len(prices),
            "touch_count": len(cl),
            "max_volume": max(p[2] for p in cl),
            "latest_index": max(p[0] for p in cl),
            "indices": sorted(p[0] for p in cl),
        })
    return out


# ── Level scoring ────────────────────────────────────────────────────

def score_level(
    level: Dict[str, Any],
    total_candles: int,
) -> float:
    """Compute a 0-100 strength score combining touches, volume, and
    recency.

    Components:
      touches_score  = min(level.touch_count, 5) / 5 * 50   (max 50)
      recency_score  = (latest_index / total_candles) * 30  (max 30)
      vol_score      = min(level.max_volume / max_vol, 1) * 20 — but
                       caller would need to pass max_vol; for now we
                       use a simple log-volume tier (max 20).

    Touch count dominates because a level confirmed 4-5 times is
    structurally meaningful. Recency matters because old levels
    decay. Volume confirms a level wasn't just noise.
    """
    touches_score = min(level["touch_count"], 5) / 5.0 * 50
    recency = level["latest_index"] / total_candles if total_candles > 0 else 0
    recency_score = max(0.0, min(1.0, recency)) * 30
    # Volume tier: log-scaled — anything below 1k = 0; 1k-10k = 5;
    # 10k-100k = 10; 100k-1m = 15; 1m+ = 20.
    v = level["max_volume"]
    if v < 1_000: vol_score = 0
    elif v < 10_000: vol_score = 5
    elif v < 100_000: vol_score = 10
    elif v < 1_000_000: vol_score = 15
    else: vol_score = 20
    return round(touches_score + recency_score + vol_score, 1)


# ── Main analysis ────────────────────────────────────────────────────

def analyze(
    candles: List[Candle],
    *,
    pivot_n: int = 3,
    cluster_tolerance_pct: float = 1.0,
    near_threshold_pct: float = 1.0,
    min_strength: float = 30.0,
) -> Dict[str, Any]:
    """Compute full S/R analysis for a single timeframe.

    Returns a feature dict with the most important numbers for
    downstream filtering:
      current_price
      nearest_support_price, nearest_support_pct_below, support_strength
      nearest_resistance_price, nearest_resistance_pct_above, resistance_strength
      at_support (bool — within near_threshold_pct of a level with
                  strength >= min_strength)
      at_resistance (bool — same for resistance)
      below_broken_support (bool — current price < a level that was
                  recent support; signals breakdown / falling knife)
      levels_below, levels_above (full list, sorted by price)

    near_threshold_pct controls how close "at" support means (default 1%)
    min_strength controls which levels count as "strong" for at_X bools
    """
    if not candles:
        return {
            "current_price": None,
            "nearest_support_price": None,
            "nearest_support_pct_below": None,
            "support_strength": None,
            "nearest_resistance_price": None,
            "nearest_resistance_pct_above": None,
            "resistance_strength": None,
            "at_support": False,
            "at_resistance": False,
            "below_broken_support": False,
            "levels_below": [],
            "levels_above": [],
        }

    current_price = candles[-1].close

    # Pivot detection
    pivot_highs = find_pivot_highs(candles, n=pivot_n)
    pivot_lows = find_pivot_lows(candles, n=pivot_n)

    # Cluster pivots into levels
    high_levels = cluster_levels(pivot_highs, tolerance_pct=cluster_tolerance_pct)
    low_levels = cluster_levels(pivot_lows, tolerance_pct=cluster_tolerance_pct)

    total_candles = len(candles)
    for lvl in high_levels:
        lvl["strength"] = score_level(lvl, total_candles)
    for lvl in low_levels:
        lvl["strength"] = score_level(lvl, total_candles)

    # Combine into below/above current price
    levels_below = [l for l in (high_levels + low_levels) if l["price"] < current_price]
    levels_above = [l for l in (high_levels + low_levels) if l["price"] > current_price]
    levels_below.sort(key=lambda l: -l["price"])  # closest below first
    levels_above.sort(key=lambda l: l["price"])   # closest above first

    nearest_support = levels_below[0] if levels_below else None
    nearest_resistance = levels_above[0] if levels_above else None

    nss_pct = (
        (current_price - nearest_support["price"]) / current_price * 100
        if nearest_support and current_price > 0 else None
    )
    nra_pct = (
        (nearest_resistance["price"] - current_price) / current_price * 100
        if nearest_resistance and current_price > 0 else None
    )

    at_support = bool(
        nearest_support
        and nss_pct is not None
        and nss_pct <= near_threshold_pct
        and nearest_support["strength"] >= min_strength
    )
    at_resistance = bool(
        nearest_resistance
        and nra_pct is not None
        and nra_pct <= near_threshold_pct
        and nearest_resistance["strength"] >= min_strength
    )

    # Below broken support: did we recently fall through a level?
    # Check if ANY recent level (in the last 30% of candles) had
    # touches at or below current price + near_threshold_pct
    # but also had touches above current_price. That signals
    # we broke through it.
    recent_cutoff = int(total_candles * 0.7)
    below_broken_support = False
    for lvl in low_levels:
        # Consider only "recent" support levels
        if lvl["latest_index"] < recent_cutoff:
            continue
        if lvl["strength"] < min_strength:
            continue
        # If current price is more than near_threshold_pct BELOW this
        # level, we've broken support
        if current_price > 0 and lvl["price"] > current_price * (1 + near_threshold_pct / 100):
            below_broken_support = True
            break

    return {
        "current_price": current_price,
        "nearest_support_price": nearest_support["price"] if nearest_support else None,
        "nearest_support_pct_below": round(nss_pct, 3) if nss_pct is not None else None,
        "support_strength": nearest_support["strength"] if nearest_support else None,
        "nearest_resistance_price": nearest_resistance["price"] if nearest_resistance else None,
        "nearest_resistance_pct_above": round(nra_pct, 3) if nra_pct is not None else None,
        "resistance_strength": nearest_resistance["strength"] if nearest_resistance else None,
        "at_support": at_support,
        "at_resistance": at_resistance,
        "below_broken_support": below_broken_support,
        "levels_below_count": len(levels_below),
        "levels_above_count": len(levels_above),
        # Lists trimmed for entry_meta serialization size — top 3 each
        "levels_below": [
            {"price": round(l["price"], 8), "strength": l["strength"], "touches": l["touch_count"]}
            for l in levels_below[:3]
        ],
        "levels_above": [
            {"price": round(l["price"], 8), "strength": l["strength"], "touches": l["touch_count"]}
            for l in levels_above[:3]
        ],
    }
