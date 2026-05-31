#!/usr/bin/env python
"""Large-sample test of the rolling never-green scorer on the UNIVERSE recorder.

The traded-token history caps at ~153 tokens. The universe recorder logs EVERY
token the scanner saw (~25k records, hundreds/day) with +forward outcomes — the
true population of entry candidates. This re-runs the SAME rolling methodology
(train trailing N days -> predict next day) on that large sample, using the
~25 features the recorder stores, to see if the never-green signal holds at scale.

Leakage control: drop all forward-outcome / timestamp / id fields; keep only
features known at detection time.

Usage: python scripts/universe_scorer_test.py [--lookback 7] [--block-rate 0.10]
"""
from __future__ import annotations
import argparse, json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import cross_val_predict, GroupKFold
import xgboost as xgb

LEAK = {"peak_pct", "exit_pct", "won", "won_5pct", "won_10pct", "outcome_at_ts",
        "outcome_at_iso", "n_post_candles", "event_ts", "event_id", "detected_at_iso",
        "close_at_event", "high_at_event", "low_at_event",  # post/at-event price extremes
        "outcome_at", "pair_address"}


def _model():
    return xgb.XGBClassifier(n_estimators=120, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.6, min_child_weight=5, reg_lambda=2.0,
        eval_metric="auc", n_jobs=4, random_state=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=".uni_recorder.json")
    ap.add_argument("--lookback", type=int, default=7)
    ap.add_argument("--block-rate", type=float, default=0.10)
    args = ap.parse_args()

    d = json.load(open(args.file))
    arr = d if isinstance(d, list) else d.get("events", d.get("records", d))
    rows = []
    for r in arr:
        if not isinstance(r, dict) or r.get("peak_pct") is None:
            continue
        iso = r.get("detected_at_iso") or r.get("outcome_at_iso") or ""
        rows.append((r, iso[:10]))
    df = pd.DataFrame([r for r, _ in rows])
    df["_day"] = [day for _, day in rows]
    df["_ng"] = (pd.to_numeric(df["peak_pct"], errors="coerce") < 1.0).astype(int)
    df["_tok"] = df.get("pair_address", df.get("symbol", pd.Series(range(len(df))))).astype(str)
    feats = [c for c in df.columns if c not in LEAK and not c.startswith("_")
             and "iso" not in c and "symbol" not in c.lower()]
    feats = [f for f in feats if pd.to_numeric(df[f], errors="coerce").notna().sum() > len(df) * 0.3]
    for f in feats:
        df[f] = pd.to_numeric(df[f], errors="coerce")
    df = df[df["_day"].str.len() == 10].reset_index(drop=True)
    days = sorted(df["_day"].unique())
    print(f"universe records w/ outcome: {len(df)} | tokens {df['_tok'].nunique()} | "
          f"days {len(days)} [{days[0]}..{days[-1]}] | never-green base {df['_ng'].mean()*100:.0f}%")
    print(f"features ({len(feats)}): {', '.join(feats)}")

    pool = {"y": [], "p": [], "tok": [], "day": []}
    perday = []
    for i in range(args.lookback, len(days)):
        tr = df[df["_day"].isin(days[i - args.lookback:i])]
        te = df[df["_day"] == days[i]]
        if len(te) < 30 or tr["_ng"].nunique() < 2:
            continue
        y = tr["_ng"].values; pos = y.sum(); neg = len(y) - pos
        m = _model(); m.set_params(scale_pos_weight=neg / max(pos, 1))
        m.fit(tr[feats], y)
        p = m.predict_proba(te[feats])[:, 1]
        # OOS-calibrated threshold from trailing window
        g = min(5, max(2, tr["_tok"].nunique()))
        try:
            oos = cross_val_predict(_model().set_params(scale_pos_weight=neg/max(pos,1)),
                tr[feats], y, groups=tr["_tok"].values, cv=GroupKFold(g),
                method="predict_proba", n_jobs=4)[:, 1]
        except Exception:
            oos = m.predict_proba(tr[feats])[:, 1]
        thr = np.quantile(oos, 1 - args.block_rate)
        auc = roc_auc_score(te["_ng"], p) if te["_ng"].nunique() == 2 else float("nan")
        perday.append((days[i], len(te), te["_ng"].mean(), auc, (p >= thr).mean()))
        pool["y"] += list(te["_ng"]); pool["p"] += list(p)
        pool["tok"] += list(te["_tok"]); pool["day"] += [days[i]] * len(te)

    print(f"\n{'test day':12}{'n':>6}{'NG%':>6}{'fwdAUC':>8}{'block%':>8}")
    for d_, n, ng, a, br in perday:
        print(f"{d_:12}{n:>6}{ng*100:>5.0f}%{a:>8.3f}{br*100:>7.0f}%")
    y = np.array(pool["y"]); p = np.array(pool["p"])
    aucs = [a for _, _, _, a, _ in perday if a == a]
    print(f"\nPOOLED forward AUC (event-level, n={len(y)}): {roc_auc_score(y, p):.3f}")
    print(f"per-day fwd-AUC: mean {np.mean(aucs):.3f}, median {np.median(aucs):.3f}, "
          f">0.55 on {sum(a>0.55 for a in aucs)}/{len(aucs)} days")
    # token-deduped
    g = pd.DataFrame(pool).groupby("tok").agg(y=("y","median"), p=("p","mean"))
    g = g[(g["y"]==0)|(g["y"]==1)]
    print(f"POOLED forward TOKEN-AUC (n={len(g)}): {roc_auc_score(g['y'],g['p']):.3f}")
    # gate at block rate (event-level, pooled — block top by proba within each day already via thr)
    print(f"\nGATE precision/recall by global proba percentile (pooled forward):")
    order = np.argsort(-p)
    for fr in (0.05,0.10,0.15,0.20):
        k=int(len(p)*fr); idx=order[:k]
        print(f"  block top {int(fr*100)}%: NG-precision {y[idx].mean()*100:.0f}% "
              f"(base {y.mean()*100:.0f}%, lift {y[idx].mean()/y.mean():.1f}x)  recall {y[idx].sum()/y.sum()*100:.0f}%")


if __name__ == "__main__":
    main()
