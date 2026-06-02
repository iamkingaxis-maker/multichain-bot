"""Live-probe fill instrumentation (piece 3/4) — pure, no I/O.

The probe's deliverable is a per-leg fill dataset to answer the fidelity gate (does the
candidate's edge survive real execution; break-even ~0.9pp/leg). These pure helpers turn
raw (mid, fill, route, latency, ...) into the signed metrics the live bridge (piece 1b)
stamps on each trade record. Convention: a POSITIVE slippage % is ADVERSE (you paid up on
a buy / received less on a sell) — that is the cost the gate measures.
"""
from typing import Optional


def fill_slippage_pct(mid: Optional[float], fill: Optional[float], side: str) -> Optional[float]:
    """Signed per-leg slippage %, ADVERSE-positive.

    buy:  (fill - mid)/mid * 100   -> positive = paid more than mid (adverse)
    sell: (mid - fill)/mid * 100   -> positive = received less than mid (adverse)
    Returns None if inputs are unusable.
    """
    try:
        m = float(mid); f = float(fill)
    except (TypeError, ValueError):
        return None
    if m <= 0 or f <= 0:
        return None
    raw = (f - m) / m * 100.0
    return round(raw if side == "buy" else -raw, 4)


def entry_vs_local_low_pct(entry: Optional[float], local_low: Optional[float]) -> Optional[float]:
    """How far ABOVE the recent local low the entry actually filled, % — the
    'paper assumes the dip-low, live doesn't' gap. Positive = filled above the low.
    """
    try:
        e = float(entry); lo = float(local_low)
    except (TypeError, ValueError):
        return None
    if e <= 0 or lo <= 0:
        return None
    return round((e - lo) / lo * 100.0, 4)


def fill_metrics(side: str, mid: Optional[float], fill: Optional[float],
                 route: Optional[str] = None, latency_ms: Optional[float] = None,
                 ultra_slippage_pct: Optional[float] = None,
                 entry_price: Optional[float] = None,
                 local_low: Optional[float] = None,
                 partial_fill_frac: Optional[float] = None) -> dict:
    """Assemble the per-leg instrumentation dict the bridge stamps on the trade record.
    All keys prefixed `live_` so they sit alongside the existing shadow stamps and are
    trivially filterable. Missing inputs -> None (fail-soft)."""
    d = {
        "live_side": side,
        "live_mid_price": mid,
        "live_fill_price": fill,
        "live_slippage_pct": fill_slippage_pct(mid, fill, side),
        "live_route": route,
        "live_latency_ms": latency_ms,
        "live_ultra_slippage_pct": ultra_slippage_pct,
        "live_partial_fill_frac": partial_fill_frac,
    }
    if side == "buy":
        d["live_entry_vs_local_low_pct"] = entry_vs_local_low_pct(entry_price, local_low)
    return d


# ── Live-execution bridge GATING (piece 1b safety core) ───────────────────────
# The fail-closed decision for whether a bot's fill routes through the live
# MEV-protected swap. ALL THREE must be true; any false -> paper. This is the
# single guard that keeps the fleet paper unless a probe is DELIBERATELY enabled
# AND the env flags are set AND a real key is present.
def should_route_live(live_probe: bool, ultra_enabled: bool, has_private_key: bool) -> bool:
    """True ONLY when a bot opted in (live_probe), Ultra is enabled (USE_JUPITER_ULTRA),
    AND a real private key is present (live mode). Fail-closed: any missing -> False (paper).
    Size is FIXED per probe bot (probe_tightexit_live_{20,50,100}) — no in-bot rotation;
    each size is its own bot for a clean per-token paired measurement."""
    return bool(live_probe) and bool(ultra_enabled) and bool(has_private_key)
