"""Badday microcap admission lane (2026-06-11).

The badday family (badday_flush/badday_momo) hunts 50-500k microcaps in deep-
flush or momentum-wave states — and the dip scanner's admission layer was
tuned to discard exactly that prey (mcap floor 500k, $200k/day volume floor,
plus regime rejects: trend_reversal, red_h24, no_dip, m5_dip_over,
mega_pump_middle, bs_h6, seller gates). Audit 2026-06-11: 31 flush + 5 momo
qualifying opportunities overnight, zero reached bot evaluation.

This lane mirrors the young_token_probe / low_mcap_probe pattern:
  - ADMISSION: keep_token() admits envelope tokens below the fleet mcap floor
    and exempts them from the regime rejects (scanner checks `_bdl`).
  - CONTAINMENT: buy_gate_skip() — sub-floor tokens are tradeable ONLY by
    bots with a microcap mandate (badday_*, young_token_probe,
    low_mcap_probe) or user-watchlist members. Controls and production
    never see them at buy time, so every other cohort's universe is
    UNCHANGED (the control counterfactual stays pure).

Built lane-first per AxiS's from-scratch principle (2026-06-11): trace the
full pipeline a new component depends on at design time.

Env: BADDAY_LANE=on|off (default on — the family is AxiS-approved).
"""
from __future__ import annotations
import os

MCAP_MIN = 50_000.0
MCAP_MAX = 500_000.0
AGE_MIN_H = 6.0          # the rug mine's #1 screen: catastrophes are <6h old
LIQ_MIN = 15_000.0
FLUSH_PC_H1 = -20.0      # bad-day surviving state 1 (56-59% win10 both folds)
MOMO_PC_H1 = 30.0        # bad-day surviving state 2 (63% win10 both folds)


def lane_enabled() -> bool:
    return os.environ.get("BADDAY_LANE", "on").strip().lower() not in ("off", "0", "false")


def in_envelope(mcap, age_h, liq_usd, pc_h1) -> bool:
    """The badday hunting envelope: microcap + rug-screen age + liq + state."""
    try:
        if not (MCAP_MIN <= float(mcap) < MCAP_MAX):
            return False
        if float(age_h) < AGE_MIN_H:
            return False
        if float(liq_usd) < LIQ_MIN:
            return False
        p1 = float(pc_h1)
        return p1 <= FLUSH_PC_H1 or p1 >= MOMO_PC_H1
    except (TypeError, ValueError):
        return False


def keep_token(mcap, liq_usd, age_h, pc_h1, std_min_mcap) -> bool:
    """Discovery admission: keep a sub-floor token when the lane is on and the
    token is in the envelope. OFF -> always False (zero behavior change)."""
    if not lane_enabled():
        return False
    try:
        if float(mcap) >= float(std_min_mcap):
            return False   # above the fleet floor -> normal pipeline applies
    except (TypeError, ValueError):
        return False
    return in_envelope(mcap, age_h, liq_usd, pc_h1)


def buy_gate_skip(is_sub_floor: bool, has_microcap_mandate: bool,
                  is_user_watch: bool = False) -> bool:
    """Containment: skip the buy when the token is below the fleet mcap floor
    and this bot has no microcap mandate (and the user didn't curate it)."""
    if not lane_enabled():
        return False
    return bool(is_sub_floor and not has_microcap_mandate and not is_user_watch)
