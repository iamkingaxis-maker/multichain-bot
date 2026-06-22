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

def no_route_skip(fresh_source, mode) -> bool:
    """True (skip) when the gate is armed (mode shadow/enforce) AND there is no
    on-chain fresh price route, mirroring a live no-route abort. Fail-open: any
    missing/unrecognized data => False (don't skip)."""
    try:
        m = str(mode).strip().lower()
        if m not in ("shadow", "enforce"):
            return False
        if fresh_source is None:
            return False
        src = str(fresh_source).strip().lower()
        if not src:
            return False
        return src != "onchain"
    except Exception:
        return False

def slippage_cap_skip(modeled_slip_pct, cap_pct=None) -> bool:
    """True (skip) when modeled slippage (%) meets/exceeds the cap, mirroring a
    live slippage-cap revert. Default cap = PROBE_ULTRA_SLIPPAGE_BPS env /100
    (default 400 bps => 4.0%). Fail-open: bad/missing slip => False."""
    try:
        slip = float(modeled_slip_pct)
    except (TypeError, ValueError):
        return False
    try:
        if cap_pct is None:
            cap = float(os.environ.get("PROBE_ULTRA_SLIPPAGE_BPS", "400")) / 100.0
        else:
            cap = float(cap_pct)
    except (TypeError, ValueError):
        return False
    return slip >= cap

def paper_entry_decision(decision_mid, fresh_price, fresh_source, modeled_slip_pct,
                         mode, size_usd, slip_pct=None, fee_usd=None, max_runup=0.05):
    """Compose the full paper-buy fidelity decision into a single pure call.

    Returns (entry_basis|None, reason). None => paper should SKIP the buy
    (mirrors a live abort). FAIL-OPEN: any exception => (decision_mid,
    "error_fallback") so this never blocks the buy path.

    Order, when mode != off:
      no_route_skip -> (None,"no_route")
      reprice_entry -> if None (runup) => (None,"runup_abort")
      slippage_cap_skip -> (None,"slippage_cap")
      effective_fill(repriced,"buy",...) => (eff,"fresh")
    """
    try:
        m = str(mode).strip().lower() if mode is not None else "off"
        if m == "off":
            return (decision_mid, "off")
        if no_route_skip(fresh_source, m):
            return (None, "no_route")
        eb, why = reprice_entry(decision_mid, fresh_price, max_runup=max_runup)
        if eb is None:
            return (None, why or "runup_abort")
        if slippage_cap_skip(modeled_slip_pct):
            return (None, "slippage_cap")
        sp = slip_pct if slip_pct is not None else measured_live_slip_pct()
        fu = fee_usd if fee_usd is not None else paper_fee_usd()
        eff = effective_fill(eb, "buy", sp, fu, size_usd)
        return (eff, "fresh")
    except Exception:
        return (decision_mid, "error_fallback")

def gap_through_extra_pct(exit_reason, base_pct=None) -> float:
    """Extra NEGATIVE slippage (%) for gap-prone exits, mirroring live fills
    landing BELOW the trigger on dumps. Returns GAP_THROUGH_HAIRCUT_PCT (env,
    default 5.0) when the exit reason names a gap-prone exit (substring match on
    lowercased reason: hard_stop/stop/fast_bail/giveback); else 0.0 (e.g. TP).
    Caller subtracts this from the sell price for those exits only. Pure +
    fail-open: None/garbage reason or bad env => 0.0 / default, never raises."""
    try:
        r = str(exit_reason).strip().lower()
    except Exception:
        return 0.0
    if not r:
        return 0.0
    if not any(tok in r for tok in ("hard_stop", "stop", "fast_bail", "giveback")):
        return 0.0
    try:
        if base_pct is not None:
            return float(base_pct)
        return float(os.environ.get("GAP_THROUGH_HAIRCUT_PCT", "5.0"))
    except (TypeError, ValueError):
        return 5.0
