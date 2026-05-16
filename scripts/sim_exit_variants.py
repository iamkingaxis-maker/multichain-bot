"""Variants of exit options — variations on B and C to find optimum.

Tests:
  V1: B with different activation/giveback combos
  V2: TP1 sell% raised to 75% (more at TP1)
  V3: TP1 sell% raised to 100% (all at TP1)
  V4: Post-TP1 trail at 0.5% (very tight)
  V5: Combined V1 + V4 + V2
"""
from __future__ import annotations
import requests

API = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"


def fetch():
    trades = requests.get(API, timeout=20).json()
    trades = [t for t in trades if isinstance(t, dict)]
    return [t for t in trades if t.get("pnl_pct") is not None and t.get("peak_pnl_pct") is not None]


def categorize(t):
    peak = t["peak_pnl_pct"]
    actual = t["pnl_pct"]
    if peak >= 5.0:
        X = 2 * actual - 5.0
        return peak, actual, "TP1_FIRED", X
    return peak, actual, "NO_TP1", None


# --- Variant B (pre-TP1 trail tunable) ---
def sim_B(peak, actual, path, X, activate=2.5, giveback=1.5):
    if peak < activate:
        return actual
    if path == "TP1_FIRED":
        return actual
    return max(peak - giveback, actual)


# --- Variant C (post-TP1 trail tunable, modeling latency-floor at 1pp) ---
def sim_C(peak, actual, path, X, tighten=1.0):
    if path == "NO_TP1":
        return actual
    remainder_peak = 2 * peak - 5
    old_giveback = remainder_peak - X
    new_giveback = max(1.0, old_giveback - tighten)
    new_X = remainder_peak - new_giveback
    return 0.5 * 5.0 + 0.5 * new_X


# --- Variant TP1 sell% changes (assume same TP1 price = 5%) ---
def sim_tp1_sell(peak, actual, path, X, sell_frac=0.75):
    """Raise TP1 sell from 50% to sell_frac. Remainder still rides post-TP1 trail."""
    if path == "NO_TP1":
        return actual
    # Current: realized = 0.5*5 + 0.5*X. So X = 2*actual - 5.
    # New: realized = sell_frac*5 + (1-sell_frac)*X.
    return sell_frac * 5.0 + (1 - sell_frac) * X


# --- Variant: tighter post-TP1 trail (e.g., 0.5%) — uses sim_C with tighten=1.5 ---
def sim_tight_post_tp1(peak, actual, path, X, target_trail=0.5):
    """Tighten post-TP1 from 2.0% spec → target_trail spec.
    Model: every 1pp tightening reduces actual give-back by 1pp (linear)."""
    tighten = 2.0 - target_trail
    return sim_C(peak, actual, path, X, tighten=tighten)


def evaluate(trades, simfn, **kwargs):
    rs = [simfn(*categorize(t), **kwargs) for t in trades]
    actuals = [t["pnl_pct"] for t in trades]
    avg = sum(rs) / len(rs)
    delta_avg = avg - sum(actuals) / len(actuals)
    wr = sum(1 for x in rs if x > 0) / len(rs)
    dollar = avg * 20 / 100
    delta_dollar = dollar - (sum(actuals) / len(actuals)) * 20 / 100
    return avg, delta_avg, wr, dollar, delta_dollar


def main():
    trades = fetch()
    print(f"n trades: {len(trades)}\n")

    # Baseline
    baseline = sum(t["pnl_pct"] for t in trades) / len(trades)
    print(f"Baseline (actual): avg={baseline:+.2f}%  $/trade={baseline*20/100:+.3f}")
    print()

    print(f"{'Variant':<45} {'Avg':>8} {'Δ':>7} {'WR':>5} {'$/tr':>8} {'Δ$':>7}")
    print("-" * 88)

    variants = [
        ("B(activate=2.5, gb=1.5) — baseline B", sim_B, {"activate": 2.5, "giveback": 1.5}),
        ("B(activate=2.0, gb=1.5)", sim_B, {"activate": 2.0, "giveback": 1.5}),
        ("B(activate=3.0, gb=1.5)", sim_B, {"activate": 3.0, "giveback": 1.5}),
        ("B(activate=2.5, gb=1.0) — tighter", sim_B, {"activate": 2.5, "giveback": 1.0}),
        ("B(activate=2.5, gb=2.0) — looser", sim_B, {"activate": 2.5, "giveback": 2.0}),
        ("B(activate=2.0, gb=1.0) — aggressive", sim_B, {"activate": 2.0, "giveback": 1.0}),
        (None, None, None),  # separator
        ("TP1 sell=50% (current)", sim_tp1_sell, {"sell_frac": 0.50}),
        ("TP1 sell=66%", sim_tp1_sell, {"sell_frac": 0.66}),
        ("TP1 sell=75%", sim_tp1_sell, {"sell_frac": 0.75}),
        ("TP1 sell=100% (all at TP1)", sim_tp1_sell, {"sell_frac": 1.00}),
        (None, None, None),
        ("Post-TP1 trail 2.0% → 1.5%", sim_tight_post_tp1, {"target_trail": 1.5}),
        ("Post-TP1 trail 2.0% → 1.0% — baseline C", sim_tight_post_tp1, {"target_trail": 1.0}),
        ("Post-TP1 trail 2.0% → 0.5%", sim_tight_post_tp1, {"target_trail": 0.5}),
        ("Post-TP1 trail 2.0% → 0% (snap-out)", sim_tight_post_tp1, {"target_trail": 0.0}),
    ]
    for label, fn, kw in variants:
        if label is None:
            print()
            continue
        avg, dlt, wr, dollar, ddollar = evaluate(trades, fn, **kw)
        print(f"{label:<45} {avg:>+7.2f}% {dlt:>+6.2f}% {wr*100:>4.0f}% {dollar:>+7.3f} {ddollar:>+6.3f}")

    # --- Composite: best B + best TP1-sell + best post-TP1 ---
    print("\n=== COMPOSITE STACKS ===")
    print(f"{'Stack':<60} {'Avg':>8} {'WR':>5} {'$/tr':>8} {'Δ$':>7}")
    print("-" * 95)

    def composite(t, b_act, b_gb, tp1_sell, tight_target):
        peak, actual, path, X = categorize(t)
        # Apply B first
        if peak < b_act:
            pass  # actual untouched
        elif path == "NO_TP1":
            # Trail catches in pre-TP1 zone
            return max(peak - b_gb, actual)
        # path == TP1_FIRED: B doesn't fire. Apply TP1 sell% + tighter post-TP1.
        if path == "NO_TP1":
            return actual
        # Recompute post-TP1 path with both tp1_sell% and tightened trail
        tighten = 2.0 - tight_target
        remainder_peak = 2 * peak - 5
        old_giveback = remainder_peak - X
        new_giveback = max(1.0, old_giveback - tighten)
        new_X = remainder_peak - new_giveback
        return tp1_sell * 5.0 + (1 - tp1_sell) * new_X

    stacks = [
        ("B(2.5/1.5) + TP1=50% + post=1.0 — recommended B+C", 2.5, 1.5, 0.50, 1.0),
        ("B(2.5/1.5) + TP1=50% + post=0.5", 2.5, 1.5, 0.50, 0.5),
        ("B(2.5/1.5) + TP1=75% + post=1.0", 2.5, 1.5, 0.75, 1.0),
        ("B(2.5/1.5) + TP1=75% + post=0.5", 2.5, 1.5, 0.75, 0.5),
        ("B(2.0/1.0) + TP1=50% + post=0.5 — aggressive", 2.0, 1.0, 0.50, 0.5),
        ("B(2.0/1.0) + TP1=75% + post=0.5", 2.0, 1.0, 0.75, 0.5),
    ]
    for label, b_act, b_gb, tp1_sell, tight in stacks:
        rs = [composite(t, b_act, b_gb, tp1_sell, tight) for t in trades]
        avg = sum(rs) / len(rs)
        wr = sum(1 for x in rs if x > 0) / len(rs)
        dollar = avg * 20 / 100
        baseline_dollar = baseline * 20 / 100
        print(f"{label:<60} {avg:>+7.2f}% {wr*100:>4.0f}% {dollar:>+7.3f} {dollar-baseline_dollar:>+6.3f}")


if __name__ == "__main__":
    main()
