"""Profit-sweep executor — hot→cold one-way SOL transfer of banked profit.

PRE-LIVE feature. No-op in paper mode. Moves REAL money in live mode, so it is
deliberately minimal, fail-closed, dry-run-by-default, and loud. Implements the
2026-05-25 design spec: pure money-math + a sweep() that builds/signs/sends a
System Program transfer via an injected sender (so the signing primitive lives in
the Trader, where the hot key is, and this module stays unit-testable with no key).

Safety invariants (all unit-tested):
- Never sweep below the working-capital floor or the gas buffer.
- Fail-CLOSED on a bad/empty/mismatched destination (a mutated config cannot
  redirect funds).
- Dry-run by default — logs the intended transfer without sending.
- A hard per-call USD cap (used by the $5 manual test) clamps any single sweep.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000


# ── pure money-math ────────────────────────────────────────────────────────────
def compute_sweepable_sol(balance_sol: float, floor_sol: float,
                          gas_buffer_sol: float) -> float:
    """Idle SOL safely movable to cold: max(0, balance − floor − gas_buffer).
    Never returns negative; never lets a sweep dip into the floor or gas reserve."""
    try:
        return max(0.0, float(balance_sol) - float(floor_sol) - float(gas_buffer_sol))
    except (TypeError, ValueError):
        return 0.0


def usd_to_sol(usd: float, sol_price_usd: float) -> Optional[float]:
    """Convert a USD amount to SOL at the given price. None if price is unusable
    (so the caller fail-closes rather than sweeping a wild amount)."""
    if not isinstance(sol_price_usd, (int, float)) or sol_price_usd <= 0:
        return None
    return float(usd) / float(sol_price_usd)


def _is_valid_pubkey(addr: str) -> bool:
    if not addr or not isinstance(addr, str):
        return False
    try:
        from solders.pubkey import Pubkey
        Pubkey.from_string(addr)
        return True
    except Exception:
        return False


def validate_destination(dest: str, hot_addr: str, configured: str) -> bool:
    """FAIL-CLOSED. True only if dest is a valid Solana pubkey, equals the
    configured PROFIT_WALLET_ADDRESS, and is NOT the hot wallet (never sweep to
    self; a mutated/empty/attacker config yields False -> no transfer)."""
    if not _is_valid_pubkey(dest):
        return False
    if not configured or dest != configured:
        return False
    if hot_addr and dest == hot_addr:
        return False
    return True


def ratchet_target_sol(realized_pnl_sol: float, profit_hwm_sol: float,
                       total_swept_sol: float, fraction: float) -> tuple[float, float]:
    """DEPRECATED / DO NOT WIRE (2026-06-13). The realized-HWM ratchet was RETIRED in
    favor of the fixed-floor policy (auto_sweep_decision) — see memory
    feedback_sweep_banks_peak_not_net ("NOT the realized-HWM function"). Kept only so
    its unit tests document the rejected design; nothing in the live path calls it.

    HWM profit ratchet. Returns (new_profit_hwm, sweep_target_sol). Monotonic:
    profit_hwm only rises; target = max(0, fraction*hwm − already_swept)."""
    hwm = max(float(profit_hwm_sol), float(realized_pnl_sol))
    desired = max(0.0, float(fraction)) * hwm
    target = max(0.0, desired - float(total_swept_sol))
    return hwm, target


# ── config (env) ────────────────────────────────────────────────────────────────
def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _flag("PROFIT_SWEEP_ENABLED", "0")


def dry_run_default() -> bool:
    return _flag("PROFIT_SWEEP_DRY_RUN", "1")  # dry-run ON by default


def floor_sol() -> float:
    return _f("WORKING_CAPITAL_FLOOR_SOL", 0.0)


def gas_buffer_sol() -> float:
    return _f("SWEEP_GAS_BUFFER_SOL", 0.05)


def threshold_sol() -> float:
    return _f("SWEEP_THRESHOLD_SOL", 1.0)


def test_cap_usd() -> float:
    # Hard ceiling on a single manual/test sweep — the $5 test rides this.
    return _f("PROFIT_SWEEP_MAX_USD_PER_CALL", 5.0)


# ── production auto-sweep config (fixed-floor, USD-pegged) ──────────────────────
def working_floor_usd() -> float:
    # The working-capital baseline kept in the hot wallet (USD). MUST be set > 0 for
    # the auto-sweep to run — fail-closed otherwise (never drain the float).
    return _f("WORKING_CAPITAL_FLOOR_USD", 0.0)


def min_increment_usd() -> float:
    # Don't sweep until idle profit above the floor clears this (avoid fee churn).
    return _f("SWEEP_MIN_INCREMENT_USD", 5.0)


def min_interval_secs() -> float:
    return _f("SWEEP_MIN_INTERVAL_SECS", 3600.0)


# ── flaw-fix knobs (2026-06-13, AxiS "all of them") ─────────────────────────────
def opportunistic_usd() -> float:
    # #5 giveback window: bank a BIG realized win immediately (bypassing the
    # hourly interval) once idle profit clears this. 0 = off (hourly-only, the
    # current behavior). Closes the intra-hour exposure window for amounts that
    # matter while small amounts still batch hourly (fee-efficient).
    return _f("SWEEP_OPPORTUNISTIC_USD", 0.0)


def check_interval_secs() -> float:
    # How often the opportunistic check may run (bounds RPC). Only relevant when
    # SWEEP_OPPORTUNISTIC_USD > 0. Default 5min.
    return _f("SWEEP_CHECK_INTERVAL_SECS", 300.0)


def min_floor_usd() -> float:
    # #6 anti-mis-set: reject a floor below this sanity minimum (set it to your
    # starting capital so a typo to a tiny value can't drain the float). 0 = off.
    return _f("SWEEP_MIN_FLOOR_USD", 0.0)


def max_per_sweep_usd() -> float:
    # #6 blast-radius cap: clamp any single sweep to this (a mis-set floor then
    # moves at most this much per fire, loudly, before you catch it). 0 = off.
    return _f("SWEEP_MAX_PER_SWEEP_USD", 0.0)


def floor_drop_guard_frac() -> float:
    # #6 floor high-water guard: refuse if the configured floor drops below this
    # fraction of the highest floor ever seen (catches "fat-fingered 2000->200").
    return _f("SWEEP_FLOOR_DROP_FRAC", 0.5)


def price_risk_ack() -> bool:
    # #4 over-sweep guard (2026-06-17): a USD-pegged floor is re-converted to a SOL
    # amount at the LIVE SOL price every cycle. On a transiently-HIGH price tick the
    # kept SOL floor shrinks -> a LARGER sweep is authorized; when price reverts down
    # the SOL left is worth LESS than the USD floor -> the hot wallet drains BELOW its
    # working capital (the 2026-06-17 ~$330 sub-floor drain). A SOL-NATIVE floor
    # (WORKING_CAPITAL_FLOOR_SOL) has no price dependency and is the correct fix; a
    # bare blast-radius cap (SWEEP_MAX_PER_SWEEP_USD) does NOT remove this price risk.
    # This ack is the explicit "yes, I accept the SOL-price risk of a USD-only floor"
    # escape hatch — required (alongside a bound) for a LIVE USD-floor sweep.
    return _flag("SWEEP_PRICE_RISK_ACK", "0")


def floor_price_buffer_frac() -> float:
    # #4 over-sweep haircut (USD-floor path only): keep extra SOL so the floor stays
    # >= the USD target even if SOL drops by this fraction before price reverts. The
    # effective kept floor becomes floor_usd / (price * (1 - buffer_frac)), i.e. we
    # value our retained SOL at a STRESSED (lower) price so a real-money drop can't
    # take the hot wallet sub-floor. 0 = off (legacy behavior; the trader guard then
    # requires a SOL-native floor or an explicit price-risk ack). Capped at 0.9.
    v = _f("SWEEP_FLOOR_PRICE_BUFFER_FRAC", 0.0)
    if v < 0.0:
        return 0.0
    return min(v, 0.9)


def single_config_ack() -> bool:
    # #1 commingled-wallet guard: the sweep banks FLEET-aggregate profit from one
    # shared hot wallet — a losing live bot draws the balance down and eats a
    # winning bot's un-swept gains. The only real fix is running ONE live config
    # so the wallet isn't commingled. A LIVE sweep refuses unless the operator has
    # set this ack (a deliberate "yes, the live set is a single isolated config").
    # Dry-run never needs it. See the go-live runbook.
    return _flag("SWEEP_SINGLE_CONFIG_ACK", "0")


def auto_sweep_decision(balance_sol, sol_price, floor_usd, gas_buffer_sol,
                        min_increment_usd_v, *,
                        floor_sol_override=None, floor_hwm_usd=None,
                        min_floor_usd_v=None, max_per_sweep_usd_v=None,
                        floor_drop_frac=None, opportunistic_usd_v=None,
                        floor_price_buffer_frac_v=None) -> dict:
    """PURE production auto-sweep decision: keep the working-capital floor in the hot
    wallet, sweep ALL idle SOL above it to cold once the excess clears the min
    increment. Returns {should_sweep, reason, sweepable_sol, lamports, sweepable_usd,
    floor_sol, below_floor, opportunistic}. FAIL-CLOSED throughout.

    Flaw-fix params (2026-06-13, all optional/back-compat; None = use env getter or off):
      floor_sol_override  (#3) SOL-native floor — banks pure SOL alpha, no USD-rate
                          leak. When set (>0) it OVERRIDES the USD floor.
      floor_hwm_usd       (#6) highest floor ever configured — refuse if the live
                          floor dropped below floor_drop_frac of it (fat-finger guard).
      min_floor_usd_v     (#6) reject a floor below this sanity minimum.
      max_per_sweep_usd_v (#6) clamp a single sweep to this (blast-radius bound).
      opportunistic_usd_v (#5) flag the sweep `opportunistic` when it clears this, so
                          the caller may bank a big win immediately (bypass the interval).
      floor_price_buffer_frac_v (#4) USD-floor over-sweep haircut: value retained SOL at
                          a STRESSED price (price * (1 - buffer)) so the kept floor stays
                          >= the USD target even if SOL drops before price reverts. Ignored
                          for a SOL-native floor (no price dependency).
    """
    if not (isinstance(sol_price, (int, float)) and 30.0 <= sol_price <= 2000.0):
        return {"should_sweep": False, "reason": "implausible_sol_price"}

    # #3 denomination: a SOL-native floor (if set) is used directly; else USD-pegged.
    if floor_sol_override and floor_sol_override > 0:
        floor_sol = float(floor_sol_override)
        floor_usd_eff = floor_sol * float(sol_price)
    else:
        if not floor_usd or floor_usd <= 0:
            return {"should_sweep": False, "reason": "no_floor_set"}
        floor_usd_eff = float(floor_usd)
        # #4 over-sweep haircut: convert the USD floor to SOL at a STRESSED (lower)
        # price so we KEEP MORE SOL than the naive floor_usd/price. This makes the kept
        # floor robust to a SOL-price drop after the sweep (the cause of the 06-17
        # sub-floor drain). buffer=0 -> legacy floor_usd/price (the trader guard then
        # demands a SOL-native floor or an explicit price-risk ack instead).
        _buf = floor_price_buffer_frac() if floor_price_buffer_frac_v is None \
            else float(floor_price_buffer_frac_v)
        if _buf < 0.0:
            _buf = 0.0
        if _buf > 0.9:
            _buf = 0.9
        _stressed_price = float(sol_price) * (1.0 - _buf)
        floor_sol = floor_usd_eff / _stressed_price

    # #6 mis-set guards (USD-floor path only; a SOL override is explicit by definition).
    if floor_sol_override is None or floor_sol_override <= 0:
        _minf = min_floor_usd() if min_floor_usd_v is None else float(min_floor_usd_v)
        if _minf > 0 and floor_usd_eff < _minf:
            return {"should_sweep": False, "reason": "floor_below_min_sanity",
                    "floor_usd": round(floor_usd_eff, 2), "min_floor_usd": _minf}
        _frac = floor_drop_guard_frac() if floor_drop_frac is None else float(floor_drop_frac)
        if (floor_hwm_usd and floor_hwm_usd > 0 and 0 < _frac <= 1
                and floor_usd_eff < _frac * float(floor_hwm_usd)):
            return {"should_sweep": False, "reason": "floor_dropped_suspicious",
                    "floor_usd": round(floor_usd_eff, 2),
                    "floor_hwm_usd": round(float(floor_hwm_usd), 2)}

    below_floor = float(balance_sol) < floor_sol
    sweepable_sol = compute_sweepable_sol(balance_sol, floor_sol, gas_buffer_sol)
    sweepable_usd = sweepable_sol * float(sol_price)

    if sweepable_usd < float(min_increment_usd_v):
        return {"should_sweep": False, "reason": "below_increment",
                "sweepable_usd": round(sweepable_usd, 2), "floor_sol": round(floor_sol, 6),
                "below_floor": below_floor}

    # #6 blast-radius clamp: a single sweep moves at most max_per_sweep_usd (if set).
    _cap = max_per_sweep_usd() if max_per_sweep_usd_v is None else float(max_per_sweep_usd_v)
    clamped = False
    if _cap and _cap > 0 and sweepable_usd > _cap:
        sweepable_sol = float(_cap) / float(sol_price)
        sweepable_usd = float(_cap)
        clamped = True

    # #3/#4 HARD post-conversion floor assertion: a sweep can NEVER reduce the hot
    # balance below the floor SOL used in THIS decision. This is the last-line backstop
    # that makes an over-sweep arithmetically impossible regardless of how floor_sol was
    # derived (USD peg, stressed-price haircut, or SOL-native) and after the clamp. Use a
    # tiny epsilon for float noise. Fail-CLOSED (never sweep) on any violation.
    _post_balance = float(balance_sol) - float(sweepable_sol)
    if _post_balance < floor_sol - 1e-9:
        return {"should_sweep": False, "reason": "floor_assertion_failed",
                "floor_sol": round(floor_sol, 9),
                "post_sweep_balance_sol": round(_post_balance, 9),
                "sweepable_sol": round(sweepable_sol, 9)}

    _opp = opportunistic_usd() if opportunistic_usd_v is None else float(opportunistic_usd_v)
    return {"should_sweep": True, "sweepable_sol": round(sweepable_sol, 9),
            "lamports": int(sweepable_sol * LAMPORTS_PER_SOL),
            "sweepable_usd": round(sweepable_usd, 2), "floor_sol": round(floor_sol, 6),
            "below_floor": below_floor, "clamped": clamped,
            "opportunistic": bool(_opp and _opp > 0 and sweepable_usd >= _opp)}


# ── executor ──────────────────────────────────────────────────────────────────
class ProfitSweeper:
    """Orchestrates one sweep. Dependencies injected so the money-moving primitive
    (signing) stays in the Trader and this stays testable with fakes."""

    def __init__(self, *, get_balance_sol: Callable[[], Optional[float]],
                 send_transfer: Callable[[str, int], Optional[str]],
                 get_sol_price_usd: Callable[[], Optional[float]],
                 configured_dest: str, hot_addr: str,
                 floor: Optional[float] = None, gas_buffer: Optional[float] = None,
                 threshold: Optional[float] = None):
        self._get_balance = get_balance_sol
        self._send = send_transfer
        self._get_price = get_sol_price_usd
        self._dest = configured_dest
        self._hot = hot_addr
        self._floor = floor if floor is not None else floor_sol()
        self._gas = gas_buffer if gas_buffer is not None else gas_buffer_sol()
        self._threshold = threshold if threshold is not None else threshold_sol()

    def sweep_once(self, *, dry_run: bool = True, max_usd: Optional[float] = None,
                   ignore_threshold: bool = False) -> dict:
        """Execute (or dry-run) a single sweep. Returns a loud, structured result.
        max_usd clamps the amount (the $5 test passes 5.0). ignore_threshold lets
        the tiny test fire below SWEEP_THRESHOLD_SOL (the test cap is the guard)."""
        balance = self._get_balance()
        if balance is None:
            logger.warning("[Sweep] balance fetch failed — skip")
            return {"sent": False, "reason": "balance_fetch_failed"}

        sweepable = compute_sweepable_sol(balance, self._floor, self._gas)

        # Hard USD cap (the $5 manual test). Fail-closed if price is unusable.
        if max_usd is not None:
            price = self._get_price()
            cap_sol = usd_to_sol(max_usd, price)
            if cap_sol is None:
                logger.error("[Sweep] SOL price unavailable — refusing capped sweep (fail-closed)")
                return {"sent": False, "reason": "no_sol_price"}
            sweepable = min(sweepable, cap_sol)

        if sweepable <= 0:
            return {"sent": False, "reason": "nothing_sweepable",
                    "balance_sol": round(balance, 6)}

        if not ignore_threshold and sweepable < self._threshold:
            return {"sent": False, "reason": "below_threshold",
                    "sweepable_sol": round(sweepable, 6), "threshold_sol": self._threshold}

        # FAIL-CLOSED destination validation — the one money-direction guard.
        if not validate_destination(self._dest, self._hot, self._dest):
            logger.critical(f"[Sweep] REFUSED — invalid/mismatched destination "
                            f"{self._dest!r} (fail-closed)")
            return {"sent": False, "reason": "bad_destination", "dest": self._dest}

        lamports = int(sweepable * LAMPORTS_PER_SOL)
        if dry_run:
            logger.warning(f"[Sweep] DRY-RUN intent: would send {sweepable:.6f} SOL "
                           f"({lamports} lamports) -> {self._dest}")
            return {"sent": False, "dry_run": True, "amount_sol": round(sweepable, 6),
                    "lamports": lamports, "dest": self._dest}

        logger.critical(f"[Sweep] LIVE: sending {sweepable:.6f} SOL ({lamports} lamports) "
                        f"-> {self._dest}")
        sig = self._send(self._dest, lamports)
        if not sig:
            logger.error("[Sweep] transfer failed (no signature)")
            return {"sent": False, "reason": "transfer_failed",
                    "amount_sol": round(sweepable, 6)}
        logger.critical(f"[Sweep] CONFIRMED {sweepable:.6f} SOL -> {self._dest} sig={sig}")
        return {"sent": True, "amount_sol": round(sweepable, 6), "lamports": lamports,
                "dest": self._dest, "sig": sig}
