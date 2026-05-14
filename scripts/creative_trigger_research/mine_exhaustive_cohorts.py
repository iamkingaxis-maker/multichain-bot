"""Exhaustive 2-feature pair mining for high-EV cohorts the bot hasn't found.

Strategy: brute-force every 2D grid across all em_ + bot-state features.
For each cell, compute: n, WR, total$, avg$. Filter to cohorts with
  n >= 15  AND  WR >= 60%  AND  total$ >= +$5.0

Then for the top-10 strongest cohorts, try adding a 3rd feature to find
even tighter sub-patterns.

Goal: surface ANY combination of features that produces a clean profitable
cohort, regardless of whether it matches an existing trigger predicate.
"""
import pickle
from itertools import product


def stats(group):
    if not group:
        return 0, 0, 0, 0
    wins = sum(1 for r in group if r.get("win"))
    pnls = [r.get("pnl") or 0 for r in group]
    return len(group), wins / len(group) * 100, sum(pnls) / len(pnls), sum(pnls)


# Bucket definitions per feature
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


def in_range(val, lo, hi):
    return val is not None and lo <= val < hi


def main():
    with open("scripts/creative_trigger_research/.dataset.pkl", "rb") as f:
        rows = pickle.load(f)

    print(f"Total rows: {len(rows)}")
    baseline_n, baseline_wr, baseline_avg, baseline_tot = stats(rows)
    print(f"Baseline: n={baseline_n} WR={baseline_wr:.1f}% avg=${baseline_avg:+.2f} total=${baseline_tot:+.2f}")

    # === Phase 1: all 2D pairs ===
    print()
    print("=" * 110)
    print("PHASE 1: 2D cohort scan — filter n>=15, WR>=60%, total$>=+$5")
    print("=" * 110)
    results = []
    for i, fa in enumerate(NUMERIC_FEATS):
        for fb in NUMERIC_FEATS[i+1:]:
            for (a_lo, a_hi), (b_lo, b_hi) in product(BUCKETS[fa], BUCKETS[fb]):
                g = [
                    r for r in rows
                    if in_range(r.get(fa), a_lo, a_hi)
                    and in_range(r.get(fb), b_lo, b_hi)
                ]
                n, wr, avg, tot = stats(g)
                if n >= 15 and wr >= 60 and tot >= 5.0:
                    results.append({
                        "fa": fa, "a_lo": a_lo, "a_hi": a_hi,
                        "fb": fb, "b_lo": b_lo, "b_hi": b_hi,
                        "n": n, "wr": wr, "avg": avg, "tot": tot,
                    })
    # Sort by total $ desc (highest EV first)
    results.sort(key=lambda r: -r["tot"])
    print(f"\nFound {len(results)} qualifying 2D cohorts")
    print(f"\n{'rank':<5} {'feat A':<28} {'feat B':<28} {'n':>4} {'WR':>6} {'avg':>8} {'total':>9}")
    print("-" * 110)
    for i, r in enumerate(results[:30], 1):
        fa_lbl = f"{r['fa'].replace('em_','').replace('entry_','')[:14]}[{r['a_lo']:g},{r['a_hi']:g})"
        fb_lbl = f"{r['fb'].replace('em_','').replace('entry_','')[:14]}[{r['b_lo']:g},{r['b_hi']:g})"
        print(f"{i:<5} {fa_lbl:<28} {fb_lbl:<28} {r['n']:>4} {r['wr']:>5.1f}% ${r['avg']:>+6.2f} ${r['tot']:>+8.2f}")

    # === Phase 2: 3rd-feature refinement on top-10 2D cohorts ===
    print()
    print("=" * 110)
    print("PHASE 2: 3D refinement on top-10 cohorts — find even tighter sub-patterns")
    print("=" * 110)
    for rank, base in enumerate(results[:10], 1):
        fa, fb = base["fa"], base["fb"]
        base_group = [
            r for r in rows
            if in_range(r.get(fa), base["a_lo"], base["a_hi"])
            and in_range(r.get(fb), base["b_lo"], base["b_hi"])
        ]
        print(f"\n--- Rank {rank} base: {fa.replace('em_','')}[{base['a_lo']:g},{base['a_hi']:g}) × {fb.replace('em_','')}[{base['b_lo']:g},{base['b_hi']:g}) (n={base['n']} WR={base['wr']:.1f}% ${base['tot']:+.1f}) ---")
        # Try adding each remaining feature
        third_results = []
        for fc in NUMERIC_FEATS:
            if fc in (fa, fb):
                continue
            for (c_lo, c_hi) in BUCKETS[fc]:
                g = [r for r in base_group if in_range(r.get(fc), c_lo, c_hi)]
                n, wr, avg, tot = stats(g)
                # Want at least 10 samples and meaningful lift
                if n >= 10 and wr >= base["wr"] + 5 and tot >= base["tot"] * 0.3:
                    third_results.append({
                        "fc": fc, "c_lo": c_lo, "c_hi": c_hi,
                        "n": n, "wr": wr, "avg": avg, "tot": tot,
                    })
        third_results.sort(key=lambda r: -r["wr"])
        for r in third_results[:5]:
            fc_lbl = f"+ {r['fc'].replace('em_','').replace('entry_','')[:14]}[{r['c_lo']:g},{r['c_hi']:g})"
            print(f"  {fc_lbl:<35} n={r['n']:>3} WR={r['wr']:>5.1f}% avg=${r['avg']:+.2f} total=${r['tot']:+.2f}")

    # === Phase 3: Categorical overlays — protocol × top features ===
    print()
    print("=" * 110)
    print("PHASE 3: Protocol-conditioned cohorts")
    print("=" * 110)
    from collections import Counter
    protos = Counter(r.get("em_protocol") for r in rows)
    print(f"Protocols: {dict(protos)}")
    for proto in protos:
        if not proto or protos[proto] < 100:
            continue
        proto_rows = [r for r in rows if r.get("em_protocol") == proto]
        n, wr, avg, tot = stats(proto_rows)
        print(f"\n{proto}: n={n} WR={wr:.1f}% total=${tot:+.2f}")
        # Best 2D cells within this protocol
        proto_results = []
        for fa in NUMERIC_FEATS[:5]:  # just top 5 numeric feats to limit search
            for fb in NUMERIC_FEATS[:5]:
                if fa >= fb:
                    continue
                for (a_lo, a_hi), (b_lo, b_hi) in product(BUCKETS[fa], BUCKETS[fb]):
                    g = [
                        r for r in proto_rows
                        if in_range(r.get(fa), a_lo, a_hi)
                        and in_range(r.get(fb), b_lo, b_hi)
                    ]
                    pn, pwr, pavg, ptot = stats(g)
                    if pn >= 12 and pwr >= 65 and ptot >= 5.0:
                        proto_results.append((fa, a_lo, a_hi, fb, b_lo, b_hi, pn, pwr, ptot))
        proto_results.sort(key=lambda x: -x[8])
        for fa, a_lo, a_hi, fb, b_lo, b_hi, pn, pwr, ptot in proto_results[:5]:
            print(f"  {fa.replace('em_','')[:14]}[{a_lo:g},{a_hi:g}) × {fb.replace('em_','')[:14]}[{b_lo:g},{b_hi:g}) n={pn} WR={pwr:.1f}% ${ptot:+.1f}")


if __name__ == "__main__":
    main()
