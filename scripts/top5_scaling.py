#!/usr/bin/env python
"""Rank bots (judgment) + estimate top-5 P&L at $100 positions.

Per bot: closed n, realized total $, EV $/trade, WR, avg size. Ranks by realized
total $ (the leaderboard the user reads) AND by EV $/trade (research-correct,
size-normalized). Then scales the top 5's realized P&L to a $100 base.

Scaling: per-trade pnl_pct is ~size-invariant, so $100 vs the $20 base is ~5x
the realized $. Two real adjustments are flagged, not silently linear:
  - FIXED fee ($0.10/tx, 2 legs) is a smaller % drag at $100 (~0.1% vs ~0.5%
    of notional) -> helps pnl_pct by ~+0.8pp/round-trip.
  - SLIPPAGE impact grows with size/liq on low-liq memecoins -> hurts fills at
    $100 (the 5x is an UPPER BOUND for low-liq names).
Read-only.
"""
from __future__ import annotations
import sys, json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
NEW_BASE_USD = 100.0
CUR_BASE_USD = 20.0   # champions' base_position_usd


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
    sells = [x for x in trades if x.get("type") == "sell" and x.get("pnl_pct") is not None]
    by = {}
    for s in sells:
        b = s.get("bot_id")
        by.setdefault(b, []).append(s)

    stats = []
    for b, ss in by.items():
        pnl_usd = sum(s.get("pnl", 0.0) or 0.0 for s in ss)
        pcts = [s["pnl_pct"] for s in ss]
        sizes = [float(s.get("amount_usd") or 0) for s in ss if s.get("amount_usd")]
        stats.append(dict(bot=b, n=len(ss), total=pnl_usd,
                          ev_usd=pnl_usd/len(ss), ev_pct=np.mean(pcts),
                          wr=100*np.mean([p > 0 for p in pcts]),
                          avg_size=np.mean(sizes) if sizes else 0))

    by_total = sorted(stats, key=lambda x: -x["total"])
    print("=== ranked by REALIZED TOTAL $ (the leaderboard) ===")
    print(f"{'bot':30} {'n':>4} {'total$':>9} {'$/trade':>8} {'EV%':>6} {'WR%':>5} {'avg$':>6}")
    for s in by_total[:12]:
        print(f"{s['bot']:30} {s['n']:>4} {s['total']:>+9.2f} {s['ev_usd']:>+8.3f} "
              f"{s['ev_pct']:>+6.2f} {s['wr']:>5.0f} {s['avg_size']:>6.0f}")

    print("\n=== ranked by EV $/trade (research-correct, n>=30 only) ===")
    by_ev = sorted([s for s in stats if s["n"] >= 30], key=lambda x: -x["ev_usd"])
    for s in by_ev[:10]:
        flag = "" if s["n"] >= 50 else "  (n<50 underpowered)"
        print(f"{s['bot']:30} {s['n']:>4} {s['ev_usd']:>+8.3f}/tr  total {s['total']:>+8.2f}{flag}")

    # $100 scaling of the leaderboard top 5
    top5 = by_total[:5]
    print(f"\n=== TOP 5 (by realized $) scaled to ${NEW_BASE_USD:.0f} base (from ${CUR_BASE_USD:.0f}) ===")
    mult = NEW_BASE_USD / CUR_BASE_USD
    cur_sum = sum(s["total"] for s in top5)
    print(f"{'bot':30} {'n':>4} {'cur$':>9} {'~$100 (x%.0f)' % mult:>12}")
    for s in top5:
        print(f"{s['bot']:30} {s['n']:>4} {s['total']:>+9.2f} {s['total']*mult:>+12.2f}")
    print(f"{'TOTAL':30} {sum(s['n'] for s in top5):>4} {cur_sum:>+9.2f} {cur_sum*mult:>+12.2f}")

    # fee-drag adjustment: fixed ~$0.20 round-trip fee. At $20 that's ~1.0% of
    # notional; at $100 ~0.2%. Recovering ~0.8pp/trade * notional.
    fee_recover = sum(0.008 * NEW_BASE_USD/100.0 * (s["avg_size"]*mult/NEW_BASE_USD) * s["n"] for s in top5)
    print(f"\nadjustments to the naive x{mult:.0f}:")
    print(f"  + fixed-fee drag shrinks at $100  -> ~+0.8pp/round-trip (helps)")
    print(f"  - slippage impact grows with size -> low-liq fills worse (hurts; x{mult:.0f} is an UPPER BOUND)")
    print(f"\nCAVEATS: (1) SELECTION BIAS — top 5 are ex-post winners; sizing them up")
    print(f"assumes they STAY top 5 (the overfit trap). Forward expectation < in-sample.")
    print(f"(2) capital: $100 base x premium_runner(3x)=$300; 3 concurrent=$900 of $2000 OK.")
    print(f"(3) per-bot fleet EV != portfolio — judge winners real (n>=50, --unrealized) first.")


if __name__ == "__main__":
    main()
