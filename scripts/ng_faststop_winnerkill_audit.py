#!/usr/bin/env python
"""Never-green fast-stop winner-kill audit (the PRIMARY avg-loss lever).

Reads the ng_faststop SHADOW field (shipped) off closed per-bot sells:
ng_faststop_shadow_fired = the position NEVER peaked >=2% AND hit <=-4% (the
78%-of-loss never-green dying slice). A fast-stop would exit it at ~-4 instead
of the -8.27% bleed. This audit decides whether enforcing it is +EV and safe:

  fired position that ended a LOSS  -> rescue SAVED it (benefit = -4 - actual, when actual<-4)
  fired position that ended a WIN   -> rescue KILLED it (cost = -4 - actual < 0)

Historical estimate (entry-time scan): winner-kill ~4% (only 4% of winners ever
peak <2%), addresses 71% of losers. This confirms it FORWARD. Net EV/trade on
the fired cohort + the winner-kill rate decide enforcement. Token-deduped (FCM).
Read-only. Fields are stripped from the default /api/trades view -> ?full=1.
Accrues only while trading (SOL-macro pause halts entries) — run once trades
resume with >=~20 fired cases.
"""
from __future__ import annotations
import sys, json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
MIN_FIRED = 20
STOP_LEVEL = -4.0   # the fast-stop exit level


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
    have = [s for s in sells if "ng_faststop_shadow_fired" in s]
    fired = [s for s in have if s.get("ng_faststop_shadow_fired")]
    print(f"per-bot sells with ng_faststop field: {len(have)} | fired=True: {len(fired)}")

    def dedup(rows):
        by = {}
        for r in rows:
            by.setdefault(r.get("token"), []).append(r)
        return [sorted(g, key=lambda x: x["pnl_pct"])[len(g)//2] for g in by.values()]

    all_dd = dedup(have)
    winners_all = [s for s in all_dd if s["pnl_pct"] > 0]
    print(f"all closed (deduped): {len(all_dd)} | winners {len(winners_all)} "
          f"(WR {100*len(winners_all)/max(len(all_dd),1):.0f}%)")
    if len(fired) < MIN_FIRED:
        print(f"\n[PENDING] only {len(fired)} fired (<{MIN_FIRED}). Accrues only while "
              f"trading (SOL-paused). Re-run once trades resume.")

    fired_dd = dedup(fired)
    if not fired_dd:
        print("no fired cases yet.")
        return

    rows = []
    for s in fired_dd:
        actual = float(s["pnl_pct"])
        af = s.get("ng_faststop_pnl_at_fire")
        rescue = float(af) if isinstance(af, (int, float)) else STOP_LEVEL  # exit ~-4
        rows.append(dict(token=s.get("token"), actual=actual, rescue=rescue,
                         delta=rescue - actual, win=actual > 0))

    won = [r for r in rows if r["win"]]
    lost = [r for r in rows if not r["win"]]
    saved = sum(r["delta"] for r in lost)
    cost = sum(r["delta"] for r in won)
    net = saved + cost
    wk = 100 * len(won) / max(len(winners_all), 1)
    print(f"\n=== never-green fast-stop audit (token-deduped) ===")
    print(f"fired tokens: {len(rows)} | ended LOSS (saved): {len(lost)} | ended WIN (killed): {len(won)}")
    print(f"WINNER-KILL RATE: {wk:.0f}% ({len(won)}/{len(winners_all)} of all winners)  [hist est ~4%]")
    print(f"benefit (losers saved):  {saved:+.2f}pp")
    print(f"cost (winners killed):   {cost:+.2f}pp")
    print(f"NET on fired cohort:     {net:+.2f}pp ({net/max(len(rows),1):+.2f}pp/fired)")
    print(f"\nVERDICT: enforce iff NET>0 AND winner-kill stays low (~<=5%). This is the")
    print(f"78%-of-loss lever -> if it holds, it flips fleet EV positive at 60% WR.")
    for r in sorted(rows, key=lambda x: x["delta"])[:12]:
        print(f"  {str(r['token'])[:12]:12} actual {r['actual']:>+6.2f} rescue {r['rescue']:>+5.1f} "
              f"delta {r['delta']:>+6.2f} {'WIN-KILL' if r['win'] else 'save'}")


if __name__ == "__main__":
    main()
