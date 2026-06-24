#!/usr/bin/env python
"""Mine fleet's OWN trades for entry patterns winning in the SOL-pump/euphoric
regime (2026-06-14 ~16:00+ UTC). Two layers:

  Layer A (large-n): top-level sell fields present on ALL closed sells
     - entry_age_hours, entry_market_cap_usd, entry_volume_h1_usd, realized_slippage_pct
  Layer B (rich, thin-n): entry_meta scanner features (only on ~21 instrumented sells)
     - shape_90m_drawdown_from_max_pct, bs_m5, net_flow_60s_usd/imbalance,
       pc_h1, pc_h24, vol_5m_burst_vs_h1, etc.

Split SELLS with real closed pnl into winners (pnl>0) vs losers (pnl<=0).
For each numeric feature report median(win) vs median(loss) with n on each side
and a robust standardized effect (MAD-based). Flag thin / low separation.
"""
from __future__ import annotations
import json, statistics as st, sys
from collections import defaultdict, Counter

SRC = "_euphoric_mine.json"
WINDOW_START = "2026-06-14T16:00"   # last ~6.5h euphoric tape

trades = json.load(open(SRC))
trades.sort(key=lambda t: t.get("time") or "")

# --- pair buys -> sells per (bot, address) to attach entry_meta to the close ---
# We key entry by ADDRESS (memory: always key per-token state by ADDRESS).
open_pos = defaultdict(list)   # (bot, addr) -> list of buy dicts
closes = []                    # one row per completed sell-with-pnl in window

for t in trades:
    bot = t.get("bot_id")
    addr = t.get("address") or t.get("token")
    ty = (t.get("type") or "").lower()
    if not bot or not addr:
        continue
    k = (bot, addr)
    if ty == "buy":
        open_pos[k].append(t)
    elif ty == "sell":
        buy = open_pos[k].pop(0) if open_pos[k] else None
        pnl = t.get("pnl")
        if pnl is None:
            continue
        tm = t.get("time") or ""
        if tm[:16] < WINDOW_START:    # only the euphoric window
            continue
        em = (buy or {}).get("entry_meta") or t.get("entry_meta") or {}
        row = {
            "bot": bot, "addr": addr, "tok": t.get("token"),
            "time": tm, "pnl": float(pnl),
            "pnl_pct": t.get("pnl_pct"), "peak": t.get("peak_pnl_pct"),
            "win": float(pnl) > 0,
            # Layer A top-level
            "entry_age_hours": t.get("entry_age_hours"),
            "entry_market_cap_usd": t.get("entry_market_cap_usd"),
            "entry_volume_h1_usd": t.get("entry_volume_h1_usd"),
            "realized_slippage_pct": t.get("realized_slippage_pct"),
            "max_drawdown_pct": t.get("max_drawdown_pct"),
            "em": em,
        }
        closes.append(row)

W = [c for c in closes if c["win"]]
L = [c for c in closes if not c["win"]]
print(f"WINDOW >= {WINDOW_START}  closed sells={len(closes)}  "
      f"winners={len(W)}  losers={len(L)}  "
      f"fleetWR={100*len(W)/len(closes):.1f}%  "
      f"net=${sum(c['pnl'] for c in closes):.2f}  $/tr=${sum(c['pnl'] for c in closes)/len(closes):.3f}")
print(f"distinct tokens: {len(set(c['addr'] for c in closes))}  "
      f"distinct bots: {len(set(c['bot'] for c in closes))}")
print()

# bot-level breakdown (which bots are even firing in this window)
byb = defaultdict(list)
for c in closes:
    byb[c["bot"]].append(c)
print("=== BOT ACTIVITY IN WINDOW (closes, WR, $/tr) ===")
for b, cs in sorted(byb.items(), key=lambda kv: -len(kv[1])):
    w = sum(1 for c in cs if c["win"])
    print(f"  {b:34s} n={len(cs):3d} WR={100*w/len(cs):3.0f}% $/tr={sum(c['pnl'] for c in cs)/len(cs):+6.2f} net={sum(c['pnl'] for c in cs):+8.2f}")
print()


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def robust_eff(wv, lv):
    wv = [x for x in wv if x is not None]
    lv = [x for x in lv if x is not None]
    if len(wv) < 3 or len(lv) < 3:
        return None
    wm, lm = st.median(wv), st.median(lv)
    allv = wv + lv
    m = st.median(allv)
    mad = st.median([abs(x - m) for x in allv]) or st.pstdev(allv)
    scale = (mad * 1.4826) if mad else 1e-9
    return (wm - lm) / scale


def layer_report(name, feats, getter):
    print("=" * 90)
    print(f"LAYER {name}")
    print("=" * 90)
    rows = []
    for f in feats:
        wv = [getter(c, f) for c in W]
        lv = [getter(c, f) for c in L]
        wvn = [x for x in wv if x is not None]
        lvn = [x for x in lv if x is not None]
        if len(wvn) < 3 or len(lvn) < 3:
            continue
        eff = robust_eff(wv, lv)
        rows.append({
            "f": f, "wmed": st.median(wvn), "lmed": st.median(lvn),
            "nw": len(wvn), "nl": len(lvn), "eff": eff or 0.0,
        })
    rows.sort(key=lambda r: -abs(r["eff"]))
    print(f"{'feature':40s}{'nW':>4}{'nL':>4}{'win_med':>14}{'loss_med':>14}{'eff':>8}")
    for r in rows:
        print(f"{r['f']:40s}{r['nw']:>4}{r['nl']:>4}{r['wmed']:>14.4g}{r['lmed']:>14.4g}{r['eff']:>8.2f}")
    print()
    return rows


# Layer A: top-level (large n)
A_FEATS = ["entry_age_hours", "entry_market_cap_usd", "entry_volume_h1_usd",
           "realized_slippage_pct", "max_drawdown_pct"]
layer_report("A — top-level fields (full sell set)", A_FEATS, lambda c, f: c.get(f))

# Layer B: rich entry_meta (thin n) — the task-named features + a few momentum ones
B_FEATS = ["shape_90m_drawdown_from_max_pct", "shape_60m_drawdown_from_max_pct",
           "shape_30m_drawdown_from_max_pct", "shape_90m_chg_pct",
           "shape_90m_max_over_entry_pct", "shape_90m_mins_since_max",
           "shape_90m_pump_bleed_score",
           "bs_m5", "net_flow_60s_usd", "net_flow_60s_imbalance",
           "net_flow_5m_usd", "net_flow_5m_imbalance",
           "net_flow_15s_usd", "net_flow_15s_imbalance",
           "pc_h1", "pc_h24", "pc_h1_change_since_lookback",
           "sol_pc_h1", "sol_pc_h24", "btc_pc_h1",
           "vol_5m_burst_vs_h1", "vol_h1_accel_vs_h6", "vol_5m_proj_hr_usd",
           "lifecycle_age_hours", "entry_volume_h24_usd",
           "buy_sell_volume_imbalance", "large_buyer_volume_pct",
           "smart_wallet_volume_pct", "top5_buyer_volume_pct",
           "mtf_vol_align", "1m_volume_spike", "regime_dip_breadth_pct",
           "1s_vol_decay_120s", "5m_vol_decay", "token_volatility_h24_pct"]
layer_report("B — rich entry_meta (thin n ~21)", B_FEATS, lambda c, f: c["em"].get(f))
