"""Profit-sweep simulator — paper-mode SHADOW, display-only.

Computes how much profit each bot WOULD have banked to the cold wallet under
three policies, by replaying its realized-P&L curve. Pure accounting: moves
nothing, touches no ledger, has zero effect on trading or the per-bot research
metrics. See docs/superpowers/specs/2026-05-25-profit-sweep-design.md.

Policies compared:
  HWM-50  — bank 50% of every new realized-profit high-water mark
  HWM-100 — bank 100% of every new high-water mark (maximally safe)
  Step    — bank 50% each time realized profit crosses another `step_dollars`
            (the literal "up another X%" model; lumpier, size-sensitive)

Key identity: banking `f` of each new high telescopes to `f * peak`, so the
HWM closed form is just `f * (peak cumulative realized)`. The step model
quantizes the peak to whole `step_dollars` increments first.
"""
from __future__ import annotations
from typing import Sequence


def realized_curve(pnls: Sequence[float]) -> tuple[float, float]:
    """Return (current_cumulative_realized, peak_cumulative_realized) over the
    TIME-ORDERED per-sell pnl series. Peak = max prefix sum (the high-water mark)."""
    cum = 0.0
    peak = 0.0
    for p in pnls:
        cum += (p or 0.0)
        if cum > peak:
            peak = cum
    return cum, peak


def hwm_banked(peak: float, fraction: float) -> float:
    """HWM ratchet banked total = fraction * peak (never negative)."""
    return max(0.0, peak) * fraction


def step_banked(peak: float, step_dollars: float, fraction: float = 0.5) -> float:
    """Step model: bank `fraction` of each `step_dollars` crossed at the peak.
    = fraction * step_dollars * floor(peak / step_dollars). Size-sensitive by
    design — illustrates why a fixed dollar/% trigger misfires across bot sizes."""
    if step_dollars <= 0:
        return 0.0
    steps = int(max(0.0, peak) // step_dollars)
    return fraction * step_dollars * steps


def simulate_bot(pnls: Sequence[float], step_dollars: float) -> dict:
    """Per-bot sim summary. `pnls` = time-ordered sell pnls; `step_dollars` =
    the step trigger size (dashboard uses 0.25 * base_position_usd)."""
    cur, peak = realized_curve(pnls)
    hwm50 = hwm_banked(peak, 0.5)
    return {
        "realized_now": round(cur, 2),
        "realized_peak": round(peak, 2),
        "banked_hwm_50": round(hwm50, 2),
        "banked_hwm_100": round(hwm_banked(peak, 1.0), 2),
        "banked_step": round(step_banked(peak, step_dollars), 2),
        # Under HWM-50, how much of CURRENT profit is still unbanked / exposed.
        "at_risk_now": round(max(0.0, cur - hwm50), 2),
        "step_dollars": round(step_dollars, 2),
    }
