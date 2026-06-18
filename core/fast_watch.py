# core/fast_watch.py
"""Pure logic for the fast-watch loop (no scanner/asyncio imports).

The fast-watch loop re-checks the already-watched token cohort every few
seconds and escalates fresh dips into the existing scanner evaluation, instead
of waiting for the compute-bound ~150-165s main sweep. This module holds only
the cheap, deterministic decision logic so it is trivially unit-testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

# Live pool + dip-entry heavy hitters; used when FAST_WATCH_BOT_ALLOWLIST unset.
_DEFAULT_ALLOWLIST = frozenset({
    "badday_flush", "badday_flush_conviction", "deepflush_timebox", "timebox_probe_5mgreen",
    "badday_flush_live", "badday_flush_conviction_live", "deepflush_timebox_live",
    "timebox_probe_5mgreen_live",
})


def _f(env_key: str, default: float) -> float:
    try:
        return float(os.environ.get(env_key, "").strip())
    except (TypeError, ValueError):
        return default


def _i(env_key: str, default: int) -> int:
    try:
        return int(os.environ.get(env_key, "").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class FastWatchConfig:
    mode: str                 # "off" | "shadow" | "enforce"
    interval_secs: float
    trend_secs: int
    dip_pct: float
    eval_cooldown_secs: float
    bot_allowlist: frozenset

    @classmethod
    def from_env(cls) -> "FastWatchConfig":
        mode = os.environ.get("FAST_WATCH_MODE", "off").strip().lower()
        if mode not in ("off", "shadow", "enforce"):
            mode = "off"
        raw = os.environ.get("FAST_WATCH_BOT_ALLOWLIST", "").strip()
        if raw:
            allow = frozenset(b.strip() for b in raw.split(",") if b.strip())
        else:
            allow = _DEFAULT_ALLOWLIST
        return cls(
            mode=mode,
            interval_secs=_f("FAST_WATCH_INTERVAL_SECS", 3.0),
            trend_secs=_i("FAST_WATCH_TREND_SECS", 90),
            dip_pct=_f("FAST_WATCH_DIP_PCT", 3.0),
            eval_cooldown_secs=_f("FAST_WATCH_EVAL_COOLDOWN_SECS", 60.0),
            bot_allowlist=allow,
        )


def dip_trigger(trend_pct: Optional[float], threshold_pct: float) -> bool:
    """True when the token dipped at least `threshold_pct` over the trend window.

    Deliberately a LOOSE superset signal — the real entry gates inside
    `_evaluate_pair` make the actual buy decision. None (no buffered ticks) never
    triggers, so the fast loop is best-effort and the main sweep stays the net.
    """
    if trend_pct is None:
        return False
    return trend_pct <= -abs(threshold_pct)


class FastWatchDedup:
    """Per-token TTL guard so the fast loop doesn't re-evaluate the same token
    every tick. `now` is injected (seconds) for testability."""

    def __init__(self, ttl_secs: float):
        self.ttl = ttl_secs
        self._last: dict[str, float] = {}

    def should_eval(self, addr: str, now: float) -> bool:
        t = self._last.get(addr)
        return t is None or (now - t) >= self.ttl

    def mark(self, addr: str, now: float) -> None:
        self._last[addr] = now


def shortlist(snapshot, get_trend: Callable, dedup: FastWatchDedup,
              is_held_or_blocked: Callable, cfg: FastWatchConfig, now: float):
    """Return [(addr, entry, trend)] for cohort tokens worth a full evaluation.

    `snapshot` is a list of (addr, entry) pairs (a copy of the sticky watchlist).
    `get_trend(addr, secs)` and `is_held_or_blocked(addr)` are injected so this
    stays pure and testable.
    """
    out = []
    for addr, entry in snapshot:
        trend = get_trend(addr, cfg.trend_secs)
        if not dip_trigger(trend, cfg.dip_pct):
            continue
        if not dedup.should_eval(addr, now):
            continue
        if is_held_or_blocked(addr):
            continue
        out.append((addr, entry, trend))
    return out
