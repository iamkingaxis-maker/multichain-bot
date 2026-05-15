"""Exhaustive 3D cohort mining across the FULL dataset (daytime+overnight).

Existing exhaustive miner only did 3D refinement on the top 10 2D cohorts.
This widens to every triple of features × every bucket combination, looking
for tight high-WR/high-EV cohorts anywhere in the feature space.

Threshold:
  n >= 10, WR >= 75%, total_$ >= +$15
Sort by total $ descending, report top 50.

Then do a tighter pass (WR >= 85%) for ship-worthy candidates.
"""
import pickle
from itertools import combinations, product


BUCKETS = {
    "em_bs_h6": [(0, 0.9), (0.9, 1.1), (1.1, 1.3), (1.3, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 99)],
    "em_bs_h1": [(0, 0.9), (0.9, 1.1), (1.1, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 99)],
    "em_bs_m5": [(0, 0.8), (0.8, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 99)],
    "em_peak_h24_6h_pct": [(0, 25), (25, 50), (50, 150), (150, 300), (300, 500), (500, 1000), (1000, 99999)],
    "em_h24_ratio_to_peak": [(0, 0.10), (0.10, 0.30), (0.30, 0.50), (0.50, 0.70), (0.70, 0.85), (0.85, 1.01)],
    "em_avg_trade_size_h1_usd": [(0, 30), (30, 60), (60, 100), (100, 200), (200, 500), (500, 9999)],
    "em_cycles_seen_before_buy": [(0, 10), (10, 30), (30, 60), (60, 150), (150, 9999)],
    "em_liquidity_usd": [(0, 50_000), (50_000, 100_000), (100_000, 250_000), (250_000, 1_000_000), (1_000_000, 9_999_999)],
    "entry_market_cap_usd": [(0, 500_000), (500_000, 2_000_000), (2_000_000, 10_000_000), (10_000_000, 999_999_999)],
    "entry_age_hours": [(0, 1), (1, 6), (6, 24), (24, 168), (168, 720), (720, 999_999)],
    "hour_ct": [(0, 4), (4, 8), (8, 12), (12, 17), (17, 22), (22, 24)],
}
NUMERIC_FEATS = list(BUCKETS.keys())


def stats(group):
    if not group:
        return 0, 0, 0, 0
    wins = sum(1 for r in group if r.get("win"))
    pnls = [r.get("pnl") or 0 for r in group]
    return len(group), wins / len(group) * 100, sum(pnls) / len(pnls), sum(pnls)


def in_range(val, lo, hi):
    return val is not None and lo <= val < hi


def main():
    with open("scripts/creative_trigger_research/.dataset.pkl", "rb") as f:
        rows = pickle.load(f)
    print(f"Total rows: {len(rows)}")
    print(f"Feature space: {len(NUMERIC_FEATS)} features, "
          f"{sum(len(v) for v in BUCKETS.values())} total buckets")
    print(f"3-feature triples: {len(list(combinations(NUMERIC_FEATS, 3)))}")
    print()

    # === EXHAUSTIVE 3D SCAN ===
    print("=" * 120)
    print("EXHAUSTIVE 3D SCAN — n>=10, WR>=75%, total_$ >= +$15")
    print("=" * 120)
    results = []
    cell_count = 0
    for fa, fb, fc in combinations(NUMERIC_FEATS, 3):
        for (a_lo, a_hi), (b_lo, b_hi), (c_lo, c_hi) in product(
            BUCKETS[fa], BUCKETS[fb], BUCKETS[fc]
        ):
            cell_count += 1
            g = [
                r for r in rows
                if in_range(r.get(fa), a_lo, a_hi)
                and in_range(r.get(fb), b_lo, b_hi)
                and in_range(r.get(fc), c_lo, c_hi)
            ]
            n, wr, avg, tot = stats(g)
            if n >= 10 and wr >= 75.0 and tot >= 15.0:
                results.append({
                    "fa": fa, "a_lo": a_lo, "a_hi": a_hi,
                    "fb": fb, "b_lo": b_lo, "b_hi": b_hi,
                    "fc": fc, "c_lo": c_lo, "c_hi": c_hi,
                    "n": n, "wr": wr, "avg": avg, "tot": tot,
                })
    print(f"\nCells evaluated: {cell_count:,}")
    print(f"Cohorts passing n>=10 / WR>=75% / total>=+$15: {len(results)}\n")

    results.sort(key=lambda r: -r["tot"])
    print(f"{'rank':<5} {'feat A bucket':32s} {'feat B bucket':32s} {'feat C bucket':32s} {'n':>4} {'WR':>6} {'avg':>8} {'total':>10}")
    print("-" * 150)
    for i, r in enumerate(results[:50], 1):
        a = f"{r['fa'].replace('em_','').replace('entry_','')[:14]}[{r['a_lo']:g},{r['a_hi']:g})"
        b = f"{r['fb'].replace('em_','').replace('entry_','')[:14]}[{r['b_lo']:g},{r['b_hi']:g})"
        c = f"{r['fc'].replace('em_','').replace('entry_','')[:14]}[{r['c_lo']:g},{r['c_hi']:g})"
        print(f"{i:<5} {a:32s} {b:32s} {c:32s} {r['n']:>4} {r['wr']:>5.1f}% ${r['avg']:>+6.2f} ${r['tot']:>+8.2f}")

    # === SHIP-WORTHY PASS — WR >= 85%, total >= +$25 ===
    print()
    print("=" * 120)
    print("SHIP-WORTHY: WR >= 85% AND total >= +$25 AND n >= 10")
    print("=" * 120)
    ship = [r for r in results if r["wr"] >= 85.0 and r["tot"] >= 25.0]
    print(f"\nCandidate triggers: {len(ship)}\n")
    print(f"{'rank':<5} {'feat A bucket':32s} {'feat B bucket':32s} {'feat C bucket':32s} {'n':>4} {'WR':>6} {'avg':>8} {'total':>10}")
    print("-" * 150)
    for i, r in enumerate(ship, 1):
        a = f"{r['fa'].replace('em_','').replace('entry_','')[:14]}[{r['a_lo']:g},{r['a_hi']:g})"
        b = f"{r['fb'].replace('em_','').replace('entry_','')[:14]}[{r['b_lo']:g},{r['b_hi']:g})"
        c = f"{r['fc'].replace('em_','').replace('entry_','')[:14]}[{r['c_lo']:g},{r['c_hi']:g})"
        print(f"{i:<5} {a:32s} {b:32s} {c:32s} {r['n']:>4} {r['wr']:>5.1f}% ${r['avg']:>+6.2f} ${r['tot']:>+8.2f}")

    # === HALL OF FAME — biggest $ regardless of WR threshold ===
    print()
    print("=" * 120)
    print("BIG-DOLLAR: Top 20 by total $ (any WR >= 60%)")
    print("=" * 120)
    bigwin = [r for r in results if r["wr"] >= 60.0]
    bigwin.sort(key=lambda r: -r["tot"])
    print()
    print(f"{'rank':<5} {'feat A bucket':32s} {'feat B bucket':32s} {'feat C bucket':32s} {'n':>4} {'WR':>6} {'avg':>8} {'total':>10}")
    print("-" * 150)
    for i, r in enumerate(bigwin[:20], 1):
        a = f"{r['fa'].replace('em_','').replace('entry_','')[:14]}[{r['a_lo']:g},{r['a_hi']:g})"
        b = f"{r['fb'].replace('em_','').replace('entry_','')[:14]}[{r['b_lo']:g},{r['b_hi']:g})"
        c = f"{r['fc'].replace('em_','').replace('entry_','')[:14]}[{r['c_lo']:g},{r['c_hi']:g})"
        print(f"{i:<5} {a:32s} {b:32s} {c:32s} {r['n']:>4} {r['wr']:>5.1f}% ${r['avg']:>+6.2f} ${r['tot']:>+8.2f}")


if __name__ == "__main__":
    main()
