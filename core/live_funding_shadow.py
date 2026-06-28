"""SHADOW counter for the LIVE SOL-reserve / funding gate (Tier-2 GAP C, 2026-06-28).

THE GAP
-------
LIVE gates every buy on the REAL wallet SOL: ``trader._check_sol_reserve`` aborts
the buy when ``wallet SOL < MIN_SOL_RESERVE`` (default 0.05), and the buy path
returns at ``feeds/dip_scanner.py`` on ``not _reserve_ok``. PAPER's
``PerBotCapital.reserve_for_buy`` only checks the PAPER ``balance_usd`` (which
starts at the bot's paper_capital_usd, e.g. 2000) — it NEVER sees the real wallet.
So with the live wallet drained (~$10 of SOL) paper books buys a funded-live
wallet could not have made. There was NO counter for "paper buys the live funding
gate would have killed", so paper throughput is silently inflated vs live.

THIS MODULE (measurement only — NEVER blocks a paper buy)
---------------------------------------------------------
Maintains a single per-process simulated wallet SOL balance, starting at
``LIVE_FUNDING_SHADOW_SOL`` (a snapshot of the real drained balance, e.g. 0.06),
and decrements it as paper buys "spend" SOL. When a buy's SOL need would push the
sim balance below ``MIN_SOL_RESERVE`` the buy is COUNTED + logged as a would-block
(via the proven ``core.shadow_gate_log.log_shadow_block`` pattern) and the sim
balance is left untouched (a real funded-live wallet would have refused to spend
below the reserve). Otherwise the sim balance is debited and the buy is NOT
counted. A ``credit_sell`` hook lets a shadow sell add SOL back so the sim balance
tracks a draining-then-recovering wallet rather than monotonically pinning to a
permanent block.

DESIGN CHOICE (documented): a SINGLE per-process running balance (module-level).
The bot runs as one process, so one running counter is the simplest correct model
of "would this funded-live wallet have had the gas?". It is fail-open and
self-contained; if running state is ever undesirable, set LIVE_FUNDING_SHADOW_SOL
high and it never blocks. ``_reset()`` re-initializes it (used by tests).

FLAGS
-----
  LIVE_FUNDING_SHADOW_MODE   off (default) | shadow.   off == byte-identical no-op.
  LIVE_FUNDING_SHADOW_SOL    starting simulated wallet SOL balance (default 0.06).
  MIN_SOL_RESERVE            reused live reserve floor (default 0.05).

CONTRACTS (hard):
  * FAIL-OPEN — every public entrypoint swallows all errors and returns "no block".
    This is pure observability; it must NEVER raise into, block, or alter a buy.
  * off mode is a true no-op: no counters touched, no file IO, no log.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Log a running summary line every this-many would-blocks so the count is
# observable in the bot log without an offline join.
_SUMMARY_EVERY = 25

# ── per-process running state ────────────────────────────────────────────────
# _sim_sol is None until the first shadow evaluation, then lazily initialized
# from LIVE_FUNDING_SHADOW_SOL so a test/env change before first use is honored.
_sim_sol = None          # type: float | None
_count_total = 0         # paper buys evaluated under shadow mode
_count_blocked = 0       # of those, how many a funded-live wallet would have aborted


def shadow_mode() -> str:
    """'off' (default) | 'shadow'. 'off' == fully dormant no-op."""
    try:
        return os.environ.get("LIVE_FUNDING_SHADOW_MODE", "off").strip().lower()
    except Exception:
        return "off"


def _min_reserve() -> float:
    try:
        return float(os.environ.get("MIN_SOL_RESERVE", "0.05"))
    except Exception:
        return 0.05


def _start_sol() -> float:
    try:
        return float(os.environ.get("LIVE_FUNDING_SHADOW_SOL", "0.06"))
    except Exception:
        return 0.06


def _reset() -> None:
    """Re-initialize the per-process running state (tests / a fresh run)."""
    global _sim_sol, _count_total, _count_blocked
    _sim_sol = None
    _count_total = 0
    _count_blocked = 0


def stats() -> dict:
    """Snapshot of the running counters (observability / reporting)."""
    return {
        "mode": shadow_mode(),
        "sim_sol": _sim_sol,
        "start_sol": _start_sol(),
        "min_reserve": _min_reserve(),
        "buys_evaluated": _count_total,
        "would_block": _count_blocked,
        "would_block_pct": (100.0 * _count_blocked / _count_total)
        if _count_total else 0.0,
    }


def credit_sell(sol_amount) -> None:
    """Optional shadow-sell hook: add SOL back to the sim balance so it tracks a
    recovering wallet. FAIL-OPEN no-op when mode is off or input is bad."""
    global _sim_sol
    try:
        if shadow_mode() == "off":
            return
        amt = float(sol_amount)
        if amt <= 0:
            return
        if _sim_sol is None:
            _sim_sol = _start_sol()
        _sim_sol += amt
    except Exception:
        pass  # pure observability — never raise


def note_paper_buy(need_sol, bot, token_address, symbol) -> bool:
    """Record a paper buy against the simulated live wallet.

    Returns True iff this buy WOULD have aborted on a funded-live wallet (i.e. it
    was counted + logged as a would-block). Returns False otherwise. The return
    value is informational ONLY — the caller MUST proceed with the paper buy
    regardless. NEVER raises (fail-open -> returns False on any error).
    """
    global _sim_sol, _count_total, _count_blocked
    try:
        if shadow_mode() == "off":
            return False
        try:
            need = float(need_sol)
        except (TypeError, ValueError):
            return False  # fail-open: bad input -> no count, no block
        if need < 0:
            return False

        if _sim_sol is None:
            _sim_sol = _start_sol()
        reserve = _min_reserve()

        _count_total += 1
        if (_sim_sol - need) < reserve:
            # A funded-live wallet would have refused to spend below the reserve:
            # count + log, and DO NOT debit (the SOL would not have left).
            _count_blocked += 1
            try:
                from core.shadow_gate_log import log_shadow_block as _sgl
                _sgl("live_funding_gate", str(bot or ""),
                     str(token_address or ""), str(symbol or ""),
                     need_sol=need, sim_sol=_sim_sol, min_reserve=reserve,
                     would_block=True)
            except Exception:
                pass
            if _count_blocked % _SUMMARY_EVERY == 0 or _count_blocked == 1:
                logger.info(
                    "[live-funding-shadow] paper booked %d buys; %d (%.1f%%) "
                    "would have aborted on a funded-live wallet (sim_sol=%.4f "
                    "need=%.4f reserve=%.4f)",
                    _count_total, _count_blocked,
                    100.0 * _count_blocked / _count_total,
                    _sim_sol, need, reserve)
            return True

        # Buy fits: the funded-live wallet would have had the gas -> debit it.
        _sim_sol -= need
        return False
    except Exception:
        # Pure observability: any unexpected error must never block a paper buy.
        return False
