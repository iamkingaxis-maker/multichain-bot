#!/usr/bin/env python
"""Held-out validation of the whale_conviction trigger cut.

whale_conviction is the highest-VOLUME trigger and per-trigger EV showed it at
-2.37% (n=53, the largest/most-reliable). Question: if we DEMOTE it (stop it
firing), does realized EV rise WITHOUT killing winners — on TWO independent
windows (the overfit guard)?

Cut semantics (standard trigger-retirement): a trade only vanishes if
whale_conviction was DECISIVE = it fired AND no OTHER trigger fired (with
min_triggers_to_fire=1, any other trigger keeps the trade). So "whale-decisive"
trades are exactly the ones removing the trigger would drop. We compare:
  - full cohort EV   vs  cohort-after-removing-whale-decisive EV
  - whale-decisive EV (the removed slice — want it << kept)
  - winner-kill: of the >=+10% winners, how many are whale-decisive (want ~0)

Windows: (A) live /api/trades?full=1 (~05-29..31) and (B) .hist_trades_5000.json
(~05-27/28). Token-deduped (FCM). Read-only.
"""
from __future__ import annotations
import sys, json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
TRIG = "trigger_whale_conviction_match"


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def fired_triggers(em):
    return {k for k, v in em.items()
            if k.startswith("trigger_") and k.endswith("_match") and v}


def build_rows(trades):
    buys = [x for x in trades if x.get("type") == "buy" and x.get("entry_meta")]
    sells = [x for x in trades if x.get("type") == "sell"]
    sidx = {}
    for s in sells:
        sidx.setdefault((s.get("bot_id"), s.get("address")), []).append(s)
    rows = []
    for b in buys:
        sc = sidx.get((b.get("bot_id"), b.get("address")))
        if not sc or sc[0].get("pnl_pct") is None:
            continue
        trigs = fired_triggers(b["entry_meta"])
        if not trigs:
            continue
        whale = TRIG in trigs
        decisive = whale and len(trigs) == 1   # only whale fired -> cut drops it
        rows.append(dict(token=b.get("token"), pnl=float(sc[0]["pnl_pct"]),
                         whale=whale, decisive=decisive, ntrig=len(trigs)))
    return rows


def dedup(rs):
    by = {}
    for r in rs:
        by.setdefault(r["token"], []).append(r)
    return [sorted(g, key=lambda x: x["pnl"])[len(g)//2] for g in by.values()]


def report(rows, label):
    ded = dedup(rows)
    if len(ded) < 10:
        print(f"\n=== {label}: n={len(ded)} too thin ==="); return
    full_ev = np.mean([r["pnl"] for r in ded])
    full_wr = 100*np.mean([r["pnl"] > 0 for r in ded])
    dec = [r for r in ded if r["decisive"]]
    kept = [r for r in ded if not r["decisive"]]
    whale_any = [r for r in ded if r["whale"]]
    w10 = [r for r in ded if r["pnl"] >= 10]
    killed = [r for r in w10 if r["decisive"]]
    print(f"\n=== {label} (token-deduped n={len(ded)}) ===")
    print(f"FULL cohort:                 EV {full_ev:+.2f}% WR {full_wr:.0f}%")
    print(f"whale fired (any):           EV {np.mean([r['pnl'] for r in whale_any]):+.2f}% n={len(whale_any)}")
    if dec:
        print(f"whale-DECISIVE (removed):    EV {np.mean([r['pnl'] for r in dec]):+.2f}% n={len(dec)}  [want << kept]")
    else:
        print("whale-DECISIVE (removed):    n=0 (whale never fired solo here)")
    if kept:
        kept_ev = np.mean([r["pnl"] for r in kept])
        print(f"KEPT (after cut):            EV {kept_ev:+.2f}% n={len(kept)}  (delta {kept_ev-full_ev:+.2f}pp vs full)")
    wk = 100*len(killed)/max(len(w10), 1)
    print(f"winner-kill: {len(killed)}/{len(w10)} of >=+10% winners are whale-decisive ({wk:.0f}%)")


def main():
    # window A — live
    report(build_rows(_get("/api/trades?limit=5000&full=1")), "WINDOW A — live ~05-29..31")
    # window B — held-out historical
    try:
        hist = json.load(open(".hist_trades_5000.json"))
        report(build_rows(hist), "WINDOW B — held-out ~05-27/28 (.hist_trades_5000.json)")
    except Exception as e:
        print(f"\n[window B unavailable: {e}]")
    print("\nVERDICT: cut is validated if whale-DECISIVE EV << kept EV AND kept EV > full")
    print("EV in BOTH windows AND winner-kill low. Then demote whale_conviction.")


if __name__ == "__main__":
    main()
