"""Regime BUY-GATE (2026-06-17) — binary BUY / DON'T-BUY for dip entries.

AxiS: "buy or don't buy, not size." When the market is clearly crashing, dip bots
should open ZERO new positions until it clears — not catch the falling knife.

Backtest (34k-trade / 4-week _bleed_trades set, 331 w/ regime features):
  - downside breadth (regime_h1_neg_pct) >= 35 = the bleed cliff: [35,40)=-2.3%/tr,
    [40,50)=-10.7%/tr; blocking >=35 removed -218% of bleed and flipped the kept book
    from net-negative to breakeven. BELOW 35 the edge is breakeven+ ([30,35)=+0.2%) ->
    do NOT block there (that's the overblock AxiS warned about).
  - SOL arm uses the 6h window, NOT 24h: sol_pc_h24<=-3 would keep the gate OFF for up
    to 24h after a crash (the lookback "remembers" it) -> miss the recovery bounce =
    overblock. sol_pc_h6 releases within hours.

ANTI-OVERBLOCK (AxiS constraint "don't overblock or for too long, or dip buying won't
work"): (1) thresholds catch only the crash cliff, not the breakeven mid-zone; (2)
re-evaluated EVERY scan cycle off live breadth + 6h SOL -> releases the instant the
regime clears, no cooldown/lockout; (3) FAIL-OPEN: missing features never block.

Applies to dip entries on live AND paper. Momentum-mode bots are exempt (they are not
dip-buyers; momentum continuation can work when dips don't)."""
import os
import logging

logger = logging.getLogger(__name__)

BREADTH_OFF = float(os.environ.get("BUY_GATE_BREADTH_OFF", "35"))   # h1_neg_pct >= -> broad-dump crash
SOL_H6_OFF = float(os.environ.get("BUY_GATE_SOL_H6_OFF", "-3"))     # sol_pc_h6 <= -> hard SOL crash (6h, fast-release)


def mode() -> str:
    """enforce | shadow | off. Default shadow (deploy safe; flip to enforce via env)."""
    return os.environ.get("REGIME_BUY_GATE_MODE", "shadow").strip().lower()


def gate_blocks(regime_h1_neg_pct, sol_pc_h6):
    """Return (block: bool, reason: str). DON'T-BUY only on a clear crash.
    FAIL-OPEN: any missing/bad feature contributes no block (never halts on a data gap).
    OR of the two crash arms; re-evaluate each cycle for fast release."""
    reasons = []
    try:
        if regime_h1_neg_pct is not None and float(regime_h1_neg_pct) >= BREADTH_OFF:
            reasons.append(f"breadth={float(regime_h1_neg_pct):.0f}>={BREADTH_OFF:.0f}")
    except Exception:
        pass
    try:
        if sol_pc_h6 is not None and float(sol_pc_h6) <= SOL_H6_OFF:
            reasons.append(f"sol_h6={float(sol_pc_h6):+.1f}<={SOL_H6_OFF:.0f}")
    except Exception:
        pass
    return (bool(reasons), " OR ".join(reasons) if reasons else "ok")


def verdict(regime_h1_neg_pct, sol_pc_h6):
    """Cycle-level convenience: returns dict the scanner stashes + logs.
    off=False means the gate is OFF (don't buy); enforced reflects mode."""
    block, reason = gate_blocks(regime_h1_neg_pct, sol_pc_h6)
    m = mode()
    return {
        "block": block,                 # would-block (regime is a crash)
        "enforced": block and m == "enforce",
        "mode": m,
        "reason": reason,
        "breadth": regime_h1_neg_pct,
        "sol_h6": sol_pc_h6,
    }
