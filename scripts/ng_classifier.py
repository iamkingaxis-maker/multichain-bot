#!/usr/bin/env python
"""Multivariate never-green classifier — the definitive 'is there a gate' test.

Univariate differentials + hand-built conjunctions all failed to generalize as a
gate (every per-entry feature separates at the median but trades winners ~1:1
held-out). That points to a MULTIVARIATE boundary that no single rule finds. This
trains a gradient-boosted tree on ALL numeric entry_meta features to predict
NEVER-GREEN (peak_pnl_pct < 1% = a pure entry-quality failure), with a strict
TIME-BASED held-out split (no leakage) and TOKEN-DEDUPED held-out AUC (so a
correlated cluster can't inflate it).

Read the held-out AUC:
  ~0.50-0.55  signal genuinely not in the features (stop mining candles; the
              lever is structural — per-token cap / correlation / exits)
  >=0.65      a real multivariate gate exists -> extract via predict_proba,
              validate winner-kill, ship.

Feature importances from a held-out-validated model = the multivariate answer to
"what separates winners from losers."

Usage:
    python scripts/ng_classifier.py
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
import xgboost as xgb

sys.path.insert(0, "scripts")
from ps_scan import load_completed, _in, confound_flag, _SKIP_SUBSTR
from win_loss_diff import DEFAULT_FILES, DEFAULT_TRAIN, DEFAULT_TEST


def build_frame(comp):
    feats = set()
    for c in comp:
        for k, v in c["f"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and not any(s in k.lower() for s in _SKIP_SUBSTR):
                feats.add(k)
    # Drop time/identity/regime confounds and obvious leak proxies.
    feats = [f for f in feats if not confound_flag(f) and "ts_ms" not in f.lower()
             and "signal_ts" not in f.lower()]
    rows = []
    for c in comp:
        r = {f: c["f"].get(f, np.nan) for f in feats}
        r["_tok"] = c["tok"]; r["_t"] = c["t"]
        r["_ng"] = 1 if (c["peak"] is not None and c["peak"] < 1.0) else 0
        r["_haspeak"] = c["peak"] is not None
        rows.append(r)
    df = pd.DataFrame(rows)
    for f in feats:
        df[f] = pd.to_numeric(df[f], errors="coerce")
    return df, feats


def token_auc(df_test, proba):
    d = df_test.copy(); d["_p"] = proba
    g = d.groupby("_tok").agg(p=("_p", "mean"), ng=("_ng", "median"))
    g = g[(g["ng"] == 0) | (g["ng"] == 1)]
    if g["ng"].nunique() < 2:
        return float("nan"), len(g)
    return roc_auc_score(g["ng"], g["p"]), len(g)


def main():
    comp = load_completed(DEFAULT_FILES)
    df, feats = build_frame(comp)
    df = df[df["_haspeak"]]
    tr = df[df["_t"].str[:10].between(*DEFAULT_TRAIN)]
    te = df[df["_t"].str[:10].between(*DEFAULT_TEST)]
    print(f"features: {len(feats)} (confounds/time dropped)")
    print(f"TRAIN rows {len(tr)} (NG {tr['_ng'].mean()*100:.0f}%) | "
          f"TEST rows {len(te)} (NG {te['_ng'].mean()*100:.0f}%)")

    Xtr, ytr = tr[feats], tr["_ng"]
    Xte, yte = te[feats], te["_ng"]
    pos = ytr.sum(); neg = len(ytr) - pos
    clf = xgb.XGBClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.6, min_child_weight=5,
        reg_lambda=2.0, scale_pos_weight=neg / max(pos, 1),
        eval_metric="auc", n_jobs=4, random_state=0,
    )
    clf.fit(Xtr, ytr)

    # In-sample (train) AUC vs held-out (test) AUC — gap = overfit.
    auc_tr = roc_auc_score(ytr, clf.predict_proba(Xtr)[:, 1])
    p_te = clf.predict_proba(Xte)[:, 1]
    auc_te = roc_auc_score(yte, p_te)
    auc_tok, ntok = token_auc(te, p_te)
    print()
    print(f"TRAIN AUC (in-sample):      {auc_tr:.3f}")
    print(f"TEST  AUC (held-out, trade):{auc_te:.3f}")
    print(f"TEST  AUC (held-out, token):{auc_tok:.3f}  (n={ntok} tokens)")
    print()
    imp = sorted(zip(feats, clf.feature_importances_), key=lambda x: -x[1])
    print("TOP 20 features (gain importance):")
    for f, g in imp[:20]:
        print(f"   {g:6.3f}  {f}")

    # If held-out signal exists, show the precision/recall a proba-threshold gate gives.
    if auc_te >= 0.60:
        print("\nGate sweep on TEST (proba threshold -> block precision / recall / winner-impact):")
        order = np.argsort(-p_te)
        yte_arr = yte.values
        for frac in (0.05, 0.10, 0.15, 0.20):
            k = int(len(p_te) * frac)
            idx = order[:k]
            prec = yte_arr[idx].mean()
            recall = yte_arr[idx].sum() / yte_arr.sum()
            print(f"   block top {frac*100:.0f}%: precision(NG)={prec*100:.0f}%  recall={recall*100:.0f}%")


if __name__ == "__main__":
    main()
