#!/usr/bin/env python3
"""Extended reconstruction: add raw demand-shape features beyond the 4 stamped
ones, plus availability flag, to test whether ANY entry-time flow feature is a
regime-robust runner/dier separator."""
import sys, os, json
sys.path.insert(0, r"C:\Users\jcole\multichain-bot")
import importlib.util
spec = importlib.util.spec_from_file_location("recon", os.path.join(os.path.dirname(__file__), "_rh_recon.py"))
recon = importlib.util.module_from_spec(spec); spec.loader.exec_module(recon)
from core.retrace_microstructure import sell_distribution_flag, net_flow_persistence, _window, _sum_usd


def extra_feats(trades, ref):
    w60 = _window(trades, ref, -60.0, 0.0)
    n = len(w60)
    buy60 = _sum_usd(w60, "buy"); sell60 = _sum_usd(w60, "sell")
    tot = buy60 + sell60
    return {
        "n_trades_60": n,
        "buy_rate_60": round(buy60/60.0, 2),
        "buy_frac_usd_60": round(buy60/tot, 3) if tot > 1e-9 else None,
        "n_buys_60": sum(1 for t in w60 if t["kind"] == "buy"),
        "n_sells_60": sum(1 for t in w60 if t["kind"] == "sell"),
        "vol_60": round(tot, 2),
        "avail": n >= 3,
    }


def build_all():
    allrows = []
    for i in range(1, 11):
        p = os.path.join(recon.TAPE_DIR, f"paper_lane_session{i}.log")
        if not os.path.exists(p):
            continue
        tape, anchors, buys, sells, ts_of, streams = recon.build(p)
        rows = recon.event_features(p)
        for r in rows:
            r["session"] = i
            r["regime"] = recon.REGIME[i]
            trades = streams.get(r["sym"], [])
            r.update(extra_feats(trades, r["ref_ts"]))
        allrows.extend(rows)
    json.dump(allrows, open(os.path.join(recon.TAPE_DIR, "_recon_events2.json"), "w"))
    print("saved", len(allrows), "events with extra feats")
    return allrows


if __name__ == "__main__":
    build_all()
