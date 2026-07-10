# scripts/probe_rate_report.py
"""Daily live-probe rate report (2026-07-09, the mission tracker).

MISSION (prove-the-rate week): >=20 live fills across >=4 days with the
wallet-truth SOL delta positive and edge > friction. This prints, in one
command, everything the morning report needs:
  - wallet-truth delta (THE honest P&L) + baseline age
  - live fills to date: count, days-with-fills, per-fill W/L
  - fill quality: fill_vs_mid distribution (the 4% question)
  - full-chain latency on probe fills: detect(price_age) + exec
  - probe gate activity (what the guards blocked)
Usage: python scripts/probe_rate_report.py
"""
import base64
import json
import os
import statistics as st
import urllib.request

BASE = os.environ.get(
    "DASH_BASE", "https://gracious-inspiration-production.up.railway.app")
AUTH = os.environ.get("DASH_AUTH", "jcole:pMIwPSmRmoPfteWViuGgjaTdnx5JfO-g-e6-_zjdlmo")
PROBE = "badday_young_rt"
GO_LIVE = "2026-07-09T19:10"   # probe #2 flip + rebase moment


def _get(path):
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization",
                   "Basic " + base64.b64encode(AUTH.encode()).decode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    wt = _get("/api/wallet-truth")
    print("=== WALLET TRUTH (the only honest P&L) ===")
    print("  paper_mode=%s | open=%s | delta_sol=%+.4f (baseline %.4f)"
          % (wt.get("paper_mode"), wt.get("open_live_positions"),
             wt.get("delta_sol", 0), wt.get("baseline_sol", 0)))

    swaps = _get("/api/live-swaps").get("recent", [])
    fills = [s for s in swaps if s.get("live_mode")
             and str(s.get("ts", "")) >= GO_LIVE.replace("T", " ")[:10]]
    buys = [s for s in fills if s.get("side") == "buy" and s.get("success", True)]
    print("\n=== LIVE FILLS since go-live (%s) ===" % GO_LIVE)
    days = sorted(set(str(s.get("ts", ""))[:10] for s in buys))
    print("  buys=%d | days-with-fills=%d (%s) | mission bar: >=20 fills / >=4 days"
          % (len(buys), len(days), ",".join(days) or "-"))
    fv = [s["fill_vs_mid_slippage_pct"] for s in buys
          if s.get("fill_vs_mid_slippage_pct") is not None]
    if fv:
        print("  fill_vs_mid: median %+.2f%% | worst %+.2f%% (baseline era: +4.06/+15.77)"
              % (st.median(fv), max(fv)))
    lat = [s["total_latency_ms"] / 1000 for s in buys if s.get("total_latency_ms")]
    if lat:
        print("  exec: median %.2fs" % st.median(lat))

    trades = _get("/api/trades?full=1&limit=1500")
    trades = trades if isinstance(trades, list) else trades.get("trades", [])
    pt = [x for x in trades if x.get("bot_id") == PROBE
          and str(x.get("time", "")) >= GO_LIVE]
    sells = [x for x in pt if x.get("type") == "sell"
             and x.get("pnl_pct") is not None]
    if sells:
        p = [x["pnl_pct"] for x in sells]
        print("\n=== PROBE round-trips ===")
        print("  n=%d | WR=%.0f%% | mean %+0.2f%% | median %+0.2f%%"
              % (len(p), 100 * sum(1 for x in p if x > 0) / len(p),
                 st.mean(p), st.median(p)))
        for x in sells[-6:]:
            print("   ", x.get("time", "")[5:19], x.get("token"),
                  "%+.1f%%" % x["pnl_pct"],
                  (x.get("reason") or "")[:60])
    ages = [(x.get("entry_meta") or {}).get("latency_price_age_secs")
            for x in pt if x.get("type") == "buy"]
    ages = [a for a in ages if isinstance(a, (int, float))]
    if ages:
        print("  probe detect(price_age): median %.2fs" % st.median(ages))
    print("\n(gate blocks: grep railway logs for MIN-LIQ-FLOOR/VSNAP-REJECT/"
          "RETRACE-MICRO AVOID-BLOCK/LP-RUG-FLAG bot=%s)" % PROBE)


if __name__ == "__main__":
    main()
