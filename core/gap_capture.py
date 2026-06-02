"""Gap-capture re-pricing for honest paper-EV (2026-06-02).

THE PROBLEM (found by the fidelity re-run): the paper engine books a TP exit at the
tick price even when the token GAPPED far above the TP trigger between polls. E.g.
champion_premium_tightexit's TP1 trigger is +5% but a tick read +47% and the engine
booked the full +47% — a +42pp gap a real poll-based market TP cannot reliably capture
(you'd fill near the trigger, or eat impact/latency on the way). Since the candidate's
edge IS those runner wins, this systematically OVER-CREDITS paper EV fleet-wide.

THE FIX (analysis-layer, no live behavior change): re-price TP exits by haircutting the
gap above the trigger. gap_capture in [0,1] = the fraction of the above-trigger gap you
realistically capture:
    realistic_pnl = realized - (1 - gap_capture) * max(0, realized - trigger)
  gap_capture=1.0 -> realized (current paper assumption, full gap)
  gap_capture=0.0 -> trigger  (conservative: fill AT the trigger, no gap)
  gap_capture=0.5 -> half the gap (a reasonable default until the LIVE probe measures it)
Non-TP exits (trail / slow_bleed / hard_stop / stall / open) are NOT haircut — they have
no fixed-trigger gap-UP over-credit (the true value is unknown only for TP gap-ups).

The true gap_capture is UNKNOWN until the live measurement probe observes real TP fills;
this module makes EV reads HONEST + tunable in the meantime. It does NOT change the live
bot's booked P&L (that enforce step is separate + approval-gated).
"""
from typing import Optional


def _is_tp(reason: Optional[str]) -> Optional[str]:
    """Return 'TP1'/'TP2' if the exit reason is a take-profit, else None."""
    r = str(reason or "").strip().upper()
    if r.startswith("TP1"):
        return "TP1"
    if r.startswith("TP2"):
        return "TP2"
    return None


def realistic_exit_pnl_pct(reason, realized_pnl_pct, tp1_pct, tp2_pct, gap_capture=0.5):
    """Honest TP-fill pnl%: haircut the gap above the TP trigger by (1-gap_capture).
    Only TP1/TP2 exits are adjusted; everything else (trail/stop/bleed/open) is returned
    unchanged. Clamps gap_capture to [0,1]; returns realized on bad inputs (fail-soft)."""
    try:
        realized = float(realized_pnl_pct)
    except (TypeError, ValueError):
        return realized_pnl_pct
    leg = _is_tp(reason)
    if leg is None:
        return realized
    trigger = tp1_pct if leg == "TP1" else tp2_pct
    try:
        trigger = float(trigger)
    except (TypeError, ValueError):
        return realized
    g = min(1.0, max(0.0, float(gap_capture)))
    gap_above = max(0.0, realized - trigger)   # only TP fills ABOVE trigger gapped
    return round(realized - (1.0 - g) * gap_above, 6)
