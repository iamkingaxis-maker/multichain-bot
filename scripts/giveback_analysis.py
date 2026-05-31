#!/usr/bin/env python
"""Quantify the give-back / loss-magnitude leak across realized exits, and
estimate how much a tighter trail (the tight-exit A/B: trail_pp 1.5 vs prod 3.0)
would recover.

Mission-2 asymmetry lever. The expectancy problem is avg_loss ~2x avg_win.
The scorer-sizing table showed even best-entry (Q1, 87% WR, 0% never-green)
trades have avg_loss -11.65% -- good entries that went GREEN then reversed hard
to the stop. That's a give-back problem, not an entry problem -> exit RESCUE
territory (feedback_rescue_over_block), which the tight-exit A/B targets.

For every realized SELL with peak_pnl_pct available:
  give_back = peak_pnl_pct - pnl_pct        (how much of the peak was surrendered)
  capture   = pnl_pct / peak_pnl_pct        (fraction of peak kept, peak>0)
Segments: winners vs losers; "went-green-then-reversed" losers (peak>=+3 but
realized<=0) are the rescue-addressable cohort. Then a counterfactual: if a
trail at peak*(1) - TRAIL_PP had fired, realized ~= max(stop, peak - TRAIL_PP)
for trades whose peak exceeded TRAIL_PP -- compare realized-now vs trail-1.5.
Read-only.
"""
from __future__ import annotations
import sys, json, urllib.request, time

BASE = "https://gracious-inspiration-production.up.railway.app"
TRAIL_NEW = 1.5    # tight-exit A/B
TRAIL_PROD = 3.0   # current champion prod
HARD_STOP = -15.0


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def main():
    trades = _get("/api/trades?limit=5000&full=1")
    sells = [x for x in trades if x.get("type") == "sell"
             and x.get("peak_pnl_pct") is not None and x.get("pnl_pct") is not None]
    print(f"realized sells with peak data: {len(sells)}")
    if not sells:
        return

    def seg(rows, label):
        if not rows:
            print(f"{label:34} n=0"); return
        gb = [r["peak_pnl_pct"] - r["pnl_pct"] for r in rows]
        cap = [r["pnl_pct"]/r["peak_pnl_pct"] for r in rows if r["peak_pnl_pct"] > 0.5]
        pk = [r["peak_pnl_pct"] for r in rows]
        pn = [r["pnl_pct"] for r in rows]
        print(f"{label:34} n={len(rows):>4} | peak {sum(pk)/len(pk):+5.2f} "
              f"realized {sum(pn)/len(pn):+5.2f} | give-back {sum(gb)/len(gb):5.2f}pp "
              f"| capture {100*sum(cap)/len(cap) if cap else 0:4.0f}%")

    winners = [r for r in sells if r["pnl_pct"] > 0]
    losers = [r for r in sells if r["pnl_pct"] <= 0]
    seg(sells, "ALL")
    seg(winners, "WINNERS")
    seg(losers, "LOSERS")

    # rescue-addressable: peaked >=+3% then realized <= 0 (went green, reversed)
    rescue = [r for r in losers if r["peak_pnl_pct"] >= 3.0]
    seg(rescue, "RESCUE-ABLE losers (peak>=+3, real<=0)")
    print(f"\nrescue-able = {len(rescue)}/{len(losers)} losers "
          f"({100*len(rescue)/max(len(losers),1):.0f}%) went green then reversed to a loss")

    # counterfactual trail: realized_trail = max(HARD_STOP, peak - TRAIL) for
    # trades whose peak exceeded the trail band (otherwise unchanged: never
    # reached a trailing-eligible peak). Compare prod(3.0) vs new(1.5).
    def cf(trail):
        tot = 0.0; changed = 0
        for r in sells:
            pk, real = r["peak_pnl_pct"], r["pnl_pct"]
            if pk > trail:
                # a trail would have exited at ~peak-trail (bounded below by stop)
                new_real = max(HARD_STOP, pk - trail)
                # only counts as a change if it BEATS what actually happened
                if new_real > real + 0.01:
                    changed += 1
                tot += max(new_real, real)  # trail can't do worse than a later stop... be conservative: take better
            else:
                tot += real
        return tot/len(sells), changed

    base = sum(r["pnl_pct"] for r in sells)/len(sells)
    ev_prod, ch_prod = cf(TRAIL_PROD)
    ev_new, ch_new = cf(TRAIL_NEW)
    print(f"\n--- counterfactual trail (mean realized pnl%, conservative: trail only when it beats actual) ---")
    print(f"actual realized:        {base:+.2f}%")
    print(f"trail_pp={TRAIL_PROD} (prod):    {ev_prod:+.2f}%  (would improve {ch_prod} trades)")
    print(f"trail_pp={TRAIL_NEW} (A/B):     {ev_new:+.2f}%  (would improve {ch_new} trades)")
    print(f"tighter-trail marginal lift: {ev_new-ev_prod:+.2f}pp/trade over prod trail")
    print("NOTE: upper-bound estimate — assumes the trail fires cleanly at peak-pp on the")
    print("realtime feed; real fills give back the poll-granularity gap (see feed analysis).")


if __name__ == "__main__":
    main()
