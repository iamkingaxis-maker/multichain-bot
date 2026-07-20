#!/usr/bin/env python3
"""revenue_check.py — DISTANCE-TO-REVENUE, the career-mode loop metric
(2026-07-20, AxiS: "the bills have to be paid").

One command answers the only career question: how far is the nearest seat
from producing real dollars, and what (if anything) blocks it TODAY.
Reads the freshest gradebook output + live regime + wallet; checks every
pre-registered GO-LIVE GATE from REVENUE_PLAN.md section 4 that is
machine-checkable, and prints the human-decision ones as reminders.
Run: python scripts/gradebook.py && python scripts/revenue_check.py
"""
from __future__ import annotations
import json
import os
import subprocess
import time
import urllib.request

BASE = os.environ.get("RH_DASH_BASE",
                      "https://gracious-inspiration-production.up.railway.app")
GB = os.path.join("scratchpad", "_gradebook.json")
CANDIDATE = "rh_pro_agedflush"
BAR = {"n": 30, "days": 5, "tokens": 20}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "revenue-check"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    print("=== DISTANCE TO REVENUE (career mode) ===")
    # freshness guard on the gradebook itself (instrument rule)
    try:
        age_m = (time.time() - os.stat(GB).st_mtime) / 60
        gb = json.load(open(GB))
        if age_m > 120:
            print(f"!! gradebook {age_m:.0f}m old — run gradebook.py first")
    except Exception:
        print("!! no gradebook output — run scripts/gradebook.py first")
        return

    arm = None
    for e in gb.get("experiments", []):
        for a in e.get("arms", []):
            if a.get("bot") == CANDIDATE:
                arm = a
    if not arm:
        print(f"!! {CANDIDATE} not in gradebook")
        return

    gates = []
    gates.append(("n>=30 closes", arm["n"] >= BAR["n"],
                  f"{arm['n']}/{BAR['n']}"))
    gates.append((">=5 days", arm["days"] >= BAR["days"],
                  f"{arm['days']}/{BAR['days']}"))
    gates.append((">=20 tokens", arm["tokens"] >= BAR["tokens"],
                  f"{arm['tokens']}/{BAR['tokens']}"))
    gates.append(("fidelity-$ positive", (arm.get("fid_usd") or 0) > 0,
                  f"${arm.get('fid_usd'):+.2f}"))
    gates.append(("drop-top-2 positive", (arm.get("drop_top2") or 0) > 0,
                  f"${arm.get('drop_top2'):+.2f}"))
    gates.append(("dead re-book was ON", bool(gb.get("dead_rebooked")),
                  str(gb.get("dead_rebooked"))))
    # population check: entries/day from the ledger (2..20 per pre-reg)
    try:
        rows = (_get(f"{BASE}/api/rh-paper?bot={CANDIDATE}&raw=1")
                .get("rows")) or []
        buys = [r for r in rows if r.get("ev") == "buy"]
        days = {str(r.get("ts"))[:10] for r in buys}
        epd = len(buys) / max(1, len(days))
        gates.append(("entries/day in [2,20]", 2 <= epd <= 20,
                      f"{epd:.1f}/day over {len(days)}d"))
    except Exception as e:
        gates.append(("entries/day in [2,20]", False, f"check failed: {e}"))
    # tape context now (never arm into a pump window per the inverted map)
    try:
        rh = (_get(f"{BASE}/api/regime").get("chains") or {}).get("rh") or {}
        drift = rh.get("median_drift_pct")
        pumpy = isinstance(drift, (int, float)) and drift > 3
        gates.append(("not a pump window now", not pumpy,
                      f"drift={drift}%"))
    except Exception:
        gates.append(("not a pump window now", False, "sensor unreachable"))

    n_pass = sum(1 for _, ok, _ in gates if ok)
    for name, ok, detail in gates:
        print(f"  [{'x' if ok else ' '}] {name:24} {detail}")
    print(f"\nmachine gates: {n_pass}/{len(gates)}")
    print("human gates (never automated): RE-ARM CHECKLIST + sell canary "
          "end-to-end + test_pre_live_invariants + AxiS explicit YES")
    if n_pass == len(gates):
        print(">>> ALL MACHINE GATES PASS — present the go-live case to AxiS")
    else:
        blockers = [name for name, ok, _ in gates if not ok]
        print(f"blockers today: {', '.join(blockers)}")
    # runner-up candidate status
    print("\nrunner-up: young-S sick-window router cell — see map re-mine "
          "(needs 5 green sick-days; route refill = flip-sim Gate A starts)")


if __name__ == "__main__":
    main()
