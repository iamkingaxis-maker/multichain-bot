#!/usr/bin/env python
"""Validate an EXTENDED-ENTRY gate on realized outcomes (feedback_buying_too_high).

The green-give-back differential (giveback_entry_diff.py) showed trades that went
green then gave it all back were EXTENDED chases: high pc_h24 (already pumped),
above VWAP, on a 90m up-move, in a sell-burst frenzy. HELD winners were genuine
dips. This is the user's #1 recurring complaint ("buys extended runners near
local tops"). Here we test whether an extension gate raises realized WR/EV
across ALL realized trades, and AUDIT winner-kill (don't cut the held winners).

Each candidate gate BLOCKS an entry if extended. We compare realized WR/EV of
BLOCKED vs ALLOWED, and the kill-rate on actual >=+10% realized winners (must be
low). Token-deduped (FCM). Read-only; ship as SHADOW first.
"""
from __future__ import annotations
import json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def fv(em, *names):
    for n in names:
        v = em.get(n)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def main():
    trades = _get("/api/trades?limit=5000&full=1")
    buys = [x for x in trades if x.get("type") == "buy" and x.get("entry_meta")]
    sells = [x for x in trades if x.get("type") == "sell"]
    sidx = {}
    for s in sells:
        sidx.setdefault((s.get("bot_id"), s.get("address")), []).append(s)

    rows = []
    for b in buys:
        scand = sidx.get((b.get("bot_id"), b.get("address")))
        if not scand:
            continue
        s = scand[0]
        if s.get("pnl_pct") is None:
            continue
        em = b["entry_meta"]
        rows.append(dict(
            token=b.get("token"), pnl=float(s["pnl_pct"]),
            peak=s.get("peak_pnl_pct"),
            pc_h24=fv(em, "pc_h24"),
            vwap=fv(em, "pct_above_vwap_h24"),
            m90=fv(em, "shape_90m_chg_pct"),
            tdens=fv(em, "trade_density_30s_vs_5m"),
            sburst=fv(em, "sell_burst_30s_count"),
        ))

    # token-dedup: median-pnl row per token
    bytok = {}
    for r in rows:
        bytok.setdefault(r["token"], []).append(r)
    ded = []
    for t, g in bytok.items():
        g.sort(key=lambda x: x["pnl"])
        ded.append(g[len(g)//2])
    print(f"realized trades {len(rows)} | unique tokens {len(ded)}")
    base_wr = 100*np.mean([r["pnl"] > 0 for r in ded])
    base_ev = np.mean([r["pnl"] for r in ded])
    winners10 = [r for r in ded if r["pnl"] >= 10]
    print(f"baseline: WR {base_wr:.0f}% EV {base_ev:+.2f}% | >=+10% winners: {len(winners10)} tokens\n")

    def test_gate(name, pred):
        blocked = [r for r in ded if pred(r)]
        allowed = [r for r in ded if not pred(r)]
        if not blocked:
            print(f"{name:48} blocks 0"); return
        bwr = 100*np.mean([r["pnl"] > 0 for r in blocked])
        bev = np.mean([r["pnl"] for r in blocked])
        awr = 100*np.mean([r["pnl"] > 0 for r in allowed]) if allowed else 0
        aev = np.mean([r["pnl"] for r in allowed]) if allowed else 0
        killed = [r for r in winners10 if pred(r)]
        killrate = 100*len(killed)/max(len(winners10), 1)
        print(f"{name:48} blk={len(blocked):>3} | BLOCKED WR {bwr:>3.0f}% EV {bev:>+6.2f}% "
              f"| kept WR {awr:>3.0f}% EV {aev:>+5.2f}% | winner-kill {killrate:>3.0f}% ({len(killed)})")

    print("--- single-feature extension gates (BLOCK if condition true) ---")
    test_gate("pc_h24 >= 80 (already pumped 80%+)", lambda r: r["pc_h24"] is not None and r["pc_h24"] >= 80)
    test_gate("pc_h24 >= 200", lambda r: r["pc_h24"] is not None and r["pc_h24"] >= 200)
    test_gate("pct_above_vwap_h24 >= 5", lambda r: r["vwap"] is not None and r["vwap"] >= 5)
    test_gate("pct_above_vwap_h24 >= 10", lambda r: r["vwap"] is not None and r["vwap"] >= 10)
    test_gate("shape_90m_chg_pct >= 5 (90m up-chase)", lambda r: r["m90"] is not None and r["m90"] >= 5)
    test_gate("trade_density_30s_vs_5m >= 1.5 (frenzy)", lambda r: r["tdens"] is not None and r["tdens"] >= 1.5)
    test_gate("sell_burst_30s_count >= 2", lambda r: r["sburst"] is not None and r["sburst"] >= 2)

    print("\n--- compound extension gates ---")
    def ext_any(r):
        c = 0
        if r["pc_h24"] is not None and r["pc_h24"] >= 200: c += 1
        if r["vwap"] is not None and r["vwap"] >= 10: c += 1
        if r["m90"] is not None and r["m90"] >= 5: c += 1
        return c
    test_gate("EXTENDED (pc_h24>=200 OR vwap>=10 OR 90m>=5)", lambda r: ext_any(r) >= 1)
    test_gate("EXTENDED x2 (>=2 of the three)", lambda r: ext_any(r) >= 2)
    test_gate("pc_h24>=200 AND vwap>=10", lambda r: r["pc_h24"] is not None and r["pc_h24"] >= 200 and r["vwap"] is not None and r["vwap"] >= 10)

    print("\nNOTE: token-deduped. Want BLOCKED EV << kept EV AND low winner-kill. Thin —")
    print("ship the survivor as a measure-only SHADOW + phantom parity, forward-validate.")


if __name__ == "__main__":
    main()
