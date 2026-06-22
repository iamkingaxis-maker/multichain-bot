"""Pure helpers that make the PAPER twin simulate the LIVE bot's execution
constraints, so paper P&L predicts live. Every helper is pure + fail-open."""
from __future__ import annotations
import os

def paper_fidelity_enabled(flag: str, default: str = "off") -> str:
    try:
        v = os.environ.get(flag, default).strip().lower()
    except Exception:
        return default
    return v if v in ("off", "on", "shadow", "enforce") else default

def reprice_entry(decision_mid, fresh_price, max_runup=None):
    """Entry basis paper should BOOK: the reachable fresh price, mirroring live.
    Returns (entry_basis|None, reason). None => paper skips (mirrors live abort)."""
    try:
        dm = float(decision_mid)
    except (TypeError, ValueError):
        return (None, "bad_mid")
    try:
        fp = float(fresh_price) if fresh_price is not None else 0.0
    except (TypeError, ValueError):
        fp = 0.0
    if fp <= 0:
        return (dm, "stale_fallback")
    if max_runup is not None and dm > 0:
        runup = (fp / dm) - 1.0
        if runup > float(max_runup):
            return (None, "runup_abort")
    return (fp, "fresh")

def measured_live_slip_pct() -> float:
    """Measured live slippage (%) for the token class. Env PAPER_LIVE_SLIP_PCT, default 1.5."""
    try:
        return float(os.environ.get("PAPER_LIVE_SLIP_PCT", "1.5"))
    except Exception:
        return 1.5

def paper_fee_usd() -> float:
    """Per-tx fee in USD that paper should book. Env PAPER_FEE_USD_PER_TX, default 0.17."""
    try:
        return float(os.environ.get("PAPER_FEE_USD_PER_TX", "0.17"))
    except Exception:
        return 0.17

def effective_fill(mid, side, slip_pct, fee_usd, size_usd) -> float:
    """Price paper should BOOK including measured live slippage + fee drag.
    buy pays up (mid * (1 + slip + fee_frac)); sell receives less. Fail-open:
    bad mid => return mid unchanged."""
    try:
        m = float(mid)
    except (TypeError, ValueError):
        return mid
    try:
        slip = float(slip_pct) / 100.0
    except (TypeError, ValueError):
        slip = 0.0
    try:
        sz = float(size_usd)
        fee_frac = (float(fee_usd) / sz) if sz else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        fee_frac = 0.0
    drag = slip + fee_frac
    if str(side).strip().lower() == "buy":
        return m * (1.0 + drag)
    return m * (1.0 - drag)
