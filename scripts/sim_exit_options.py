"""Simulate the 4 exit-logic options against actual closed trades.

Available data per trade:
  peak_pnl_pct    — max %PnL during hold
  peak_pnl_at_secs — seconds into hold when peak was hit
  hold_secs       — total hold time
  pnl_pct         — realized exit
  pnl             — realized $ P&L

Current rules (utils/config.py):
  dip_tp1_pct=5.0 / dip_tp1_sell=0.5  → at +5% sell 50%
  dip_tp2_pct=12.0 / dip_tp2_sell=1.0 → at +12% sell remaining
  dip_stop_pct=7.0                    → hard stop at -7%
  dip_winner_trail_pct=2.0            → post-TP1 trail 2% give-back

Memory note: actual post-TP1 trail give-back is empirically 5.9% median
(7.3% mean) due to 5s management-cycle latency — much wider than spec.

Sim approach for each trade:
  Decompose realized vs peak into "which path did this trade take":
    Path P (peak < 5%):       no TP1, exit via stop/trail/timeout. exit = actual
    Path T (peak >= 5%):      TP1=5%/50% fired. Remainder rode.
                              Remainder exit X solved from: actual = 0.5*5 + 0.5*X
                              → X = 2*actual - 5
                              post-TP1 give-back = peak - X
  Then for each option, recompute realized.
"""
from __future__ import annotations
import requests, json

API = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"


def fetch_trades():
    trades = requests.get(API, timeout=20).json()
    trades = [t for t in trades if isinstance(t, dict)]
    paired = [
        t for t in trades
        if t.get("pnl_pct") is not None and t.get("peak_pnl_pct") is not None
    ]
    return paired


def categorize(trade):
    """Return (peak, actual, path, X_post_tp1) where X is remainder exit if TP1 fired."""
    peak = trade["peak_pnl_pct"]
    actual = trade["pnl_pct"]
    if peak >= 5.0:
        # TP1 fired in actual run
        X = 2 * actual - 5.0
        return peak, actual, "TP1_FIRED", X
    else:
        return peak, actual, "NO_TP1", None


def sim_current(peak, actual, path, X):
    """Sanity: current rules → exactly actual."""
    return actual


def sim_option_A(peak, actual, path, X):
    """A: TP1 lowered to +3% / 33% sell. TP2 stays. Stop/trail post-TP1 unchanged."""
    if peak < 3.0:
        # Neither TP fires under A either. Same outcome.
        return actual
    if path == "NO_TP1":
        # 3 <= peak < 5. Under A, TP1 fires at +3, sell 33%. Remainder rides.
        # Remainder takes same trajectory as full position did → realizes actual.
        return 0.33 * 3.0 + 0.67 * actual
    else:
        # peak >= 5. Under A, TP1 fires at +3 / 33% sell. Then 67% remainder
        # rides under post-TP1 trail (2.0% give-back unchanged).
        # The remainder's exit price-wise = X (same as current's 50% remainder exit).
        return 0.33 * 3.0 + 0.67 * X


def sim_option_B(peak, actual, path, X, trail_giveback=1.5, activate_at=2.5):
    """B: Pre-TP1 trail. Once peak >= +2.5%, trail 1.5% give-back on FULL position.

    Assumption: pre-TP1 trail fires whenever peak retraces by giveback. Once
    peak reaches the TP1 threshold (5%) without triggering the trail, TP1
    takes over and the post-TP1 trail (unchanged at 2%) applies.

    Crude model: if peak < 5, the position never reached TP1 — pre-TP1 trail
    fires at peak - giveback (or stop -7 if that came first).
    If peak >= 5, TP1 already fired in current rules and post-TP1 trail
    governs the remainder — pre-TP1 trail doesn't change this.
    """
    if peak < activate_at:
        return actual
    if path == "TP1_FIRED":
        # peak >= 5: TP1 fired first; pre-TP1 trail irrelevant.
        return actual
    # 2.5 <= peak < 5: pre-TP1 trail catches the giveback.
    trail_exit = peak - trail_giveback
    # Trail vs actual: take whichever happened FIRST (better outcome for us).
    # If actual >= trail_exit: trade exited stronger, trail didn't fire → actual.
    # If actual < trail_exit: trade gave back past trail → trail catches at trail_exit.
    return max(trail_exit, actual)


def sim_option_C(peak, actual, path, X, tighten_amount=1.0):
    """C: post-TP1 trail tightened 2.0% → 1.0%. Only affects path TP1_FIRED.

    NOTE on math: peak is BLENDED PnL (50% locked at TP1=5 + 50% remainder).
    So remainder_peak = 2*peak - 5 (when blended peak occurs post-TP1).
    Old remainder exit X = 2*actual - 5.
    Old give-back on remainder = remainder_peak - X = 2*(peak - actual).
    Memory: empirically actual trail give-back is 5.9% median.
    Model: tightening spec by 1pp reduces actual give-back by ~1pp (linear).
    """
    if path == "NO_TP1":
        return actual
    remainder_peak = 2 * peak - 5
    old_giveback = remainder_peak - X  # = 2*(peak - actual)
    new_giveback = max(1.0, old_giveback - tighten_amount)
    new_X = remainder_peak - new_giveback
    return 0.5 * 5.0 + 0.5 * new_X


def sim_option_D(peak, actual, path, X, hold_threshold=300, profit_floor=1.0):
    """D: Time-based scratch exit. If hold >= 5min AND no new peak in 5min AND
    unrealized > +1%, exit at scratch. We approximate by: if peak < 3%
    (never reached TP1) AND hold > 5min AND peak > 1%, exit at +1% (scratch).
    """
    # Approximation: trade fits "no progress" pattern if peak was modest (<3%)
    # and we held > 5min.
    # In actual: we held the whole time. Under D we'd have exited at +1%
    # somewhere along the way once the timer expired.
    # Tight modeling needs per-minute path — this is a rough proxy.
    pass  # Placeholder — see notes below


def run_simulations():
    trades = fetch_trades()
    print(f"Sim against {len(trades)} closed paired trades\n")

    options = {
        "Current (baseline)": sim_current,
        "A: TP1 +3%/33%": sim_option_A,
        "B: Pre-TP1 trail @+2.5%/1.5%gb": sim_option_B,
        "C: Post-TP1 trail 2%→1%": sim_option_C,
        # B+C combined would be 2 fixes together
    }

    results = {name: [] for name in options}
    for t in trades:
        peak, actual, path, X = categorize(t)
        for name, fn in options.items():
            results[name].append({
                "token": t.get("token"),
                "peak": peak,
                "path": path,
                "actual": actual,
                "new": fn(peak, actual, path, X),
                "delta": fn(peak, actual, path, X) - actual,
                "pnl_usd": t.get("pnl") or 0,
            })

    # B+C combined: apply B first, then C-like adjustment to TP1_FIRED trades
    bc_results = []
    for t in trades:
        peak, actual, path, X = categorize(t)
        # Apply B
        new_b = sim_option_B(peak, actual, path, X)
        # Then C if TP1 fired (peak >= 5)
        if path == "TP1_FIRED":
            new_bc = sim_option_C(peak, actual, path, X)
        else:
            new_bc = new_b
        bc_results.append({
            "token": t.get("token"),
            "peak": peak,
            "path": path,
            "actual": actual,
            "new": new_bc,
            "delta": new_bc - actual,
            "pnl_usd": t.get("pnl") or 0,
        })
    results["B+C combined"] = bc_results

    # Aggregate summary
    print(f"{'Option':<32} {'Avg %':>9} {'Δ vs cur':>9} {'WR%':>6} {'$/trade':>9} {'Δ $/tr':>8}")
    print("-" * 80)
    baseline_avg = sum(r["actual"] for r in results["Current (baseline)"]) / len(trades)
    baseline_dollar = baseline_avg * 20 / 100  # $20 position
    baseline_wr = sum(1 for r in results["Current (baseline)"] if r["actual"] > 0) / len(trades)
    print(f"{'Current (baseline)':<32} {baseline_avg:>+8.2f}% {0:>+8.2f}% {baseline_wr*100:>5.0f}% {baseline_dollar:>+8.3f} {0:>+7.3f}")
    for name in ["A: TP1 +3%/33%", "B: Pre-TP1 trail @+2.5%/1.5%gb", "C: Post-TP1 trail 2%→1%", "B+C combined"]:
        rs = results[name]
        avg = sum(r["new"] for r in rs) / len(rs)
        wins = sum(1 for r in rs if r["new"] > 0)
        wr = wins / len(rs)
        dollar = avg * 20 / 100
        print(f"{name:<32} {avg:>+8.2f}% {avg-baseline_avg:>+8.2f}% {wr*100:>5.0f}% {dollar:>+8.3f} {dollar-baseline_dollar:>+7.3f}")

    # Per-bucket breakdown
    print(f"\nPer peak-bucket impact (n trades / Δ %):\n")
    print(f"{'Bucket':<20}", end="")
    for name in ["A: TP1 +3%/33%", "B: Pre-TP1 trail @+2.5%/1.5%gb", "C: Post-TP1 trail 2%→1%", "B+C combined"]:
        print(f" {name:<24}", end="")
    print()
    buckets = [
        ("peak < 2.5%", lambda p: p < 2.5),
        ("2.5% <= peak < 5%", lambda p: 2.5 <= p < 5),
        ("5% <= peak < 10%", lambda p: 5 <= p < 10),
        ("peak >= 10%", lambda p: p >= 10),
    ]
    for blabel, bfilter in buckets:
        bk_trades = [t for t in trades if bfilter(t["peak_pnl_pct"])]
        n = len(bk_trades)
        print(f"{blabel:<20} n={n:<4}", end="")
        for name in ["A: TP1 +3%/33%", "B: Pre-TP1 trail @+2.5%/1.5%gb", "C: Post-TP1 trail 2%→1%", "B+C combined"]:
            if n == 0:
                print(f" {'-':<24}", end="")
                continue
            rs = [r for r in results[name] if any(t.get("token")==r["token"] for t in bk_trades)]
            # Recompute from bucket trades to be precise
            bucket_results = []
            for t in bk_trades:
                peak, actual, path, X = categorize(t)
                if name == "A: TP1 +3%/33%":
                    new = sim_option_A(peak, actual, path, X)
                elif name == "B: Pre-TP1 trail @+2.5%/1.5%gb":
                    new = sim_option_B(peak, actual, path, X)
                elif name == "C: Post-TP1 trail 2%→1%":
                    new = sim_option_C(peak, actual, path, X)
                elif name == "B+C combined":
                    new_b = sim_option_B(peak, actual, path, X)
                    if path == "TP1_FIRED":
                        new = sim_option_C(peak, actual, path, X)
                    else:
                        new = new_b
                bucket_results.append(new - actual)
            avg_delta = sum(bucket_results) / len(bucket_results)
            print(f" Δ={avg_delta:+5.2f}% ({sum(1 for x in bucket_results if x>0):>2}↑/{sum(1 for x in bucket_results if x<0):>2}↓){'':6}", end="")
        print()

    # Show worst losers under each option (to confirm we're not creating new losses)
    print(f"\nTop 5 trades where each option HURTS (delta most negative):\n")
    for name in ["A: TP1 +3%/33%", "B: Pre-TP1 trail @+2.5%/1.5%gb", "C: Post-TP1 trail 2%→1%"]:
        rs = sorted(results[name], key=lambda x: x["delta"])[:5]
        print(f"\n  {name}:")
        for r in rs:
            print(f"    {r['token']:<10} peak={r['peak']:+5.2f}% actual={r['actual']:+5.2f}% → new={r['new']:+5.2f}% (Δ={r['delta']:+5.2f}%)")

    # Top 5 trades where each option helps
    print(f"\nTop 5 trades where each option HELPS (delta most positive):\n")
    for name in ["A: TP1 +3%/33%", "B: Pre-TP1 trail @+2.5%/1.5%gb", "C: Post-TP1 trail 2%→1%"]:
        rs = sorted(results[name], key=lambda x: -x["delta"])[:5]
        print(f"\n  {name}:")
        for r in rs:
            print(f"    {r['token']:<10} peak={r['peak']:+5.2f}% actual={r['actual']:+5.2f}% → new={r['new']:+5.2f}% (Δ={r['delta']:+5.2f}%)")


if __name__ == "__main__":
    run_simulations()
