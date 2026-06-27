"""Shadow-gate enforce-readiness scoreboard (2026-06-27 winner-mining cycle).

For every shipped SHADOW entry-filter, join its per-entry verdict (logged in
entry_meta.<field>) to the position's REALIZED blended pnl_pct (address+entry_price
join, fraction-weighted by sell_fraction), and report blocked-vs-kept mean/WR/n on
ALL data and the NEWEST HALF. A gate is enforce-ready when, on fresh data, the
BLOCK cohort is meaningfully worse than KEPT (it removes -EV flow) with enough n
and a favorable loss-avoid:winner-kill ratio.

Judge MEAN + tail, not median (the strategy is structurally fat-tail). Realized
trade-join (every shadow trade happened) — NOT forward-candle, so not subject to
the order-blind scorer bug. NO rosy projection: these are realized $/close splits.

Usage: python scripts/shadow_gate_scoreboard.py   # reads _full_trades.json
"""
from __future__ import annotations
import json
import os
import statistics as st
import sys
from collections import defaultdict

PATH = "_full_trades.json"

# verdict field in entry_meta -> human label. Add gates here as they ship.
GATES = {
    "filter_falling_knife_verdict": "falling_knife (mtf<=-1 & 1m red)",
    "filter_mtf_strong_downtrend_verdict": "mtf_strong_downtrend (mtf<=-2)",
    "filter_5m_downtrend_verdict": "5m_downtrend",
    "filter_negative_net_flow_5m_verdict": "negative_net_flow_5m",
    "filter_1m_steep_fall_verdict": "1m_steep_fall",
    "filter_consec_red_verdict": "consec_red (main-scan)",
    "filter_real_dip_3_verdict": "real_dip_3",
    "filter_real_dip_5_verdict": "real_dip_5",
    "filter_knife_catch_peak_verdict": "knife_catch_peak",
    "filter_turn_verdict": "turn",
    "filter_double_bottom_verdict": "double_bottom",
    "filter_blowoff_top_verdict": "blowoff_top",
    "filter_post_pump_corpse_verdict": "post_pump_corpse",
    "filter_high_activity_fomo_verdict": "high_activity_fomo",
    "filter_extended_chase_verdict": "extended_chase",
}


def _blended(legs):
    fr = [(p, float(f)) for p, f in legs if isinstance(f, (int, float))]
    tot = sum(f for _p, f in fr)
    if fr and 0.8 <= tot <= 1.2:
        return sum(p * f for p, f in fr)
    cl = [p for p, f in legs]  # fallback: mean of available pnl legs
    return sum(cl) / len(cl) if cl else None


def positions(recs):
    """Group legs into positions keyed by (address, entry_price); attach each
    gate's verdict (from the entry leg) and the blended realized pnl + peak + ts."""
    pos = defaultdict(lambda: {"verdicts": {}, "ts": None, "legs": [], "peak": None})
    for r in recs:
        key = (r.get("address"), r.get("entry_price"))
        em = r.get("entry_meta") or {}
        for field in GATES:
            v = em.get(field)
            if v in ("BLOCK", "PASS"):
                pos[key]["verdicts"][field] = v
        if em.get("signal_ts_ms"):
            pos[key]["ts"] = em["signal_ts_ms"]
        pn = r.get("pnl_pct")
        if isinstance(pn, (int, float)):
            pos[key]["legs"].append((float(pn), r.get("sell_fraction")))
        pk = r.get("peak_pnl_pct")
        if isinstance(pk, (int, float)):
            prev = pos[key]["peak"]
            pos[key]["peak"] = pk if prev is None else max(prev, pk)
    out = []
    for d in pos.values():
        b = _blended(d["legs"]) if d["legs"] else None
        if b is None:
            continue
        out.append({"verdicts": d["verdicts"], "pnl": b, "ts": d["ts"], "peak": d["peak"]})
    return out


def _agg(rows):
    if not rows:
        return None
    pn = [r["pnl"] for r in rows]
    wr = sum(1 for p in pn if p > 0) / len(pn)
    ng = sum(1 for r in rows if isinstance(r["peak"], (int, float)) and r["peak"] <= 0)
    return {"n": len(pn), "mean": st.mean(pn), "wr": wr, "never_green": ng}


def score_gate(allpos, field):
    have = [p for p in allpos if field in p["verdicts"]]
    if not have:
        return None
    def split(rows):
        return ([r for r in rows if r["verdicts"][field] == "BLOCK"],
                [r for r in rows if r["verdicts"][field] == "PASS"])
    blk, pas = split(have)
    a_blk, a_pas = _agg(blk), _agg(pas)
    # newest half by ts
    tsd = sorted([r for r in have if r["ts"]], key=lambda x: x["ts"])
    half = tsd[len(tsd) // 2:]
    h_blk, h_pas = split(half)
    return {
        "all_block": a_blk, "all_pass": a_pas,
        "new_block": _agg(h_blk), "new_pass": _agg(h_pas),
    }


def main():
    if not os.path.exists(PATH):
        print(f"{PATH} not found — run scripts/pull_full_trades.py first.")
        sys.exit(1)
    allpos = positions(json.load(open(PATH)))
    print(f"=== Shadow-gate enforce scoreboard — {len(allpos)} positions ===")
    print("ENFORCE-READY = fresh BLOCK mean <= -2.0pp AND <= kept-2pp AND n_block>=30.\n")
    rows = []
    for field, label in GATES.items():
        s = score_gate(allpos, field)
        if not s or not s["all_block"] or not s["all_pass"]:
            continue
        rows.append((label, s))
    # rank by newest-half (kept - block) mean separation (bigger = better blocker)
    def sep(s):
        nb, nps = s["new_block"], s["new_pass"]
        if not nb or not nps:
            ab, aps = s["all_block"], s["all_pass"]
            return (aps["mean"] - ab["mean"]) if ab and aps else -999
        return nps["mean"] - nb["mean"]
    rows.sort(key=lambda r: sep(r[1]), reverse=True)
    for label, s in rows:
        ab, aps = s["all_block"], s["all_pass"]
        nb, nps = s["new_block"], s["new_pass"]
        ready = (nb and nb["n"] >= 30 and nb["mean"] <= -2.0
                 and nps and nb["mean"] <= nps["mean"] - 2.0)
        flag = "  >>> ENFORCE-READY" if ready else ""
        print(f"{label}{flag}")
        print(f"  ALL : BLOCK n={ab['n']:<4} mean={ab['mean']:+6.2f}% WR={ab['wr']:.0%}"
              f"  |  PASS n={aps['n']:<4} mean={aps['mean']:+6.2f}% WR={aps['wr']:.0%}"
              f"  d={aps['mean']-ab['mean']:+.2f}pp")
        if nb and nps:
            print(f"  NEW : BLOCK n={nb['n']:<4} mean={nb['mean']:+6.2f}% WR={nb['wr']:.0%}"
                  f"  |  PASS n={nps['n']:<4} mean={nps['mean']:+6.2f}% WR={nps['wr']:.0%}"
                  f"  d={nps['mean']-nb['mean']:+.2f}pp")
        print()


if __name__ == "__main__":
    main()
