"""Per-trigger token-state gates — SHADOW (2026-06-10).

Operationalizes the 2026-06-08 7-agent validation of AxiS's thesis: ~40 of 50
triggers only win in a specific TOKEN-STATE; pooling triggers with opposite
state needs is what produced the old "entry features non-predictive" null
(decisive proof: informed_cluster wins on CALM flow <=0.40 while
swing_structure_rsi wins on HOT flow >=0.57 — same feature, opposite splits).

This module stamps a per-trigger verdict into entry_meta at buy time — SHADOW
ONLY, zero behavior change. Forward validation at n>=50 per gate (the 3rd
independent confirm after the two held-out folds) decides which gates get
enforced. Archetype thresholds from the mined gate map (held-out stable in
BOTH May26-31 / Jun1-8 folds; per-cell n 20-60, hence shadow-first).

Verdicts: "pass" (state matches), "block" (trigger fired outside its state),
"na" (feature missing — fail-open by convention).
"""
from __future__ import annotations

# trigger -> (feature, op, threshold). One primary state marker per archetype.
TRIGGER_STATE_GATES: dict[str, tuple[str, str, float]] = {
    # 1 — DEEP DIP / off-peak entries
    "power_dip_runner":            ("pct_off_peak", "<=", -24.0),
    "volume_profile_aligned":      ("pct_off_peak", "<=", -24.0),
    "support_big_buyer":           ("pct_off_peak", "<=", -24.0),
    "textbook_pullback_big_buyer": ("pct_off_peak", "<=", -24.0),
    "deep_1h_dip":                 ("pct_off_peak", "<=", -24.0),
    # 2 — FRESH PEAK (pullbacks die when the peak is stale)
    "pullback_in_uptrend":         ("time_since_h1_peak_secs", "<=", 2820.0),
    "textbook_pullback_vol_accel": ("time_since_h1_peak_secs", "<=", 2820.0),
    "liq_velocity_big_buyers":     ("time_since_h1_peak_secs", "<=", 2820.0),
    # 3 — NOT-EUPHORIC FLOW
    "whale_conviction":            ("buy_sell_volume_imbalance", "<=", 0.38),
    "calm_buyer_demand":           ("buy_sell_volume_imbalance", "<=", 0.38),
    "cnn_cluster_16":              ("buy_sell_volume_imbalance", "<=", 0.38),
    "cnn_cluster_13":              ("buy_sell_volume_imbalance", "<=", 0.38),
    "informed_cluster":            ("buy_pressure_60s", "<=", 0.40),
    # 4 — MTF AGREEMENT (continuation patterns need timeframe agreement)
    "bullish_engulfing_5m":        ("chart_mtf_score", ">=", 2.0),
    "support_with_60s_flow":       ("chart_mtf_score", ">=", 2.0),
    "calm_at_support":             ("chart_mtf_score", ">=", 2.0),
    # 5 — REAL FLUSH (reversal triggers need an actual flush)
    "1s_capit_reversal":           ("macro30_pct", "<=", -8.3),
    # hot-flow side of the decisive-proof pair
    "swing_structure_rsi":         ("buy_pressure_60s", ">=", 0.57),
}


def trigger_state_verdicts(triggers_fired, feats: dict) -> dict[str, str]:
    """Verdict per FIRED trigger that has a mined gate. Fail-soft everywhere."""
    out: dict[str, str] = {}
    for trig in (triggers_fired or ()):
        gate = TRIGGER_STATE_GATES.get(trig)
        if gate is None:
            continue
        feat, op, thr = gate
        try:
            v = (feats or {}).get(feat)
            if not isinstance(v, (int, float)):
                out[trig] = "na"
            elif (v <= thr) if op == "<=" else (v >= thr):
                out[trig] = "pass"
            else:
                out[trig] = "block"
        except Exception:
            out[trig] = "na"
    return out


# ── Enforcement (2026-06-12, built DORMANT) ───────────────────────────────────
# The 06-11 scorecard showed 4 gates crossing the pre-registered enforce bar
# (n>=50 + WR lift): calm_at_support (86% pass vs 57% block), informed_cluster
# (66/55), support_with_60s_flow (80/63), whale_conviction (74/61).
# Activation is a ONE-VAR flip (AxiS approval):
#   TRIGGER_STATE_ENFORCE="calm_at_support,informed_cluster,support_with_60s_flow,whale_conviction"
# Default empty = shadow-only (today's behavior). Enforcement drops a fired
# trigger when it fired OUTSIDE its mined state; entry-stack control bots are
# exempt in the evaluator (clean counterfactual). NOTE deep_1h_dip's gate reads
# BACKWARDS forward (block-cohort 88% WR) — do NOT enforce it; re-mine.
import os as _os


def enforce_set() -> set:
    raw = _os.environ.get("TRIGGER_STATE_ENFORCE", "").strip()
    return {t.strip() for t in raw.split(",") if t.strip()} if raw else set()


def should_drop_trigger(trig: str, feats: dict) -> bool:
    """True if trig is in the enforce set AND fired outside its state."""
    if trig not in enforce_set():
        return False
    v = trigger_state_verdicts((trig,), feats)
    return v.get(trig) == "block"
