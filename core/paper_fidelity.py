"""Pure helpers that make the PAPER twin simulate the LIVE bot's execution
constraints, so paper P&L predicts live. Every helper is pure + fail-open."""
from __future__ import annotations
import logging
import math
import os

# Imported at module level so the EXIT_SLIP_LIQ liquidity-scaled path can be
# monkeypatched in tests (patch core.paper_fidelity.impact_pct_for_size) and so a
# raise inside it is caught by the gate's fail-open guard.
from core.slippage_model import impact_pct_for_size

logger = logging.getLogger(__name__)

# Hard backstop so a modeled fill can never cross zero / invert. A normal fill
# has drag well under 0.1; this only ever bites the pathological dust-slice case
# where fee_usd >= size_usd (e.g. a $0.10 remainder sell vs a $0.17 fee), which
# without clamping yields drag > 1 -> a NEGATIVE booked sell price (phantom
# catastrophic loss) or an absurd buy pay-up. 0.95 => a sell never books below
# ~5% of mid, a buy never above ~195% of mid.
MAX_FILL_DRAG = 0.95

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

def _paper_fee_placeholder() -> float:
    """The fixed placeholder per-tx fee (USD). Env PAPER_FEE_USD_PER_TX, default 0.17.
    This is the historical value — assumes the priority fee = the 1M-2M lamport cap,
    which overstates the REAL ~175k-lamport priority fee ~6x."""
    try:
        return float(os.environ.get("PAPER_FEE_USD_PER_TX", "0.17"))
    except Exception:
        return 0.17

def _sol_price_for_fee_calib(sol_price_usd=None):
    """Resolve a SOL price (USD) for fee calibration: explicit arg first, else env
    SOL_PRICE_USD. None when unavailable (calibrator then fails open to placeholder)."""
    try:
        if sol_price_usd is not None:
            v = float(sol_price_usd)
            return v if v > 0 else None
    except (TypeError, ValueError):
        pass
    try:
        env = os.environ.get("SOL_PRICE_USD")
        if env is not None and str(env).strip() != "":
            v = float(env)
            return v if v > 0 else None
    except (TypeError, ValueError):
        pass
    return None

def paper_fee_usd(sol_price_usd=None) -> float:
    """Per-tx fee in USD that paper should book.

    Default-off byte-identical: env PAPER_FEE_CALIBRATION_MODE (off/shadow/enforce,
    default off). When off -> the fixed placeholder (PAPER_FEE_USD_PER_TX, 0.17)
    EXACTLY as before. When shadow -> log the calibrated-vs-placeholder delta but
    still BOOK the placeholder. When enforce -> book the calibrated fee (median real
    priority+base fee from live_swaps), fail-open to the placeholder until the live
    sample is sufficient. Pure + fail-open: any error -> placeholder."""
    placeholder = _paper_fee_placeholder()
    try:
        mode = paper_fidelity_enabled("PAPER_FEE_CALIBRATION_MODE")
        if mode not in ("shadow", "enforce"):
            return placeholder
        sol_px = _sol_price_for_fee_calib(sol_price_usd)
        from core.fill_calibration import load_fee_calibration
        calibrated = load_fee_calibration(placeholder, sol_px)
        if mode == "enforce":
            return calibrated
        # shadow: log the delta, but BOOK the placeholder (no behavior change).
        try:
            if calibrated != placeholder:
                logger.info("[paper-fee-calib] SHADOW calibrated=$%.4f vs placeholder=$%.4f "
                            "(delta $%.4f) — booking placeholder",
                            calibrated, placeholder, calibrated - placeholder)
        except Exception:
            pass
        return placeholder
    except Exception:
        return placeholder

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
    # Backstop: a tiny dust slice (size_usd < fee_usd) makes fee_frac > 1 ->
    # drag > 1 -> a sell would book a NEGATIVE price (and a buy an absurd
    # multiple). Clamp so a fill can never cross zero / invert. No effect on
    # normal fills (drag << MAX_FILL_DRAG).
    if drag > MAX_FILL_DRAG:
        drag = MAX_FILL_DRAG
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

# ─── EXIT-SLIP LIQUIDITY-AWARENESS (SHADOW-first, EXIT_SLIP_LIQ_MODE) ──────────
# Today the PAPER twin books an exit with a FLAT slip (measured_live_slip_pct,
# default 1.5%) that is BLIND to liquidity, and it never models that LIVE REVERTS
# a sell when the real price-impact exceeds the sell cap (PROBE_ULTRA_SELL_SLIPPAGE_BPS,
# default 600 bps = 6%). So paper OVERSTATES live exit P&L on every illiquid /
# crashing exit. These helpers make the exit slip liquidity-scaled and flag the
# would-revert case. Pure + fail-open; the caller gates on EXIT_SLIP_LIQ_MODE so
# OFF never reaches any of this (byte-identical short-circuit upstream).

# Solana sqrt market-impact coefficient — mirrors core/paper_slippage.py
# IMPACT_COEFFICIENT["solana"] (the only liquidity-aware impact model already in
# the tree). impact% = COEFF * sqrt(size/liq) * 100.
_EXIT_IMPACT_COEFF = 0.10
_EXIT_CURVE_SAMPLE_SIZES = (500.0, 2000.0, 5000.0)

# Sentinel: the exit must NOT be booked this tick (mirror live's sell cap-revert —
# the position stays open and retries). Distinct object so callers test identity.
EXIT_HOLD = object()


def sell_slippage_cap_pct() -> float:
    """LIVE sell-slippage cap (%). Env PROBE_ULTRA_SELL_SLIPPAGE_BPS (default
    600 bps = 6.0%) — the SAME var the live sell path reads. Fail-open => 6.0."""
    try:
        return float(os.environ.get("PROBE_ULTRA_SELL_SLIPPAGE_BPS", "600")) / 100.0
    except Exception:
        return 6.0


def liq_scaled_exit_slip_pct(size_usd, exit_liq_usd):
    """Liquidity-aware exit slip (%) for a SELL of ``size_usd`` into a pool with
    ``exit_liq_usd`` USD of FRESH liquidity.

    Builds a sqrt market-impact SELL slip curve from the fresh liquidity (sampled
    at the canonical 500/2000/5000 sizes) and lets ``impact_pct_for_size``
    interpolate for the actual trade size — so this reuses the existing impact
    machinery rather than inventing a parallel model.

    Returns None when liquidity/size is missing or non-positive (the caller then
    fails open to the flat slip). Does NOT swallow an ``impact_pct_for_size``
    raise — that propagates so the gate's fail-open guard books the flat value."""
    try:
        sz = float(size_usd)
        liq = float(exit_liq_usd)
    except (TypeError, ValueError):
        return None
    if not (sz > 0 and liq > 0):
        return None
    curve = {}
    for s, label in zip(_EXIT_CURVE_SAMPLE_SIZES, ("500", "2000", "5000")):
        curve[f"slip_sell_{label}_pct"] = _EXIT_IMPACT_COEFF * math.sqrt(s / liq) * 100.0
    # may raise -> propagates to exit_slip_liq_eval's guarded call site
    return impact_pct_for_size(sz, curve, "sell")


def exit_slip_liq_eval(decision_mid, fresh_price, exit_reason, size_usd,
                       exit_liq_usd, flat_slip_pct=None, fee_usd=None,
                       low_price=None, sell_cap_pct=None) -> dict:
    """Pure liquidity-aware exit-slip evaluation (no mode branching — the caller
    gates on EXIT_SLIP_LIQ_MODE first so OFF never reaches here).

    Computes the flat-slip booked exit and the liquidity-scaled booked exit
    (both through ``paper_exit_decision`` so reprice / gap-through / clamp-to-low
    are identical and ONLY the slip term differs), plus the would-revert flag
    (modeled liq slip >= sell cap).

    FAIL-OPEN: when fresh liquidity is missing/non-positive OR
    ``impact_pct_for_size`` raises, the liq-scaled path degrades to the flat slip
    (``liq_available`` False, ``would_revert`` False, eff_liqscaled == eff_flat)
    so booking never changes. Returns a dict of shadow fields + booked candidates."""
    flat = flat_slip_pct if flat_slip_pct is not None else measured_live_slip_pct()
    fee = fee_usd if fee_usd is not None else paper_fee_usd()
    cap = sell_cap_pct if sell_cap_pct is not None else sell_slippage_cap_pct()
    try:
        liq_scaled = liq_scaled_exit_slip_pct(size_usd, exit_liq_usd)
    except Exception:
        # impact_pct_for_size (or curve build) failed — fail open to flat.
        liq_scaled = None
    eff_flat = paper_exit_decision(
        decision_mid, fresh_price, exit_reason, "enforce", size_usd,
        slip_pct=flat, fee_usd=fee, low_price=low_price)[0]
    if liq_scaled is None:
        liq_available = False
        liq_scaled_out = float(flat) if flat is not None else flat
        would_revert = False
        eff_liq = eff_flat
    else:
        liq_available = True
        liq_scaled_out = liq_scaled
        try:
            would_revert = float(liq_scaled) >= float(cap)
        except (TypeError, ValueError):
            would_revert = False
        eff_liq = paper_exit_decision(
            decision_mid, fresh_price, exit_reason, "enforce", size_usd,
            slip_pct=liq_scaled, fee_usd=fee, low_price=low_price)[0]
    try:
        delta = ((float(eff_liq) / float(eff_flat) - 1.0) * 100.0) if eff_flat else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        delta = 0.0
    return {
        "exit_liq_usd": (float(exit_liq_usd) if isinstance(exit_liq_usd, (int, float)) else None),
        "liq_available": liq_available,
        "flat_slip_pct": flat,
        "liq_scaled_slip_pct": liq_scaled_out,
        "sell_cap_pct": cap,
        "would_revert": bool(would_revert),
        "eff_exit_flat": eff_flat,
        "eff_exit_liqscaled": eff_liq,
        "exit_price_delta_pct": delta,
    }


def exit_slip_liq_book(mode, current_booked_exit, decision_mid, fresh_price,
                       exit_reason, size_usd, exit_liq_usd, flat_slip_pct=None,
                       fee_usd=None, low_price=None, sell_cap_pct=None):
    """Resolve what the PAPER twin should book for this exit under
    EXIT_SLIP_LIQ_MODE. Returns ``(booked_exit, info_or_None)``:

      off / unknown -> (current_booked_exit, None)   # BYTE-IDENTICAL short-circuit
      shadow        -> (current_booked_exit, info)    # book the OLD value; info=>JSONL
      enforce       -> (eff_exit_liqscaled, info)     # book the worse liq-scaled fill
                       (EXIT_HOLD, info) when would_revert (NON-FILL — hold/retry)
                       (current_booked_exit, info) when fresh liq is unavailable
                       (fail open to the existing flat booking).

    FAIL-OPEN: any exception => (current_booked_exit, None) so the exit path is
    never broken and booking is unchanged."""
    try:
        m = str(mode).strip().lower() if mode is not None else "off"
        if m not in ("shadow", "enforce"):
            return (current_booked_exit, None)
        info = exit_slip_liq_eval(
            decision_mid, fresh_price, exit_reason, size_usd, exit_liq_usd,
            flat_slip_pct=flat_slip_pct, fee_usd=fee_usd, low_price=low_price,
            sell_cap_pct=sell_cap_pct)
        if m == "enforce":
            if not info.get("liq_available"):
                # no fresh liq -> keep the existing flat booking (fail open)
                return (current_booked_exit, info)
            if info.get("would_revert"):
                return (EXIT_HOLD, info)
            return (info["eff_exit_liqscaled"], info)
        # shadow: book the OLD value, return info for the JSONL log
        return (current_booked_exit, info)
    except Exception:
        return (current_booked_exit, None)
