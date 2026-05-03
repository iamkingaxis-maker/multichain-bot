"""
Phase 10 of chart-reading rebuild — stop-cluster level detection.

Memecoin-specific. On low-cap (~$1M-$100M FDV) tokens, retail stops
cluster at predictable levels because most traders use the same
defaults: round prices, fixed percentages below recent highs (5%/8%/
10%/15%), and visible swing lows. Smart money knows this and raids
the densest stop pool — that's the thing Phase 9 (liquidity_sweeps)
detects after the fact. Phase 10 detects it BEFORE: which level is
the most likely raid target?

Cluster types (a level is "denser" when multiple types stack on it):
  round_price       psychologically significant round numbers
                    (0.005, 0.01, 0.05, 0.10, 0.50, 1, 5, etc.).
  pct_below_high    standard stop-loss percentages below the recent
                    high (5%, 8%, 10%, 15%, 20%).
  swing_low         prior fractal pivot lows in the visible window.

Density score = number of cluster types within ±1% of each other.
A level with all 3 types stacking = density 3 = highest-conviction
sweep target. Density 1 = ordinary level.

Output:
  nearest_stop_cluster_price         price of the densest cluster below current
  nearest_stop_cluster_pct_below     % distance from current to that cluster
  stop_cluster_density               1..3 (how many types overlap)
  stop_cluster_at_round_price        bool — round price contributes
  stop_cluster_at_pct_below          bool — fixed pct contributes
  stop_cluster_at_swing_low          bool — prior swing low contributes
  stop_cluster_count_total           total number of cluster levels below current

This is pure logic on a candle series. Pairs with Phase 9 — when a
sweep fires below a high-density cluster, it's the strongest reversal
signal in the framework.
"""
from __future__ import annotations

import math
from typing import List, Dict, Any, Optional, Tuple

from feeds.candle_utils import Candle
from feeds.support_resistance import find_pivot_lows


def _round_price_levels(current_price: float, span_powers: int = 2) -> List[float]:
    """Generate psychologically round price levels around current_price.

    For each order-of-magnitude near current_price, emit 1x, 2x, 5x
    multiples (i.e., 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, ...).
    """
    if current_price <= 0:
        return []
    log10p = math.log10(current_price)
    base = int(math.floor(log10p))
    out = []
    for power in range(base - span_powers, base + span_powers + 1):
        for mantissa in (1, 2, 5):
            level = mantissa * (10 ** power)
            if level > 0:
                out.append(level)
    return sorted(set(out))


def _pct_below_levels(recent_high: float, percents=(5.0, 8.0, 10.0, 15.0, 20.0)) -> List[float]:
    if recent_high <= 0:
        return []
    return [recent_high * (1 - p / 100.0) for p in percents]


# ── Main analysis ──────────────────────────────────────────────────

def analyze(
    candles: List[Candle],
    current_price: Optional[float] = None,
    *,
    pivot_n: int = 2,
    cluster_tolerance_pct: float = 1.0,
) -> Dict[str, Any]:
    """Find the densest stop-cluster level below current price.

    Returns the densest level (most cluster-type overlap) within the
    band [50% below current .. just below current]. Distant clusters
    aren't useful — we want to know where the next stop raid will hit.
    """
    blank = {
        "nearest_stop_cluster_price": None,
        "nearest_stop_cluster_pct_below": None,
        "stop_cluster_density": 0,
        "stop_cluster_at_round_price": False,
        "stop_cluster_at_pct_below": False,
        "stop_cluster_at_swing_low": False,
        "stop_cluster_count_total": 0,
    }
    if not candles:
        return blank
    if current_price is None:
        current_price = candles[-1].close
    if current_price <= 0:
        return blank

    # Recent high — last 12 candles is a reasonable "recent" window
    recent_window = candles[-12:] if len(candles) >= 12 else candles
    recent_high = max(c.high for c in recent_window)

    # Build candidate levels with type tag
    candidates: List[Tuple[float, str]] = []
    for lvl in _round_price_levels(current_price):
        if 0 < lvl < current_price:
            candidates.append((lvl, "round"))
    for lvl in _pct_below_levels(recent_high):
        if 0 < lvl < current_price:
            candidates.append((lvl, "pct"))
    if len(candles) >= 2 * pivot_n + 1:
        for _i, p, _v in find_pivot_lows(candles, n=pivot_n):
            if 0 < p < current_price:
                candidates.append((p, "swing"))

    if not candidates:
        return blank

    # Restrict to within 50% below current — stops further down aren't
    # raidable in a single move; not the next-target level.
    floor = current_price * 0.50
    band = [(p, t) for p, t in candidates if p >= floor]
    if not band:
        # Fall back to whatever we have
        band = candidates

    # For each level, count how many DIFFERENT types overlap within tolerance.
    best_price: Optional[float] = None
    best_density = 0
    best_types: set = set()
    tol = cluster_tolerance_pct / 100.0

    for price, _src_type in band:
        types_here: set = set()
        for other_price, other_type in band:
            if abs(other_price - price) / max(price, 1e-12) <= tol:
                types_here.add(other_type)
        density = len(types_here)
        # Prefer denser; tie-break with closer to current (= higher price)
        if density > best_density or (density == best_density and (best_price is None or price > best_price)):
            best_density = density
            best_price = price
            best_types = types_here

    if best_price is None:
        return blank

    pct_below = (current_price - best_price) / current_price * 100.0

    return {
        "nearest_stop_cluster_price": round(best_price, 10),
        "nearest_stop_cluster_pct_below": round(pct_below, 3),
        "stop_cluster_density": best_density,
        "stop_cluster_at_round_price": "round" in best_types,
        "stop_cluster_at_pct_below": "pct" in best_types,
        "stop_cluster_at_swing_low": "swing" in best_types,
        "stop_cluster_count_total": len(band),
    }
