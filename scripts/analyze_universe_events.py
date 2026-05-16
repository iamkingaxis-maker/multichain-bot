"""Analyze the events file produced by mine_broader_universe_r7.py.

Mine compound predictors of (a) won_5pct (peak >= +5% within 30min) and
(b) won_10pct (peak >= +10% within 30min) using features available at
entry time.

Goal: find candle/pair-level compound triggers that beat the universe
baseline WR.
"""
from __future__ import annotations
import json, itertools, statistics
from pathlib import Path
from collections import defaultdict

EVENTS = Path(".universe_mine/events.json")


def cohen_d(a, b):
    if not a or not b or len(a) < 2 or len(b) < 2:
        return 0.0
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    pooled = (((len(a)-1)*sa**2 + (len(b)-1)*sb**2) / (len(a)+len(b)-2)) ** 0.5
    if pooled == 0:
        return 0.0
    return (ma - mb) / pooled


def main():
    with EVENTS.open() as f:
        events = json.load(f)
    print(f"Total events: {len(events)}")

    labels = ["won", "won_5pct", "won_10pct"]
    for label in labels:
        wins = sum(1 for e in events if e[label])
        print(f"  Baseline {label}: {wins}/{len(events)} = {wins/len(events):.0%}")

    # Numeric features
    FEATS = [
        "pc_m5", "pc_h1", "pc_h6", "pc_h24",
        "bs_m5", "bs_h1", "bs_h6", "bs_h24",
        "buys_h1", "sells_h1",
        "vol_m5", "vol_h1", "vol_h6", "vol_h24",
        "liq_usd", "fdv", "mcap", "age_hours",
        "cum_pct_to_dip", "vol_at_event", "vol_prev3_avg", "vol_prev15_avg",
        "body_pct", "range_pct", "lower_wick_ratio",
    ]
    print(f"\nCohen's d ranking (winners vs losers, won_5pct label):")
    print(f"{'Feature':<30} {'d':>7} {'win_med':>10} {'loss_med':>10}")
    print("-" * 60)
    rankings = []
    for f in FEATS:
        wins = [e.get(f) for e in events if e["won_5pct"] and e.get(f) is not None]
        losses = [e.get(f) for e in events if not e["won_5pct"] and e.get(f) is not None]
        if len(wins) < 5 or len(losses) < 5:
            continue
        d = cohen_d(wins, losses)
        rankings.append({
            "f": f, "d": d,
            "win_med": statistics.median(wins),
            "loss_med": statistics.median(losses),
        })
    rankings.sort(key=lambda x: -abs(x["d"]))
    for r in rankings[:20]:
        print(f"{r['f']:<30} {r['d']:>+6.2f}  {r['win_med']:>10.3g}  {r['loss_med']:>10.3g}")

    # Threshold mining per feature (single predicate)
    print(f"\nSingle-predicate top filters / triggers (won_5pct label, n>=20):")
    print(f"{'predicate':<55} {'n':>4} {'WR':>5} {'lift':>6}")
    print("-" * 80)
    base_wr = sum(1 for e in events if e["won_5pct"]) / len(events)
    single_preds = []
    for r in rankings[:15]:
        f = r["f"]
        vals = sorted([e[f] for e in events if e.get(f) is not None])
        if not vals:
            continue
        for pct in (10, 20, 30, 50, 70, 80, 90):
            idx = int(len(vals) * pct / 100)
            if idx >= len(vals):
                continue
            thr = vals[idx]
            for op in (">=", "<="):
                cohort = [e for e in events
                          if e.get(f) is not None
                          and ((e[f] >= thr) if op == ">=" else (e[f] <= thr))]
                if len(cohort) < 20:
                    continue
                w = sum(1 for e in cohort if e["won_5pct"])
                wr = w / len(cohort)
                if wr - base_wr < 0.10:
                    continue
                single_preds.append({
                    "pred": f"{f}{op}{thr:.3g}", "n": len(cohort), "wr": wr,
                    "lift": wr - base_wr, "fn": (
                        (lambda e, ff=f, tt=thr: e.get(ff) is not None and e[ff] >= tt)
                        if op == ">=" else
                        (lambda e, ff=f, tt=thr: e.get(ff) is not None and e[ff] <= tt)
                    )
                })
    single_preds.sort(key=lambda x: -x["lift"])
    for p in single_preds[:20]:
        print(f"{p['pred']:<55} {p['n']:>4} {p['wr']:.0%} {p['lift']:>+5.1%}")

    # 2-way compounds from top single preds
    print(f"\n2-way compound predicates (won_5pct label, n>=15, WR>=80%):")
    print(f"{'predicates':<90} {'n':>4} {'WR':>5} {'lift':>6}")
    print("-" * 110)
    pairs = []
    for p1, p2 in itertools.combinations(single_preds[:15], 2):
        # Skip same-feature combos
        if p1["pred"].split(">=")[0].split("<=")[0] == p2["pred"].split(">=")[0].split("<=")[0]:
            continue
        cohort = [e for e in events if p1["fn"](e) and p2["fn"](e)]
        if len(cohort) < 15:
            continue
        w = sum(1 for e in cohort if e["won_5pct"])
        wr = w / len(cohort)
        if wr < 0.80:
            continue
        pairs.append({
            "preds": [p1["pred"], p2["pred"]], "n": len(cohort), "wr": wr,
            "lift": wr - base_wr,
        })
    pairs.sort(key=lambda x: (-x["wr"], -x["n"]))
    for p in pairs[:25]:
        preds = " & ".join(p["preds"])
        print(f"{preds:<90} {p['n']:>4} {p['wr']:.0%} {p['lift']:>+5.1%}")

    # 3-way compounds — only on the top-8 single preds (combinatorial explosion control)
    print(f"\n3-way compound predicates (won_5pct label, n>=10, WR>=90%):")
    triples = []
    for combo in itertools.combinations(single_preds[:10], 3):
        feats = [c["pred"].split(">=")[0].split("<=")[0] for c in combo]
        if len(set(feats)) < 3:
            continue
        cohort = [e for e in events if all(c["fn"](e) for c in combo)]
        if len(cohort) < 10:
            continue
        w = sum(1 for e in cohort if e["won_5pct"])
        wr = w / len(cohort)
        if wr < 0.90:
            continue
        triples.append({"preds": [c["pred"] for c in combo], "n": len(cohort), "wr": wr})
    triples.sort(key=lambda x: (-x["wr"], -x["n"]))
    for t in triples[:20]:
        preds = " & ".join(t["preds"])
        print(f"{preds:<110} {t['n']:>4} {t['wr']:.0%}")

    # Also mine won_10pct
    print(f"\n\n=== Top 2-way compound predicates for won_10pct (n>=8, WR>=70%) ===")
    base_wr_10 = sum(1 for e in events if e["won_10pct"]) / len(events)
    rankings_10 = []
    for f in FEATS:
        wins = [e.get(f) for e in events if e["won_10pct"] and e.get(f) is not None]
        losses = [e.get(f) for e in events if not e["won_10pct"] and e.get(f) is not None]
        if len(wins) < 5 or len(losses) < 5:
            continue
        d = cohen_d(wins, losses)
        rankings_10.append({"f": f, "d": d,
                            "win_med": statistics.median(wins),
                            "loss_med": statistics.median(losses)})
    rankings_10.sort(key=lambda x: -abs(x["d"]))

    sp_10 = []
    for r in rankings_10[:12]:
        f = r["f"]
        vals = sorted([e[f] for e in events if e.get(f) is not None])
        for pct in (10, 20, 30, 50, 70, 80, 90):
            idx = int(len(vals) * pct / 100)
            if idx >= len(vals):
                continue
            thr = vals[idx]
            for op in (">=", "<="):
                cohort = [e for e in events
                          if e.get(f) is not None
                          and ((e[f] >= thr) if op == ">=" else (e[f] <= thr))]
                if len(cohort) < 10:
                    continue
                w = sum(1 for e in cohort if e["won_10pct"])
                wr = w / len(cohort)
                if wr - base_wr_10 < 0.10:
                    continue
                sp_10.append({
                    "pred": f"{f}{op}{thr:.3g}", "n": len(cohort), "wr": wr,
                    "lift": wr - base_wr_10, "fn": (
                        (lambda e, ff=f, tt=thr: e.get(ff) is not None and e[ff] >= tt)
                        if op == ">=" else
                        (lambda e, ff=f, tt=thr: e.get(ff) is not None and e[ff] <= tt)
                    )
                })
    sp_10.sort(key=lambda x: -x["lift"])
    for combo in itertools.combinations(sp_10[:12], 2):
        p1, p2 = combo
        if p1["pred"].split(">=")[0].split("<=")[0] == p2["pred"].split(">=")[0].split("<=")[0]:
            continue
        cohort = [e for e in events if p1["fn"](e) and p2["fn"](e)]
        if len(cohort) < 8:
            continue
        w = sum(1 for e in cohort if e["won_10pct"])
        wr = w / len(cohort)
        if wr < 0.70:
            continue
        print(f"{p1['pred']} & {p2['pred']:<60} n={len(cohort):<3} WR={wr:.0%} lift={wr-base_wr_10:+.1%}")


if __name__ == "__main__":
    main()
