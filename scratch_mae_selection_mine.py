#!/usr/bin/env python3
"""SHALLOW-vs-DEEP-MAE selection mine (2026-06-30).

The resting-bid sim proved: tokens that DON'T dip after entry (shallow MAE) are
the winners (+9.5%); tokens that keep falling (deep MAE) are losers. So the lever
is SELECTION: which ENTRY signal (known at decision time, in entry_meta) predicts
shallow MAE / won't-keep-falling?

Target y=1 if the trade did NOT keep falling (mae_pct > -3%); y=0 if it cratered
(mae_pct <= -3%). For each numeric entry_meta feature we compute a rank-AUC of the
feature predicting y (0.5=no signal, >0.6 or <0.4 = real separation), trade-level
with token coverage reported. Then we cross-check the top features against realized
pnl (token-level) so a MAE-predictor that doesn't lift pnl is flagged.
Honest: fleet buys same token ~12-22x so trade-level n is correlated -> token_n is
the real n; treat <30-token findings as directional only.
"""
import json, statistics as st, math
from collections import defaultdict

d = json.load(open("_full_trades.json"))

def fl(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None

# entry_meta lives on BUY records — build per-address time-sorted buy list so each
# sell can be matched to the buy that opened it (nearest preceding buy time).
buys_by_addr = defaultdict(list)
for t in d:
    if t.get("type") != "buy":
        continue
    em = t.get("entry_meta")
    if isinstance(em, dict) and em:
        buys_by_addr[t.get("address") or t.get("token")].append((str(t.get("time", "")), em))
for a in buys_by_addr:
    buys_by_addr[a].sort(key=lambda x: x[0])

def match_em(addr, sell_time):
    lst = buys_by_addr.get(addr)
    if not lst:
        return {}
    st_ = str(sell_time or "")
    # nearest buy with time <= sell time; else the earliest buy
    cand = [em for (tm, em) in lst if tm <= st_]
    return cand[-1] if cand else lst[0][1]

recs = []
for t in d:
    if t.get("type") != "sell":
        continue
    mae = fl(t.get("mae_pct")); pnl = fl(t.get("pnl_pct"))
    if mae is None or pnl is None:
        continue
    em = match_em(t.get("address") or t.get("token"), t.get("time"))
    recs.append({"addr": t.get("address") or t.get("token"),
                 "mae": mae, "pnl": pnl, "em": em,
                 "y": 1 if mae > -3.0 else 0})
N = len(recs)
shallow = sum(r["y"] for r in recs)
print(f"=== MAE SELECTION MINE | {N} trades | shallow(mae>-3%)={shallow} ({100*shallow/N:.0f}%) deep={N-shallow} ===")
print("(y=1 shallow/won't-keep-falling=WINNER per sim; AUC>0.6 or <0.4 = real separation; token_n is the honest n)\n")

# collect candidate numeric features with coverage
feat_vals = defaultdict(list)   # feat -> list of (val, y, addr, pnl)
for r in recs:
    for k, v in r["em"].items():
        fv = fl(v)
        if fv is None:
            continue
        feat_vals[k].append((fv, r["y"], r["addr"], r["pnl"]))

def auc(pairs):
    """rank-AUC of feature value predicting y. pairs: (val,y)."""
    pos = [v for v, y in pairs if y == 1]
    neg = [v for v, y in pairs if y == 0]
    if not pos or not neg:
        return None
    # Mann-Whitney rank approach
    allv = sorted(set(v for v, _ in pairs))
    # rank by value
    ranks = {}
    s = sorted(pairs, key=lambda x: x[0])
    # average ranks for ties
    i = 0; rk = 1
    while i < len(s):
        j = i
        while j < len(s) and s[j][0] == s[i][0]:
            j += 1
        avg = (rk + (rk + (j - i) - 1)) / 2.0
        for k in range(i, j):
            ranks[k] = avg
        rk += (j - i); i = j
    rsum_pos = sum(ranks[idx] for idx, (v, y) in enumerate(s) if y == 1)
    npos, nneg = len(pos), len(neg)
    u = rsum_pos - npos * (npos + 1) / 2.0
    return u / (npos * nneg)

rows = []
for feat, vals in feat_vals.items():
    if len(vals) < 250:
        continue
    toks = len(set(a for _, _, a, _ in vals))
    if toks < 25:
        continue
    a = auc([(v, y) for v, y, _, _ in vals])
    if a is None:
        continue
    rows.append((feat, a, len(vals), toks))

rows.sort(key=lambda x: abs(x[1] - 0.5), reverse=True)
print(f"{'feature':38} {'AUC':>6} {'|sep|':>6} {'trades':>7} {'toks':>5} dir")
for feat, a, n, toks in rows[:25]:
    direction = "HIGH->shallow(win)" if a > 0.5 else "LOW->shallow(win)"
    print(f"{feat:38} {a:6.3f} {abs(a-0.5):6.3f} {n:7d} {toks:5d} {direction}")

# cross-check the top features against realized PnL (token-level), + known signals
print("\n=== top features: realized PnL in HIGH vs LOW half (token-level) ===")
known = ["median_buy_size_usd", "pc_h6", "pc_h1", "1m_consec_red", "rsi_5m",
         "rsi_15m", "net_flow_15s_imbalance", "1s_close_pos_60s", "liquidity_usd",
         "unique_buyers_n", "1m_volume_spike", "1s_bottom_score"]
check = [f for f, *_ in rows[:12]] + [k for k in known if k in feat_vals]
seen = set()
for feat in check:
    if feat in seen:
        continue
    seen.add(feat)
    vals = feat_vals[feat]
    med = st.median([v for v, _, _, _ in vals])
    # token-level mean pnl for high vs low halves
    hi = defaultdict(list); lo = defaultdict(list)
    for v, y, a, pnl in vals:
        (hi if v >= med else lo)[a].append(pnl)
    if not hi or not lo:
        continue
    hitm = st.mean([st.mean(x) for x in hi.values()])
    lotm = st.mean([st.mean(x) for x in lo.values()])
    arow = next((r for r in rows if r[0] == feat), None)
    aucs = f"AUC={arow[1]:.3f}" if arow else "AUC=n/a"
    print(f"{feat:34} med={med:>10.3g} | HIGH tok_pnl {hitm:+6.2f}% vs LOW {lotm:+6.2f}%  gap {hitm-lotm:+6.2f}pp  {aucs}")
