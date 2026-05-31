#!/usr/bin/env python
"""Rolling never-green scorer — retrains on the trailing window, scores entries.

WHY ROLLING: the never-green signal is real but NON-STATIONARY (proven 2026-05-30:
grouped-CV AUC 0.66 / walk-forward token-AUC 0.65 vs null 0.45, robust for 5-10d
lookbacks, FAILS at 14d). A frozen gate can't hold it; a model retrained every few
days on recent completed trades can. See
reference_entry_nonpredictive_concentration_is_lever_2026_05_30 (entry features are
non-predictive STATICALLY) — this is the rolling complement that DOES transfer.

PRODUCTION SHAPE: the gate is a PROBABILITY THRESHOLD, not an oracle 'top X%'. At
entry you score one token and don't know the day's distribution, so the threshold
is set from the trailing window (the proba quantile that blocks ~target_block_rate
of recent entries). This is exactly reproducible live.

  target = never-green (peak_pnl_pct < 1.0)  [pure entry-quality failure]
  model  = XGBoost, trailing `lookback_days`, retrained each scoring period
  gate   = block if P(never-green) >= threshold (trailing target_block_rate quantile)

Usage:
    python scripts/rolling_scorer.py                 # walk-forward test (default)
    python scripts/rolling_scorer.py --lookback 5 --block-rate 0.10
"""
from __future__ import annotations
import argparse
import json
import sys
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import cross_val_predict, GroupKFold
import xgboost as xgb

sys.path.insert(0, "scripts")
from ps_scan import load_completed
from win_loss_diff import DEFAULT_FILES
import ng_classifier as NG


def _model():
    return xgb.XGBClassifier(
        n_estimators=120, max_depth=3, learning_rate=0.05, subsample=0.8,
        colsample_bytree=0.6, min_child_weight=5, reg_lambda=2.0,
        eval_metric="auc", n_jobs=4, random_state=0,
    )


class RollingNeverGreenScorer:
    """Train on trailing completed positions; score entry feature-dicts.

    Self-contained so production can: nightly -> .train(trailing_positions);
    at entry -> .should_block(entry_meta_features).
    """

    def __init__(self, target_block_rate: float = 0.10):
        self.target_block_rate = target_block_rate
        self.clf = None
        self.feats: list[str] = []
        self.threshold: float = 1.0  # fail-open until trained

    def train(self, df_train: pd.DataFrame, feats: list[str]):
        if df_train["_ng"].nunique() < 2 or len(df_train) < 80:
            self.clf = None  # not enough signal/data -> fail-open (block nothing)
            return self
        self.feats = feats
        y = df_train["_ng"].values
        pos = y.sum(); neg = len(y) - pos
        self.clf = _model()
        self.clf.set_params(scale_pos_weight=neg / max(pos, 1))
        self.clf.fit(df_train[feats], y)
        # Threshold must be calibrated on OUT-OF-SAMPLE probabilities, not the
        # in-sample fit (the model is overconfident in-sample, so an in-sample
        # quantile barely blocks anything forward). Use grouped (by token) CV on
        # the trailing window to get OOS-scale probas, then take the quantile that
        # blocks ~target_block_rate. This reproduces forward.
        groups = df_train["_tok"].values
        ng = min(5, max(2, len(np.unique(groups))))
        try:
            oos = cross_val_predict(_model().set_params(scale_pos_weight=neg / max(pos, 1)),
                                    df_train[feats], y, groups=groups,
                                    cv=GroupKFold(ng), method="predict_proba", n_jobs=4)[:, 1]
        except Exception:
            oos = self.clf.predict_proba(df_train[feats])[:, 1]
        self.threshold = float(np.quantile(oos, 1.0 - self.target_block_rate))
        return self

    def score(self, X: pd.DataFrame) -> np.ndarray:
        if self.clf is None:
            return np.zeros(len(X))
        return self.clf.predict_proba(X[self.feats])[:, 1]

    def should_block(self, X: pd.DataFrame) -> np.ndarray:
        return self.score(X) >= self.threshold

    def save(self, path: str):
        if self.clf is None:
            return
        self.clf.save_model(path + ".ubj")
        with open(path + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"feats": self.feats, "threshold": self.threshold,
                       "target_block_rate": self.target_block_rate}, fh)

    def load(self, path: str):
        self.clf = _model(); self.clf.load_model(path + ".ubj")
        with open(path + ".meta.json", encoding="utf-8") as fh:
            m = json.load(fh)
        self.feats = m["feats"]; self.threshold = m["threshold"]
        self.target_block_rate = m["target_block_rate"]
        return self


def walk_forward(df, feats, lookback, block_rate, peakmax):
    days = sorted(df["_day"].unique())
    rows = []
    pool = {"y": [], "p": [], "tok": [], "blk": []}
    for i in range(lookback, len(days)):
        d = days[i]
        tr = df[df["_day"].isin(days[i - lookback:i])]
        te = df[df["_day"] == d]
        if len(te) < 15:
            continue
        sc = RollingNeverGreenScorer(block_rate).train(tr, feats)
        if sc.clf is None:
            continue
        p = sc.score(te); blk = p >= sc.threshold
        rows.append((d, len(te), te["_ng"].mean(),
                     roc_auc_score(te["_ng"], p) if te["_ng"].nunique() == 2 else float("nan"),
                     blk.mean()))
        pool["y"] += list(te["_ng"]); pool["p"] += list(p)
        pool["tok"] += list(te["_tok"]); pool["blk"] += list(blk)
    return rows, pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--lookback", type=int, default=7)
    ap.add_argument("--block-rate", type=float, default=0.10)
    ap.add_argument("--big-peak", type=float, default=20.0)
    args = ap.parse_args()

    comp = load_completed([f.strip() for f in args.files.split(",") if f.strip()])
    df, feats = NG.build_frame(comp)
    df = df[df["_haspeak"]].reset_index(drop=True)
    df["_day"] = df["_t"].str[:10]
    peakmax = {}
    for c in comp:
        pk = c["peak"] if c["peak"] is not None else 0
        peakmax[c["tok"]] = max(peakmax.get(c["tok"], -1e9), pk)

    print(f"positions {len(df)} | features {len(feats)} | lookback {args.lookback}d | "
          f"target block-rate {args.block_rate*100:.0f}% (PROBA THRESHOLD, production-realistic)")
    rows, pool = walk_forward(df, feats, args.lookback, args.block_rate, peakmax)

    print(f"\n{'test day':12}{'nTrades':>8}{'NG%':>6}{'fwdAUC':>8}{'block%':>8}")
    for d, n, ng, auc, br in rows:
        print(f"{d:12}{n:>8}{ng*100:>5.0f}%{auc:>8.3f}{br*100:>7.0f}%")

    y = np.array(pool["y"]); p = np.array(pool["p"]); blk = np.array(pool["blk"])
    print(f"\nPOOLED forward (trade-level): AUC {roc_auc_score(y, p):.3f}  "
          f"block-rate {blk.mean()*100:.0f}%")

    # Token-deduped gate metrics (the honest unit).
    g = pd.DataFrame({"tok": pool["tok"], "y": y, "p": p, "blk": blk})
    gt = g.groupby("tok").agg(y=("y", "median"), blk=("blk", "mean")).reset_index()
    gt["blocked"] = gt["blk"] > 0.5
    gt["bigwin"] = gt["tok"].map(lambda t: peakmax.get(t, 0) >= args.big_peak)
    gt["isng"] = gt["y"] == 1
    nb = gt["blocked"].sum()
    base = gt["isng"].mean()
    prec = gt[gt["blocked"]]["isng"].mean() if nb else float("nan")
    recall = gt[gt["blocked"]]["isng"].sum() / max(gt["isng"].sum(), 1)
    bigkill = gt[gt["blocked"]]["bigwin"].sum()
    bigtot = gt["bigwin"].sum()
    kept = gt[~gt["blocked"]]
    print(f"\n=== ROLLING GATE @ block-rate {args.block_rate*100:.0f}% (token-deduped, forward) ===")
    print(f"  tokens {len(gt)} | base never-green rate {base*100:.0f}%")
    print(f"  blocked {nb} tokens | NG-precision {prec*100:.0f}% (lift {prec/base:.1f}x) | "
          f"recall {recall*100:.0f}%")
    print(f"  big-winner kill: {bigkill}/{bigtot} ({100*bigkill/max(bigtot,1):.0f}%)  "
          f"[target <=5%]")
    print(f"  token never-green rate kept vs all: {kept['isng'].mean()*100:.0f}% vs {base*100:.0f}%")
    tok_auc = roc_auc_score(gt["y"], g.groupby("tok")["p"].mean().values) if gt["y"].nunique() == 2 else float("nan")
    print(f"  token-level forward AUC: {tok_auc:.3f}")


if __name__ == "__main__":
    main()
