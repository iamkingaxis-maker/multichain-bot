#!/usr/bin/env python
"""Validate the correlated-cluster sizing brake lever on realized data.

#2 from the morning recommendation: catastrophic losses come from the fleet
SWARMING one token (TinyWorld -$565 = ~30 correlated entries). The brake sizes
DOWN entries when fleet exposure to that token is already high. Before building,
confirm the lever:

  For each BUY, swarm_at_entry = # of OTHER fleet buys of the SAME token (address)
  in the preceding WINDOW (the concurrent pile-in the brake would see in real
  time). Bucket entries by swarm_at_entry; show realized EV + total $ + big
  winners per bucket.

Brake helps iff high-swarm entries are -EV AND sizing them down doesn't gut a
pile of winner-swarms (the fleet correctly mobbing a real runner). The
per-entry $ (NOT token-deduped) is the right unit here — the whole thesis is
that 30 entries on one dud each lose, so the aggregate tail is what we cut.
Read-only.
"""
from __future__ import annotations
import sys, json, urllib.request, time
import numpy as np
from datetime import datetime

BASE = "https://gracious-inspiration-production.up.railway.app"
WINDOW_S = 1800   # 30-min concurrent-swarm window


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def _ep(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def main():
    trades = _get("/api/trades?limit=5000&full=1")
    buys = [x for x in trades if x.get("type") == "buy"]
    sells = [x for x in trades if x.get("type") == "sell"]
    sidx = {}
    for s in sells:
        sidx.setdefault((s.get("bot_id"), s.get("address")), []).append(s)

    # buys per token-address with epoch
    by_addr = {}
    for b in buys:
        e = _ep(b.get("time"))
        if e is None:
            continue
        by_addr.setdefault(b.get("address"), []).append(e)
    for a in by_addr:
        by_addr[a].sort()

    rows = []
    for b in buys:
        e = _ep(b.get("time"))
        if e is None:
            continue
        # swarm_at_entry: other buys of same address in [e-WINDOW, e)
        times = by_addr.get(b.get("address"), [])
        swarm = sum(1 for t in times if e - WINDOW_S <= t < e)
        sc = sidx.get((b.get("bot_id"), b.get("address")))
        pnl = float(sc[0]["pnl_pct"]) if (sc and sc[0].get("pnl_pct") is not None) else None
        size = float(b.get("amount_usd") or 0.0)
        rows.append(dict(token=b.get("token"), swarm=swarm, pnl=pnl, size=size,
                         peak=sc[0].get("peak_pnl_pct") if sc else None))

    closed = [r for r in rows if r["pnl"] is not None]
    print(f"buys {len(buys)} | closed (joined) {len(closed)} | window {WINDOW_S//60}min")

    def bucket(s):
        return "0 solo" if s == 0 else "1-4" if s < 5 else "5-9" if s < 10 \
            else "10-19" if s < 20 else "20+"
    order = ["0 solo", "1-4", "5-9", "10-19", "20+"]
    print(f"\n{'swarm@entry':12} {'n':>5} {'WR%':>5} {'EV%':>7} {'tot$(size-wtd)':>14} {'>=+10% wins':>11}")
    for bk in order:
        sub = [r for r in closed if bucket(r["swarm"]) == bk]
        if not sub:
            continue
        wr = 100*np.mean([r["pnl"] > 0 for r in sub])
        ev = np.mean([r["pnl"] for r in sub])
        tot = sum(r["pnl"]/100.0 * r["size"] for r in sub)   # realized $ (pnl% * size)
        bigw = sum(1 for r in sub if r["pnl"] >= 10)
        print(f"{bk:12} {len(sub):>5} {wr:>5.0f} {ev:>+7.2f} {tot:>+14.2f} {bigw:>11}")

    # the fat tail: worst single-token aggregate losses by swarm
    by_tok = {}
    for r in closed:
        by_tok.setdefault(r["token"], []).append(r)
    agg = []
    for tok, rs in by_tok.items():
        tot = sum(r["pnl"]/100.0 * r["size"] for r in rs)
        maxswarm = max(r["swarm"] for r in rs)
        agg.append((tot, len(rs), maxswarm, tok))
    agg.sort()
    print(f"\nworst 10 single-token aggregate P&L (the fat left tail the brake targets):")
    print(f"{'token':14} {'entries':>7} {'max_swarm':>9} {'total$':>10}")
    for tot, n, ms, tok in agg[:10]:
        print(f"{str(tok)[:14]:14} {n:>7} {ms:>9} {tot:>+10.2f}")

    # winner-kill preview: how much WINNER $ lives in high-swarm (>=5) entries?
    hi = [r for r in closed if r["swarm"] >= 5]
    hi_win = sum(r["pnl"]/100.0*r["size"] for r in hi if r["pnl"] > 0)
    hi_loss = sum(r["pnl"]/100.0*r["size"] for r in hi if r["pnl"] <= 0)
    print(f"\nhigh-swarm (>=5) entries: {len(hi)} | winner$ {hi_win:+.2f} | loser$ {hi_loss:+.2f} "
          f"| net {hi_win+hi_loss:+.2f}")
    print("brake sizes these down: saves |loser$| but gives up some winner$. Net>0 +")
    print("low winner-kill = enforce. Calibrate the multiplier curve from the buckets.")


if __name__ == "__main__":
    main()
