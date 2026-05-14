"""Re-mine whale_conviction trigger bucketed by pc_h24 to validate
the user's chart-read hypothesis: high-pc_h24 fires are distributions
(whales selling to bag-holders at the top), not pre-cursors.

Loads the same .dataset.pkl that 08_microstructure.py used. Filters to
rows where whale_conviction would FIRE (whale_buy_present_2k=True OR
top10_buyer_within_60s_count >= 3), excludes the orthogonal-population
filter (clean_break / high_regime), and buckets by em_pc_h24.

Outputs WR / avg% / avg$ per pc_h24 bucket.
"""
import pickle


def fires_either(r):
    ts = r.get("em_trigger_source") or ""
    return "clean_break" in ts or "high_regime" in ts


def whale_conviction_fires(r):
    """Does whale_conviction's predicate match on this row?"""
    if r.get("em_whale_buy_present_2k") is True:
        return True
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

    # Same orthogonal-population restriction as the original mining
    orth = [r for r in rows if not fires_either(r)]
    print(f"Total dataset rows: {len(rows)}")
    print(f"Orth pop (excludes clean_break/high_regime fires): {len(orth)}")

    # Population-level baseline
    print("\n=== Baseline (orth population, all fires regardless of whale_conviction) ===")
    stats("baseline_all_orth", orth)

    # whale_conviction fires only
    wc = [r for r in orth if whale_conviction_fires(r)]
    print(f"\n=== whale_conviction fires (orth subset): n={len(wc)} ===")
    stats("whale_conviction_all", wc)

    # Sub-branches
    sub_whale = [r for r in orth if r.get("em_whale_buy_present_2k") is True]
    sub_t10 = [r for r in orth if (r.get("em_top10_buyer_within_60s_count") or 0) >= 3]
    print("\n=== Sub-branches ===")
    stats("whale_buy_present_2k=True", sub_whale)
    stats("top10_buyer_within_60s_count>=3", sub_t10)

    # Bucket whale_conviction fires by pc_h24
    print("\n=== whale_conviction fires bucketed by pc_h24 ===")
    buckets = [
        ("pc_h24 missing/none", lambda v: v is None),
        ("pc_h24 < 0%", lambda v: v is not None and v < 0),
        ("pc_h24 0-25%", lambda v: v is not None and 0 <= v < 25),
        ("pc_h24 25-50%", lambda v: v is not None and 25 <= v < 50),
        ("pc_h24 50-100%", lambda v: v is not None and 50 <= v < 100),
        ("pc_h24 100-200%", lambda v: v is not None and 100 <= v < 200),
        ("pc_h24 200-500%", lambda v: v is not None and 200 <= v < 500),
        ("pc_h24 500-1000%", lambda v: v is not None and 500 <= v < 1000),
        ("pc_h24 >= 1000%", lambda v: v is not None and v >= 1000),
    ]
    for label, pred in buckets:
        g = [r for r in wc if pred(r.get("em_pc_h24"))]
        stats(label, g)

    # Same buckets but for the sub-branches separately
    print("\n=== whale_buy_present_2k=True bucketed by pc_h24 ===")
    for label, pred in buckets:
        g = [r for r in sub_whale if pred(r.get("em_pc_h24"))]
        stats(label, g)

    print("\n=== top10_buyer_within_60s_count>=3 bucketed by pc_h24 ===")
    for label, pred in buckets:
        g = [r for r in sub_t10 if pred(r.get("em_pc_h24"))]
        stats(label, g)

    # Find cleanest pc_h24 cap that maximizes EV
    print("\n=== Cumulative caps (pc_h24 <= X) — whale_conviction all ===")
    for cap in (10, 25, 50, 75, 100, 150, 200, 300, 500, 1000):
        g = [
            r for r in wc
            if r.get("em_pc_h24") is not None and r["em_pc_h24"] <= cap
        ]
        stats(f"pc_h24 <= {cap}%", g)


if __name__ == "__main__":
    main()
