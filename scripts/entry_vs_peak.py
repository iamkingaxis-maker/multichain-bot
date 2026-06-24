#!/usr/bin/env python
"""Entry-mcap vs eventual-peak across our PUMPS.

Question (from the GACHA runner-tilt discussion): when our bots buy a token that
goes on to pump, where do we enter relative to its peak? If entries land early
(lots of upside left), the runner-tilt can capture the tail; if late (near the
top), even a perfect runner-tilt loses.

Method (price-based, == mcap since supply is fixed): for each token, reconstruct
the peak price we SAW = max over our positions of entry_price*(1+peak_pnl_pct/100).
"Our pumps" = tokens whose peak we saw > +30% (a real run, not the capped ~+15%
majority). For each entry on a pumping token:
    upside_left% = (token_peak / entry_price - 1) * 100   # how far it ran AFTER our entry
    entry_frac   = entry_price / token_peak               # 0 = bottom, 1.0 = at the peak
High upside_left / low entry_frac = we bought early (good for runner-tilt).
Low upside_left / high entry_frac = we bought near the top (runner-tilt can't help).

Excludes phantom ticks (peak_pnl_pct > 200 or phantom_scrubbed). Token-level
peak is a PROXY for the true ATH (it's the max WE saw while holding — undercounts
if every bot exited before the real top). Read-only.
"""
from __future__ import annotations
import sys, json, urllib.request, time, collections
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
PUMP_PEAK = 30.0   # token counts as a "pump" if we saw its peak run > +30%


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
    sells = [s for s in trades if s.get("type") == "sell"
             and isinstance(s.get("entry_price"), (int, float)) and s["entry_price"] > 0
             and isinstance(s.get("peak_pnl_pct"), (int, float))
             and not s.get("phantom_scrubbed")
             and s["peak_pnl_pct"] <= 200]   # drop phantom glitch ticks

    by_tok = collections.defaultdict(list)
    for s in sells:
        by_tok[s.get("token")].append(s)

    pumps = []
    for tok, ss in by_tok.items():
        peak_price = max(s["entry_price"] * (1 + s["peak_pnl_pct"]/100.0) for s in ss)
        peak_run = max(s["peak_pnl_pct"] for s in ss)   # the biggest run we saw
        if peak_run < PUMP_PEAK:
            continue
        entries = []
        for s in ss:
            upside_left = (peak_price / s["entry_price"] - 1) * 100
            entries.append(dict(upside_left=upside_left,
                                frac=s["entry_price"]/peak_price,
                                realized=s.get("pnl_pct")))
        pumps.append(dict(token=tok, n=len(ss), peak_run=peak_run,
                          peak_price=peak_price, entries=entries))

    pumps.sort(key=lambda p: -p["peak_run"])
    print(f"PUMPING tokens (we saw peak > +{PUMP_PEAK:.0f}%): {len(pumps)}")
    print(f"{'token':12} {'entries':>7} {'peak_run':>8} {'med_upside_left':>15} {'med_entry_frac':>14}")
    all_upside = []
    all_frac = []
    for p in pumps:
        ul = [e["upside_left"] for e in p["entries"]]
        fr = [e["frac"] for e in p["entries"]]
        all_upside += ul
        all_frac += fr
        print(f"{str(p['token'])[:12]:12} {p['n']:>7} {p['peak_run']:>+7.0f}% "
              f"{np.median(ul):>+14.0f}% {np.median(fr):>14.2f}")

    if all_upside:
        a = np.array(all_upside)
        print(f"\n=== across all entries on pumping tokens (n={len(a)}) ===")
        print(f"  upside-left-at-entry: median {np.median(a):+.0f}% | "
              f"p25 {np.percentile(a,25):+.0f}% | p75 {np.percentile(a,75):+.0f}%")
        for thr, lbl in [(100,"early (>100% left)"),(30,"mid (30-100% left)"),(10,"late (<10% left)")]:
            if thr == 100:
                frac = np.mean(a >= 100)
            elif thr == 30:
                frac = np.mean((a >= 10) & (a < 100))
            else:
                frac = np.mean(a < 10)
            print(f"  {lbl:22}: {100*frac:.0f}% of entries")
        print(f"  median entry_frac (0=bottom,1=peak): {np.median(all_frac):.2f}")
        print("\nREAD: high upside-left / low entry_frac = we buy EARLY (runner-tilt has tail")
        print("to capture). If most entries are 'late' (<10% left), the runner-tilt can't help —")
        print("the lever is entry TIMING, not the exit. (peak = max WE saw; true ATH may be higher.)")


if __name__ == "__main__":
    main()
