# core/young_token_probe.py
"""Young-token probe gating (2026-06-02, #4.1).

The universe mine found `age_hours <= 2` is the strongest cross-regime entry
separator (69-71% WR vs 36-40%, +24.9% forward peak), but the scanner's
`min_age_days=7.0` gate hard-skips young tokens in discovery, so the fleet's
median fill age is ~36 days — it never touches the runner-tail cohort.

This module gates a DELIBERATE, ISOLATED experiment to measure young-token
outcomes on REALISTIC dip-buy paths (the mine's signal is a 30-min forward-peak
proxy, not realized P&L) WITHOUT changing production:

- `YOUNG_TOKEN_PROBE` env flag (default OFF) -> when OFF, nothing changes: the
  scanner skips young tokens exactly as before.
- When ON: sub-min-age tokens that clear a liquidity floor are KEPT in discovery,
  but only `young_token_probe` bots may BUY them; production bots SKIP them. The
  probe bots trade YOUNG-ONLY (skip old) so the experiment is clean. So even with
  the flag on, production bots never start buying rug-prone fresh tokens.

All functions are pure (env read once at the edges) so they're unit-testable.
"""
from __future__ import annotations
import os
from typing import Optional


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def probe_enabled() -> bool:
    return _flag("YOUNG_TOKEN_PROBE")


def max_age_hours() -> float:
    try:
        return float(os.environ.get("YOUNG_TOKEN_MAX_AGE_H", "2"))
    except (TypeError, ValueError):
        return 2.0


def min_liq_usd() -> float:
    try:
        return float(os.environ.get("YOUNG_TOKEN_MIN_LIQ_USD", "40000"))
    except (TypeError, ValueError):
        return 40000.0


def is_young(age_hours, max_h: Optional[float] = None) -> bool:
    """True if the token's age (hours) is below the young threshold. None -> False."""
    if age_hours is None:
        return False
    if max_h is None:
        max_h = max_age_hours()
    try:
        return float(age_hours) < max_h
    except (TypeError, ValueError):
        return False


def keep_subminage_token(liq_usd, age_hours=None, probe_on: Optional[bool] = None,
                         min_liq: Optional[float] = None, max_h: Optional[float] = None) -> bool:
    """Discovery gate: a token younger than the fleet min_age — should it be KEPT
    (instead of skipped)? KEEP only when ALL hold: the probe is ON, the token is
    genuinely YOUNG (age < max_age_hours — NOT the whole sub-min-age range, so
    production's universe never expands to e.g. 2h-7d tokens), and it clears the young
    liquidity floor. Probe OFF -> always False -> skip as before (ZERO production change)."""
    if probe_on is None:
        probe_on = probe_enabled()
    if not probe_on:
        return False
    if not is_young(age_hours, max_h):
        return False
    if min_liq is None:
        min_liq = min_liq_usd()
    try:
        return (liq_usd or 0) >= min_liq
    except (TypeError, ValueError):
        return False


def buy_gate_skip(is_young_tok: bool, is_probe_bot: bool,
                  probe_on: Optional[bool] = None) -> bool:
    """Per-bot BUY gate when the probe is on. Returns True if this bot should SKIP
    this token. Probe bots trade YOUNG-ONLY; production bots SKIP young. When the
    probe is OFF, never skips here (young tokens aren't surfaced anyway)."""
    if probe_on is None:
        probe_on = probe_enabled()
    if not probe_on:
        return False
    if is_probe_bot:
        return not is_young_tok      # probe bot: young-only
    return is_young_tok              # production bot: skip young
