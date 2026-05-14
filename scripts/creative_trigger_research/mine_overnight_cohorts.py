"""Mine high-EV cohorts WITHIN overnight hours (7pm-7am CT).

Currently the bot trades 7am-5pm CT (TRADING_START_HOUR_CT=7, END=17). The
14h overnight band is OFF. The lifetime-hour audit showed mixed results:
  hour 18-20 CT: -$29 (worst)
  hour 21-23 CT: +$3.53 (positive!)
  hour 00-02 CT: -$25
  hour 03-05 CT: -$20
  hour 06-08 CT: +$5.72 (positive)

So overnight isn't uniformly bad — it has profitable pockets if we find
the right patterns. This script filters the dataset to overnight (CT) and
runs 2D pair mining to surface specific cohorts that work in that band.
"""
import pickle
from itertools import product


def is_overnight_ct(h):
    """Overnight CT = hour in [19, 24) OR [0, 7)."""
    if h is None:
        return False
    return 19 <= h < 24 or 0 <= h < 7


def stats(group):
    if not group:
        return 0, 0.0, 0.0, 0.0
    wins = sum(1 for r in group if r.get("win"))
    pnls = [r.get("pnl") or 0 for r in group]
    return len(group), wins / len(group) * 100, sum(pnls) / len(pnls), sum(pnls)


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
}

NUMERIC_FEATS = list(BUCKETS.keys())


def in_range(val, lo, hi):
    return val is not None and lo <= val < hi


def main():
    with open("scripts/creative_trigger_research/.dataset.pkl", "rb") as f:
        rows = pickle.load(f)

    overnight = [r for r in rows if is_overnight_ct(r.get("hour_ct"))]
    daytime = [r for r in rows if not is_overnight_ct(r.get("hour_ct"))]

    print(f"Total rows: {len(rows)}")
    n, wr, avg, tot = stats(overnight)
    print(f"Overnight (19-7 CT): n={n} WR={wr:.1f}% avg=${avg:+.2f} total=${tot:+.2f}")
    n, wr, avg, tot = stats(daytime)
    print(f"Daytime (7-19 CT):   n={n} WR={wr:.1f}% avg=${avg:+.2f} total=${tot:+.2f}")

    # === Per-hour breakdown ===
    print()
    print("=== Per-hour breakdown ===")
    print(f"{'hour CT':<10} {'n':>5} {'WR':>7} {'avg':>9} {'total':>9}")
    for h in list(range(19, 24)) + list(range(0, 7)):
        g = [r for r in rows if r.get("hour_ct") == h]
        n, wr, avg, tot = stats(g)
        print(f"  {h:02d}:00     {n:>5} {wr:>6.1f}% ${avg:>+7.2f} ${tot:>+7.2f}")

    # === 2D cohort scan WITHIN overnight ===
    print()
    print("=" * 110)
    print("PHASE 1: 2D cohort scan WITHIN overnight (19-7 CT)")
    print("Filter: n>=12, WR>=60%, total$>=+$3")
    print("=" * 110)
    results = []
    for i, fa in enumerate(NUMERIC_FEATS):
        for fb in NUMERIC_FEATS[i+1:]:
            for (a_lo, a_hi), (b_lo, b_hi) in product(BUCKETS[fa], BUCKETS[fb]):
                g = [
                    r for r in overnight
                    if in_range(r.get(fa), a_lo, a_hi)
                    and in_range(r.get(fb), b_lo, b_hi)
                ]
                n, wr, avg, tot = stats(g)
                if n >= 12 and wr >= 60 and tot >= 3.0:
                    results.append({
                        "fa": fa, "a_lo": a_lo, "a_hi": a_hi,
                        "fb": fb, "b_lo": b_lo, "b_hi": b_hi,
                        "n": n, "wr": wr, "avg": avg, "tot": tot,
                    })
    results.sort(key=lambda r: -r["tot"])
    print(f"\nFound {len(results)} qualifying overnight cohorts\n")
    print(f"{'rank':<5} {'feat A':<28} {'feat B':<28} {'n':>4} {'WR':>6} {'avg':>8} {'total':>9}")
    print("-" * 110)
    for i, r in enumerate(results[:25], 1):
        fa_lbl = f"{r['fa'].replace('em_','').replace('entry_','')[:14]}[{r['a_lo']:g},{r['a_hi']:g})"
        fb_lbl = f"{r['fb'].replace('em_','').replace('entry_','')[:14]}[{r['b_lo']:g},{r['b_hi']:g})"
        print(f"{i:<5} {fa_lbl:<28} {fb_lbl:<28} {r['n']:>4} {r['wr']:>5.1f}% ${r['avg']:>+6.2f} ${r['tot']:>+8.2f}")

    # === Compare day vs night for top 10 cohorts ===
    print()
    print("=" * 110)
    print("PHASE 2: Day-vs-night WR comparison for top overnight cohorts")
    print("If overnight WR is meaningfully > daytime WR, that's an overnight-specific edge")
    print("=" * 110)
    for rank, r in enumerate(results[:10], 1):
        # Same predicate, daytime only
        day_g = [
            row for row in daytime
            if in_range(row.get(r["fa"]), r["a_lo"], r["a_hi"])
            and in_range(row.get(r["fb"]), r["b_lo"], r["b_hi"])
        ]
        dn, dwr, davg, dtot = stats(day_g)
        delta = r["wr"] - dwr
        marker = "OVERNIGHT-EDGE" if delta >= 10 else ("DAYTIME-EDGE" if delta <= -10 else "")
        print(
            f"  Rank {rank}: {r['fa'].replace('em_','')[:12]}[{r['a_lo']:g},{r['a_hi']:g}) × "
            f"{r['fb'].replace('em_','')[:12]}[{r['b_lo']:g},{r['b_hi']:g})  "
            f"night n={r['n']} WR={r['wr']:.1f}% ${r['tot']:+.1f}  |  "
            f"day n={dn} WR={dwr:.1f}% ${dtot:+.1f}  |  Δ={delta:+.1f}pp {marker}"
        )

    # === 3D refinement on top 5 overnight cohorts ===
    print()
    print("=" * 110)
    print("PHASE 3: 3D refinement on top 5 overnight cohorts")
    print("=" * 110)
    for rank, base in enumerate(results[:5], 1):
        fa, fb = base["fa"], base["fb"]
        base_group = [
            r for r in overnight
            if in_range(r.get(fa), base["a_lo"], base["a_hi"])
            and in_range(r.get(fb), base["b_lo"], base["b_hi"])
        ]
        print(f"\n--- Rank {rank} base: {fa.replace('em_','')}[{base['a_lo']:g},{base['a_hi']:g}) × {fb.replace('em_','')}[{base['b_lo']:g},{base['b_hi']:g}) (overnight n={base['n']} WR={base['wr']:.1f}%) ---")
        third_results = []
        for fc in NUMERIC_FEATS:
            if fc in (fa, fb):
                continue
            for (c_lo, c_hi) in BUCKETS[fc]:
                g = [r for r in base_group if in_range(r.get(fc), c_lo, c_hi)]
                n, wr, avg, tot = stats(g)
                if n >= 8 and wr >= base["wr"] + 5:
                    third_results.append({
                        "fc": fc, "c_lo": c_lo, "c_hi": c_hi,
                        "n": n, "wr": wr, "tot": tot,
                    })
        third_results.sort(key=lambda r: -r["wr"])
        for r in third_results[:4]:
            fc_lbl = f"+ {r['fc'].replace('em_','').replace('entry_','')[:14]}[{r['c_lo']:g},{r['c_hi']:g})"
            print(f"  {fc_lbl:<35} n={r['n']:>3} WR={r['wr']:>5.1f}% total=${r['tot']:+.2f}")


if __name__ == "__main__":
    main()
