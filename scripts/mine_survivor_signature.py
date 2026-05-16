"""Mine post-pump SURVIVOR signature on universe-recorder data.

Question: What entry features distinguish tokens that hit a real pump
AND held up vs tokens that pumped briefly and died?

Cohorts (on universe_fresh.json, n=2049):
  SURVIVOR: peak_pct >= +10  AND  exit_pct >= 0
            (hit a real move + didn't fully give it back)
  DYER:     exit_pct <= -20
            (lost significant value within forward window)

The "mid" cohort (everything else) is excluded — too noisy to mine
useful separators from.

Method:
  1. Cohen's d on every numeric entry-snapshot feature.
  2. Threshold sweep on top discriminators → find precision-maximizing
     cuts where SURVIVOR rate >= 60% within the cohort.
  3. Pairwise compound: combine top-2 features for higher precision.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

UNIVERSE_PATH = Path("universe_fresh.json")


def cohen_d(a, b):
    if len(a) < 5 or len(b) < 5:
        return None
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    p = math.sqrt((va + vb) / 2)
    if p == 0:
        return None
    return (ma - mb) / p


def main():
    events = json.loads(UNIVERSE_PATH.read_text())
    print(f"Loaded {len(events)} universe events")

    # Classify
    survivors = [e for e in events
                 if isinstance(e.get("peak_pct"), (int, float))
                 and e["peak_pct"] >= 10.0
                 and isinstance(e.get("exit_pct"), (int, float))
                 and e["exit_pct"] >= 0]
    dyers = [e for e in events
             if isinstance(e.get("exit_pct"), (int, float))
             and e["exit_pct"] <= -20.0]
    mid = len(events) - len(survivors) - len(dyers)
    print(f"  SURVIVORS (peak>=+10 AND exit>=0):  {len(survivors)} ({len(survivors)/len(events)*100:.0f}%)")
    print(f"  DYERS     (exit<=-20):              {len(dyers)} ({len(dyers)/len(events)*100:.0f}%)")
    print(f"  MID (excluded):                     {mid} ({mid/len(events)*100:.0f}%)")

    # Collect numeric features (exclude outcome labels + IDs)
    EXCLUDE = {"peak_pct", "exit_pct", "won", "won_10pct", "won_5pct",
               "event_ts", "outcome_at_ts", "n_post_candles",
               "high_at_event", "low_at_event", "open_at_event",
               "close_at_event", "entry_price", "vol_at_event"}
    all_feats = set()
    for e in survivors + dyers:
        for k, v in e.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                all_feats.add(k)
    feats = sorted(all_feats - EXCLUDE)
    print(f"  Features evaluated: {len(feats)}")

    # Cohen's d
    results = []
    for k in feats:
        a = [e[k] for e in survivors if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool)]
        b = [e[k] for e in dyers if isinstance(e.get(k), (int, float)) and not isinstance(e.get(k), bool)]
        d = cohen_d(a, b)
        if d is None or abs(d) < 0.3:
            continue
        results.append({
            "feat": k, "d": d,
            "surv_mean": sum(a)/len(a), "surv_n": len(a),
            "dyer_mean": sum(b)/len(b), "dyer_n": len(b),
            "surv_median": sorted(a)[len(a)//2],
            "dyer_median": sorted(b)[len(b)//2],
        })
    results.sort(key=lambda r: -abs(r["d"]))

    print(f"\n=== Top features distinguishing SURVIVORS (n={len(survivors)}) from DYERS (n={len(dyers)}) ===")
    print(f"  d > 0 → higher value favors SURVIVOR")
    print(f"  {'Feature':<28} {'d':>6} {'surv_med':>10} {'dyer_med':>10}  {'surv_mean':>10} {'dyer_mean':>10}")
    for r in results[:25]:
        print(f"  {r['feat']:<28} {r['d']:>+5.2f}  "
              f"{r['surv_median']:>+9.2f}  {r['dyer_median']:>+9.2f}  "
              f"{r['surv_mean']:>+9.2f}  {r['dyer_mean']:>+9.2f}")

    # ── Threshold sweep on top 5 features ────────────────────────────
    print(f"\n=== Threshold sweep — precision when cut applied ===")
    print(f"  Goal: high SURVIVOR rate within cohort, n>=20")
    top_features = results[:8]
    for r in top_features:
        feat = r["feat"]
        direction = "gte" if r["d"] > 0 else "lte"
        # Sweep across percentiles of the combined SURVIVOR+DYER distribution
        vals = sorted([e[feat] for e in survivors + dyers
                       if isinstance(e.get(feat), (int, float))])
        if len(vals) < 50:
            continue
        cuts = [vals[int(len(vals) * p)] for p in (0.25, 0.5, 0.7, 0.85, 0.95)]
        print(f"\n  {feat}  ({'higher=better' if r['d']>0 else 'lower=better'})")
        for cut in cuts:
            if direction == "gte":
                s = [e for e in survivors if isinstance(e.get(feat),(int,float)) and e[feat] >= cut]
                d = [e for e in dyers if isinstance(e.get(feat),(int,float)) and e[feat] >= cut]
            else:
                s = [e for e in survivors if isinstance(e.get(feat),(int,float)) and e[feat] <= cut]
                d = [e for e in dyers if isinstance(e.get(feat),(int,float)) and e[feat] <= cut]
            total = len(s) + len(d)
            if total < 20:
                continue
            prec = len(s) / total
            sym = "≥" if direction == "gte" else "≤"
            print(f"    {feat}{sym}{cut:.3g}: n={total:>4}  surv={len(s):>4} ({prec*100:>4.0f}%) "
                  f"dyer={len(d):>4} ({(1-prec)*100:>3.0f}%)")

    # ── 2-feature compound search ────────────────────────────────────
    print(f"\n=== 2-feature compound search (top discriminators) ===")
    print(f"  Looking for compounds with n>=20 AND surv_rate>=65%")
    top_keys = [(r["feat"], "gte" if r["d"]>0 else "lte") for r in results[:8]]
    # For each pair, find best cut combination
    base_rate = len(survivors) / (len(survivors) + len(dyers))
    print(f"  Base survivor rate (no filter): {base_rate*100:.1f}%")
    print(f"  {'Compound':<55} {'n':>5} {'prec':>6} {'lift':>6}")
    seen_combos = set()
    compounds = []
    for (f1, d1), (f2, d2) in combinations(top_keys, 2):
        for p1 in (0.4, 0.5, 0.6, 0.7):
            for p2 in (0.4, 0.5, 0.6, 0.7):
                vals1 = sorted([e[f1] for e in survivors + dyers
                                if isinstance(e.get(f1),(int,float))])
                vals2 = sorted([e[f2] for e in survivors + dyers
                                if isinstance(e.get(f2),(int,float))])
                if not vals1 or not vals2:
                    continue
                cut1 = vals1[int(len(vals1) * p1)]
                cut2 = vals2[int(len(vals2) * p2)]
                def match(e):
                    v1 = e.get(f1); v2 = e.get(f2)
                    if not isinstance(v1,(int,float)) or not isinstance(v2,(int,float)):
                        return False
                    ok1 = v1 >= cut1 if d1 == "gte" else v1 <= cut1
                    ok2 = v2 >= cut2 if d2 == "gte" else v2 <= cut2
                    return ok1 and ok2
                s = [e for e in survivors if match(e)]
                d = [e for e in dyers if match(e)]
                total = len(s) + len(d)
                if total < 20 or len(s) < 5:
                    continue
                prec = len(s) / total
                if prec < 0.60:
                    continue
                key = (f1, d1, round(cut1, 3), f2, d2, round(cut2, 3))
                if key in seen_combos:
                    continue
                seen_combos.add(key)
                compounds.append({
                    "f1": f1, "d1": d1, "cut1": cut1,
                    "f2": f2, "d2": d2, "cut2": cut2,
                    "n": total, "surv": len(s), "prec": prec,
                    "lift": prec / base_rate,
                })
    compounds.sort(key=lambda c: (-c["prec"], -c["n"]))
    for c in compounds[:15]:
        s1 = "≥" if c["d1"] == "gte" else "≤"
        s2 = "≥" if c["d2"] == "gte" else "≤"
        label = f"{c['f1']}{s1}{c['cut1']:.3g} AND {c['f2']}{s2}{c['cut2']:.3g}"
        print(f"  {label[:54]:<55} {c['n']:>5} {c['prec']*100:>5.0f}% {c['lift']:>+5.2f}x")

    # ── Apply best compounds to our live trade cohort (sanity check) ─
    # Note: this depends on which entry_meta features overlap with universe.
    # That's the next step — left as TODO for the controller.


if __name__ == "__main__":
    main()
