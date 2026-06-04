"""Buyer-concentration verdict (2026-06-04).

THE first entry-side signal to clear the fleet's full discipline (held-out-by-token
+ token-clustered null + BH): on FRESH tokens, whale-dominated BUYING bleeds, broad
distributed buying continues. Fleet evidence: large_buyer_volume_pct Cohen d=-0.80
on fresh<24h trades (>=0.5 -> 9% WR vs 0-0.5 -> 78%); grad mine buyer_hhi/n_buyers
survive BH across 50 distinct tokens. Signal WASHES OUT on aged tokens -> fresh-only.

Pure (env read at the edge) so it is unit-testable and shared by the dip_scanner
shadow stamp and documented as the logic behind the momentum_grad_probe gate.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple


def block_threshold() -> float:
    try:
        return float(os.environ.get("BUYER_CONC_BLOCK_THR", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def buyer_concentration_verdict(meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (verdict, reasons). BLOCK if buying is whale-dominated
    (large_buyer_volume_pct >= threshold); PASS if below; NEUTRAL (fail-open) when
    the feature is absent/non-numeric — a value we cannot read never blocks."""
    thr = block_threshold()
    lbv = meta.get("large_buyer_volume_pct")
    if not isinstance(lbv, (int, float)) or isinstance(lbv, bool):
        return "NEUTRAL", []
    if lbv >= thr:
        return "BLOCK", [f"large_buyer_volume_pct={lbv:.2f}>={thr} (whale-dominated buying)"]
    return "PASS", []
