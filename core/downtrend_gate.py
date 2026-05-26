"""Watchlist-bypass downtrend gate — SHADOW (2026-05-26).

The `user_watchlist_bypass` trigger ("April-era filter-only mode") lets a
watchlist token enter whenever the filter chain passes, with NO positive entry
signal. For a token in a sustained, *still-falling* downtrend this means the
fleet re-buys a falling knife over and over: TROLL 2026-05-26 was bought 108×
into a −16% daily slide (−$224), held a median 6.5h each, while the same-size
neet (+$116, 97% WR) trended favorably. Size doesn't separate them — *momentum
direction* does.

This gate flags the falling-knife signature: a sustained multi-timeframe decline
that is STILL actively falling (not a sharp dip that's already bouncing — those
are the entries we want to keep). It is SHADOW only: `downtrend_verdict` is a
pure function; the scanner stamps its verdict into entry_meta for forward
analysis and does NOT block. Phase 1.5 mines the blocked cohort's realized P&L
to validate/tune before any per-bot enforcement.

Design note: the discriminator is the "still-falling" confirmation. A token can
be deeply red on h6 (normal for a dip buyer) and still be a great entry if it's
reversing now (pc_m5 ≥ 0). Only a deep decline that is *also* dropping on the 5m
/ recent window is treated as a knife.
"""

from typing import Optional, Tuple, List

# A "sustained decline" requires BOTH of these (deep on the hour and the 6h).
# Starting values — tunable; the shadow's block-rate-vs-P&L mining sets the real
# thresholds before enforcement.
DOWNTREND_PC_H1_MAX = -8.0
DOWNTREND_PC_H6_MAX = -15.0


def downtrend_verdict(
    meta: dict,
    pc_h1_max: float = DOWNTREND_PC_H1_MAX,
    pc_h6_max: float = DOWNTREND_PC_H6_MAX,
) -> Tuple[str, List[str]]:
    """Classify a candidate's trend as a still-falling knife.

    Returns (verdict, reasons):
      "BLOCK"   — sustained multi-TF decline AND still actively falling
      "PASS"    — shallow, or a deep dip that is reversing (pc_m5 ≥ 0)
      "UNKNOWN" — core fields missing; can't assess (fail-open, never blocks)

    Pure + defensive: reads via .get(), tolerates missing keys.
    """
    pc_h1 = meta.get("pc_h1")
    pc_h6 = meta.get("pc_h6")
    if pc_h1 is None or pc_h6 is None:
        return "UNKNOWN", []

    # Step 1: sustained decline on both the hour and the 6h.
    if not (pc_h1 < pc_h1_max and pc_h6 < pc_h6_max):
        return "PASS", []

    # Step 2: still-falling confirmation. A sharp pullback that is already
    # bouncing (pc_m5 ≥ 0, no further lower-low) is a real dip — let it through.
    reasons: List[str] = []
    pc_m5 = meta.get("pc_m5")
    h1_chg = meta.get("pc_h1_change_since_lookback")
    struct5 = meta.get("chart_structure_5m_state")

    if pc_m5 is not None and pc_m5 < 0:
        reasons.append(f"pc_m5={pc_m5:.2f}<0")
    if h1_chg is not None and h1_chg < 0:
        reasons.append(f"pc_h1_chg_since_lookback={h1_chg:.2f}<0")
    if isinstance(struct5, str) and struct5.lower() == "downtrend":
        reasons.append("chart_structure_5m=downtrend")

    if not reasons:
        # Deep decline but no active-falling signal → treat as a reversing dip.
        return "PASS", []

    reasons.insert(0, f"pc_h1={pc_h1:.1f}<{pc_h1_max} AND pc_h6={pc_h6:.1f}<{pc_h6_max}")
    return "BLOCK", reasons
