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

# Recalibrated 2026-06-18 from the 4-week backtest (n=763): the unambiguous crash cliff is
# breadth >= 40 ([40,50)=-6.93%/tr, 27% WR); the [35,40) zone is TWO-SIDED (51% WR) so it is
# only a don't-buy when SOL is ALSO down on the day. The prior sol_pc_h6<=-3 arm was DEAD
# (never fired); replaced with sol_pc_h24<=-1 gating only the mid-breadth band. Net: removes
# ~311pp of bleed killing only ~9 winners (vs ~35 at the old flat breadth>=35).
BREADTH_OFF = float(os.environ.get("BUY_GATE_BREADTH_OFF", "40"))     # >= -> crash cliff, block outright
BREADTH_MID = float(os.environ.get("BUY_GATE_BREADTH_MID", "35"))     # [MID,OFF) two-sided -> block only if SOL also down
SOL_H24_OFF = float(os.environ.get("BUY_GATE_SOL_H24_OFF", "-1"))     # sol_pc_h24 <= -> the day's tape is red


def mode() -> str:
    """enforce | shadow | off. Default shadow (deploy safe; flip to enforce via env)."""
    return os.environ.get("REGIME_BUY_GATE_MODE", "shadow").strip().lower()


def gate_blocks(regime_h1_neg_pct, sol_pc_h24):
    """Return (block: bool, reason: str). DON'T-BUY only on a clear crash.
    FAIL-OPEN: missing/bad feature contributes no block (never halts on a data gap).
    Two arms: breadth>=40 (outright), OR breadth in [35,40) AND sol_pc_h24<=-1 (mid-breadth
    on a red day). Re-evaluated each cycle (h1 breadth + h24 SOL) for fast release."""
    b = s = None
    try:
        b = float(regime_h1_neg_pct) if regime_h1_neg_pct is not None else None
    except Exception:
        b = None
    try:
        s = float(sol_pc_h24) if sol_pc_h24 is not None else None
    except Exception:
        s = None
    if b is not None and b >= BREADTH_OFF:
        return True, f"breadth={b:.0f}>={BREADTH_OFF:.0f}"
    if (b is not None and b >= BREADTH_MID
            and s is not None and s <= SOL_H24_OFF):
        return True, f"breadth={b:.0f}>={BREADTH_MID:.0f} AND sol_h24={s:+.1f}<={SOL_H24_OFF:.0f}"
    return False, "ok"


def verdict(regime_h1_neg_pct, sol_pc_h24):
    """Cycle-level convenience: returns dict the scanner stashes + logs.
    off=False means the gate is OFF (don't buy); enforced reflects mode."""
    block, reason = gate_blocks(regime_h1_neg_pct, sol_pc_h24)
    m = mode()
    return {
        "block": block,                 # would-block (regime is a crash)
        "enforced": block and m == "enforce",
        "mode": m,
        "reason": reason,
        "breadth": regime_h1_neg_pct,
        "sol_h24": sol_pc_h24,
    }
