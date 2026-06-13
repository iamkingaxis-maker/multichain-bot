"""P0: decode the unlabeled sensor-panel wallets and propose archetype labels.

Outputs per wallet: trips, WR, hold distribution, sizing style, time-box
signature, win/loss medians — plus a PROPOSED archetype from the decoded
taxonomy (the five wallet archetypes + scalper/sprayer):

  time_boxer    losers cluster at one duration (Dw5 signature)
  scalper       median hold < 10min, high frequency
  swing         median hold > 4h
  surgical      WR >= 0.70 with tiny median loss (|loss| <= 8%)
  thesis_holder conviction (variable) sizing + spread holds + sells winners
  conviction    variable sizing, otherwise unclassified
  sprayer       fixed sizing, otherwise unclassified

Proposals are LEADS — eyeball before writing config/sensor_panel.json.
Usage: python scripts/panel_label_batch.py [sigs=300]
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from wallet_decode import trade_map


def decode_metrics(addr: str, sigs: int):
    tok = trade_map(addr, sigs)
    trips = []
    sizes = []
    for m, r in tok.items():
        if not r["buys"]:
            continue
        sizes.append(sum(b[1] for b in r["buys"]))
        if not r["sells"] or not r["spent"]:
            continue
        b0 = min(b[0] for b in r["buys"])
        s1 = max(s[0] for s in r["sells"])
        trips.append((max(0, s1 - b0), (r["recv"] / r["spent"] - 1) * 100))
    if len(trips) < 8:
        return None
    holds = sorted(h for h, _ in trips)
    rets = [x for _, x in trips]
    wins = sorted(x for x in rets if x > 0)
    losses = sorted(x for x in rets if x <= 0)
    med_sz = statistics.median(sizes) if sizes else 0
    fixed = (statistics.pstdev(sizes) / med_sz < 0.15) if med_sz else False
    lh = sorted(h for h, x in trips if x < 0)
    timebox = False
    if len(lh) >= 5:
        lmed = lh[len(lh) // 2]
        timebox = sum(1 for h in lh if abs(h - lmed) < 600) / len(lh) >= 0.6
    return {
        "n": len(trips),
        "wr": round(len(wins) / len(rets), 2),
        "med_hold_min": round(holds[len(holds) // 2] / 60, 1),
        "p75_hold_min": round(holds[(3 * len(holds)) // 4] / 60, 1),
        "med_win": round(wins[len(wins) // 2], 1) if wins else None,
        "med_loss": round(losses[len(losses) // 2], 1) if losses else None,
        "fixed_size": fixed,
        "timebox_sig": timebox,
    }


def propose(m: dict) -> str:
    if m["timebox_sig"]:
        return "time_boxer"
    if m["med_hold_min"] < 10:
        return "scalper"
    if m["med_hold_min"] > 240:
        return "swing"
    if m["wr"] >= 0.70 and m["med_loss"] is not None and abs(m["med_loss"]) <= 8:
        return "surgical"
    if not m["fixed_size"]:
        return "thesis_holder" if m["p75_hold_min"] > 3 * m["med_hold_min"] else "conviction"
    return "sprayer"


def main():
    sigs = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    panel = json.load(open("config/sensor_panel.json"))
    todo = [a for a, meta in panel.items()
            if not meta.get("archetype") and meta.get("status") != "unfollowable"]
    print(f"decoding {len(todo)} unlabeled wallets ({sigs} sigs each)\n", flush=True)
    out = {}
    for a in todo:
        try:
            m = decode_metrics(a, sigs)
        except Exception as e:
            print(f"{a[:8]}… DECODE FAIL: {e}", flush=True)
            continue
        if m is None:
            print(f"{a[:8]}… too few closed trips", flush=True)
            continue
        label = propose(m)
        out[a] = {"metrics": m, "proposed": label}
        print(f"{a[:8]}… n={m['n']:3d} wr={m['wr']:.0%} hold med={m['med_hold_min']:.0f}m "
              f"p75={m['p75_hold_min']:.0f}m win={m['med_win']} loss={m['med_loss']} "
              f"{'FIXED' if m['fixed_size'] else 'VAR'}-size"
              f"{' TIMEBOX' if m['timebox_sig'] else ''} -> {label}", flush=True)
    json.dump(out, open("_panel_label_proposals.json", "w"), indent=1)
    print("\nproposals saved to _panel_label_proposals.json", flush=True)


if __name__ == "__main__":
    main()
