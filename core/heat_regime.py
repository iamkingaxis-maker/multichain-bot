"""Trailing universe-heat regime (2026-07-12, scratchpad/_sol_hot_market.md).

A DECISION-TIME regime signal: the rolling fraction of the last K fleet fills whose
realized peak reached >= +20% (call it reach20_roll). HIGH regime (fraction >= threshold)
means the hot right-tail is live -> the runner/TP2 exit target should be lifted so a fixed
+12 TP2 doesn't cap exactly the trips that now run further (given a token reaches +12,
55-62% reach +20; reach>=30 21% recent vs 9% prior; reach>=50 7.7% vs 0).

Computed strictly from PAST closes (no leakage), 4-half OOS validated (the HIGH-LOW
reach20 spread holds in all 4 chrono quarters). Process-global rolling window: it resets
on restart, which is acceptable for the forward-grading PAPER A/B (the window re-warms in
~25 fills). FAIL-SAFE: thin history -> COLD (never lift the runner on too little data).

This is EXIT-side only. Size-up in HIGH heat is deliberately NOT done here (second-order,
ruin-math exposure -- exit lever first, per the scan).
"""
from __future__ import annotations
import os
import threading
from collections import deque

_K = 25              # rolling window length (last K fleet fills)
_MIN_FILLS = 15      # need at least this many before producing any HIGH signal
_REACH_PCT = 20.0    # a fill "reached" if its peak_pnl_pct >= this
_THRESHOLD = 0.20    # HIGH when reach20_roll >= this

_lock = threading.Lock()
_window: deque[bool] = deque(maxlen=_K)


def mode() -> str:
    """HEAT_REGIME_MODE env kill: 'off' disables recording + the HIGH signal."""
    return os.environ.get("HEAT_REGIME_MODE", "on").strip().lower()


def record_close(peak_pnl_pct) -> None:
    """Record one FULLY-closed fleet fill's realized peak into the rolling window.
    Fail-safe on non-numeric/NaN. Called fleet-wide from the sell-booking path."""
    if mode() == "off":
        return
    try:
        pk = float(peak_pnl_pct)
    except (TypeError, ValueError):
        return
    if pk != pk:  # NaN
        return
    with _lock:
        _window.append(pk >= _REACH_PCT)


def reach20_roll() -> float:
    """Current rolling fraction reaching >=+20%. Returns 0.0 (COLD) until at least
    _MIN_FILLS have accumulated (fail-safe: never lift on thin history)."""
    with _lock:
        n = len(_window)
        if n < _MIN_FILLS:
            return 0.0
        return sum(1 for x in _window if x) / n


def is_high(threshold: float = _THRESHOLD) -> bool:
    """True when the trailing universe-heat regime is HIGH (lift the runner target)."""
    if mode() == "off":
        return False
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = _THRESHOLD
    return reach20_roll() >= thr


def window_state() -> dict:
    """Introspection for tests / telemetry."""
    with _lock:
        return {"n": len(_window), "reach20": (
            sum(1 for x in _window if x) / len(_window)) if _window else 0.0}


def reset() -> None:
    """Clear the window (tests / session boundary)."""
    with _lock:
        _window.clear()
