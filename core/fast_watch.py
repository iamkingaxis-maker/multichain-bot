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
    dip_pct: float
    rise_pct: float
    eval_cooldown_secs: float
    bot_allowlist: frozenset
    armed_max: int
    sample_window: int
    arm_band_pp: float

    @classmethod
    def from_env(cls) -> "FastWatchConfig":
        mode = os.environ.get("FAST_WATCH_MODE", "off").strip().lower()
        if mode not in ("off", "shadow", "enforce"):
            mode = "off"
        raw = os.environ.get("FAST_WATCH_BOT_ALLOWLIST", "").strip()
        allow = (frozenset(b.strip() for b in raw.split(",") if b.strip())
                 if raw else _DEFAULT_ALLOWLIST)
        return cls(
            mode=mode,
            interval_secs=_f("FAST_WATCH_INTERVAL_SECS", 3.0),
            dip_pct=_f("FAST_WATCH_DIP_PCT", 3.0),
            rise_pct=_f("FAST_WATCH_RISE_PCT", 3.0),
            eval_cooldown_secs=_f("FAST_WATCH_EVAL_COOLDOWN_SECS", 60.0),
            bot_allowlist=allow,
            armed_max=_i("FAST_WATCH_ARMED_MAX", 30),
            sample_window=_i("FAST_WATCH_SAMPLE_WINDOW", 40),
            arm_band_pp=_f("FAST_WATCH_ARM_BAND_PP", 15.0),
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


def shortlist(snapshot, trigger_fn: Callable, dedup: FastWatchDedup,
              is_held_or_blocked: Callable, now: float):
    """Return [(addr, entry)] for armed tokens worth a full evaluation.
    `trigger_fn(addr)` and `is_held_or_blocked(addr)` are injected for testability.
    `trigger_fn` is a generic move detector (dip OR rise) — see `move_fires`."""
    out = []
    for addr, entry in snapshot:
        if not trigger_fn(addr):
            continue
        if not dedup.should_eval(addr, now):
            continue
        if is_held_or_blocked(addr):
            continue
        out.append((addr, entry))
    return out


def arm_subset(candidates, cfg: FastWatchConfig):
    """Select the armed token addresses for the fast loop (Rev 2.1: in-play, volume-ranked).

    `candidates`: list of dicts {addr, pc_h1 (float|None), vol_h1 (float|None), in_band (bool)}.
    Arm the in-band tokens that are *in play* — near a threshold on either side
    (`abs(pc_h1) ≤ cfg.arm_band_pp`, i.e. not already far gone up *or* down) — ranked by recent
    volume (`vol_h1` desc, the tokens the fleet is most likely to buy). Returns an ordered list of
    addresses (≤ armed_max). Entry-type-agnostic so it serves both dip and momentum bots.
    """
    inplay = [
        c for c in candidates
        if c.get("in_band")
        and c.get("pc_h1") is not None
        and abs(c["pc_h1"]) <= cfg.arm_band_pp
    ]
    inplay.sort(key=lambda c: (c.get("vol_h1") or 0.0), reverse=True)
    return [c["addr"] for c in inplay][:cfg.armed_max]


def rolling_dip_pct(samples):
    """% drop of the latest sample off the window max. None if <2 valid (>0) samples.
    `samples`: iterable of prices (oldest→newest)."""
    vals = [p for p in samples if isinstance(p, (int, float)) and p > 0]
    if len(vals) < 2:
        return None
    hi = max(vals)
    if hi <= 0:
        return None
    return round((vals[-1] / hi - 1.0) * 100.0, 6)


def rolling_rise_pct(samples):
    """% gain of the latest sample off the window min. None if <2 valid (>0) samples.
    `samples`: iterable of prices (oldest→newest)."""
    vals = [p for p in samples if isinstance(p, (int, float)) and p > 0]
    if len(vals) < 2:
        return None
    lo = min(vals)
    if lo <= 0:
        return None
    return round((vals[-1] / lo - 1.0) * 100.0, 6)


def move_fires(samples, dip_pct: float, rise_pct: float) -> bool:
    """Bidirectional move detector: True on a fresh dip (off the window max) serving dip bots,
    OR a fresh rise (off the window min) serving momentum bots. Loose superset — the per-bot gates
    in `_evaluate_pair` make the actual buy decision."""
    dip = rolling_dip_pct(samples)
    if dip is not None and dip <= -abs(dip_pct):
        return True
    rise = rolling_rise_pct(samples)
    if rise is not None and rise >= abs(rise_pct):
        return True
    return False
