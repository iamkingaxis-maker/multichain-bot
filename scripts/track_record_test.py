#!/usr/bin/env python
"""Does a token's own recent track-record lift the never-green scorer? (held-out)

Principled (NOT a per-token cap): re-buys into deteriorating tokens lose because the
stateless entry signal doesn't know the token's history. Feed that history as FEATURES
and let the model SELECT against bad re-buys. This tests whether they actually add
held-out lift before anything ships.

STRICT NO-LEAKAGE: a token's prior positions only count if they CLOSED before this
entry's buy time (so their outcome was knowable at entry). Requires close-time-aware
pairing (ps_scan's pair_file drops close time, so we pair here).

Track-record features (token-level, across the fleet — the token deteriorates
regardless of which bot bought it):
  tr_n_prior_closed_6h   prior positions on this token that closed in the last 6h
  tr_n_prior_total       prior closed positions on this token (lifetime, pre-entry)
  tr_frac_prior_ng       fraction of prior closed positions that were never-green
  tr_last_pnl            realized pnl of the most-recent prior closed position
  tr_last_ng             1 if the most-recent prior closed position was never-green
  tr_mean_prior_peak     mean peak_pnl_pct of prior closed positions
  tr_mins_since_last     minutes since the most-recent prior close

Compares walk-forward never-green token-AUC: BASELINE (entry_meta only) vs
BASELINE + track-record.
"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
from datetime import datetime
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.ng_scorer import _is_feat  # same feature filter as production

FILES = [".trades_now.json", ".overnight_trades.json", ".watch7h/val_wide.json",
         "ep_verify.json", ".t_big.json", "trades_dump_candidates.json",
         "trades_dump.json", "trades_local_dump.json"]


def _epoch(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def pair_with_close(files):
    seen = set(); trades = []
    for f in files:
        if not os.path.exists(f):
            continue
        d = json.load(open(f, encoding="utf-8"))
        trades += d if isinstance(d, list) else d.get("trades", [])
    trades.sort(key=lambda x: x.get("time", "") or "")
    ob = defaultdict(list); out = []
    for tr in trades:
        bot, tok, ty = tr.get("bot_id"), tr.get("token"), (tr.get("type") or "").lower()
        if not bot or not tok:
            continue
        k = (bot, tok)
        if ty == "buy":
            ob[k].append({"buy": tr, "rem": 1.0, "peak": None, "close_t": None})
        elif ty == "sell" and ob[k]:
            x = ob[k][0]; fr = tr.get("sell_fraction")
            x["rem"] -= float(fr) if fr is not None else x["rem"]
            pk = tr.get("peak_pnl_pct")
            if pk is not None and (x["peak"] is None or float(pk) > x["peak"]):
                x["peak"] = float(pk)
            x["close_t"] = tr.get("time")
            if tr.get("fully_closed") or x["rem"] <= 0.01:
                if x["peak"] is not None:
                    em = x["buy"].get("entry_meta"); em = em if isinstance(em, dict) else x["buy"]
                    bt = x["buy"].get("time", "") or ""
                    key = (bot, tok, bt[:16])
                    if key not in seen:
                        seen.add(key)
                        out.append({"bot": bot, "tok": tok, "buy_t": bt,
                                    "buy_e": _epoch(bt), "close_e": _epoch(x["close_t"]),
                                    "peak": x["peak"], "ng": 1 if x["peak"] < 1.0 else 0,
                                    "f": em})
                ob[k].pop(0)
    return [c for c in out if c["buy_e"] is not None]


def add_track_record(comp):
    by_tok = defaultdict(list)
    for c in comp:
        by_tok[c["tok"]].append(c)
    for tok, cs in by_tok.items():
        cs.sort(key=lambda c: c["buy_e"])
        for i, c in enumerate(cs):
            # prior positions on this token that CLOSED strictly before this buy
            prior = [q for q in cs[:i]
                     if q["close_e"] is not None and q["close_e"] < c["buy_e"]]
            tr = {"tr_n_prior_total": len(prior),
                  "tr_n_prior_closed_6h": sum(1 for q in prior if c["buy_e"] - q["close_e"] <= 21600),
                  "tr_frac_prior_ng": (np.mean([q["ng"] for q in prior]) if prior else np.nan),
                  "tr_mean_prior_peak": (np.mean([q["peak"] for q in prior]) if prior else np.nan),
                  "tr_last_pnl": (prior[-1]["peak"] if prior else np.nan),
                  "tr_last_ng": (prior[-1]["ng"] if prior else np.nan),
                  "tr_mins_since_last": ((c["buy_e"] - prior[-1]["close_e"]) / 60.0 if prior else np.nan)}
            c["tr"] = tr
    return comp


def frame(comp, with_tr):
    cov = defaultdict(int)
    for c in comp:
        for k, v in c["f"].items():
            if _is_feat(k, v):
                cov[k] += 1
    base = sorted(k for k, n in cov.items() if n >= 0.30 * len(comp))
    trf = sorted(comp[0]["tr"].keys()) if with_tr else []
    feats = base + trf
    rows = []
    for c in comp:
        r = {k: (c["f"].get(k) if isinstance(c["f"].get(k), (int, float))
                 and not isinstance(c["f"].get(k), bool) else np.nan) for k in base}
        if with_tr:
            r.update(c["tr"])
        r["_ng"] = c["ng"]; r["_tok"] = c["tok"]; r["_day"] = c["buy_t"][:10]
        rows.append(r)
    return pd.DataFrame(rows), feats


def walk(df, feats):
    days = sorted(df["_day"].unique())
    Y = []; P = []; T = []
    for i in range(7, len(days)):
        tr = df[df["_day"].isin(days[i - 7:i])]; te = df[df["_day"] == days[i]]
        if len(te) < 15 or tr["_ng"].nunique() < 2:
            continue
        m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150,
                                           l2_regularization=2.0, min_samples_leaf=20, random_state=0)
        m.fit(tr[feats], tr["_ng"])
        P += list(m.predict_proba(te[feats])[:, 1]); Y += list(te["_ng"]); T += list(te["_tok"])
    g = pd.DataFrame({"t": T, "y": Y, "p": P}).groupby("t").agg(y=("y", "median"), p=("p", "mean"))
    g = g[(g["y"] == 0) | (g["y"] == 1)]
    return roc_auc_score(Y, P), roc_auc_score(g["y"], g["p"]), len(g)


def main():
    comp = add_track_record(pair_with_close(FILES))
    print(f"positions {len(comp)} | tokens {len(set(c['tok'] for c in comp))}")
    re_entries = sum(1 for c in comp if c["tr"]["tr_n_prior_total"] > 0)
    print(f"positions that are re-entries (>=1 prior closed): {re_entries} ({100*re_entries/len(comp):.0f}%)")

    df0, f0 = frame(comp, with_tr=False)
    df1, f1 = frame(comp, with_tr=True)
    a0_trade, a0_tok, n0 = walk(df0, f0)
    a1_trade, a1_tok, n1 = walk(df1, f1)
    print(f"\nBASELINE (entry_meta, {len(f0)} feat):     trade-AUC {a0_trade:.3f}  token-AUC {a0_tok:.3f}")
    print(f"+ TRACK-RECORD ({len(f1)} feat):            trade-AUC {a1_trade:.3f}  token-AUC {a1_tok:.3f}")
    print(f"LIFT:                                       trade {a1_trade-a0_trade:+.3f}  token {a1_tok-a0_tok:+.3f}")

    # importances of the track features (train on all, just to rank)
    m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150,
                                       l2_regularization=2.0, min_samples_leaf=20, random_state=0)
    from sklearn.inspection import permutation_importance
    Xtr = df1[f1].fillna(np.nan)
    m.fit(Xtr, df1["_ng"])
    pi = permutation_importance(m, Xtr, df1["_ng"], n_repeats=5, random_state=0, scoring="roc_auc")
    imp = sorted(zip(f1, pi.importances_mean), key=lambda x: -x[1])
    print("\ntop features by permutation importance (track-record marked *):")
    for f, v in imp[:15]:
        mark = " *" if f.startswith("tr_") else ""
        print(f"   {v:7.4f}  {f}{mark}")


if __name__ == "__main__":
    main()
