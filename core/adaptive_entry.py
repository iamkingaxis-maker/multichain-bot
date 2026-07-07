# core/adaptive_entry.py
"""Adaptive per-token entry levers (2026-07-07 entry-timing fleet + token-
conditional decode). Two pure, fail-open helpers:

1. swing_size_multiplier — the token-conditional finding: SWING/VOLATILITY is
   the dominant gap-through predictor (fat tail — violent tokens win more AND
   gap more). Blanket avoidance kills EV; the fix is to SIZE DOWN on violent
   *shallow* entries (the dead-cat tail) while keeping violent *deep* dips near
   full (they carry the EV). Uses pc_h24 (blow-off swing proxy) + pc_h6
   (deep-dip), which are ALWAYS present at decision time — never starves.

2. vsnap_reject — the fleet finding: the reachable held-vs-dead separator is the
   TIME-SHAPE of the bottom. Fast <4-min V-snaps die (27% hold); slow >=6-min
   grinds hold (48%). "Minutes since the recent low" = how long since the HL
   running-low was last set (a token still printing fresh lows just V-snapped;
   an old low means it based and is grinding). FAIL-OPEN: unknown low-age -> do
   NOT reject (never dark the lane).

Both are PURE and never raise — the caller passes already-extracted values.
"""
from __future__ import annotations
from typing import Optional


def swing_size_multiplier(pc_h24, pc_h6,
                          high_swing_pc_h24: float = 80.0,
                          deep_dip_pc_h6: float = -40.0,
                          violent_shallow_mult: float = 0.45,
                          violent_deep_mult: float = 0.70,
                          calm_mult: float = 1.0) -> float:
    """Return a size multiplier in (0,1]. Violent (big pump / high swing) AND
    shallow (not a deep dip) = the dead-cat tail -> size DOWN hard. Violent AND
    deep = keep most size (carries EV). Calm = full size. Missing data -> full
    size (fail-open, never shrinks a trade on unknown)."""
    try:
        h24 = None if pc_h24 is None else float(pc_h24)
        if h24 is not None and h24 != h24:  # NaN
            h24 = None
    except (TypeError, ValueError):
        h24 = None
    try:
        h6 = None if pc_h6 is None else float(pc_h6)
        if h6 is not None and h6 != h6:
            h6 = None
    except (TypeError, ValueError):
        h6 = None
    if h24 is None:
        return float(calm_mult)                 # can't assess swing -> full size
    violent = h24 >= float(high_swing_pc_h24)
    if not violent:
        return float(calm_mult)
    deep = (h6 is not None) and (h6 <= float(deep_dip_pc_h6))
    return float(violent_deep_mult if deep else violent_shallow_mult)


def update_recent_low(prev_low, price):
    """Incremental recent-low tracker feeding the vsnap low-age map. Returns
    (new_low, restamp): restamp=True when `price` is a new low or seeds the
    tracker (fresh knife -> caller stamps low_ts=now); False when price sits
    above the tracked low (grind -> low_ts ages). Bad price -> keep, no stamp."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return prev_low, False
    if prev_low is None:
        return p, True                          # seed on first sample
    try:
        pl = float(prev_low)
    except (TypeError, ValueError):
        return p, True
    if p < pl:
        return p, True                          # new lower low -> fresh
    return pl, False                            # above the low -> ages


def vsnap_reject(low_age_secs, min_age_secs: float) -> tuple[bool, str]:
    """Reject a fast V-snap: True if the token's recent low is YOUNGER than
    min_age_secs (still knifing / just V-snapped). FAIL-OPEN: low_age_secs None
    or min_age_secs<=0 -> (False, ...) never rejects. Grinds (old low) pass."""
    try:
        thr = float(min_age_secs)
    except (TypeError, ValueError):
        return False, "vsnap: bad threshold -> allow"
    if thr <= 0:
        return False, "vsnap: off"
    if low_age_secs is None:
        return False, "vsnap: low-age unknown -> allow (fail-open)"
    try:
        age = float(low_age_secs)
    except (TypeError, ValueError):
        return False, "vsnap: bad low-age -> allow (fail-open)"
    if age < thr:
        return True, f"vsnap: low is {age:.0f}s old < {thr:.0f}s (fresh V-snap/knife)"
    return False, f"vsnap: low is {age:.0f}s old >= {thr:.0f}s (grind, allow)"
