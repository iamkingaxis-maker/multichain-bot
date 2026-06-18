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
    eval_cooldown_secs: float
    bot_allowlist: frozenset
    armed_max: int
    sample_window: int
    volatility_reserve: float
    dip_zone_pct: float
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
            eval_cooldown_secs=_f("FAST_WATCH_EVAL_COOLDOWN_SECS", 60.0),
            bot_allowlist=allow,
            armed_max=_i("FAST_WATCH_ARMED_MAX", 30),
            sample_window=_i("FAST_WATCH_SAMPLE_WINDOW", 40),
            volatility_reserve=_f("FAST_WATCH_VOLATILITY_RESERVE", 0.2),
            dip_zone_pct=_f("FAST_WATCH_DIP_ZONE_PCT", -12.0),
            arm_band_pp=_f("FAST_WATCH_ARM_BAND_PP", 12.0),
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
    """Return [(addr, entry, trend)] for armed tokens worth a full evaluation.
    `get_trend(addr)` and `is_held_or_blocked(addr)` are injected for testability."""
    out = []
    for addr, entry in snapshot:
        trend = get_trend(addr)
        if not dip_trigger(trend, cfg.dip_pct):
            continue
        if not dedup.should_eval(addr, now):
            continue
        if is_held_or_blocked(addr):
            continue
        out.append((addr, entry, trend))
    return out


def arm_subset(candidates, cfg: FastWatchConfig):
    """Select the armed token addresses for the fast loop.

    `candidates`: list of dicts {addr, pc_h1 (float|None), vol_h1 (float|None), in_band (bool)}.
    distance = pc_h1 − cfg.dip_zone_pct  (pp ABOVE the dip-zone edge; e.g. pc_h1=-8, edge=-12 → 4pp).
    Arm tokens approaching the zone (0 < distance ≤ arm_band_pp), smallest distance first (closest to
    firing), filling cfg.armed_max; reserve a fraction for highest-volatility in-band tokens so a sudden
    crash on a non-near-miss can still be caught. Returns an ordered list of addresses (≤ armed_max).
    """
    in_band = [c for c in candidates if c.get("in_band")]
    cusp = []
    for c in in_band:
        pc = c.get("pc_h1")
        if pc is None:
            continue
        dist = pc - cfg.dip_zone_pct
        if 0 < dist <= cfg.arm_band_pp:
            cusp.append((dist, c["addr"]))
    cusp.sort(key=lambda t: t[0])
    n_cusp = max(0, int(round(cfg.armed_max * (1.0 - cfg.volatility_reserve))))
    armed = [a for _d, a in cusp[:n_cusp]]
    chosen = set(armed)
    n_reserve = cfg.armed_max - n_cusp
    if n_reserve > 0:
        vol = sorted(
            (c for c in in_band if c["addr"] not in chosen and c.get("vol_h1") is not None),
            key=lambda c: c["vol_h1"], reverse=True,
        )
        armed.extend(c["addr"] for c in vol[:n_reserve])
    return armed[:cfg.armed_max]


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
