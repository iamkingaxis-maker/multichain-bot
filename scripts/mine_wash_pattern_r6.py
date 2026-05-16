"""Round 6: Look for FILTERS that catch wash-pattern entries even when
mean_buy_size_usd >= $15 passes. Maybe buy-size variance + tx frequency
+ chart position reveals wash trades that survive the $15 floor.

Also: check if any already-shipped trigger has a tunable threshold that
would improve WR.
"""
from __future__ import annotations
import requests
from collections import defaultdict

API = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"


def fetch():
    trades = requests.get(API, timeout=20).json()
    trades = [t for t in trades if isinstance(t, dict)]
    return [t for t in trades if t.get("pnl_pct") is not None]


def main():
    paired = fetch()
    print(f"n={len(paired)}")

    # Subset with wash floor (mean_buy>=15) applied
    def get(t, k):
        return (t.get("entry_meta") or {}).get(k)

    base = [t for t in paired if (get(t, "mean_buy_size_usd") or 0) >= 15]
    print(f"With mean_buy>=15: {len(base)}")

    # Check buy-size-distribution features
    print(f"\n=== Buy-size distribution filter mining (within wash-floor population) ===")
    candidates = [
        ("buy_size_stddev_last60s", "<=", [10, 15, 20, 25, 30, 40, 50]),
        ("buy_size_n_prior60s",     "<=", [3, 4, 5, 6, 7, 8, 10]),
        ("freq_n_prior60s",         "<=", [4, 5, 6, 7, 8, 10]),
        ("trades_per_sec_prior60s", "<=", [0.05, 0.07, 0.10, 0.15, 0.20]),
        ("unique_buyers_n",         "<=", [3, 4, 5, 6, 8, 10]),
        ("rt_consec_sells",         ">=", [2, 3, 4, 5]),
        ("buy_size_stddev_last60s", ">=", [100, 150, 200, 300]),  # HIGH variance = whale presence?
        ("buy_size_n_prior60s",     ">=", [10, 15, 20]),  # many buys
        ("chart_trendline_5m_pct_to_support", "<=", [1, 2, 3, 5]),
        ("chart_trendline_5m_pct_to_support", ">=", [10, 15, 20]),
    ]
    print(f"{'Feature':<40} {'op':<3} {'thr':<8} {'n_blk':>5} {'L':>3} {'W':>3} {'netΔ%':>8} {'win_blk_pct':>11}")
    print("-" * 90)
    for feat, op, thrs in candidates:
        for thr in thrs:
            blocked = []
            for t in base:
                v = get(t, feat)
                if v is None:
                    continue
                if op == "<=" and v <= thr:
                    blocked.append(t)
                elif op == ">=" and v >= thr:
                    blocked.append(t)
            if not blocked or len(blocked) < 5:
                continue
            L = sum(1 for t in blocked if t["pnl_pct"] <= 0)
            W = len(blocked) - L
            if W >= L:
                continue  # only report filters that block more losers than winners
            net = -sum(t["pnl_pct"] for t in blocked)
            win_blk_pct = W / len(blocked)
            if win_blk_pct >= 0.30:
                continue
            print(f"{feat:<40} {op:<3} {thr:<8} {len(blocked):>5} {L:>3} {W:>3} {net:>+7.1f}% {win_blk_pct:>10.0%}")

    # Round 6b: check if tightening shipped triggers helps
    print(f"\n=== Threshold tuning on already-shipped triggers (within wash-floor base) ===")
    # mean_buy>=15 is already the floor. Let's see if mean_buy>=25 / mean_buy>=50 helps further.
    base_wr = sum(1 for t in base if t["pnl_pct"] > 0) / len(base)
    avg = sum(t["pnl_pct"] for t in base) / len(base)
    print(f"Floor 15: n={len(base)} WR={base_wr:.0%} avg={avg:+.2f}%")
    for floor in (20, 25, 30, 40, 50, 75, 100):
        cohort = [t for t in base if (get(t, "mean_buy_size_usd") or 0) >= floor]
        if len(cohort) < 5:
            continue
        wr = sum(1 for t in cohort if t["pnl_pct"] > 0) / len(cohort)
        a = sum(t["pnl_pct"] for t in cohort) / len(cohort)
        print(f"Floor {floor}: n={len(cohort)} WR={wr:.0%} avg={a:+.2f}%")

    # Check net_flow_60s_usd thresholds (used in strong_orderflow at >0; vp_poc at >50)
    print(f"\nnet_flow_60s_usd thresholds (within wash-floor base):")
    for thr in (0, 25, 50, 75, 100, 150, 200, 300):
        cohort = [t for t in base if (get(t, "net_flow_60s_usd") or 0) >= thr]
        if len(cohort) < 5:
            continue
        wr = sum(1 for t in cohort if t["pnl_pct"] > 0) / len(cohort)
        a = sum(t["pnl_pct"] for t in cohort) / len(cohort)
        print(f"  flow60>={thr}: n={len(cohort)} WR={wr:.0%} avg={a:+.2f}%")

    # chart_mtf_score
    print(f"\nchart_mtf_score thresholds (within wash-floor base):")
    for thr in (-1, 0, 1, 2, 3):
        cohort = [t for t in base if (get(t, "chart_mtf_score") or -99) >= thr]
        if len(cohort) < 5:
            continue
        wr = sum(1 for t in cohort if t["pnl_pct"] > 0) / len(cohort)
        a = sum(t["pnl_pct"] for t in cohort) / len(cohort)
        print(f"  mtf>={thr}: n={len(cohort)} WR={wr:.0%} avg={a:+.2f}%")


if __name__ == "__main__":
    main()
