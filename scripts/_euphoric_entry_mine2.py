#!/usr/bin/env python
"""v2: entry_meta lives on the BUY. Pair buy(addr)->sell, attach buy.entry_meta
to the closed sell. Window = euphoric tape (2026-06-14 16:00+ UTC).
Winner/loser split + robust effect + quartile WR tables for top separators so we
get NUMERIC entry thresholds, not vibes. Also derive mcap from psych-level.
"""
from __future__ import annotations
import json, statistics as st, math
from collections import defaultdict

SRC = "_euphoric_mine.json"
WINDOW_START = "2026-06-14T16:00"

trades = json.load(open(SRC))
trades.sort(key=lambda t: t.get("time") or "")

open_pos = defaultdict(list)
closes = []
for t in trades:
    bot = t.get("bot_id"); addr = t.get("address") or t.get("token")
    ty = (t.get("type") or "").lower()
    if not bot or not addr:
        continue
    k = (bot, addr)
    if ty == "buy":
        open_pos[k].append(t)
    elif ty == "sell":
        buy = open_pos[k].pop(0) if open_pos[k] else None
        if t.get("pnl") is None:
            continue
        if (t.get("time") or "")[:16] < WINDOW_START:
            continue
        if buy is None:
            continue  # need entry features
        em = dict(buy.get("entry_meta") or {})
        # derive approximate mcap from nearest psych level + distance%
        psych = em.get("mcap_nearest_psych_level_usd")
        distpct = em.get("mcap_distance_to_psych_pct")
        if psych and distpct is not None:
            em["mcap_derived_usd"] = psych * (1 + distpct / 100.0)
        em["__pnl"] = float(t["pnl"])
        em["__win"] = float(t["pnl"]) > 0
        em["__bot"] = bot
        em["__addr"] = addr
        em["__peak"] = t.get("peak_pnl_pct")
        closes.append(em)

W = [c for c in closes if c["__win"]]
L = [c for c in closes if not c["__win"]]
n = len(closes)
print(f"PAIRED closes (have entry features) >= {WINDOW_START}: n={n}  "
      f"W={len(W)} L={len(L)} WR={100*len(W)/n:.1f}%  "
      f"net=${sum(c['__pnl'] for c in closes):+.2f} $/tr={sum(c['__pnl'] for c in closes)/n:+.3f}")
print(f"distinct tokens={len(set(c['__addr'] for c in closes))} distinct bots={len(set(c['__bot'] for c in closes))}")
print()


def vals(rows, f):
    return [r[f] for r in rows if isinstance(r.get(f), (int, float)) and not isinstance(r.get(f), bool)]


def robust_eff(wv, lv):
    if len(wv) < 3 or len(lv) < 3:
        return None
    wm, lm = st.median(wv), st.median(lv)
    allv = wv + lv
    m = st.median(allv)
    mad = st.median([abs(x - m) for x in allv]) or st.pstdev(allv)
    scale = (mad * 1.4826) if mad else 1e-9
    return (wm - lm) / scale


FEATS = ["shape_90m_drawdown_from_max_pct", "shape_60m_drawdown_from_max_pct",
         "shape_30m_drawdown_from_max_pct", "shape_90m_chg_pct",
         "shape_90m_max_over_entry_pct", "shape_90m_mins_since_max",
         "shape_90m_pump_bleed_score", "shape_90m_range_pct",
         "bs_m5", "net_flow_60s_usd", "net_flow_60s_imbalance",
         "net_flow_5m_usd", "net_flow_5m_imbalance",
         "net_flow_15s_usd", "net_flow_15s_imbalance",
         "pc_h1", "pc_h24", "pc_h1_change_since_lookback",
         "sol_pc_h1", "sol_pc_h24", "btc_pc_h1",
         "vol_5m_burst_vs_h1", "vol_h1_accel_vs_h6", "vol_5m_proj_hr_usd",
         "lifecycle_age_hours", "dev_baseline_age_hours",
         "entry_volume_h24_usd", "mcap_derived_usd", "mcap_distance_to_psych_pct",
         "buy_sell_volume_imbalance", "large_buyer_volume_pct",
         "smart_wallet_volume_pct", "top5_buyer_volume_pct",
         "mtf_vol_align", "1m_volume_spike", "regime_dip_breadth_pct",
         "1s_vol_decay_120s", "5m_vol_decay", "token_volatility_h24_pct",
         "chart_buyvol_ratio_60m", "5m_vol_decay"]

rows = []
for f in dict.fromkeys(FEATS):
    wv, lv = vals(W, f), vals(L, f)
    if len(wv) < 5 or len(lv) < 5:
        continue
    eff = robust_eff(wv, lv)
    rows.append((abs(eff or 0), eff or 0, f, st.median(wv), st.median(lv), len(wv), len(lv)))
rows.sort(reverse=True)
print(f"{'feature':38s}{'nW':>4}{'nL':>4}{'win_med':>13}{'loss_med':>13}{'eff':>7}")
for _, eff, f, wm, lm, nw, nl in rows:
    print(f"{f:38s}{nw:>4}{nl:>4}{wm:>13.4g}{lm:>13.4g}{eff:>7.2f}")

# ---- Quartile WR tables for the strongest clean separators ----
print("\n" + "=" * 78)
print("QUARTILE WR TABLES (numeric thresholds) — top separators")
print("=" * 78)
TOP = [f for _, _, f, *_ in rows[:10]]
# always include the directly-actionable momentum features
for must in ["pc_h1", "sol_pc_h1", "shape_90m_drawdown_from_max_pct", "net_flow_60s_usd", "bs_m5", "vol_5m_burst_vs_h1"]:
    if must not in TOP:
        TOP.append(must)

allrows = closes
for f in TOP:
    fv = sorted(c[f] for c in allrows if isinstance(c.get(f), (int, float)) and not isinstance(c.get(f), bool))
    if len(fv) < 20:
        continue
    qs = [fv[int(len(fv) * p)] for p in (0.25, 0.5, 0.75)]
    print(f"\n{f}  (cov {len(fv)}/{n})  quartile cuts: {[round(x,3) for x in qs]}")
    edges = [(-math.inf, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], math.inf)]
    labs = [f"Q1 <={qs[0]:.3g}", f"Q2 {qs[0]:.3g}..{qs[1]:.3g}",
            f"Q3 {qs[1]:.3g}..{qs[2]:.3g}", f"Q4 >{qs[2]:.3g}"]
    for (lo, hi), lab in zip(edges, labs):
        sub = [c for c in allrows if isinstance(c.get(f), (int, float)) and not isinstance(c.get(f), bool) and lo < c[f] <= hi]
        if not sub:
            continue
        w = sum(1 for c in sub if c["__win"])
        dpt = sum(c["__pnl"] for c in sub) / len(sub)
        print(f"   {lab:26s} n={len(sub):3d}  WR={100*w/len(sub):3.0f}%  $/tr={dpt:+6.2f}  net={sum(c['__pnl'] for c in sub):+8.2f}")
