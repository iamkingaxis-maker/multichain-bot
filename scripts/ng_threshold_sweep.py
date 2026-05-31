#!/usr/bin/env python
"""Sweep the never-green PEAK threshold to find which target cuts the most loss-$.

Live scorer labels trash = peak<1%. The loss taxonomy says 94% of loss is peak<3%
(never-green 75% + shallow-green-fade 19%). But a higher threshold mixes clean
never-green with marginal-green entries that briefly worked — which may be LESS
separable. So test empirically: for each peak threshold, walk-forward (rolling,
trailing 7d), gate the top ~10% trash-proba, and measure the ACTUAL dollar impact:
loss-$ removed vs winner-$ killed (net P&L improvement) + big-winner kill.

The right target maximizes net loss-$ cut at acceptable winner-kill, not AUC.
"""
from __future__ import annotations
import sys, os
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import cross_val_predict, GroupKFold

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "scripts")
from ps_scan import load_completed
from core.ng_scorer import _is_feat

FILES = [".trades_now.json", ".overnight_trades.json", ".watch7h/val_wide.json",
         "ep_verify.json", ".t_big.json", "trades_dump_candidates.json",
         "trades_dump.json", "trades_local_dump.json"]
BLOCK_RATE = 0.10
BIG_PEAK = 20.0


def _model():
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150,
                                          l2_regularization=2.0, min_samples_leaf=20, random_state=0)


def main():
    comp = load_completed(FILES)
    comp = [c for c in comp if c["peak"] is not None]
    cov = {}
    for c in comp:
        for k, v in c["f"].items():
            if _is_feat(k, v):
                cov[k] = cov.get(k, 0) + 1
    feats = [k for k, n in cov.items() if n >= 0.30 * len(comp)]
    base = pd.DataFrame([{**{k: (c["f"].get(k) if isinstance(c["f"].get(k), (int, float))
                                 and not isinstance(c["f"].get(k), bool) else np.nan) for k in feats},
                          "_peak": c["peak"], "_pnl": c["pnl"], "_tok": c["tok"],
                          "_day": c["t"][:10]} for c in comp])
    days = sorted(base["_day"].unique())
    print(f"positions {len(base)} | tokens {base['_tok'].nunique()} | feats {len(feats)}")
    print(f"\n{'peak<':>6}{'NGrate':>8}{'tokAUC':>8}{'blkTok':>7}{'lossCut$':>10}{'winKill$':>10}{'net$':>9}{'bigKill':>9}")

    for thr in (1.0, 2.0, 3.0, 5.0):
        df = base.copy(); df["_ng"] = (df["_peak"] < thr).astype(int)
        Y = []; P = []; rows_idx = []
        for i in range(7, len(days)):
            tr = df[df["_day"].isin(days[i-7:i])]; te = df[df["_day"] == days[i]]
            if len(te) < 15 or tr["_ng"].nunique() < 2:
                continue
            m = _model(); m.fit(tr[feats], tr["_ng"])
            p = m.predict_proba(te[feats])[:, 1]
            # OOS-calibrated per-fold threshold (grouped by token)
            try:
                g = min(5, max(2, tr["_tok"].nunique()))
                oos = cross_val_predict(_model(), tr[feats], tr["_ng"], groups=tr["_tok"].values,
                                        cv=GroupKFold(g), method="predict_proba")[:, 1]
                t_thr = np.quantile(oos, 1 - BLOCK_RATE)
            except Exception:
                t_thr = np.quantile(p, 1 - BLOCK_RATE)
            sub = te.copy(); sub["_p"] = p; sub["_blk"] = p >= t_thr
            rows_idx.append(sub); Y += list(te["_ng"]); P += list(p)
        allte = pd.concat(rows_idx)
        # token-dedup: token blocked if >50% of its fires blocked; token pnl = sum
        g = allte.groupby("_tok").agg(blk=("_blk", "mean"), pnl=("_pnl", "sum"),
                                      peak=("_peak", "max"), p=("_p", "mean"), ng=("_ng", "median")).reset_index()
        g["blocked"] = g["blk"] > 0.5
        auc = roc_auc_score(g["ng"], g["p"]) if g["ng"].nunique() == 2 else float("nan")
        blk = g[g["blocked"]]
        loss_cut = -blk[blk["pnl"] < 0]["pnl"].sum()       # $ of losses removed (positive number)
        win_kill = blk[blk["pnl"] > 0]["pnl"].sum()        # $ of winners removed
        net = -blk["pnl"].sum()                            # net P&L improvement from removing blocked
        big_kill = int(blk[(blk["peak"] >= BIG_PEAK) & (blk["pnl"] > 0)].shape[0])
        big_tot = int(g[(g["peak"] >= BIG_PEAK) & (g["pnl"] > 0)].shape[0])
        print(f"{thr:>6.0f}{df['_ng'].mean()*100:>7.0f}%{auc:>8.3f}{int(blk.shape[0]):>7}"
              f"{loss_cut:>10.0f}{win_kill:>10.0f}{net:>9.0f}{big_kill:>5}/{big_tot}")
    print("\n(lossCut$=loss removed, winKill$=winner-$ removed, net$=P&L improvement; size-mixed, token-deduped)")


if __name__ == "__main__":
    main()
