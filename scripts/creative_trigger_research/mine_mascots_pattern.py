"""Mine the MASCOTS winning pattern:
   peak_h24_6h_pct in [50, 150] AND h24_ratio_to_peak < 0.10

MASCOTS won via: modest pump (103%) + extreme retracement (ratio=0.057) +
3-trigger confluence. Existing audited cohorts were:
   peak>=200% AND ratio<0.40 → 75% WR / +$3.95 (n=12)
   peak>=1000% AND ratio<0.60 → 66.7% WR / +$7.55 (n=18)

MASCOTS' bucket (modest pump + extreme retrace) is unaudited. This script
buckets the dataset to find if it's a +EV pattern.
"""
import pickle


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

    print(f"Total dataset rows: {len(rows)}")
    stats("baseline (all rows)", rows)

    # 2D bucket: peak_h24 × h24_ratio_to_peak
    print()
    print("=" * 100)
    print("FULL 2D GRID: peak_h24 × h24_ratio_to_peak")
    print("=" * 100)
    peak_bands = [
        ("peak<25",     (0, 25)),
        ("peak 25-50",  (25, 50)),
        ("peak 50-150", (50, 150)),
        ("peak 150-300",(150, 300)),
        ("peak 300-500",(300, 500)),
        ("peak 500-1k", (500, 1000)),
        ("peak>=1k",    (1000, 99999)),
    ]
    ratio_bands = [
        ("ratio<0.05",     (0, 0.05)),
        ("ratio 0.05-0.10",(0.05, 0.10)),
        ("ratio 0.10-0.20",(0.10, 0.20)),
        ("ratio 0.20-0.40",(0.20, 0.40)),
        ("ratio 0.40-0.60",(0.40, 0.60)),
        ("ratio 0.60-0.80",(0.60, 0.80)),
        ("ratio 0.80-0.95",(0.80, 0.95)),
        ("ratio>=0.95",    (0.95, 1.01)),
    ]
    print()
    print(f"{'peak band':16s} | " + " | ".join(f"{rb[0]:14s}" for rb in ratio_bands))
    print("-" * 145)
    for pb_label, (p_lo, p_hi) in peak_bands:
        cells = []
        for rb_label, (r_lo, r_hi) in ratio_bands:
            g = [
                r for r in rows
                if r.get("em_peak_h24_6h_pct") is not None
                and r.get("em_h24_ratio_to_peak") is not None
                and p_lo <= r["em_peak_h24_6h_pct"] < p_hi
                and r_lo <= r["em_h24_ratio_to_peak"] < r_hi
            ]
            if g:
                wr = sum(1 for r in g if r.get("win")) / len(g) * 100
                tot = sum(r.get("pnl") or 0 for r in g)
                cells.append(f"n={len(g):>3} {wr:5.1f}% ${tot:+5.1f}".ljust(14))
            else:
                cells.append("--".ljust(14))
        print(f"{pb_label:16s} | " + " | ".join(cells))

    # MASCOTS cohort
    print()
    print("=" * 80)
    print("MASCOTS-like cohort: peak_h24 in [50, 150] AND ratio < 0.10")
    print("=" * 80)
    mascots_cohort = [
        r for r in rows
        if r.get("em_peak_h24_6h_pct") is not None
        and r.get("em_h24_ratio_to_peak") is not None
        and 50 <= r["em_peak_h24_6h_pct"] < 150
        and r["em_h24_ratio_to_peak"] < 0.10
    ]
    stats("peak[50,150) AND ratio<0.10", mascots_cohort)

    # Variants for sensitivity
    print()
    print("=== Variant 1: tighter ratio (<0.05) ===")
    var = [r for r in mascots_cohort if r.get("em_h24_ratio_to_peak") < 0.05]
    stats("...AND ratio<0.05", var)

    print()
    print("=== Variant 2: wider peak (25-200%) ===")
    g = [
        r for r in rows
        if r.get("em_peak_h24_6h_pct") is not None
        and r.get("em_h24_ratio_to_peak") is not None
        and 25 <= r["em_peak_h24_6h_pct"] < 200
        and r["em_h24_ratio_to_peak"] < 0.10
    ]
    stats("peak[25,200) AND ratio<0.10", g)

    print()
    print("=== Variant 3: any peak size, ratio<0.10 ===")
    g = [
        r for r in rows
        if r.get("em_h24_ratio_to_peak") is not None
        and r["em_h24_ratio_to_peak"] < 0.10
    ]
    stats("any peak AND ratio<0.10", g)

    # Layer on bs_h6 (MASCOTS had bs_h6=3.48)
    print()
    print("=== Layered: + bs_h6 >= 2.0 (smart-money accumulation) ===")
    for label, gset in [
        ("peak[50,150) AND ratio<0.10 AND bs_h6>=2", [
            r for r in mascots_cohort
            if r.get("em_bs_h6") is not None and r["em_bs_h6"] >= 2.0
        ]),
        ("any peak AND ratio<0.10 AND bs_h6>=2", [
            r for r in rows
            if r.get("em_h24_ratio_to_peak") is not None
            and r["em_h24_ratio_to_peak"] < 0.10
            and r.get("em_bs_h6") is not None and r["em_bs_h6"] >= 2.0
        ]),
        ("any peak AND ratio<0.10 AND bs_h6>=3", [
            r for r in rows
            if r.get("em_h24_ratio_to_peak") is not None
            and r["em_h24_ratio_to_peak"] < 0.10
            and r.get("em_bs_h6") is not None and r["em_bs_h6"] >= 3.0
        ]),
    ]:
        stats(label, gset)

    # Win/loss detail for the proposed pattern
    print()
    print("=== Win/loss detail: peak[50,150) AND ratio<0.10 ===")
    wins = [r for r in mascots_cohort if r.get("win")]
    losses = [r for r in mascots_cohort if not r.get("win")]
    print(f"WINNERS (n={len(wins)}):")
    for r in wins[:10]:
        print(f"  {r.get('token','?')[:14]:14s} peak={r.get('em_peak_h24_6h_pct',0):.0f}% ratio={r.get('em_h24_ratio_to_peak',0):.3f} bs_h6={r.get('em_bs_h6',0):.2f} pnl=${r.get('pnl',0):+.2f}")
    print(f"LOSERS (n={len(losses)}, showing top 5):")
    for r in losses[:5]:
        print(f"  {r.get('token','?')[:14]:14s} peak={r.get('em_peak_h24_6h_pct',0):.0f}% ratio={r.get('em_h24_ratio_to_peak',0):.3f} bs_h6={r.get('em_bs_h6',0):.2f} pnl=${r.get('pnl',0):+.2f}")


if __name__ == "__main__":
    main()
