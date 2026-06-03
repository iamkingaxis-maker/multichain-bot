# core/low_mcap_probe.py
"""$500k-floor low-mcap probe (2026-06-02).

Forward data (universe recorder, age>=24h, liq-controlled) shows the 500k-1M mcap band
MATCHES/BEATS the 1M-5M band the fleet trades today (won_10% 25% vs 21%, peak 4.5% vs
4.0%, equal median exit) and ~doubles throughput — but the global min_mcap=$1M floor
hides it. This probe tests whether that edge survives on REALIZED dip-buy paths before
touching the global floor.

Surfaces ESTABLISHED tokens in [floor, min_mcap) — they already pass the age gate; only
the $1M mcap floor blocks them — for low_mcap_probe bots ONLY; production bots skip
sub-$1M tokens. Default OFF (LOW_MCAP_PROBE env) -> zero production change.
"""
from __future__ import annotations
import os
from typing import Optional


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def probe_enabled() -> bool:
    return _flag("LOW_MCAP_PROBE")


def floor_usd() -> float:
    try:
        return float(os.environ.get("LOW_MCAP_PROBE_FLOOR", "500000"))
    except (TypeError, ValueError):
        return 500000.0


def min_liq_usd() -> float:
    try:
        return float(os.environ.get("LOW_MCAP_PROBE_MIN_LIQ", "40000"))
    except (TypeError, ValueError):
        return 40000.0


def is_low_mcap(mcap, std_min_mcap, floor: Optional[float] = None) -> bool:
    """True if mcap is in the probe band [floor, std_min_mcap) — below the fleet floor
    but above the probe floor."""
    if floor is None:
        floor = floor_usd()
    try:
        m = float(mcap)
        return floor <= m < float(std_min_mcap)
    except (TypeError, ValueError):
        return False


def keep_below_floor_token(mcap, liq_usd, std_min_mcap, probe_on: Optional[bool] = None,
                           min_liq: Optional[float] = None, floor: Optional[float] = None) -> bool:
    """Discovery gate: KEEP a token below the fleet min_mcap only when the probe is ON,
    it's in [floor, std_min_mcap), and it clears the liquidity floor. OFF -> always False."""
    if probe_on is None:
        probe_on = probe_enabled()
    if not probe_on:
        return False
    if not is_low_mcap(mcap, std_min_mcap, floor):
        return False
    if min_liq is None:
        min_liq = min_liq_usd()
    try:
        return (liq_usd or 0) >= min_liq
    except (TypeError, ValueError):
        return False


def buy_gate_skip(is_low_mcap_tok: bool, is_probe_bot: bool,
                  probe_on: Optional[bool] = None) -> bool:
    """Per-bot BUY gate: probe bots trade the low-mcap band ONLY; production bots SKIP it.
    Probe OFF -> never skips here (the band isn't surfaced anyway)."""
    if probe_on is None:
        probe_on = probe_enabled()
    if not probe_on:
        return False
    if is_probe_bot:
        return not is_low_mcap_tok      # probe: low-mcap-only
    return is_low_mcap_tok              # production: skip low-mcap
