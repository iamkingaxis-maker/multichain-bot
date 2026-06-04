"""Stale-drift verdict (2026-06-04).

Multivariate entry-quality signal — the bleed localizes to age x pc_h24, NOT regime
(held-out-by-token win-AUC 0.60; regime features are NOT among the drivers). OLD tokens
(lifecycle_age_hours > AGE_HRS) sitting in the MIDDLE pc_h24 band [-20,+60) bleed:
in-sample trough x old = -$1.40/tr, 27% WR, n=522. OOS-validated on 17.8k universe events
(May 16-28): trough x old = median forward-peak 2.2% / 3% runners — the WORST cell, while
FRESH-extension (n=8795, 35% runners = the power-law tail) and DEEP-DIPS (mean-reversion)
WIN. Freshness is the dominant axis; old tokens bleed across all pc_h24 bands.

So: de-size OLD middling-drift entries; NEVER touch fresh-extension (the tail) or deep-dips.
Pure (env read at the edge), unit-testable, shared by the dip_scanner shadow stamp.
MEASURE-ONLY shadow first (per the never_runner ship pattern) -> de-size after forward confirm.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple


def age_threshold_hours() -> float:
    try:
        return float(os.environ.get("STALE_DRIFT_AGE_HRS", "168"))
    except (TypeError, ValueError):
        return 168.0


def pc_band() -> Tuple[float, float]:
    try:
        return (float(os.environ.get("STALE_DRIFT_PC_LO", "-20")),
                float(os.environ.get("STALE_DRIFT_PC_HI", "60")))
    except (TypeError, ValueError):
        return -20.0, 60.0


def stale_drift_verdict(meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (verdict, reasons). BLOCK (de-size target) when the token is OLD
    (lifecycle_age_hours > threshold) AND in the middle pc_h24 band [lo,hi) — the
    validated bleed cell. NEUTRAL (fail-open) if either feature is absent/non-numeric.
    PASS otherwise — fresh, deep-dip, or extension (the win cells, never touched)."""
    age = meta.get("lifecycle_age_hours")
    pc = meta.get("pc_h24")
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return "NEUTRAL", []
    if not isinstance(pc, (int, float)) or isinstance(pc, bool):
        return "NEUTRAL", []
    lo, hi = pc_band()
    ah = age_threshold_hours()
    if age > ah and lo <= pc < hi:
        return "BLOCK", [
            f"stale_drift: age={age:.0f}h>{ah:.0f} AND pc_h24={pc:.1f} in "
            f"[{lo:.0f},{hi:.0f}) (old middling-drift bleed)"
        ]
    return "PASS", []
