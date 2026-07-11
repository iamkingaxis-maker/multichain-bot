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


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def rug_gate_verdict(meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (verdict, reasons). Two independent rug signals; BLOCK if either fires.

    1. LP-UNLOCK (2026-06-04): lp_locked_pct < threshold and not burned = rug-pull
       capability. Burned LP passes THIS signal only — it must NOT short-circuit
       the hidden-supply check (HOODLANA's LP read clean 100 before AND after).
    2. HIDDEN-SUPPLY (2026-07-11, HOODLANA-class): hidden_supply_share_pct >= 60
       AND total_holders < 1000 = a dump-capable supply mass hidden below the
       top10 line in a thin holder base. Graded: HOODLANA-at-entry 72.84 caught;
       winner-kill 3.7-4.4% (<=5% bar); universe block ~6%. Catch-side is n=1 —
       thresholds env-tunable; the forward labeled cohort refines them.
    NEUTRAL (fail-open) when neither signal's inputs are present."""
    reasons: List[str] = []
    known = False
    # -- signal 1: LP unlock ------------------------------------------------
    if meta.get("lp_burned") is True:
        known = True  # burned = this signal known-clean
    else:
        lp = meta.get("lp_locked_pct")
        if isinstance(lp, (int, float)) and not isinstance(lp, bool):
            known = True
            thr = lp_lock_min_pct()
            if lp < thr:
                reasons.append(
                    f"lp_locked_pct={lp:.1f}<{thr:.0f} and not burned (LP unlocked = rug-pull risk)")
    # -- signal 2: hidden supply (HOODLANA class) ---------------------------
    hidden = meta.get("hidden_supply_share_pct")
    holders = meta.get("total_holders")
    if (isinstance(hidden, (int, float)) and not isinstance(hidden, bool)
            and isinstance(holders, (int, float)) and not isinstance(holders, bool)):
        known = True
        h_min = _env_f("RUG_GATE_HIDDEN_MIN", 60.0)
        h_max_holders = _env_f("RUG_GATE_HIDDEN_MAX_HOLDERS", 1000.0)
        if hidden >= h_min and holders < h_max_holders:
            reasons.append(
                f"hidden_supply={hidden:.1f}%>={h_min:.0f} with holders={int(holders)}<{h_max_holders:.0f} "
                f"(dump-capable supply below the top10 line — HOODLANA class)")
    if reasons:
        return "BLOCK", reasons
    return ("PASS" if known else "NEUTRAL"), []
