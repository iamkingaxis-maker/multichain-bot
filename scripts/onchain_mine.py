#!/usr/bin/env python
"""Mine ON-CHAIN entry features for never-green signal (held-out).

The scorer already uses all entry_meta features; this isolates the ON-CHAIN subset
(dev wallet, holder concentration, makers/snipers, LP, supply) to answer:
  1. which on-chain features carry never-green signal (held-out, token-deduped)?
  2. coverage — a high-signal but sparsely-populated feature is UNDER-exploited
     (the fix is better scanner on-chain fetching, not a new gate);
  3. are on-chain features ORTHOGONAL to candle/flow (does an on-chain-only model
     have signal, and does combining lift over candle-only)?
  4. top on-chain COMPOUNDS (per reference_onchain_compound_breakthrough).
"""
from __future__ import annotations
import sys, os
from collections import defaultdict
import statistics as st
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "scripts")
from ps_scan import load_completed, _in
from core.ng_scorer import _is_feat

FILES = [".trades_now.json", ".overnight_trades.json", ".watch7h/val_wide.json",
         "ep_verify.json", ".t_big.json", "trades_dump_candidates.json",
         "trades_dump.json", "trades_local_dump.json"]
TRAIN = ("2026-05-12", "2026-05-24"); TEST = ("2026-05-27", "2026-05-31")

ONCHAIN = ("dev_", "holder", "top10", "top_holder", "top_buy_makers", "unique_buyer",
           "bundl", "rugcheck", "lp_", "mint", "freeze", "snip", "insider", "creator",
           "supply", "concentration", "burn", "lock", "maker", "_holders", "whale_",
           "smart_money", "fresh_wallet", "bot_wallet", "wash")


def is_onchain(name):
    n = name.lower()
    return any(s in n for s in ONCHAIN)


def main():
    comp = load_completed(FILES)
    for c in comp:
        c["ng"] = 1 if (c["peak"] is not None and c["peak"] < 1.0) else 0
    comp = [c for c in comp if c["peak"] is not None]
    # feature universe (production filter), split on-chain vs other
    cov = defaultdict(int)
    for c in comp:
        for k, v in c["f"].items():
            if _is_feat(k, v):
                cov[k] += 1
    feats = [k for k, n in cov.items() if n >= 0.30 * len(comp)]
    onchain = sorted(f for f in feats if is_onchain(f))
    other = sorted(f for f in feats if not is_onchain(f))
    print(f"positions {len(comp)} | NG-rate {np.mean([c['ng'] for c in comp]):.2f}")
    print(f"features: {len(feats)} usable | on-chain {len(onchain)} | candle/flow {len(other)}")

    # ---- univariate held-out NG separation per on-chain feature (token-deduped) ----
    def tok_med(rows, f):
        d = defaultdict(list)
        for c in rows:
            v = c["f"].get(f)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                d[c["tok"]].append(v)
        ng = defaultdict(list)
        for c in rows:
            ng[c["tok"]].append(c["ng"])
        out = []
        for t in d:
            out.append((st.median(d[t]), 1 if st.median(ng[t]) >= 0.5 else 0))
        return out
    tr = [c for c in comp if _in(c, *TRAIN)]; te = [c for c in comp if _in(c, *TEST)]
    print(f"\n{'on-chain feature':34}{'cov%':>6}{'effTR':>7}{'effTE':>7}  ng_med/green_med(TEST)")
    rows = []
    for f in onchain:
        def eff(rws):
            xs = tok_med(rws, f)
            ng = [v for v, y in xs if y == 1]; gr = [v for v, y in xs if y == 0]
            if len(ng) < 5 or len(gr) < 5:
                return None, None, None
            allv = ng + gr; m = st.median(allv)
            mad = st.median([abs(x - m) for x in allv]) or st.pstdev(allv) or 1e-9
            return (st.median(ng) - st.median(gr)) / (mad * 1.4826), st.median(ng), st.median(gr)
        et, ngm, grm = eff(te); etr, _, _ = eff(tr)
        if et is None or etr is None:
            continue
        rows.append((f, 100 * cov[f] / len(comp), etr, et, ngm, grm))
    # robust = same sign both windows, sorted by min|eff|
    rows = [r for r in rows if (r[2] > 0) == (r[3] > 0)]
    rows.sort(key=lambda r: -min(abs(r[2]), abs(r[3])))
    for f, cv, etr, et, ngm, grm in rows[:20]:
        print(f"{f:34}{cv:>5.0f}%{etr:>7.2f}{et:>7.2f}  {ngm:.3g}/{grm:.3g}")

    # ---- orthogonality: walk-forward AUC on-chain-only vs candle-only vs combined ----
    df = pd.DataFrame([{**{k: (c['f'].get(k) if isinstance(c['f'].get(k), (int, float))
                              and not isinstance(c['f'].get(k), bool) else np.nan) for k in feats},
                        '_ng': c['ng'], '_tok': c['tok'], '_day': c['t'][:10]} for c in comp])

    def walk(cols):
        days = sorted(df['_day'].unique()); Y = []; P = []; T = []
        for i in range(7, len(days)):
            a = df[df['_day'].isin(days[i-7:i])]; b = df[df['_day'] == days[i]]
            if len(b) < 15 or a['_ng'].nunique() < 2:
                continue
            m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150,
                                               l2_regularization=2.0, min_samples_leaf=20, random_state=0)
            m.fit(a[cols], a['_ng']); P += list(m.predict_proba(b[cols])[:, 1]); Y += list(b['_ng']); T += list(b['_tok'])
        g = pd.DataFrame({'t': T, 'y': Y, 'p': P}).groupby('t').agg(y=('y', 'median'), p=('p', 'mean'))
        g = g[(g['y'] == 0) | (g['y'] == 1)]
        return roc_auc_score(g['y'], g['p']), len(g)
    a_oc, n = walk(onchain); a_other, _ = walk(other); a_all, _ = walk(feats)
    print(f"\nwalk-forward token-AUC (n={n} tokens):")
    print(f"  on-chain only ({len(onchain)} feat):   {a_oc:.3f}")
    print(f"  candle/flow only ({len(other)} feat):  {a_other:.3f}")
    print(f"  combined ({len(feats)} feat):          {a_all:.3f}")
    print(f"  => on-chain adds {a_all - a_other:+.3f} over candle/flow alone (orthogonality)")


if __name__ == "__main__":
    main()
