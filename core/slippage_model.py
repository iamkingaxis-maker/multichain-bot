"""Paper-fill slippage + fee model (P2, 2026-05-25).

Production paper P&L previously booked fills at the raw mid price — no
slippage, no fees — so $/day was optimistic and the optimism GREW with
position size (a $5k order "filled" as cleanly as a $20 one). This model
applies a realistic round-trip haircut:

    buy_fill  = mid * (1 + (slip_pct + fee_pct) / 100)
    sell_fill = mid * (1 - (slip_pct + fee_pct) / 100)

Slippage is interpolated from the Jupiter slip curve the scanner already
samples at $500/$2k/$5k (keys slip_buy_{500,2000,5000}_pct). Small orders
pay ~0 slippage (fee dominates); large orders pay the curve — which is what
makes the capital-absorption experiment valid. The sell-side curve isn't
persisted in entry_meta, so we reuse the buy-side estimate (symmetric proxy).

Env-tunable so the assumptions aren't a hard bake-in:
    PAPER_SLIPPAGE_ENABLED   (default "true")
    PAPER_FEE_PCT_PER_SIDE   (default 0.30 — Jupiter + priority + Jito proxy)
    PAPER_DEFAULT_SLIP_PCT   (default 0.70 — fallback when curve is missing)
"""
from __future__ import annotations
import os
from typing import Optional

SLIPPAGE_ENABLED = os.environ.get("PAPER_SLIPPAGE_ENABLED", "true").lower() == "true"
FEE_PCT_PER_SIDE = float(os.environ.get("PAPER_FEE_PCT_PER_SIDE", "0.30"))
DEFAULT_SLIP_PCT = float(os.environ.get("PAPER_DEFAULT_SLIP_PCT", "0.70"))

_CURVE_SIZES = (500.0, 2000.0, 5000.0)


def _interp(size_usd: float, pts: list[tuple[float, float]]) -> float:
    """Piecewise-linear slip% for size_usd. Anchored at (0,0): tiny orders
    approach zero slippage. Linear between sampled points; extrapolates the
    last segment's slope above the top sample."""
    pts = sorted(pts)
    anchored = [(0.0, 0.0)] + pts
    if size_usd <= 0:
        return 0.0
    if size_usd >= anchored[-1][0]:
        # extrapolate using the slope of the final segment
        (x0, y0), (x1, y1) = anchored[-2], anchored[-1]
        slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
        return max(0.0, y1 + slope * (size_usd - x1))
    for (x0, y0), (x1, y1) in zip(anchored, anchored[1:]):
        if x0 <= size_usd <= x1:
            frac = (size_usd - x0) / (x1 - x0) if x1 != x0 else 0.0
            return max(0.0, y0 + frac * (y1 - y0))
    return DEFAULT_SLIP_PCT


def slip_pct_for_size(size_usd: float, meta: Optional[dict], side: str = "buy") -> float:
    """Estimate slippage % for an order of size_usd. Uses the sampled
    slip_{side}_{500,2000,5000}_pct curve from meta; falls back to the buy
    curve (sell curve isn't persisted), then to DEFAULT_SLIP_PCT."""
    meta = meta or {}
    for s in (side, "buy"):  # sell curve absent → reuse buy curve
        pts = []
        for sz, label in zip(_CURVE_SIZES, ("500", "2000", "5000")):
            v = meta.get(f"slip_{s}_{label}_pct")
            if isinstance(v, (int, float)) and v >= 0:
                pts.append((sz, float(v)))
        if pts:
            return _interp(size_usd, pts)
    return DEFAULT_SLIP_PCT


def buy_fill_price(mid: float, size_usd: float, meta: Optional[dict]) -> tuple[float, float]:
    """Return (effective_buy_price, slip_pct_used). You pay UP on a buy."""
    if not SLIPPAGE_ENABLED or mid <= 0:
        return mid, 0.0
    slip = slip_pct_for_size(size_usd, meta, "buy")
    return mid * (1.0 + (slip + FEE_PCT_PER_SIDE) / 100.0), slip


def sell_fill_price(mid: float, slip_pct: Optional[float]) -> float:
    """Return effective sell price. You receive LESS on a sell. slip_pct is
    the estimate stored at buy time (symmetric proxy); None → default."""
    if not SLIPPAGE_ENABLED or mid <= 0:
        return mid
    slip = slip_pct if (isinstance(slip_pct, (int, float)) and slip_pct >= 0) else DEFAULT_SLIP_PCT
    return mid * (1.0 - (slip + FEE_PCT_PER_SIDE) / 100.0)
