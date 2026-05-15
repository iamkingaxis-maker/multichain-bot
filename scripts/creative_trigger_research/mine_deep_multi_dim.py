"""Deep multi-dimensional mining: 1D + 2D + 3D × 3 tiers (volume / precision / HOF).

Goes broader than earlier mining:
  - Adds derived features (ratios, products, gradients)
  - Adds categorical features (protocol, dow, weekend)
  - 1D scan first (single-feature WR — surface the strongest stand-alone signals)
  - 2D and 3D exhaustive
  - Three tiers per depth: bread-and-butter (high n, modest WR), precision
    (medium n, high WR), hall-of-fame (small n, exceptional WR).

Goal: find REPEATABLE high-WR patterns — not just rare precision strikes.
"""
import pickle
import math
from itertools import combinations, product
from collections import defaultdict


def stats(group):
    if not group:
        return 0, 0, 0, 0
    wins = sum(1 for r in group if r.get("win"))
    pnls = [r.get("pnl") or 0 for r in group]
    return len(group), wins / len(group) * 100, sum(pnls) / len(pnls), sum(pnls)


def in_range(val, lo, hi):
    return val is not None and lo <= val < hi


def derive_features(row):
    """Add derived/compound features to each row in place."""
    # Compound buyer pressure
    bs_h6 = row.get("em_bs_h6")
    bs_h1 = row.get("em_bs_h1")
    bs_m5 = row.get("em_bs_m5")
    if isinstance(bs_h6, (int, float)) and isinstance(bs_h1, (int, float)):
        row["d_bs_h6_x_h1"] = bs_h6 * bs_h1
        row["d_bs_h6_div_h1"] = bs_h6 / bs_h1 if bs_h1 > 0 else None
    # Mcap-to-liquidity ratio (depth metric)
    mc = row.get("entry_market_cap_usd")
    liq = row.get("em_liquidity_usd")
    if mc and liq and liq > 0:
        row["d_mcap_liq_ratio"] = mc / liq
    # Peak * ratio = "how far token has fallen from absolute peak (proxy for dump magnitude)"
    peak = row.get("em_peak_h24_6h_pct")
    ratio = row.get("em_h24_ratio_to_peak")
    if isinstance(peak, (int, float)) and isinstance(ratio, (int, float)):
        # Absolute drop from peak as a fraction
        row["d_drop_from_peak"] = (1 - ratio) * peak  # % drop magnitude
    # Cycles per hour of age
    cyc = row.get("em_cycles_seen_before_buy")
    age = row.get("entry_age_hours")
    if isinstance(cyc, (int, float)) and isinstance(age, (int, float)) and age > 0:
        row["d_cycles_per_hour"] = cyc / age
    # Trade-size relative to mcap (per-trade impact)
    ats = row.get("em_avg_trade_size_h1_usd")
    if isinstance(ats, (int, float)) and isinstance(mc, (int, float)) and mc > 0:
        row["d_trade_to_mcap_ppm"] = ats / mc * 1e6  # parts per million
    # Day-of-week bucket
    row["d_is_weekend"] = 1 if row.get("is_weekend") else 0
    return row


BUCKETS = {
    # Base features
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
    # Derived
    "d_bs_h6_x_h1": [(0, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.8), (1.8, 3.0), (3.0, 99)],
    "d_bs_h6_div_h1": [(0, 0.7), (0.7, 0.9), (0.9, 1.1), (1.1, 1.3), (1.3, 99)],
    "d_mcap_liq_ratio": [(0, 5), (5, 15), (15, 40), (40, 100), (100, 9999)],
    "d_drop_from_peak": [(0, 25), (25, 100), (100, 300), (300, 1000), (1000, 99999)],
    "d_cycles_per_hour": [(0, 0.1), (0.1, 0.5), (0.5, 2.0), (2.0, 10), (10, 9999)],
    "d_trade_to_mcap_ppm": [(0, 5), (5, 20), (20, 50), (50, 9999)],
    "d_is_weekend": [(0, 0.5), (0.5, 1.5)],
    "dow_ct": [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)],
}
NUMERIC_FEATS = list(BUCKETS.keys())


def scan_1d(rows, baseline_wr):
    """1D: every single bucket of every feature."""
    print("=" * 130)
    print("1D SCAN — single-feature buckets (sorted by total $)")
    print(f"Baseline WR: {baseline_wr:.1f}%  |  All rows: {len(rows)}")
    print("=" * 130)
    results = []
    for f in NUMERIC_FEATS:
        for (lo, hi) in BUCKETS[f]:
            g = [r for r in rows if in_range(r.get(f), lo, hi)]
            n, wr, avg, tot = stats(g)
            if n >= 30:  # require some volume for 1D
                results.append({
                    "feat": f, "lo": lo, "hi": hi,
                    "n": n, "wr": wr, "avg": avg, "tot": tot,
                    "lift": wr - baseline_wr,
                })
    results.sort(key=lambda r: -r["tot"])
    print(f"\nTop 30 by total $:")
    print(f"{'rank':<5} {'feature bucket':45s} {'n':>5} {'WR':>6} {'lift':>6} {'avg':>8} {'total':>10}")
    print("-" * 130)
    for i, r in enumerate(results[:30], 1):
        lbl = f"{r['feat'].replace('em_','').replace('entry_','').replace('d_','δ_')[:25]}[{r['lo']:g},{r['hi']:g})"
        print(f"{i:<5} {lbl:45s} {r['n']:>5} {r['wr']:>5.1f}% {r['lift']:>+5.1f}pp ${r['avg']:>+6.2f} ${r['tot']:>+8.2f}")
    return results


def scan_2d(rows, baseline_wr, tiers):
    """2D: every pair × every bucket combination."""
    print()
    print("=" * 130)
    print("2D SCAN — feature-pair buckets")
    print("=" * 130)
    results = []
    for fa, fb in combinations(NUMERIC_FEATS, 2):
        for (a_lo, a_hi), (b_lo, b_hi) in product(BUCKETS[fa], BUCKETS[fb]):
            g = [r for r in rows
                 if in_range(r.get(fa), a_lo, a_hi)
                 and in_range(r.get(fb), b_lo, b_hi)]
            n, wr, avg, tot = stats(g)
            if n >= 10:
                results.append({
                    "fa": fa, "a_lo": a_lo, "a_hi": a_hi,
                    "fb": fb, "b_lo": b_lo, "b_hi": b_hi,
                    "n": n, "wr": wr, "avg": avg, "tot": tot,
                    "lift": wr - baseline_wr,
                })
    print(f"\nTotal 2D cohorts: {len(results)}")
    for tname, (min_n, min_wr, min_tot) in tiers.items():
        cuts = [r for r in results if r["n"] >= min_n and r["wr"] >= min_wr and r["tot"] >= min_tot]
        cuts.sort(key=lambda r: -r["tot"])
        print()
        print(f"--- 2D TIER: {tname}  (n>={min_n}, WR>={min_wr}%, total>=+${min_tot}) ---")
        print(f"Found: {len(cuts)}  |  Top 15:")
        print(f"{'rank':<5} {'feat A bucket':35s} {'feat B bucket':35s} {'n':>4} {'WR':>6} {'lift':>6} {'avg':>8} {'total':>10}")
        for i, r in enumerate(cuts[:15], 1):
            a = f"{r['fa'].replace('em_','').replace('entry_','').replace('d_','δ_')[:18]}[{r['a_lo']:g},{r['a_hi']:g})"
            b = f"{r['fb'].replace('em_','').replace('entry_','').replace('d_','δ_')[:18]}[{r['b_lo']:g},{r['b_hi']:g})"
            print(f"{i:<5} {a:35s} {b:35s} {r['n']:>4} {r['wr']:>5.1f}% {r['lift']:>+5.1f}pp ${r['avg']:>+6.2f} ${r['tot']:>+8.2f}")
    return results


def scan_3d(rows, baseline_wr, tiers):
    """3D: every triple × every bucket combo."""
    print()
    print("=" * 130)
    print("3D SCAN — feature-triple buckets (this is the heavy lift)")
    print("=" * 130)
    results = []
    cell_count = 0
    triples = list(combinations(NUMERIC_FEATS, 3))
    print(f"Triples to scan: {len(triples)}")
    for ti, (fa, fb, fc) in enumerate(triples):
        if ti % 50 == 0:
            print(f"  ...{ti}/{len(triples)} triples processed ({cell_count:,} cells, {len(results)} qualifying)")
        for (a_lo, a_hi), (b_lo, b_hi), (c_lo, c_hi) in product(
            BUCKETS[fa], BUCKETS[fb], BUCKETS[fc]
        ):
            cell_count += 1
            g = [r for r in rows
                 if in_range(r.get(fa), a_lo, a_hi)
                 and in_range(r.get(fb), b_lo, b_hi)
                 and in_range(r.get(fc), c_lo, c_hi)]
            n, wr, avg, tot = stats(g)
            if n >= 10 and wr >= 60 and tot >= 10:
                results.append({
                    "fa": fa, "a_lo": a_lo, "a_hi": a_hi,
                    "fb": fb, "b_lo": b_lo, "b_hi": b_hi,
                    "fc": fc, "c_lo": c_lo, "c_hi": c_hi,
                    "n": n, "wr": wr, "avg": avg, "tot": tot,
                    "lift": wr - baseline_wr,
                })
    print(f"\nCells evaluated: {cell_count:,}")
    print(f"Total 3D qualifying cohorts: {len(results)}")
    for tname, (min_n, min_wr, min_tot) in tiers.items():
        cuts = [r for r in results if r["n"] >= min_n and r["wr"] >= min_wr and r["tot"] >= min_tot]
        cuts.sort(key=lambda r: -r["tot"])
        print()
        print(f"--- 3D TIER: {tname}  (n>={min_n}, WR>={min_wr}%, total>=+${min_tot}) ---")
        print(f"Found: {len(cuts)}  |  Top 20:")
        print(f"{'rank':<5} {'feat A':25s} {'feat B':25s} {'feat C':25s} {'n':>4} {'WR':>6} {'lift':>6} {'total':>10}")
        for i, r in enumerate(cuts[:20], 1):
            a = f"{r['fa'].replace('em_','').replace('entry_','').replace('d_','δ_')[:12]}[{r['a_lo']:g},{r['a_hi']:g})"
            b = f"{r['fb'].replace('em_','').replace('entry_','').replace('d_','δ_')[:12]}[{r['b_lo']:g},{r['b_hi']:g})"
            c = f"{r['fc'].replace('em_','').replace('entry_','').replace('d_','δ_')[:12]}[{r['c_lo']:g},{r['c_hi']:g})"
            print(f"{i:<5} {a:25s} {b:25s} {c:25s} {r['n']:>4} {r['wr']:>5.1f}% {r['lift']:>+5.1f}pp ${r['tot']:>+8.2f}")
    return results


def main():
    with open("scripts/creative_trigger_research/.dataset.pkl", "rb") as f:
        rows = pickle.load(f)
    print(f"Loaded {len(rows)} rows from .dataset.pkl")
    for r in rows:
        derive_features(r)
    print(f"Features: {len(NUMERIC_FEATS)} total (11 base + {len(NUMERIC_FEATS)-11} derived)")
    base_n, base_wr, base_avg, base_tot = stats(rows)
    print(f"Baseline: n={base_n} WR={base_wr:.1f}% avg=${base_avg:+.2f} total=${base_tot:+.2f}")
    print()

    tiers = {
        "BREAD-AND-BUTTER (high volume, modest WR)": (50, 65, 50),
        "PRECISION (medium volume, high WR)": (20, 80, 30),
        "HALL OF FAME (small but mighty)": (15, 85, 25),
    }

    r1 = scan_1d(rows, base_wr)
    r2 = scan_2d(rows, base_wr, tiers)
    r3 = scan_3d(rows, base_wr, tiers)


if __name__ == "__main__":
    main()
