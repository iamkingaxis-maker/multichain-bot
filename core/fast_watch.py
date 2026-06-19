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
    hot_max: int
    full_poll_every: int

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
            armed_max=_i("FAST_WATCH_ARMED_MAX", 500),
            sample_window=_i("FAST_WATCH_SAMPLE_WINDOW", 40),
            arm_band_pp=_f("FAST_WATCH_ARM_BAND_PP", 15.0),
            # TIERED POLL: hot subset (top-N armed by volume.h1) is polled EVERY
            # tick for ~3s freshness; the FULL armed set is polled every
            # full_poll_every-th tick for ~9s coverage. hot_max=50 keeps the
            # hot tier at exactly ONE Jupiter 50-id call. See _fast_watch_tick
            # for the req/min rate math.
            hot_max=_i("FAST_WATCH_HOT_MAX", 50),
            full_poll_every=max(1, _i("FAST_WATCH_FULL_POLL_EVERY", 3)),
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
    """Select the armed token addresses for the fast loop (Rev 2.3: pc_h1-AGNOSTIC, volume-ranked).

    `candidates`: list of dicts {addr, pc_h1 (float|None), vol_h1 (float|None), in_band (bool)}.
    The ONLY inclusion test is `in_band` (lane/fleet-band membership computed in
    `dip_scanner._fast_arm_subset` — the real filter). `pc_h1` direction does NOT gate which
    tokens are armed: the fleet buys BOTH deep dips AND pumps, so a one-sided `pc_h1 ≤ band`
    ceiling structurally excluded every momentum/pump token (6 bot families were 0/38 hits) and
    dropped any token with `pc_h1 = None`. `cfg.arm_band_pp` is retained on the config for docs/
    back-compat but is no longer consulted here. Ranked by recent volume (`vol_h1` desc — the
    most-active, most-buyable tokens, dips AND pumps); returns an ordered list of addresses
    (≤ armed_max, the rate-safe ceiling). Entry-type-agnostic so it serves dip and momentum bots.
    """
    inplay = [c for c in candidates if c.get("in_band")]
    inplay.sort(key=lambda c: (c.get("vol_h1") or 0.0), reverse=True)
    return [c["addr"] for c in inplay][:cfg.armed_max]


def hot_subset(armed, hot_max: int):
    """TIERED-POLL hot tier: the top `hot_max` armed addresses ranked by recent
    volume (pair `volume.h1` desc — the most-active tokens are the most likely to
    be bought, so they get the fastest ~3s freshness). Pure in-memory sort over
    the already-armed `self._fast_armed` (addr -> pair); does NOT change which
    tokens are armed (arm_subset owns that) or the trigger/escalate logic.

    `armed`: mapping addr -> pair dict (ORIGINAL-case keys; pairs carry volume.h1).
    Returns an ordered list of ≤ hot_max addresses (original case preserved). A
    non-positive `hot_max` yields an empty hot tier.
    """
    if hot_max <= 0:
        return []
    items = list((armed or {}).items())

    def _vol(pair):
        try:
            return float((pair or {}).get("volume", {}).get("h1") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    items.sort(key=lambda kv: _vol(kv[1]), reverse=True)
    return [addr for addr, _pair in items][:hot_max]


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


# ──────────────────────────────────────────────────────────────────────────────
# FORWARD FILL-SPEED CAPTURE (shadow) — fast-entry-price vs main-sweep-entry-price
# ──────────────────────────────────────────────────────────────────────────────
# The historical counterfactual (scripts/fill_speed_pnl.py) is data-blocked:
# DexScreener doesn't retain pre-entry price trajectory for old trades. Instead we
# capture the comparison AT THE MOMENT it exists — the fast-watch loop already holds
# the fresh price it WOULD fill at (the would-fill price), the main sweep produces
# the ACTUAL fill (entry_price). We log both keyed by ADDRESS (never symbol — a
# symbol-keyed price cross-poisons same-ticker mints) so we accumulate real per-trade
# (fast-entry-price vs sweep-entry-price, same exit) deltas to judge fill-speed P&L
# at n>=30 going forward. Pure + deterministic so it is trivially unit-testable.


def _pos_num(x):
    """Return float(x) if it is a real, strictly-positive number, else None."""
    if isinstance(x, bool):  # bool is an int subclass — reject it explicitly
        return None
    if not isinstance(x, (int, float)):
        return None
    f = float(x)
    if f <= 0.0:
        return None
    return f


def fill_speed_delta_pct(fast_price, sweep_price):
    """(sweep/fast - 1) * 100 — how much DEARER the main-sweep fill was vs the fast
    would-fill price (positive = the fast entry was cheaper; negative = the fast
    entry front-ran a further drop). None on any non-positive / bad input."""
    f = _pos_num(fast_price)
    s = _pos_num(sweep_price)
    if f is None or s is None:
        return None
    return (s / f - 1.0) * 100.0


def realized_pair(fast_price, sweep_price, exit_price):
    """Given the SAME exit, the realized P&L of each entry side and the edge.

    Returns (fast_pnl_pct, sweep_pnl_pct, edge_pp) where
        fast_pnl_pct  = (exit/fast  - 1) * 100
        sweep_pnl_pct = (exit/sweep - 1) * 100
        edge_pp       = fast_pnl_pct - sweep_pnl_pct   (the decisive number)
    None if any price is missing or <= 0 (guards bad/half-recorded rows)."""
    f = _pos_num(fast_price)
    s = _pos_num(sweep_price)
    x = _pos_num(exit_price)
    if f is None or s is None or x is None:
        return None
    fast_pnl = (x / f - 1.0) * 100.0
    sweep_pnl = (x / s - 1.0) * 100.0
    return (fast_pnl, sweep_pnl, fast_pnl - sweep_pnl)


def fill_speed_record(token, bot, fast_price, fast_ts, sweep_price, sweep_ts,
                      address, exit_price=None, now_ts=None):
    """Build ONE forward fill-speed shadow record (a plain dict, JSON-safe).

    ADDRESS-keyed (`token_address`) — NEVER symbol-keyed (same-ticker mints
    cross-poison symbol-keyed price state). `token`/`symbol` is carried for human
    reading only. `lead_secs` = sweep_ts - fast_ts (how much earlier the fast loop
    saw the price). `delta_pct` = fill_speed_delta_pct (sweep vs fast). Exit P&L is
    NOT captured here — the trade lifecycle already records exit_price; the offline
    joiner (scripts/fill_speed_forward.py) fills realized P&L by joining on
    address+entry. All fields degrade to None on bad input (never raises)."""
    lead = None
    try:
        if fast_ts is not None and sweep_ts is not None:
            lead = float(sweep_ts) - float(fast_ts)
    except (TypeError, ValueError):
        lead = None
    rec = {
        "ts": (now_ts if now_ts is not None else _now_iso()),
        "token_address": address,
        "symbol": token,
        "bot": bot,
        "fast_price": fast_price,
        "fast_ts": fast_ts,
        "sweep_price": sweep_price,
        "sweep_ts": sweep_ts,
        "lead_secs": lead,
        "delta_pct": fill_speed_delta_pct(fast_price, sweep_price),
    }
    if exit_price is not None:
        rec["exit_price"] = exit_price
    return rec


def _now_iso():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()
