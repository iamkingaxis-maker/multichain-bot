#!/usr/bin/env python
"""filter_extended_chase forward BLOCK-RATE + realized-edge audit.

Reads the live SHADOW verdict (filter_extended_chase_verdict, shipped 15ed4da)
off bought tokens' entry_meta (?full=1) and joins to realized outcomes. Answers
the enforcement question on FORWARD data (not the historical mine):

  1. BLOCK-RATE on actual entries = volume the universal gate would cut
     (historical estimate was ~27%; confirm live).
  2. Realized edge: WR/EV of BLOCK-verdict entries vs PASS-verdict entries.
     Enforce only if BLOCK entries are materially -EV vs PASS.
  3. WINNER-KILL: of the >=+10% realized winners, how many are BLOCK-verdict?
     (held-out mine showed 0/7; confirm forward stays low before enforcing.)

Token-deduped (FCM). Read-only. The verdict is stripped from the default
/api/trades view -> ?full=1. Accrues only while the bot trades (SOL-macro pause
halts entries); re-run once trades resume with >=~30 verdict'd entries.
"""
from __future__ import annotations
import sys, json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
MIN_VERDICTS = 30   # below this, report thin + don't conclude


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
    buys = [x for x in trades if x.get("type") == "buy" and x.get("entry_meta")]
    sells = [x for x in trades if x.get("type") == "sell"]
    sidx = {}
    for s in sells:
        sidx.setdefault((s.get("bot_id"), s.get("address")), []).append(s)

    rows = []
    for b in buys:
        v = b["entry_meta"].get("filter_extended_chase_verdict")
        if v not in ("BLOCK", "PASS"):
            continue
        sc = sidx.get((b.get("bot_id"), b.get("address")))
        pnl = float(sc[0]["pnl_pct"]) if (sc and sc[0].get("pnl_pct") is not None) else None
        rows.append(dict(token=b.get("token"), verdict=v, pnl=pnl,
                         reasons=b["entry_meta"].get("filter_extended_chase_block_reasons")))

    verdicted = len(rows)
    blocks = [r for r in rows if r["verdict"] == "BLOCK"]
    print(f"entries with filter_extended_chase verdict: {verdicted} "
          f"| BLOCK {len(blocks)} | PASS {verdicted - len(blocks)}")
    if verdicted:
        print(f"live BLOCK-RATE: {100*len(blocks)/verdicted:.0f}% "
              f"(historical estimate ~27%)")

    if verdicted < MIN_VERDICTS:
        print(f"\n[PENDING] only {verdicted} verdict'd entries (<{MIN_VERDICTS}). "
              f"Accrues only while the bot trades (SOL-macro pause halts entries). "
              f"Re-run once trades resume.")

    # realized edge (token-deduped, only closed)
    closed = [r for r in rows if r["pnl"] is not None]
    def dd(rs):
        by = {}
        for r in rs:
            by.setdefault(r["token"], []).append(r)
        return [sorted(g, key=lambda x: x["pnl"])[len(g)//2] for g in by.values()]
    cb = dd([r for r in closed if r["verdict"] == "BLOCK"])
    cp = dd([r for r in closed if r["verdict"] == "PASS"])

    def stat(rs, lbl):
        if not rs:
            print(f"  {lbl:18} n=0"); return
        pnls = [r["pnl"] for r in rs]
        print(f"  {lbl:18} tok={len(rs):>3} | realized WR {100*np.mean([p>0 for p in pnls]):>3.0f}% "
              f"EV {np.mean(pnls):>+6.2f}%")

    print(f"\n=== forward realized edge (token-deduped, closed only) ===")
    stat(cb, "BLOCK-verdict")
    stat(cp, "PASS-verdict")
    # winner-kill
    winners10 = [r for r in dd(closed) if r["pnl"] >= 10]
    killed = [r for r in winners10 if r["verdict"] == "BLOCK"]
    if winners10:
        print(f"WINNER-KILL: {len(killed)}/{len(winners10)} of >=+10% winners are BLOCK-verdict "
              f"({100*len(killed)/len(winners10):.0f}%)")
    print(f"\nVERDICT GUIDANCE: enforce universally iff BLOCK EV << PASS EV AND winner-kill")
    print(f"stays low (held-out mine: 0/7). Until then keep as shadow.")

    if blocks:
        print(f"\nrecent BLOCK-verdict entries (what the gate would cut):")
        for r in blocks[-10:]:
            pn = f"{r['pnl']:+.1f}%" if r["pnl"] is not None else "open"
            print(f"  {str(r['token'])[:12]:12} realized {pn:>7} | {str(r['reasons'])[:70]}")


if __name__ == "__main__":
    main()
