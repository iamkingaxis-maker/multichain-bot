"""Regime size-dial — SHADOW (2026-06-08).

Day-level market-state multiplier for position sizing, from the 49-day day-level
regime analysis (reference_day_level_regime_real_2026_06_08). The dip-buy edge is
REAL but DAY-STATE-dependent and persistent (daily-WR autocorr 0.31, runs-test
z=-2.09, 8-day bad stretches). A GOOD market for dip-buying = SOL modestly RED on
the day (sol_pc_h24 in roughly -3%..0) + LOW memecoin downside breadth
(regime_h1_neg_pct <= ~23) -> the dips we buy BOUNCE. The edge flips OFF on:
  - BROAD-RED days (high downside breadth) -> dips keep dumping (the bad weeks), and
  - SOL-GREEN euphoria (sol_pc_h24 >= +2) -> chasing tops (zero great days ever).

MEASURE-ONLY: stamped into entry_meta as `regime_size_shadow` (the would-be
multiplier) + reasons; it does NOT change actual position size. Forward-validate
(do 1.5x-stamped entries actually out-WR/out-return 0.5x-stamped ones?) before any
enforcement. Fail-OPEN -> 1.0 (neutral) when the regime features are missing.

NOTE: the strongest live signal found (first-25%-of-day WR -> rest-of-day WR, r=0.52)
is NOT used here — it needs realized intraday outcomes not available at entry. This
v1 dial uses only entry-time market-state (SOL + breadth). Early-day-WR is a v2 hook.
"""

GOOD_MULT = 1.5
BAD_MULT = 0.5
NEUTRAL = 1.0

# Thresholds from the day-level analysis (in-sample; tune forward).
H1NEG_GOOD_MAX = 23.0   # low downside breadth = good market
H1NEG_BAD_MIN = 40.0    # broad capitulation = bad market
SOL_EUPHORIA_MIN = 2.0  # SOL melt-up = chasing tops = bad for dip-buying


def regime_size_verdict(meta: dict):
    """Return (multiplier, reasons) for the day's regime at entry. Fail-OPEN=1.0.

    multiplier in {0.5, 1.0, 1.5}; reasons is a list of short strings for logging.
    """
    if not isinstance(meta, dict):
        return NEUTRAL, ["no_meta"]
    sol = meta.get("sol_pc_h24")
    h1neg = meta.get("regime_h1_neg_pct")
    sol_ok = isinstance(sol, (int, float)) and not isinstance(sol, bool)
    h1_ok = isinstance(h1neg, (int, float)) and not isinstance(h1neg, bool)
    if not (sol_ok or h1_ok):
        return NEUTRAL, ["no_regime_features"]

    # BAD regime: broad downside capitulation OR SOL euphoria.
    bad_reasons = []
    if h1_ok and h1neg >= H1NEG_BAD_MIN:
        bad_reasons.append(f"broad_red(h1neg={h1neg:.0f})")
    if sol_ok and sol >= SOL_EUPHORIA_MIN:
        bad_reasons.append(f"sol_euphoria({sol:+.1f})")
    if bad_reasons:
        return BAD_MULT, bad_reasons

    # GOOD regime: controlled SOL pullback + low downside breadth (needs both).
    if sol_ok and h1_ok and sol < 0 and h1neg <= H1NEG_GOOD_MAX:
        return GOOD_MULT, [f"sol_red({sol:+.1f})", f"low_breadth(h1neg={h1neg:.0f})"]

    return NEUTRAL, ["mid_regime"]


if __name__ == "__main__":
    # quick self-test
    assert regime_size_verdict({"sol_pc_h24": -1.8, "regime_h1_neg_pct": 19})[0] == GOOD_MULT
    assert regime_size_verdict({"sol_pc_h24": +0.5, "regime_h1_neg_pct": 55})[0] == BAD_MULT
    assert regime_size_verdict({"sol_pc_h24": +3.0, "regime_h1_neg_pct": 15})[0] == BAD_MULT
    assert regime_size_verdict({"sol_pc_h24": +0.5, "regime_h1_neg_pct": 30})[0] == NEUTRAL
    assert regime_size_verdict({"sol_pc_h24": -1.0, "regime_h1_neg_pct": 30})[0] == NEUTRAL
    assert regime_size_verdict({})[0] == NEUTRAL
    assert regime_size_verdict({"regime_h1_neg_pct": 50})[0] == BAD_MULT  # one feature ok
    print("regime_size_dial self-test PASS")
