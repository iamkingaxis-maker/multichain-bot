#!/usr/bin/env python
"""Give-back breakeven-rescue WINNER-KILL audit.

Reads the give-back SHADOW fields (shipped 15ed4da, measure-only) off closed
per-bot sells: giveback_shadow_fired = the position peaked >=+3% then crossed
back to <=0% while pre-TP1. A future fast peak-aware breakeven RESCUE would exit
at that crossing (~pnl_at_fire). This audit decides whether enforcing it is
+EV and safe:

  fired position that ended a LOSS  -> rescue SAVED it (benefit = pnl_at_fire - actual)
  fired position that ended a WIN   -> rescue KILLED it (cost   = pnl_at_fire - actual < 0)

Net EV/trade on the fired cohort + the winner-kill rate (fired-winners / all
winners) decide enforcement. Token-deduped (FCM). Read-only.

NOTE: requires forward data — only accrues when the bot is actually trading
(filter_sol_macro_down pauses entries during SOL downtrends). Run once trades
resume and there are >=~15-20 fired cases spanning both winners and losers.
The give-back fields are stripped from the default /api/trades view -> ?full=1.
"""
from __future__ import annotations
import sys, json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
MIN_FIRED = 15   # below this, report thin + don't conclude


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
    # only sells that carry the give-back field (post-deploy per-bot sells)
    have = [s for s in sells if "giveback_shadow_fired" in s]
    fired = [s for s in have if s.get("giveback_shadow_fired")]
    print(f"per-bot sells with give-back field: {len(have)} | gb_fired=True: {len(fired)}")

    # token-dedup helper (median realized per token)
    def dedup(rows):
        by = {}
        for r in rows:
            by.setdefault(r.get("token"), []).append(r)
        out = []
        for tok, g in by.items():
            g = sorted(g, key=lambda x: x["pnl_pct"])
            out.append(g[len(g)//2])
        return out

    all_dd = dedup(have)
    total_winners = [s for s in all_dd if s["pnl_pct"] > 0]
    print(f"all closed (token-deduped): {len(all_dd)} | winners: {len(total_winners)} "
          f"(WR {100*len(total_winners)/max(len(all_dd),1):.0f}%)")

    if len(fired) < MIN_FIRED:
        print(f"\n[PENDING] only {len(fired)} fired cases (<{MIN_FIRED}). Audit not "
              f"conclusive yet — accrues only while the bot trades (SOL-macro pause "
              f"halts entries). Re-run once trades resume. Showing what exists:")

    fired_dd = dedup(fired)
    if not fired_dd:
        print("no fired cases to analyze yet.")
        return

    rows = []
    for s in fired_dd:
        actual = float(s["pnl_pct"])
        # rescue exits at the <=0 crossing; pnl_at_fire is that price (fallback BE=0)
        at_fire = s.get("giveback_shadow_pnl_at_fire")
        rescue = float(at_fire) if isinstance(at_fire, (int, float)) else 0.0
        delta = rescue - actual           # >0 = rescue beats actual (saved); <0 = cost
        rows.append(dict(token=s.get("token"), bot=s.get("bot_id"), actual=actual,
                         rescue=rescue, delta=delta, win=actual > 0,
                         peak=s.get("peak_pnl_pct"), reason=str(s.get("reason"))[:28]))

    winners_fired = [r for r in rows if r["win"]]
    losers_fired = [r for r in rows if not r["win"]]
    saved = sum(r["delta"] for r in losers_fired)      # benefit (should be +)
    cost = sum(r["delta"] for r in winners_fired)      # cost (should be -)
    net = sum(r["delta"] for r in rows)

    print(f"\n=== give-back breakeven-rescue audit (token-deduped) ===")
    print(f"fired tokens: {len(rows)} | ended WIN (would be killed): {len(winners_fired)} "
          f"| ended LOSS (would be saved): {len(losers_fired)}")
    wk = 100 * len(winners_fired) / max(len(total_winners), 1)
    print(f"WINNER-KILL RATE: {wk:.0f}% ({len(winners_fired)}/{len(total_winners)} of all winners)")
    print(f"benefit (losers saved):  {saved:+.2f}pp summed")
    print(f"cost (winners killed):   {cost:+.2f}pp summed")
    print(f"NET EV on fired cohort:  {net:+.2f}pp ({net/max(len(rows),1):+.2f}pp/fired-trade)")
    print(f"\nVERDICT GUIDANCE: enforce the breakeven rescue iff NET>0 AND winner-kill")
    print(f"rate is low (rescue saves more loss than the winners it sacrifices). Thin "
          f"data -> shadow longer before enforcing.")

    print(f"\nper-fired-token detail:")
    print(f"{'token':12} {'bot':22} {'actual%':>8} {'rescue%':>8} {'delta':>7} {'outcome':>8}")
    for r in sorted(rows, key=lambda x: x["delta"]):
        print(f"{str(r['token'])[:12]:12} {str(r['bot'])[:22]:22} {r['actual']:>+8.2f} "
              f"{r['rescue']:>+8.2f} {r['delta']:>+7.2f} {'WIN-KILL' if r['win'] else 'save':>8}")


if __name__ == "__main__":
    main()
