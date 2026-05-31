#!/usr/bin/env python
"""Re-mine the WIDENED (post-coverage-fix) universe for an entry-SELECTION signal.

Pending task from reference_universe_coverage_gap_2026_05_27: coverage fix
(commit 62a9781, ~05-27) widened the scanned universe from ~68 tokens/day to
hundreds; the prior "no clean new strategy" verdict was provisional on the
NARROW universe -> "re-mine after coverage expands". This is that re-mine.

Data: universe recorder (.uni_remine.json) — every scanned token's entry-time
DexScreener features + a 30.7-min forward peak_pct. 956 unique tokens lifetime,
493 in the recent (>=05-25) current-filter regime.

Method (reference_onchain_compound_breakthrough): rank features by Cohen's d
(runner vs dud), exhaustive 1-3 way threshold-predicate combos, score by
winner-precision (WR) + lift over base + throughput. Held-out by TIME within the
recent window. TOKEN-DEDUPED (FCM gate): one event per token (earliest), so 493
tokens drive the stats, not 26916 events.

DISCIPLINE: 30-min forward PEAK is a known mirage (feedback_validate_on_realized
— "reversed every lead"). This mine is HYPOTHESIS-GENERATING ONLY. Survivors must
be cross-checked on realized trade outcomes and shipped only as measure-only
SHADOWS (phantom parity) before any enforcement. Reports the throughput/WR
Pareto frontier (feedback_combo_search_pareto), not just the WR-max combo.
"""
from __future__ import annotations
import json, itertools, datetime
import numpy as np

RECENT_CUT = datetime.datetime(2026, 5, 25, tzinfo=datetime.timezone.utc).timestamp()
RUNNER = 20.0   # peak_pct >= -> "runner" (tradeable, above the +30% upside cap headroom)
DUD = 2.0       # peak_pct <  -> never-green dud
HELDOUT_FRAC = 0.35  # newest 35% of the recent window = held-out

FEATURES = ["pc_m5", "pc_h1", "pc_h6", "pc_h24", "bs_m5", "bs_h1", "bs_h6",
            "bs_h24", "buys_h1", "sells_h1", "vol_m5", "vol_h1", "vol_h6",
            "vol_h24", "liq_usd", "mcap", "age_hours", "body_pct", "range_pct",
            "vol_at_event", "vol_prev3_avg", "vol_prev15_avg"]


def derive(r):
    out = dict(r)
    v3 = r.get("vol_prev3_avg") or 0
    out["vol_accel"] = (r.get("vol_m5") or 0) / v3 if v3 > 0 else 0.0
    b, s = r.get("buys_h1") or 0, r.get("sells_h1") or 0
    out["buy_ratio"] = b / (b + s) if (b + s) > 0 else 0.5
    return out


def load():
    recs = [derive(r) for r in json.load(open(".uni_remine.json"))
            if r.get("peak_pct") is not None and r.get("event_ts")]
    recs = [r for r in recs if r["event_ts"] >= RECENT_CUT]
    # token-dedup: earliest event per token
    bytok = {}
    for r in recs:
        t = r.get("symbol")
        if t not in bytok or r["event_ts"] < bytok[t]["event_ts"]:
            bytok[t] = r
    ded = sorted(bytok.values(), key=lambda r: r["event_ts"])
    return ded


def cohens_d(rows, feat, lab_a, lab_b):
    a = np.array([r[feat] for r in rows if r["_lab"] == lab_a and r.get(feat) is not None], float)
    b = np.array([r[feat] for r in rows if r["_lab"] == lab_b and r.get(feat) is not None], float)
    if len(a) < 5 or len(b) < 5:
        return 0.0, 0.0, 0.0
    sp = np.sqrt(((len(a)-1)*a.std()**2 + (len(b)-1)*b.std()**2) / max(len(a)+len(b)-2, 1))
    if sp == 0:
        return 0.0, a.mean(), b.mean()
    return (a.mean()-b.mean())/sp, a.mean(), b.mean()


FEATS_ALL = FEATURES + ["vol_accel", "buy_ratio"]


def label(rows):
    for r in rows:
        pk = r["peak_pct"]
        r["_lab"] = "runner" if pk >= RUNNER else "dud" if pk < DUD else "mid"


def predicate_grid(rows, feat):
    vals = np.array([r[feat] for r in rows if r.get(feat) is not None], float)
    if len(vals) < 10:
        return []
    qs = [np.percentile(vals, p) for p in (20, 35, 50, 65, 80)]
    grid = []
    for q in qs:
        grid.append((feat, ">=", round(float(q), 4)))
        grid.append((feat, "<=", round(float(q), 4)))
    return grid


def passes(r, preds):
    for f, op, thr in preds:
        v = r.get(f)
        if v is None:
            return False
        if op == ">=" and not (v >= thr):
            return False
        if op == "<=" and not (v <= thr):
            return False
    return True


def score_combo(rows, preds):
    sel = [r for r in rows if passes(r, preds)]
    if not sel:
        return None
    n = len(sel)
    runners = sum(1 for r in sel if r["_lab"] == "runner")
    duds = sum(1 for r in sel if r["_lab"] == "dud")
    wr = runners / n            # runner-precision
    dudrate = duds / n
    meanpeak = np.mean([r["peak_pct"] for r in sel])
    return dict(n=n, wr=wr, dudrate=dudrate, meanpeak=meanpeak,
                throughput=n/len(rows))


def main():
    ded = load()
    label(ded)
    base_runner = np.mean([r["_lab"] == "runner" for r in ded])
    base_dud = np.mean([r["_lab"] == "dud" for r in ded])
    print(f"recent token-deduped universe: {len(ded)} tokens | "
          f"base runner(>= {RUNNER}%)={100*base_runner:.0f}% dud(<{DUD}%)={100*base_dud:.0f}%")

    # time-split held-out
    cut = int(len(ded) * (1 - HELDOUT_FRAC))
    train, held = ded[:cut], ded[cut:]
    print(f"train {len(train)} tokens | held-out {len(held)} tokens "
          f"(newest {int(HELDOUT_FRAC*100)}%)\n")

    # 1) Cohen's d feature ranking (runner vs dud) on TRAIN
    print("=== Cohen's d: runner vs dud (train) ===")
    ranked = []
    for f in FEATS_ALL:
        d, ma, mb = cohens_d(train, f, "runner", "dud")
        ranked.append((abs(d), d, f, ma, mb))
    ranked.sort(reverse=True)
    for ad, d, f, ma, mb in ranked[:14]:
        print(f"  {f:16} d={d:+.2f}  runner_mean={ma:>12.3f}  dud_mean={mb:>12.3f}")

    top_feats = [f for _, _, f, _, _ in ranked[:8]]

    # 2) exhaustive 1-3 way combos over top features' predicate grids (train)
    grids = {f: predicate_grid(train, f) for f in top_feats}
    all_preds = [p for f in top_feats for p in grids[f]]
    candidates = []
    for k in (1, 2, 3):
        for combo in itertools.combinations(all_preds, k):
            feats = [c[0] for c in combo]
            if len(set(feats)) != len(feats):   # no two predicates on same feature
                continue
            sc = score_combo(train, combo)
            if sc and sc["n"] >= 12 and sc["wr"] >= base_runner * 1.4:
                candidates.append((combo, sc))
    candidates.sort(key=lambda x: (-x[1]["wr"], -x[1]["throughput"]))
    print(f"\n=== top runner-selection combos (train, WR>=1.4x base, n>=12) — {len(candidates)} found ===")
    seen_sig = set()
    shown = 0
    pareto = []
    for combo, sc in candidates:
        sig = tuple(sorted(c[0] for c in combo))
        # validate held-out
        hsc = score_combo(held, combo)
        desc = " AND ".join(f"{f}{op}{thr}" for f, op, thr in combo)
        if hsc and hsc["n"] >= 6:
            pareto.append((sc, hsc, desc, combo))
        if shown < 18:
            hwr = f"{100*hsc['wr']:.0f}%(n{hsc['n']})" if hsc and hsc['n'] >= 6 else "thin"
            print(f"  WR {100*sc['wr']:>3.0f}% dud {100*sc['dudrate']:>3.0f}% "
                  f"thru {100*sc['throughput']:>3.0f}% n={sc['n']:>3} | held-out WR {hwr:>11} | {desc}")
            shown += 1

    # 3) Pareto frontier on HELD-OUT (throughput vs held-out WR)
    print(f"\n=== HELD-OUT Pareto frontier (throughput vs held-out runner-WR) ===")
    pareto.sort(key=lambda x: -x[1]["throughput"])
    frontier = []
    best_wr = -1
    for sc, hsc, desc, combo in pareto:
        if hsc["wr"] > best_wr:
            frontier.append((sc, hsc, desc))
            best_wr = hsc["wr"]
    for sc, hsc, desc in sorted(frontier, key=lambda x: -x[1]["throughput"]):
        lift = hsc["wr"] / base_runner
        # rough $/day proxy: throughput * tokens/day(~80) * (WR*avgwin - (1-WR)*avgloss placeholder)
        print(f"  held-out WR {100*hsc['wr']:>3.0f}% ({lift:.1f}x base) thru {100*hsc['throughput']:>3.0f}% "
              f"n={hsc['n']:>3} meanpeak {hsc['meanpeak']:>5.1f}% | {desc}")
    print("\nNOTE: peak-based — HYPOTHESIS ONLY. Cross-check survivors on realized trade")
    print("outcomes + ship as measure-only shadow (phantom parity) before any enforcement.")


if __name__ == "__main__":
    main()
