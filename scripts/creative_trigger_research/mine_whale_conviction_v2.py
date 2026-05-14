"""Re-mine whale_conviction bucketed by peak_h24_6h_pct AND h24_ratio_to_peak.

User's hypothesis: high-pump tokens with whales clustering in are
distributions, not pre-cursors. The dataset has em_pc_h24=None universally
(field not captured at mining time), so use the better proxies:

  - em_peak_h24_6h_pct: size of the 24h pump (high = late cycle)
  - em_h24_ratio_to_peak: current price / 24h peak (high = near top)

A "topping" entry would have HIGH peak_h24_6h AND HIGH h24_ratio_to_peak.
A "post-pump bottom" entry would have HIGH peak_h24_6h AND LOW ratio_to_peak.

Data source: scripts/creative_trigger_research/.dataset.pkl (n=1542 rows,
~7d window). Records have em_top10_buyer_within_60s_count but NOT
em_whale_buy_present_2k — that field appears to have been added later.
So we proxy whale_conviction as just `em_top10_buyer_within_60s_count >= 3`.
"""
import pickle


def fires_either(r):
    ts = r.get("em_trigger_source") or ""
    return "clean_break" in ts or "high_regime" in ts


def whale_conviction_fires(r):
    """Proxy for whale_conviction's predicate using available fields."""
    # Original predicate: whale_buy_present_2k=True OR top10>=3
    # Dataset has only top10. Whale_buy branch is unaudited here.
    t10 = r.get("em_top10_buyer_within_60s_count") or 0
    return t10 >= 3


def stats(label, group):
    if not group:
        print(f"  {label:<55} n=  0  (empty)")
        return
    wins = [r for r in group if r.get("win")]
    pnl_pcts = [r.get("pnl_pct") or 0 for r in group]
    pnls = [r.get("pnl") or 0 for r in group]
    wr = len(wins) / len(group)
    avg_pct = sum(pnl_pcts) / len(group)
    avg_d = sum(pnls) / len(group)
    tot_d = sum(pnls)
    print(
        f"  {label:<55} n={len(group):>4}  WR={wr*100:5.1f}%  "
        f"avg%={avg_pct:+6.2f}  avg$={avg_d:+6.2f}  total$={tot_d:+8.2f}"
    )


def main():
    with open("scripts/creative_trigger_research/.dataset.pkl", "rb") as f:
        rows = pickle.load(f)

    orth = [r for r in rows if not fires_either(r)]
    print(f"Orth pop: {len(orth)}")

    wc = [r for r in orth if whale_conviction_fires(r)]
    print(f"\nwhale_conviction fires (top10>=3 only): {len(wc)}")
    stats("baseline_orth", orth)
    stats("wc_all", wc)

    print("\n=== Bucketed by peak_h24_6h_pct (how big was 24h pump) ===")
    pump_buckets = [
        ("peak<25%",      lambda v: v is not None and v < 25),
        ("peak 25-50%",   lambda v: v is not None and 25 <= v < 50),
        ("peak 50-100%",  lambda v: v is not None and 50 <= v < 100),
        ("peak 100-200%", lambda v: v is not None and 100 <= v < 200),
        ("peak 200-500%", lambda v: v is not None and 200 <= v < 500),
        ("peak 500-1000%",lambda v: v is not None and 500 <= v < 1000),
        ("peak >=1000%",  lambda v: v is not None and v >= 1000),
        ("peak None",     lambda v: v is None),
    ]
    for label, pred in pump_buckets:
        g = [r for r in wc if pred(r.get("em_peak_h24_6h_pct"))]
        stats(label, g)

    print("\n=== Bucketed by h24_ratio_to_peak (closer to 1.0 = near peak) ===")
    ratio_buckets = [
        ("ratio<0.20 (deep pull)",  lambda v: v is not None and v < 0.20),
        ("ratio 0.20-0.40",         lambda v: v is not None and 0.20 <= v < 0.40),
        ("ratio 0.40-0.60",         lambda v: v is not None and 0.40 <= v < 0.60),
        ("ratio 0.60-0.80",         lambda v: v is not None and 0.60 <= v < 0.80),
        ("ratio 0.80-0.95",         lambda v: v is not None and 0.80 <= v < 0.95),
        ("ratio>=0.95 (at peak)",   lambda v: v is not None and v >= 0.95),
        ("ratio None",              lambda v: v is None),
    ]
    for label, pred in ratio_buckets:
        g = [r for r in wc if pred(r.get("em_h24_ratio_to_peak"))]
        stats(label, g)

    # Compound bucket: which combo is the cleanest cliff?
    print("\n=== Compound: peak >= X AND ratio >= Y (the 'topping' pattern) ===")
    for peak_thr in (200, 500, 1000):
        for ratio_thr in (0.6, 0.8, 0.95):
            g = [
                r for r in wc
                if (r.get("em_peak_h24_6h_pct") or 0) >= peak_thr
                and (r.get("em_h24_ratio_to_peak") or 0) >= ratio_thr
            ]
            stats(f"peak>={peak_thr}% AND ratio>={ratio_thr}", g)

    # Inverse: post-pump deep retracement (ratio low) — should be WINS
    print("\n=== Post-pump retrace (peak >= X AND ratio < Y) ===")
    for peak_thr in (200, 500, 1000):
        for ratio_thr in (0.40, 0.60):
            g = [
                r for r in wc
                if (r.get("em_peak_h24_6h_pct") or 0) >= peak_thr
                and 0 < (r.get("em_h24_ratio_to_peak") or 0) < ratio_thr
            ]
            stats(f"peak>={peak_thr}% AND ratio<{ratio_thr}", g)


if __name__ == "__main__":
    main()
