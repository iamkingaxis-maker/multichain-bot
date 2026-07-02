#!/usr/bin/env python3
"""
loss_autopsy.py — first-pass root-cause analysis for deep losses.

For every token whose fleet net over the window is <= THRESH (default -40pp),
produce the why-chain: entry conditions at fill (from entry_meta), exit kinds,
gap-below-stop measure (booked pnl vs -12 hard stop), rebuy involvement
(re-entry within 30min of a deep stop), hour/regime tags.

Usage: PYTHONPATH=. python scripts/loss_autopsy.py [hours=24] [thresh=-40]
"""
import json, sys, time, urllib.request, gzip, io
from collections import defaultdict

DASH = "https://gracious-inspiration-production.up.railway.app"


def g(p):
    req = urllib.request.Request(DASH + p, headers={
        "User-Agent": "autopsy/1", "Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=40)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
    thresh = float(sys.argv[2]) if len(sys.argv) > 2 else -40
    import datetime as dt
    cut = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
    arr = g("/api/trades?limit=2500")
    arr = arr.get("trades", arr) if isinstance(arr, dict) else arr
    recent = [t for t in arr if str(t.get("time", "")) > cut]
    net = defaultdict(float)
    for t in recent:
        if t.get("type") == "sell" and t.get("pnl_pct") is not None:
            net[t.get("token")] += float(t["pnl_pct"]) * (t.get("sell_fraction") or 1.0)
    losers = {k: v for k, v in net.items() if v <= thresh}
    print(f"deep losers (net <= {thresh}pp, last {hours}h): {len(losers)}")
    for tok, npp in sorted(losers.items(), key=lambda x: x[1]):
        legs = [t for t in recent if t.get("token") == tok]
        buys = sorted([t for t in legs if t.get("type") == "buy"], key=lambda t: t.get("time", ""))
        sells = sorted([t for t in legs if t.get("type") == "sell"], key=lambda t: t.get("time", ""))
        print(f"\n== {tok}  net {npp:+.0f}pp | {len(buys)} buys / {len(sells)} sells "
              f"/ {len({t.get('bot_id') for t in legs})} bots")
        # gap-below-stop
        deep = [float(s["pnl_pct"]) for s in sells if s.get("pnl_pct") is not None
                and float(s["pnl_pct"]) < -12]
        if deep:
            print(f"   GAP-THROUGH: {len(deep)} legs below -12 stop, worst {min(deep):.1f} "
                  f"(avg gap {sum(deep)/len(deep)+12:.1f}pp below)")
        # rebuy after deep stop
        stop_ts = [s.get("time", "") for s in sells if s.get("pnl_pct") is not None
                   and float(s["pnl_pct"]) <= -10]
        rebuys = 0
        for st in stop_ts:
            for b in buys:
                if b.get("time", "") > st:
                    try:
                        d1 = dt.datetime.fromisoformat(st[:19])
                        d2 = dt.datetime.fromisoformat(b.get("time", "")[:19])
                        if 0 < (d2 - d1).total_seconds() < 1800:
                            rebuys += 1
                            break
                    except Exception:
                        pass
        if rebuys:
            print(f"   REBUY-ARTIFACT: {rebuys} re-entries within 30min of a deep stop "
                  f"(should be 0 post-cooldown)")
        # entry context of first buy
        if buys:
            b0 = buys[0]
            em = b0.get("entry_meta") or {}
            hr = b0.get("time", "")[11:13]
            ctx = {k: em.get(k) for k in ("pc_h1", "pc_h6", "liquidity_usd",
                   "unique_buyers_n", "rsi_15m", "net_flow_5m_usd", "age_hours",
                   "sol_pc_h6") if em.get(k) is not None}
            print(f"   FIRST ENTRY {b0.get('time','')[11:19]} UTC (hour {hr}) "
                  f"bot={b0.get('bot_id')}")
            if ctx:
                print(f"   entry ctx: {ctx}")
        print(f"   exit kinds: {[s.get('exit_kind') for s in sells[:6]]}")
    if not losers:
        print("no deep losers in window — book clean at this threshold")


if __name__ == "__main__":
    import datetime as dt
    main()
