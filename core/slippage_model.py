"""Paper-fill cost model (P2, re-spec 2026-05-25).

CALIBRATION FINDING (fee-calibration probe): the cost of a memecoin swap at
our trade sizes is NOT proportional slippage — live Jupiter price-impact at
$20-$100 is ~0%. The real cost is the FIXED priority fee per transaction
(trader.py caps it at MAX_PRIORITY_LAMPORTS = 0.001 SOL ≈ $0.17/tx) plus the
token's own price impact (from the Jupiter slip curve the scanner samples).

So cost has two parts, combined as an effective per-side %:

    per_side_pct(size) = impact_pct(size)        # from sampled slip curve, ~0 small
                       + fee_usd_per_tx / size * 100   # FIXED $ → big % on small
                                                        # trades, tiny % on big ones

    buy_fill  = mid * (1 + per_side_pct/100)   # pay up
    sell_fill = mid * (1 - per_side_pct/100)   # receive less

Key consequence: because the fee is a fixed $/tx, **bigger positions are far
cheaper per dollar traded** ($0.10 is 0.5% of $20 but 0.06% of $160). This is
why sizing-up selective trades beats high-volume small trades.

Env-tunable:
    PAPER_SLIPPAGE_ENABLED   (default "true")
    PAPER_FEE_USD_PER_TX     (default 0.10 — priority fee; cap is ~$0.17)
    PAPER_DEFAULT_IMPACT_PCT (default 0.10 — impact when slip curve absent)
"""
from __future__ import annotations
import os
from typing import Optional

SLIPPAGE_ENABLED = os.environ.get("PAPER_SLIPPAGE_ENABLED", "true").lower() == "true"
FEE_USD_PER_TX = float(os.environ.get("PAPER_FEE_USD_PER_TX", "0.10"))
DEFAULT_IMPACT_PCT = float(os.environ.get("PAPER_DEFAULT_IMPACT_PCT", "0.10"))

_CURVE_SIZES = (500.0, 2000.0, 5000.0)


def _interp(size_usd: float, pts: list[tuple[float, float]]) -> float:
    """Piecewise-linear impact% for size_usd, anchored at (0,0). Tiny orders
    approach zero impact (Jupiter-confirmed). Extrapolates the last segment."""
    pts = sorted(pts)
    anchored = [(0.0, 0.0)] + pts
    if size_usd <= 0:
        return 0.0
    if size_usd >= anchored[-1][0]:
        (x0, y0), (x1, y1) = anchored[-2], anchored[-1]
        slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
        return max(0.0, y1 + slope * (size_usd - x1))
    for (x0, y0), (x1, y1) in zip(anchored, anchored[1:]):
        if x0 <= size_usd <= x1:
            frac = (size_usd - x0) / (x1 - x0) if x1 != x0 else 0.0
            return max(0.0, y0 + frac * (y1 - y0))
    return DEFAULT_IMPACT_PCT


def impact_pct_for_size(size_usd: float, meta: Optional[dict], side: str = "buy") -> float:
    """Token price-impact % for an order of size_usd, from the sampled
    slip_{side}_{500,2000,5000}_pct curve. Falls back to the buy curve (sell
    curve isn't persisted), then to DEFAULT_IMPACT_PCT."""
    meta = meta or {}
    for s in (side, "buy"):
        pts = []
        for sz, label in zip(_CURVE_SIZES, ("500", "2000", "5000")):
            v = meta.get(f"slip_{s}_{label}_pct")
            if isinstance(v, (int, float)) and v >= 0:
                pts.append((sz, float(v)))
        if pts:
            return _interp(size_usd, pts)
    return DEFAULT_IMPACT_PCT


def per_side_cost_pct(size_usd: float, impact_pct: float) -> float:
    """Effective per-side cost %: token impact + the FIXED priority fee
    expressed as a % of this trade's size (the size-dependent part)."""
    fee_pct = (FEE_USD_PER_TX / size_usd * 100.0) if size_usd > 0 else 0.0
    return max(0.0, impact_pct) + fee_pct


def buy_fill_price(mid: float, size_usd: float, meta: Optional[dict]) -> tuple[float, float]:
    """Return (effective_buy_price, impact_pct_used). You pay UP on a buy.
    impact_pct is stored on the position so the sell can reuse it (the fee is
    re-derived from sell size)."""
    if not SLIPPAGE_ENABLED or mid <= 0:
        return mid, 0.0
    impact = impact_pct_for_size(size_usd, meta, "buy")
    return mid * (1.0 + per_side_cost_pct(size_usd, impact) / 100.0), impact


def sell_fill_price(mid: float, size_usd: float, impact_pct: Optional[float]) -> float:
    """Return effective sell price. You receive LESS. impact_pct is the
    estimate stashed at buy time; the fixed fee is re-derived from size."""
    if not SLIPPAGE_ENABLED or mid <= 0:
        return mid
    imp = impact_pct if (isinstance(impact_pct, (int, float)) and impact_pct >= 0) else DEFAULT_IMPACT_PCT
    return mid * (1.0 - per_side_cost_pct(size_usd, imp) / 100.0)
