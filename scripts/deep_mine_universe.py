"""Exhaustive mining pass on universe_fresh.json.

Stages:
  1. Single-feature threshold sweep across all numeric features → rank
     by Cohen's d AND precision-at-threshold (best single cuts).
  2. 2-way + 3-way exhaustive combination search across top-15 features
     by cohen's d. Report Pareto frontier (throughput vs precision vs
     avg_exit per matched event).
  3. Within-bucket mining: pc_h24 sub-segments (gold +20-100% vs
     suspect +100-500%) — what tips marginal cohort positive?
  4. Time-of-day × structural feature compounds (hour_ct + feature).
  5. Held-out stability: first-half vs second-half date split, report
     compounds that survive both.

Output:
  - Top 30 single-feature cuts (sorted by precision × n)
  - Top 50 multi-feature compounds (Pareto frontier across n / wr / avg_exit)
  - Within-bucket findings
  - Held-out stability report (which compounds are stable)
"""
from __future__ import annotations

import datetime as dt
import json
import math
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


def load_universe(path="universe_fresh.json"):
    events = json.loads(Path(path).read_text())
    for e in events:
        iso = e.get("detected_at_iso") or ""
        try:
            s = iso.replace("Z", "+00:00") if "Z" in iso else iso
            pdt = dt.datetime.fromisoformat(s)
            ct = pdt - dt.timedelta(hours=5)
            e["_hour_ct"] = ct.hour
            e["_date_ct"] = ct.date().isoformat()
        except Exception:
            pass
    return events


def cohen_d(a, b):
    if len(a) < 5 or len(b) < 5: return None
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    va = sum((x-ma)**2 for x in a)/(len(a)-1)
    vb = sum((x-mb)**2 for x in b)/(len(b)-1)
    p = math.sqrt((va+vb)/2)
    return (ma-mb)/p if p > 0 else None


def is_survivor(e):
    return (isinstance(e.get("peak_pct"), (int, float)) and e["peak_pct"] >= 10.0
            and isinstance(e.get("exit_pct"), (int, float)) and e["exit_pct"] >= 0)


def is_dyer(e):
    return isinstance(e.get("exit_pct"), (int, float)) and e["exit_pct"] <= -20


def is_winner(e):
    return e.get("won_10pct") is True


def is_positive_exit(e):
    return isinstance(e.get("exit_pct"), (int, float)) and e["exit_pct"] > 0


def numeric_features(events, exclude=None):
    exclude = exclude or set()
    EXCL_PREFIXES = ('_', 'won')
    feats = set()
    for e in events:
        for k, v in e.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if not any(k.startswith(p) for p in EXCL_PREFIXES) and k not in exclude:
                    feats.add(k)
    return sorted(feats)


def get_val(e, k):
    v = e.get(k)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    return None


def threshold_sweep_single(events, feat, win_pred, all_pred=None,
                            percentiles=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)):
    """For each percentile cut, compute n / win_rate / avg_exit on the
    matched cohort, sweeping both >= and <= directions."""
    if all_pred is None:
        all_pred = lambda e: True
    vals = sorted([get_val(e, feat) for e in events
                   if all_pred(e) and get_val(e, feat) is not None])
    if len(vals) < 50:
        return []
    cuts = [vals[int(len(vals) * p)] for p in percentiles]
    rows = []
    for direction in (">=", "<="):
        for cut in cuts:
            if direction == ">=":
                matched = [e for e in events
                           if all_pred(e) and get_val(e, feat) is not None
                           and get_val(e, feat) >= cut]
            else:
                matched = [e for e in events
                           if all_pred(e) and get_val(e, feat) is not None
                           and get_val(e, feat) <= cut]
            n = len(matched)
            if n < 30: continue
            wins = sum(1 for e in matched if win_pred(e))
            wr = wins / n
            avg_exit = sum(e.get("exit_pct", 0) for e in matched) / n
            rows.append({
                "feat": feat, "direction": direction, "cut": cut,
                "n": n, "wins": wins, "wr": wr, "avg_exit": avg_exit,
            })
    return rows


def stage_1_single_features(events):
    feats = numeric_features(events, exclude={
        "peak_pct", "exit_pct", "open_at_event", "close_at_event",
        "high_at_event", "low_at_event", "entry_price", "vol_at_event",
        "event_ts", "outcome_at_ts", "n_post_candles",
    })
    print(f"=== Stage 1: Single-feature sweep (n_feats={len(feats)}) ===")
    all_rows = []
    for f in feats:
        rows = threshold_sweep_single(events, f, is_survivor)
        all_rows.extend(rows)
    # Compute "lift" = wr - baseline_wr
    base = sum(1 for e in events if is_survivor(e)) / len(events)
    for r in all_rows:
        r["lift"] = r["wr"] - base
        r["score"] = r["wr"] * math.log(r["n"])  # balance precision × volume
    all_rows.sort(key=lambda r: -r["score"])
    # Dedup: keep only the best cut per feature × direction
    seen = set()
    top = []
    for r in all_rows:
        key = (r["feat"], r["direction"])
        if key in seen: continue
        seen.add(key)
        top.append(r)
        if len(top) >= 30: break
    print(f"  Baseline survivor rate: {base*100:.0f}%")
    print(f"  Top single-feature cuts (by precision × log(n)):")
    print(f"  {'Feature':<26} {'dir':>3} {'cut':>9} {'n':>5} {'wr':>5} {'avg_exit':>9} {'lift':>6}")
    for r in top:
        print(f"  {r['feat']:<26} {r['direction']:>3} {r['cut']:>+8.2f} "
              f"{r['n']:>5} {r['wr']*100:>4.0f}% {r['avg_exit']:>+7.1f}% "
              f"{r['lift']*100:>+5.0f}pp")
    return top


def stage_2_compound_search(events, top_singles, max_k=3):
    """Exhaustive k-feature combo search using top-15 features from Stage 1."""
    print(f"\n=== Stage 2: 2-way + 3-way exhaustive compound search ===")
    pool = top_singles[:15]
    print(f"  Pool: {len(pool)} feature cuts. Searching pairs + triples.")
    base = sum(1 for e in events if is_survivor(e)) / len(events)
    candidates = []
    for k in (2, 3):
        for combo in combinations(pool, k):
            def match(e, combo=combo):
                for c in combo:
                    v = get_val(e, c["feat"])
                    if v is None: return False
                    if c["direction"] == ">=":
                        if v < c["cut"]: return False
                    else:
                        if v > c["cut"]: return False
                return True
            matched = [e for e in events if match(e)]
            n = len(matched)
            if n < 30: continue
            wins = sum(1 for e in matched if is_survivor(e))
            wr = wins / n
            avg_exit = sum(e.get("exit_pct", 0) for e in matched) / n
            label = " AND ".join(
                f"{c['feat']}{c['direction']}{c['cut']:.3g}"
                for c in combo
            )
            candidates.append({
                "k": k, "label": label, "n": n, "wr": wr,
                "avg_exit": avg_exit, "lift": wr - base,
            })
    # Pareto: for each n bucket, keep top by wr
    candidates.sort(key=lambda c: (-c["wr"], -c["n"]))
    # Filter to wr > 0.7 AND n >= 30, plus separate pareto by n size
    print(f"  Candidates with n>=30, wr>=70%: ", end="")
    high_prec = [c for c in candidates if c["wr"] >= 0.70 and c["n"] >= 30]
    print(f"{len(high_prec)}")
    print(f"\n  Top 30 by (wr, n) — k=2/3 combos with wr >= 65%:")
    print(f"  {'k':>1} {'n':>5} {'wr':>5} {'exit':>7} {'compound':<80}")
    shown = 0
    seen_features = set()
    for c in candidates:
        if c["wr"] < 0.65: continue
        # Deduplicate by feature-set to avoid threshold-sweep noise
        feats_in = frozenset(p.split("=")[0].split(">")[0].split("<")[0]
                              for p in c["label"].split(" AND "))
        if feats_in in seen_features: continue
        seen_features.add(feats_in)
        print(f"  {c['k']:>1} {c['n']:>5} {c['wr']*100:>4.0f}% {c['avg_exit']:>+6.1f}% {c['label'][:80]}")
        shown += 1
        if shown >= 30: break
    return candidates


def stage_3_within_bucket(events):
    """What features tip the borderline pc_h24 100-500% cohort positive?"""
    print(f"\n=== Stage 3: Within-bucket mining (pc_h24 100-500%) ===")
    bucket = [e for e in events
              if isinstance(e.get("pc_h24"), (int, float)) and 100 <= e["pc_h24"] < 500]
    pos = [e for e in bucket if is_positive_exit(e)]
    neg = [e for e in bucket if isinstance(e.get("exit_pct"), (int, float)) and e["exit_pct"] < -10]
    print(f"  Sub-cohort sizes: bucket={len(bucket)}  pos_exit={len(pos)}  neg_exit={len(neg)}")
    if len(pos) < 30 or len(neg) < 30:
        print("  Cohorts too small")
        return []
    feats = numeric_features(events, exclude={
        "peak_pct", "exit_pct", "open_at_event", "close_at_event",
        "high_at_event", "low_at_event", "entry_price", "vol_at_event",
        "event_ts", "outcome_at_ts", "n_post_candles",
    })
    results = []
    for f in feats:
        a = [get_val(e, f) for e in pos if get_val(e, f) is not None]
        b = [get_val(e, f) for e in neg if get_val(e, f) is not None]
        d = cohen_d(a, b)
        if d is None or abs(d) < 0.3: continue
        results.append({
            "feat": f, "d": d,
            "pos_mean": sum(a)/len(a), "neg_mean": sum(b)/len(b),
            "pos_med": sorted(a)[len(a)//2], "neg_med": sorted(b)[len(b)//2],
        })
    results.sort(key=lambda r: -abs(r["d"]))
    print(f"\n  Top features distinguishing positive-exit from negative-exit:")
    print(f"  {'Feature':<26} {'d':>6} {'pos_med':>9} {'neg_med':>9}")
    for r in results[:15]:
        print(f"  {r['feat']:<26} {r['d']:>+5.2f}  {r['pos_med']:>+8.2f}  {r['neg_med']:>+8.2f}")
    return results


def stage_4_time_of_day(events):
    print(f"\n=== Stage 4: Time-of-day × structural ===")
    print(f"  Per-hour WR (n>=30):")
    by_hour = defaultdict(list)
    for e in events:
        h = e.get("_hour_ct")
        if h is None: continue
        by_hour[h].append(e)
    base = sum(1 for e in events if is_survivor(e)) / len(events)
    print(f"  {'CT_hr':>5} {'n':>5} {'surv_rate':>10} {'avg_exit':>9} {'lift':>6}")
    for h in sorted(by_hour):
        sub = by_hour[h]
        if len(sub) < 30: continue
        surv = sum(1 for e in sub if is_survivor(e)) / len(sub)
        ax = sum(e.get("exit_pct", 0) for e in sub) / len(sub)
        print(f"  {h:>5} {len(sub):>5} {surv*100:>8.0f}% {ax:>+7.1f}% {(surv-base)*100:>+5.0f}pp")
    # Hour buckets × pc_m5 deep
    print(f"\n  Hour × pc_m5<-7 (deep dip in specific hours):")
    for h_range, label in [
        ({22, 23, 0, 1, 2}, "Late night CT 22-02"),
        ({3, 4, 5, 6, 7}, "Pre-dawn CT 03-07"),
        ({8, 9, 10, 11}, "Morning CT 08-11"),
        ({12, 13, 14, 15, 16}, "Afternoon CT 12-16"),
        ({17, 18, 19, 20, 21}, "Evening CT 17-21"),
    ]:
        sub = [e for e in events
               if e.get("_hour_ct") in h_range
               and isinstance(e.get("pc_m5"), (int, float)) and e["pc_m5"] < -7]
        if len(sub) < 20: continue
        surv = sum(1 for e in sub if is_survivor(e)) / len(sub)
        ax = sum(e.get("exit_pct", 0) for e in sub) / len(sub)
        print(f"  {label:<24} n={len(sub):>4} surv={surv*100:>3.0f}% avg_exit={ax:>+5.1f}%")


def stage_5_held_out(events, top_compounds):
    """Verify top compounds on a first-half vs second-half date split."""
    print(f"\n=== Stage 5: Held-out stability (first-half vs second-half) ===")
    dates = sorted({e.get("_date_ct") for e in events if e.get("_date_ct")})
    if len(dates) < 3:
        print(f"  Only {len(dates)} dates — not enough to split")
        return
    mid = len(dates) // 2
    train_dates = set(dates[:mid])
    test_dates = set(dates[mid:])
    train = [e for e in events if e.get("_date_ct") in train_dates]
    test = [e for e in events if e.get("_date_ct") in test_dates]
    print(f"  Train: {len(train)} events ({len(train_dates)} dates)")
    print(f"  Test:  {len(test)} events ({len(test_dates)} dates)")
    print(f"\n  {'Compound':<70} {'train_wr':>9} {'test_wr':>9} {'stable':>8}")
    # Recompute compound matches on each split
    for c in top_compounds[:25]:
        # Parse cuts from label
        parts = c["label"].split(" AND ")
        preds = []
        for p in parts:
            if ">=" in p:
                f, v = p.split(">=")
                preds.append((f, ">=", float(v)))
            elif "<=" in p:
                f, v = p.split("<=")
                preds.append((f, "<=", float(v)))
        def match(e, preds=preds):
            for f, d, v in preds:
                val = get_val(e, f)
                if val is None: return False
                if d == ">=":
                    if val < v: return False
                else:
                    if val > v: return False
            return True
        tm = [e for e in train if match(e)]
        ts = [e for e in test if match(e)]
        if len(tm) < 15 or len(ts) < 15: continue
        twr = sum(1 for e in tm if is_survivor(e)) / len(tm)
        sswr = sum(1 for e in ts if is_survivor(e)) / len(ts)
        stable = "YES" if abs(twr - sswr) < 0.10 else ("DRIFT" if abs(twr - sswr) < 0.20 else "FAIL")
        print(f"  {c['label'][:69]:<70} {twr*100:>7.0f}%  {sswr*100:>7.0f}%  {stable:>8}")


if __name__ == "__main__":
    events = load_universe()
    print(f"Loaded {len(events)} events\n")
    top_singles = stage_1_single_features(events)
    top_compounds = stage_2_compound_search(events, top_singles)
    stage_3_within_bucket(events)
    stage_4_time_of_day(events)
    stage_5_held_out(events, top_compounds)
