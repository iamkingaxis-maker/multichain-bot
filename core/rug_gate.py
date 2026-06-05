"""Rug-gate verdict (2026-06-04) — fleet-wide LP-unlock/burn rug block.

Ports trader.buy's legacy LP-UNLOCK BLOCK (trader.py:1375-1380) — which only the legacy
dip path got — to the shared scan decision so EVERY fleet bot gets it. In a ~98.6%-rug
sector, a redeemable (unlocked, non-burned) LP is rug-pull capability.

LOGIC (mirrors trader.buy:1335-1400):
- LP BURNED (lp_burned=True, mintLP=system address) => PASS. Burn is MORE secure than lock
  (tokens can never be redeemed) — never block a burned pool (the burn-inconsistency fix).
- LP UNLOCKED (lp_locked_pct < threshold) and NOT burned => BLOCK (rug-pull risk).
- lp_locked_pct UNKNOWN (no rugcheck) => NEUTRAL / fail-open — exactly trader.buy's posture
  (never block a buy on a rugcheck miss).

Pure (env read at the edge), unit-testable. MEASURE-ONLY shadow first; enforce after confirm.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple


def lp_lock_min_pct() -> float:
    try:
        return float(os.environ.get("RUG_GATE_LP_LOCK_MIN", "1"))
    except (TypeError, ValueError):
        return 1.0


def rug_gate_verdict(meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (verdict, reasons). PASS if LP burned; BLOCK if LP unlocked-and-not-burned;
    NEUTRAL (fail-open) if lp_locked_pct unknown."""
    if meta.get("lp_burned") is True:
        return "PASS", []  # burned LP = secure, never block
    lp = meta.get("lp_locked_pct")
    if not isinstance(lp, (int, float)) or isinstance(lp, bool):
        return "NEUTRAL", []  # unknown -> fail-open (matches legacy trader.buy)
    thr = lp_lock_min_pct()
    if lp < thr:
        return "BLOCK", [f"lp_locked_pct={lp:.1f}<{thr:.0f} and not burned (LP unlocked = rug-pull risk)"]
    return "PASS", []
