"""Holder-concentration verdict (2026-06-04) — fleet-wide rug-proxy shadow.

In a ~98.6%-rug Solana memecoin sector, extreme holder concentration is a rug/pump-dump
signature. Tested fleet held-out: top10_holder_pct gate is net-positive ONLY at the
EXTREME — top10>=90 blocks n=55, 24% WR, net -$256 of loss avoided vs $100 winners clipped
(+$156 net); lower thresholds (>=60/70) clip far more winners than they save (net-negative).
So gate ONLY the extreme tail. Catches APM-type (91% top-10) rugs; misses moderate (grail
56%) by design — those need the missing LP-lock/mint-authority signals (fetched post-decision
today, not at scan time -> a separate wiring fix).

Pure (env read at the edge), unit-testable. MEASURE-ONLY shadow first; de-size after confirm.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple


def top10_threshold() -> float:
    try:
        return float(os.environ.get("HOLDER_CONC_TOP10_THR", "90"))
    except (TypeError, ValueError):
        return 90.0


def holder_concentration_verdict(meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (verdict, reasons). BLOCK (de-size target) when top10_holder_pct >= threshold
    (extreme concentration = rug/pump-dump risk). NEUTRAL (fail-open) if absent/non-numeric.
    PASS below the threshold (only the extreme tail is gated, to protect winners)."""
    thr = top10_threshold()
    t10 = meta.get("top10_holder_pct")
    if not isinstance(t10, (int, float)) or isinstance(t10, bool):
        return "NEUTRAL", []
    if t10 >= thr:
        return "BLOCK", [f"top10_holder_pct={t10:.1f}>={thr:.0f} (extreme holder concentration / rug risk)"]
    return "PASS", []
