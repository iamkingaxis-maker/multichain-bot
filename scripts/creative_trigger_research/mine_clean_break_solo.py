"""Mine clean_break solo (n=246) for a data-supported gate.

Lifetime: WR=46.3%, total -$48.22, avg -$0.20/trade.
Recent live: 0/5, -$5.22.

Goal: find feature buckets that cleanly separate winners from losers and
propose a gate. Same approach as whale_conviction audit.
"""
import pickle
import statistics


def stats(label, group):
    if not group:
        print(f"  {label:<60} n=  0  (empty)")
        return None
    wins = [r for r in group if r.get("win")]
    pnls = [r.get("pnl") or 0 for r in group]
    pnl_pcts = [r.get("pnl_pct") or 0 for r in group]
    wr = len(wins) / len(group) * 100
    avg = sum(pnls) / len(group)
    avg_pct = sum(pnl_pcts) / len(group)
    tot = sum(pnls)
    print(
        f"  {label:<60} n={len(group):>4}  WR={wr:5.1f}%  "
        f"avg%={avg_pct:+6.2f}  avg$={avg:+6.2f}  total$={tot:+8.2f}"
    )
    return {"n": len(group), "wr": wr, "avg": avg, "tot": tot}


def main():
    with open("scripts/creative_trigger_research/.dataset.pkl", "rb") as f:
        rows = pickle.load(f)

    cb = [r for r in rows if (r.get("em_trigger_source") or "") == "clean_break"]
    print(f"clean_break solo population: {len(cb)}")
    stats("baseline", cb)

    # Numeric feature buckets
    print("\n=== em_bs_m5 (5m buy/sell ratio) ===")
    for lo, hi in [(0, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 99)]:
        g = [r for r in cb if r.get("em_bs_m5") is not None and lo <= r["em_bs_m5"] < hi]
        stats(f"bs_m5 [{lo}, {hi})", g)

    print("\n=== em_bs_h1 (1h buy/sell ratio) ===")
    for lo, hi in [(0, 0.9), (0.9, 1.1), (1.1, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 99)]:
        g = [r for r in cb if r.get("em_bs_h1") is not None and lo <= r["em_bs_h1"] < hi]
        stats(f"bs_h1 [{lo}, {hi})", g)

    print("\n=== em_bs_h6 (6h buy/sell ratio) ===")
    for lo, hi in [(0, 0.9), (0.9, 1.1), (1.1, 1.3), (1.3, 1.5), (1.5, 2.0), (2.0, 99)]:
        g = [r for r in cb if r.get("em_bs_h6") is not None and lo <= r["em_bs_h6"] < hi]
        stats(f"bs_h6 [{lo}, {hi})", g)

    print("\n=== em_avg_trade_size_h1_usd ===")
    for lo, hi in [(0, 30), (30, 60), (60, 100), (100, 200), (200, 500), (500, 9999)]:
        g = [r for r in cb if r.get("em_avg_trade_size_h1_usd") is not None
             and lo <= r["em_avg_trade_size_h1_usd"] < hi]
        stats(f"avg_trade_size_h1 [${lo}, ${hi})", g)

    print("\n=== em_cycles_seen_before_buy ===")
    for lo, hi in [(0, 5), (5, 15), (15, 30), (30, 60), (60, 100), (100, 200), (200, 9999)]:
        g = [r for r in cb if r.get("em_cycles_seen_before_buy") is not None
             and lo <= r["em_cycles_seen_before_buy"] < hi]
        stats(f"cycles_seen [{lo}, {hi})", g)

    print("\n=== em_liquidity_usd ===")
    for lo, hi in [(0, 20_000), (20_000, 50_000), (50_000, 100_000), (100_000, 250_000), (250_000, 500_000), (500_000, 9_000_000)]:
        g = [r for r in cb if r.get("em_liquidity_usd") is not None
             and lo <= r["em_liquidity_usd"] < hi]
        stats(f"liq [${lo:,}, ${hi:,})", g)

    print("\n=== em_peak_h24_6h_pct ===")
    for lo, hi in [(0, 25), (25, 50), (50, 100), (100, 200), (200, 500), (500, 1000), (1000, 99999)]:
        g = [r for r in cb if r.get("em_peak_h24_6h_pct") is not None
             and lo <= r["em_peak_h24_6h_pct"] < hi]
        stats(f"peak_h24_6h [{lo}, {hi}%)", g)

    print("\n=== em_h24_ratio_to_peak ===")
    for lo, hi in [(0, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80), (0.80, 0.95), (0.95, 1.01)]:
        g = [r for r in cb if r.get("em_h24_ratio_to_peak") is not None
             and lo <= r["em_h24_ratio_to_peak"] < hi]
        stats(f"ratio_to_peak [{lo}, {hi})", g)

    print("\n=== hour_ct (CT) ===")
    for h in range(0, 24, 3):
        g = [r for r in cb if r.get("hour_ct") in range(h, h+3)]
        stats(f"hour [{h:02d}-{h+2:02d}]", g)

    # COMPOUND winning bucket: find the cleanest 2-feature predicate
    print("\n=== COMPOUND: bs_m5>=X AND ratio<=Y ===")
    for bs_thr in (1.0, 1.2, 1.4):
        for ratio_thr in (0.40, 0.60, 0.80):
            g = [
                r for r in cb
                if (r.get("em_bs_m5") or 0) >= bs_thr
                and (r.get("em_h24_ratio_to_peak") or 1) <= ratio_thr
            ]
            stats(f"bs_m5>={bs_thr} AND ratio<={ratio_thr}", g)

    # Most negative buckets (the gate candidates) — find single-feature cliffs
    print("\n=== TOP LOSING SUB-COHORTS (gate candidates) ===")
    cliffs = []
    feats = [
        ("em_bs_m5", [(0, 0.8), (0.8, 1.0)]),
        ("em_avg_trade_size_h1_usd", [(500, 9999)]),
        ("em_cycles_seen_before_buy", [(60, 9999), (100, 9999), (200, 9999)]),
        ("em_h24_ratio_to_peak", [(0.80, 0.95)]),
        ("em_peak_h24_6h_pct", [(0, 25)]),
    ]
    for feat, ranges in feats:
        for lo, hi in ranges:
            g = [r for r in cb if r.get(feat) is not None and lo <= r[feat] < hi]
            if len(g) >= 10:
                wr = sum(1 for r in g if r.get("win")) / len(g) * 100
                tot = sum(r.get("pnl") or 0 for r in g)
                if tot < -3.0:
                    cliffs.append((feat, lo, hi, len(g), wr, tot))

    cliffs.sort(key=lambda c: c[5])  # most negative first
    print(f"\n  {'feature':40s} {'range':>20s} {'n':>4s} {'WR':>7s} {'total':>9s}")
    for feat, lo, hi, n, wr, tot in cliffs[:15]:
        print(f"  {feat:40s} [{lo:>7}, {hi:>7}) {n:>4d} {wr:>6.1f}% ${tot:>+7.2f}")


if __name__ == "__main__":
    main()
