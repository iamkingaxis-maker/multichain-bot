"""Round 3 wash-resistant compound mining.

Goal: find ADDITIONAL high-WR compound entry triggers beyond the 9 already shipped,
all of which require mean_buy_size_usd >= $15 (wash-trade resistant size floor).

Sources of search depth:
 1) Feature dimensions deliberately uncombined in round 2: chart_reaccum_vol_return_ratio,
    chart_vp_poc_distance_pct, chart_orderflow_score, micro_pattern_score, sweep predicates,
    1s_red_pct_60s, 1s_close_pos_60s, p90_buy_size_usd.
 2) 4-way and 5-way compounds (size_floor + 3 or 4 signals).
 3) Operating points: WR>=80% n>=6, WR>=85% n>=7, WR=100% n>=5.
"""
from __future__ import annotations
import requests, json, itertools, math
from collections import defaultdict

API = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"


def fetch():
    r = requests.get(API, timeout=20)
    return r.json()


def get(meta: dict, *keys):
    """Try several aliases."""
    for k in keys:
        v = meta.get(k)
        if v is not None:
            return v
    return None


def binom_p(k, n, p0=0.40):
    """Two-tail-ish: probability of >=k wins under H0."""
    from math import comb
    p = 0.0
    for x in range(k, n + 1):
        p += comb(n, x) * (p0 ** x) * ((1 - p0) ** (n - x))
    return p


def main():
    trades = fetch()
    paired = [t for t in trades if t.get("pnl_pct") is not None]
    print(f"Total closed paired trades: {len(paired)}")

    # Extract features per trade
    rows = []
    for t in paired:
        m = (t.get("entry_meta") or {})
        row = {
            "token": t.get("token"),
            "pnl_pct": float(t.get("pnl_pct") or 0),
            "win": float(t.get("pnl_pct") or 0) > 0,
            # Wash-resistant size signals
            "mean_buy_size_usd": get(m, "mean_buy_size_usd"),
            "p90_buy_size_usd": get(m, "p90_buy_size_usd"),
            # Order flow ($ based — wash-resistant)
            "net_flow_60s_usd": get(m, "net_flow_60s_usd"),
            "net_flow_5m_usd": get(m, "net_flow_5m_usd"),
            # Multi-timeframe
            "chart_mtf_score": get(m, "chart_mtf_score"),
            # Buyer/seller count ratios (wash-VULNERABLE but useful when size_floor passes)
            "bs_m5": get(m, "bs_m5"),
            "bs_h1": get(m, "bs_h1"),
            "bs_h6": get(m, "bs_h6"),
            # Chart composites
            "chart_score": get(m, "chart_score"),
            "1s_bottom_score": get(m, "1s_bottom_score"),
            "micro_pattern_score": get(m, "micro_pattern_score"),
            "chart_reaccum_vol_return_ratio": get(m, "chart_reaccum_vol_return_ratio"),
            "chart_vp_poc_distance_pct": get(m, "chart_vp_poc_distance_pct"),
            "chart_orderflow_score": get(m, "chart_orderflow_score"),
            # 1s tape
            "1s_close_pos_60s": get(m, "1s_close_pos_60s"),
            "1s_red_pct_60s": get(m, "1s_red_pct_60s"),
            # Macro
            "pc_h6": get(m, "pc_h6", "price_change_h6"),
            "pc_h24": get(m, "pc_h24", "price_change_h24"),
            "pc_h1": get(m, "pc_h1", "price_change_h1"),
            # Sweep / capit
            "post_capit_window_5s": get(m, "post_capit_window_5s"),
            "buy_burst_30s": get(m, "buy_burst_30s"),
            "rt_buys_usd": get(m, "rt_buys_usd"),
            "pct_in_5m_range": get(m, "pct_in_5m_range"),
        }
        rows.append(row)

    print(f"Coverage by feature (non-null fraction):")
    feats = [k for k in rows[0].keys() if k not in ("token", "pnl_pct", "win")]
    for f in feats:
        n = sum(1 for r in rows if r[f] is not None)
        print(f"  {f}: {n}/{len(rows)} = {n/len(rows):.0%}")

    # ---- Define predicates ----
    # Each predicate: (name, lambda r -> bool, requires_keys)
    P = [
        # Wash-resistant size floors
        ("mean_buy>=10", lambda r: (r["mean_buy_size_usd"] or 0) >= 10, ["mean_buy_size_usd"]),
        ("mean_buy>=15", lambda r: (r["mean_buy_size_usd"] or 0) >= 15, ["mean_buy_size_usd"]),
        ("mean_buy>=25", lambda r: (r["mean_buy_size_usd"] or 0) >= 25, ["mean_buy_size_usd"]),
        ("p90_buy>=100", lambda r: (r["p90_buy_size_usd"] or 0) >= 100, ["p90_buy_size_usd"]),
        ("p90_buy>=250", lambda r: (r["p90_buy_size_usd"] or 0) >= 250, ["p90_buy_size_usd"]),
        # Order flow ($)
        ("flow60>0", lambda r: (r["net_flow_60s_usd"] or 0) > 0, ["net_flow_60s_usd"]),
        ("flow60>50", lambda r: (r["net_flow_60s_usd"] or 0) > 50, ["net_flow_60s_usd"]),
        ("flow60>100", lambda r: (r["net_flow_60s_usd"] or 0) > 100, ["net_flow_60s_usd"]),
        ("flow5m>0", lambda r: (r["net_flow_5m_usd"] or 0) > 0, ["net_flow_5m_usd"]),
        ("flow5m>200", lambda r: (r["net_flow_5m_usd"] or 0) > 200, ["net_flow_5m_usd"]),
        # MTF
        ("mtf>=0", lambda r: (r["chart_mtf_score"] is not None) and r["chart_mtf_score"] >= 0, ["chart_mtf_score"]),
        ("mtf>=1", lambda r: (r["chart_mtf_score"] is not None) and r["chart_mtf_score"] >= 1, ["chart_mtf_score"]),
        # Reaccum vol return ratio (NEW dim)
        ("reaccum_vol>1", lambda r: (r["chart_reaccum_vol_return_ratio"] or 0) > 1, ["chart_reaccum_vol_return_ratio"]),
        ("reaccum_vol>1.5", lambda r: (r["chart_reaccum_vol_return_ratio"] or 0) > 1.5, ["chart_reaccum_vol_return_ratio"]),
        ("reaccum_vol>2", lambda r: (r["chart_reaccum_vol_return_ratio"] or 0) > 2, ["chart_reaccum_vol_return_ratio"]),
        # VP POC distance (closer to POC = better, NEW dim)
        ("vp_poc<10", lambda r: (r["chart_vp_poc_distance_pct"] is not None) and abs(r["chart_vp_poc_distance_pct"]) < 10, ["chart_vp_poc_distance_pct"]),
        ("vp_poc<20", lambda r: (r["chart_vp_poc_distance_pct"] is not None) and abs(r["chart_vp_poc_distance_pct"]) < 20, ["chart_vp_poc_distance_pct"]),
        # Chart orderflow score (NEW dim)
        ("orderflow_score>0", lambda r: (r["chart_orderflow_score"] or 0) > 0, ["chart_orderflow_score"]),
        ("orderflow_score>50", lambda r: (r["chart_orderflow_score"] or 0) > 50, ["chart_orderflow_score"]),
        # Micro pattern (NEW dim)
        ("micro_pattern>0", lambda r: (r["micro_pattern_score"] or 0) > 0, ["micro_pattern_score"]),
        ("micro_pattern>=2", lambda r: (r["micro_pattern_score"] or 0) >= 2, ["micro_pattern_score"]),
        # Chart score
        ("chart>=50", lambda r: (r["chart_score"] or 0) >= 50, ["chart_score"]),
        ("chart>=60", lambda r: (r["chart_score"] or 0) >= 60, ["chart_score"]),
        # 1s bottom
        ("1s_bot>=20", lambda r: (r["1s_bottom_score"] or 0) >= 20, ["1s_bottom_score"]),
        ("1s_bot>=40", lambda r: (r["1s_bottom_score"] or 0) >= 40, ["1s_bottom_score"]),
        # 1s tape
        ("1s_red<0.5", lambda r: (r["1s_red_pct_60s"] is not None) and r["1s_red_pct_60s"] < 0.5, ["1s_red_pct_60s"]),
        ("1s_red<0.4", lambda r: (r["1s_red_pct_60s"] is not None) and r["1s_red_pct_60s"] < 0.4, ["1s_red_pct_60s"]),
        ("1s_close_pos>0.5", lambda r: (r["1s_close_pos_60s"] or 0) > 0.5, ["1s_close_pos_60s"]),
        ("1s_close_pos>0.6", lambda r: (r["1s_close_pos_60s"] or 0) > 0.6, ["1s_close_pos_60s"]),
        # Macro pullback signature
        ("pc_h6<0", lambda r: (r["pc_h6"] is not None) and r["pc_h6"] < 0, ["pc_h6"]),
        ("pc_h6_in_-30_-5", lambda r: (r["pc_h6"] is not None) and -30 < r["pc_h6"] < -5, ["pc_h6"]),
        # Buyer/seller (with size floor it's wash-resistant)
        ("bs_m5>=1.5", lambda r: (r["bs_m5"] or 0) >= 1.5, ["bs_m5"]),
        ("bs_h1>=1.5", lambda r: (r["bs_h1"] or 0) >= 1.5, ["bs_h1"]),
        ("bs_h6>=1.2", lambda r: (r["bs_h6"] or 0) >= 1.2, ["bs_h6"]),
        # Burst / activity
        ("burst30>=3", lambda r: (r["buy_burst_30s"] or 0) >= 3, ["buy_burst_30s"]),
        ("rt_buys>500", lambda r: (r["rt_buys_usd"] or 0) > 500, ["rt_buys_usd"]),
        ("pct_in_range>0.5", lambda r: (r["pct_in_5m_range"] or 0) > 0.5, ["pct_in_5m_range"]),
    ]

    print(f"\nDefined {len(P)} predicates")

    def filter_rows(predicates, rows):
        out = []
        for r in rows:
            # require all keys non-null
            keys = set()
            for p in predicates:
                keys.update(p[2])
            if any(r[k] is None for k in keys):
                continue
            if all(p[1](r) for p in predicates):
                out.append(r)
        return out

    def report(predicates, rows, label=""):
        cohort = filter_rows(predicates, rows)
        n = len(cohort)
        if n == 0:
            return None
        wins = sum(1 for r in cohort if r["win"])
        wr = wins / n
        avg = sum(r["pnl_pct"] for r in cohort) / n
        total_pnl_dollars = avg * 20 * n / 100  # $20 per trade
        return {
            "label": label,
            "predicates": [p[0] for p in predicates],
            "n": n,
            "wins": wins,
            "wr": wr,
            "avg_pnl_pct": avg,
            "net_dollar": avg * 20 / 100,  # $/trade
        }

    # Always require wash-resistant size floor as anchor
    SIZE_FLOORS = [P[0], P[1], P[2]]  # mean_buy>=10, 15, 25
    SIGNALS = [p for p in P if p[0] not in ("mean_buy>=10", "mean_buy>=15", "mean_buy>=25")]

    print(f"\n=== 3-WAY (size_floor + 2 signals), n>=8, WR>=75% ===")
    results_3way = []
    for sf in SIZE_FLOORS:
        for combo in itertools.combinations(SIGNALS, 2):
            res = report([sf, *combo], rows)
            if res and res["n"] >= 8 and res["wr"] >= 0.75:
                results_3way.append(res)
    results_3way.sort(key=lambda x: (-x["wr"], -x["n"]))
    for r in results_3way[:25]:
        preds = " & ".join(r["predicates"])
        print(f"  {preds}: n={r['n']} WR={r['wr']:.0%} avg=+{r['avg_pnl_pct']:.2f}% (${r['net_dollar']:+.2f}/trade)")

    print(f"\n=== 4-WAY (size_floor + 3 signals), n>=6, WR>=85% ===")
    results_4way = []
    for sf in SIZE_FLOORS:
        for combo in itertools.combinations(SIGNALS, 3):
            res = report([sf, *combo], rows)
            if res and res["n"] >= 6 and res["wr"] >= 0.85:
                results_4way.append(res)
    results_4way.sort(key=lambda x: (-x["wr"], -x["n"]))
    for r in results_4way[:30]:
        preds = " & ".join(r["predicates"])
        print(f"  {preds}: n={r['n']} WR={r['wr']:.0%} avg=+{r['avg_pnl_pct']:.2f}% (${r['net_dollar']:+.2f}/trade)")

    print(f"\n=== 5-WAY (size_floor + 4 signals), n>=5, WR=100% ===")
    results_5way = []
    for sf in [P[1]]:  # Only mean_buy>=15 (sweet spot)
        sig_subset = [p for p in SIGNALS if p[0] in (
            "flow60>0", "flow60>50", "flow5m>0", "flow5m>200",
            "mtf>=0", "mtf>=1", "reaccum_vol>1", "reaccum_vol>1.5",
            "vp_poc<20", "orderflow_score>0", "micro_pattern>0",
            "chart>=50", "1s_bot>=20", "1s_red<0.5", "1s_close_pos>0.5",
            "pc_h6<0", "bs_m5>=1.5", "bs_h1>=1.5", "bs_h6>=1.2",
        )]
        for combo in itertools.combinations(sig_subset, 4):
            res = report([sf, *combo], rows)
            if res and res["n"] >= 5 and res["wr"] >= 1.0:
                results_5way.append(res)
    results_5way.sort(key=lambda x: (-x["n"], -x["avg_pnl_pct"]))
    for r in results_5way[:30]:
        preds = " & ".join(r["predicates"])
        print(f"  {preds}: n={r['n']} +{r['avg_pnl_pct']:.2f}% (${r['net_dollar']:+.2f}/trade)")

    # Now show patterns where NEW dimensions (reaccum_vol, vp_poc, orderflow_score, micro_pattern)
    # appear in winning compounds — these are upgrade candidates.
    print(f"\n=== STRUCTURAL DIMENSIONS RANKING (which signals appear most in 100% WR n>=5 4-way+) ===")
    counts = defaultdict(int)
    for r in results_4way + results_5way:
        if r["wr"] >= 0.95:
            for p in r["predicates"]:
                counts[p] += 1
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k}: appears in {v} winning compounds")


if __name__ == "__main__":
    main()
