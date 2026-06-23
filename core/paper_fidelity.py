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
    """True (skip) when the gate is armed (mode shadow/enforce) AND the fresh
    source indicates NO reachable price route at all, mirroring a live no-route
    abort. A reachable route = "onchain" OR "jupiter" (both are live-fillable).
    Only "none"/""/None/unknown sources are treated as no-route. Fail-open: any
    error => False (don't skip)."""
    try:
        m = str(mode).strip().lower()
        if m not in ("shadow", "enforce"):
            return False
        if fresh_source is None:
            return True
        src = str(fresh_source).strip().lower()
        return src not in ("onchain", "jupiter")
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

def caps_would_block(open_n, open_usd, size_usd, max_n, max_usd) -> bool:
    """Mirror the LIVE per-token cap arithmetic so the PAPER twin can flag a buy
    that LIVE's caps would refuse (recorded as a reconcile/scoreboard flag — it
    does NOT remove paper's own throughput).

    Returns True (would block) when EITHER the position-count cap is met
    (open_n >= max_n) OR adding this buy exceeds the $ cap
    ((open_usd + size_usd) > max_usd). Matches feeds/dip_scanner.py LIVE cap
    (>= on count, strictly > on usd).

    FAIL-OPEN: any None/garbage input => False (don't block) — this is shadow
    telemetry, never a real-money gate."""
    try:
        n = int(open_n)
        ou = float(open_usd)
        sz = float(size_usd)
        mn = int(max_n)
        mu = float(max_usd)
    except (TypeError, ValueError):
        return False
    return n >= mn or (ou + sz) > mu

def paper_entry_decision(decision_mid, fresh_price, fresh_source, modeled_slip_pct,
                         mode, size_usd, slip_pct=None, fee_usd=None, max_runup=0.05):
    """Compose the full paper-buy fidelity decision into a single pure call.

    A dip that PRINTED was genuinely fillable (someone filled it) — the issue is
    execution price/speed, NOT that the opportunity is fake. So paper TAKES the
    trade at the realistic fill price instead of skipping; only a genuine no-route
    (no on-chain swap path) hard-skips.

    Returns (entry_basis|None, reason). None => paper should SKIP the buy. The
    ONLY None return is "no_route" (can't fill what has no route). FAIL-OPEN:
    any exception => (decision_mid, "error_fallback") so this never blocks the
    buy path.

    Order, when mode != off:
      no_route_skip            -> (None, "no_route")   # ONLY hard skip
      reprice past max_runup   -> TAKE at FRESH price -> (eff, "runup_taken")
      slippage_cap exceeded    -> TAKE at max(baseline, modeled) slip
                                  -> (eff, "slippage_taken")
      normal                   -> (eff, "fresh")
    """
    try:
        m = str(mode).strip().lower() if mode is not None else "off"
        if m == "off":
            return (decision_mid, "off")
        # 1) no_route is the ONLY hard skip — can't fill what has no route.
        if no_route_skip(fresh_source, m):
            return (None, "no_route")
        sp = slip_pct if slip_pct is not None else measured_live_slip_pct()
        fu = fee_usd if fee_usd is not None else paper_fee_usd()
        eb, why = reprice_entry(decision_mid, fresh_price, max_runup=max_runup)
        # 2) Run-up past max: TAKE IT at the real (fresh) price — honest fill,
        #    not a skip. reprice_entry returns None+"runup_abort" on run-up; in
        #    that case use the FRESH price as the entry basis.
        if eb is None and why == "runup_abort":
            try:
                basis = float(fresh_price)
            except (TypeError, ValueError):
                basis = decision_mid
            eff = effective_fill(basis, "buy", sp, fu, size_usd)
            return (eff, "runup_taken")
        if eb is None:
            # any other reprice failure (bad_mid) — fail-open to decision_mid
            eff = effective_fill(decision_mid, "buy", sp, fu, size_usd)
            return (eff, "fresh")
        # 3) Slippage-cap: do NOT skip — TAKE IT at the realistic (higher) slip,
        #    reflecting the real thin-book fill cost.
        if slippage_cap_skip(modeled_slip_pct):
            try:
                slip_used = max(float(sp), float(modeled_slip_pct or 0))
            except (TypeError, ValueError):
                slip_used = sp
            eff = effective_fill(eb, "buy", slip_used, fu, size_usd)
            return (eff, "slippage_taken")
        # 4) Normal fresh fill.
        eff = effective_fill(eb, "buy", sp, fu, size_usd)
        return (eff, "fresh")
    except Exception:
        return (decision_mid, "error_fallback")

def paper_exit_decision(decision_mid, fresh_price, exit_reason, mode, size_usd,
                        slip_pct=None, fee_usd=None, low_price=None):
    """Compose the full paper-SELL fidelity decision into a single pure call.

    Returns (exit_basis, reason). Unlike the buy path, a sell NEVER aborts/skips
    (a held position must ALWAYS be able to exit) — there is no runup_abort /
    no_route skip here.

    Order, when mode != off:
      reprice to fresh (fresh_price if valid >0 else decision_mid)
      base = effective_fill(repriced, "sell", slip, fee, size)
      exit_basis = base * (1 - gap_through_extra_pct(exit_reason)/100)  # stops only

    FAIL-OPEN: mode=="off" => (decision_mid, "off"); any exception =>
    (decision_mid, "error_fallback") so this never raises into the sell path.
    """
    try:
        m = str(mode).strip().lower() if mode is not None else "off"
        if m == "off":
            return (decision_mid, "off")
        try:
            fp = float(fresh_price) if fresh_price is not None else 0.0
        except (TypeError, ValueError):
            fp = 0.0
        repriced = fp if fp > 0 else decision_mid
        sp = slip_pct if slip_pct is not None else measured_live_slip_pct()
        fu = fee_usd if fee_usd is not None else paper_fee_usd()
        base = effective_fill(repriced, "sell", sp, fu, size_usd)
        exit_basis = base * (1.0 - gap_through_extra_pct(exit_reason) / 100.0)
        # CLAMP-TO-LOW (2026-06-23): a paper sell can NOT fill below the lowest price
        # the token actually traded. The flat gap-through haircut was booking exits
        # ~5pp below the observed low (a -13.35% stop booked -19.1%), inflating the
        # drawdown beyond reality. Clamp to ``low_price`` (the position's MAE price)
        # so the haircut still models REAL gap-through (a true rug's MAE is already
        # deep — QAI MAE -55% still books ~-55%) but can't penalize below what the
        # token printed. Fail-open: bad/absent low_price -> no clamp.
        if low_price is not None:
            try:
                lp = float(low_price)
                if lp > 0 and exit_basis < lp:
                    exit_basis = lp
            except (TypeError, ValueError):
                pass
        return (exit_basis, "fresh")
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
