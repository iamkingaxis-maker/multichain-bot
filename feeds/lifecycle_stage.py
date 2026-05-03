"""
Lifecycle-stage classifier + round-number mcap magnetism.

Memecoin-specific. Tokens at $1M-$100M FDV behave very differently
depending on where they are in their lifecycle. The same setup (e.g.
"-5% m5, +20% h1") means completely different things on a fresh
$2M launch vs a 3-day-old $50M post-pump corpse. Encoding the stage
as a categorical field lets every downstream filter or correlation be
analyzed *conditional on stage*.

Stages (mutually exclusive):
  fresh_launch       age < 24h, peak_h24 >= +50%, h24_ratio >= 0.40
                     — actively pumping or recently pumped, young pool.
  active_runner      peak_h24 >= +50% AND h24_ratio >= 0.60
                     — sustained near peak; momentum continuation candidate.
  post_pump_corpse   peak_h24 >= +100% AND h24_ratio < 0.30
                     — dumped >70% from peak; classic "buy the dip" trap.
  reviving           peak_h24 >= +50% AND h24_ratio in (0.30, 0.60)
                     AND vol_h1 strong (>= h6 average)
                     — second-leg setup, possibly reaccumulating.
  dead               vol_h24 < $300k AND |peak_h24| < 30%
                     — illiquid, no story.
  ranging            otherwise — modest vol, no clear thesis.

Round-number mcap magnetism (memecoin psych levels):
  $1M, $2M, $5M, $10M, $25M, $50M, $100M
Tokens consolidate near these. We expose proximity to the nearest one
as a continuous feature plus a boolean "within 5%" flag.

All inputs are already in the dip_scanner snapshot — pure derivation,
no new fetches.
"""
from __future__ import annotations

from typing import Dict, Any, Optional


_MCAP_PSYCH_LEVELS = [
    1_000_000, 2_000_000, 5_000_000,
    10_000_000, 25_000_000, 50_000_000, 100_000_000,
]


def classify_stage(
    *,
    mcap_usd: float,
    age_hours: float,
    peak_h24_pct: float,
    h24_ratio_to_peak: float,
    vol_h24_usd: float,
    vol_h1_usd: float,
    vol_h6_usd: float,
) -> Dict[str, Any]:
    """Classify a memecoin into a lifecycle stage based on snapshot fields."""
    # Defensive defaults
    age_hours = float(age_hours or 0)
    peak = float(peak_h24_pct or 0)
    ratio = float(h24_ratio_to_peak or 0)
    vol_h24 = float(vol_h24_usd or 0)
    vol_h1 = float(vol_h1_usd or 0)
    vol_h6 = float(vol_h6_usd or 0)

    # Fresh launch — under 24h with material recent move
    if age_hours < 24.0 and peak >= 50.0 and ratio >= 0.40:
        stage = "fresh_launch"
    # Active runner — sustained move, near peak
    elif peak >= 50.0 and ratio >= 0.60:
        stage = "active_runner"
    # Post-pump corpse — pumped hard, dumped harder
    elif peak >= 100.0 and ratio < 0.30:
        stage = "post_pump_corpse"
    # Reviving — pumped, dumped, but vol returning
    elif peak >= 50.0 and 0.30 <= ratio < 0.60 and vol_h6 > 0 and vol_h1 >= (vol_h6 / 6.0):
        stage = "reviving"
    # Dead — no volume, no move
    elif vol_h24 < 300_000 and abs(peak) < 30.0:
        stage = "dead"
    else:
        stage = "ranging"

    return {
        "lifecycle_stage": stage,
        "lifecycle_age_hours": round(age_hours, 2),
        "lifecycle_peak_h24_pct": round(peak, 2),
        "lifecycle_h24_ratio": round(ratio, 3),
    }


# ── Round-number mcap magnetism ────────────────────────────────────

def mcap_magnetism(mcap_usd: float, levels=_MCAP_PSYCH_LEVELS, near_pct: float = 5.0) -> Dict[str, Any]:
    """Distance from current mcap to the nearest psych level."""
    if not mcap_usd or mcap_usd <= 0:
        return {
            "mcap_nearest_psych_level_usd": None,
            "mcap_distance_to_psych_pct": None,
            "mcap_near_psych_level": False,
            "mcap_above_psych_level": None,
        }
    # Find nearest level by absolute % distance
    nearest = min(levels, key=lambda L: abs((mcap_usd - L) / L))
    pct_diff = (mcap_usd - nearest) / nearest * 100.0
    return {
        "mcap_nearest_psych_level_usd": nearest,
        "mcap_distance_to_psych_pct": round(pct_diff, 3),
        "mcap_near_psych_level": abs(pct_diff) <= near_pct,
        "mcap_above_psych_level": pct_diff > 0,
    }


# ── Convenience: compute both at once ──────────────────────────────

def analyze(
    *,
    mcap_usd: float,
    age_hours: float,
    peak_h24_pct: float,
    h24_ratio_to_peak: float,
    vol_h24_usd: float,
    vol_h1_usd: float,
    vol_h6_usd: float,
) -> Dict[str, Any]:
    """Return lifecycle stage + mcap magnetism in one dict."""
    out = classify_stage(
        mcap_usd=mcap_usd,
        age_hours=age_hours,
        peak_h24_pct=peak_h24_pct,
        h24_ratio_to_peak=h24_ratio_to_peak,
        vol_h24_usd=vol_h24_usd,
        vol_h1_usd=vol_h1_usd,
        vol_h6_usd=vol_h6_usd,
    )
    out.update(mcap_magnetism(mcap_usd))
    return out
