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
    """HWM profit ratchet. Returns (new_profit_hwm, sweep_target_sol). Monotonic:
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


def auto_sweep_decision(balance_sol, sol_price, floor_usd, gas_buffer_sol,
                        min_increment_usd_v) -> dict:
    """PURE production auto-sweep decision (fixed-floor, USD-pegged): keep `floor_usd`
    of working capital in the hot wallet, sweep ALL idle SOL above it to cold once the
    excess clears the min increment. Returns {should_sweep, reason, sweepable_sol,
    lamports, sweepable_usd, floor_sol}. FAIL-CLOSED: no sweep if the SOL price is
    implausible or floor_usd<=0 (an unset floor would drain the entire float)."""
    if not (isinstance(sol_price, (int, float)) and 30.0 <= sol_price <= 2000.0):
        return {"should_sweep": False, "reason": "implausible_sol_price"}
    if not floor_usd or floor_usd <= 0:
        return {"should_sweep": False, "reason": "no_floor_set"}
    floor_sol = float(floor_usd) / float(sol_price)
    sweepable_sol = compute_sweepable_sol(balance_sol, floor_sol, gas_buffer_sol)
    sweepable_usd = sweepable_sol * float(sol_price)
    if sweepable_usd < float(min_increment_usd_v):
        return {"should_sweep": False, "reason": "below_increment",
                "sweepable_usd": round(sweepable_usd, 2), "floor_sol": round(floor_sol, 6)}
    return {"should_sweep": True, "sweepable_sol": round(sweepable_sol, 9),
            "lamports": int(sweepable_sol * LAMPORTS_PER_SOL),
            "sweepable_usd": round(sweepable_usd, 2), "floor_sol": round(floor_sol, 6)}


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
