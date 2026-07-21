#!/usr/bin/env python3
"""offense_grade.py — grade the get-ahead OFFENSE signals from entry stamps
(2026-07-21, the manufacturer doctrine). Every RH entry now carries the
maker-tape read AT DECISION TIME (flow_flags since 07-21, dist_active since
30e4786) — so the offense signals grade straight from the ledger, no separate
tape collector, no entry<->tape join needed (it's built into the row).

For each signal it buckets fidelity-honest position outcomes by present/absent
and reports $/entry + corpse-rate separation. dist_active (distributor
first-sell, verified 93%@0s) is the one that matters; flow_flags (wash/rt) is
tracked to confirm the doctrine's "burner-noise" null holds forward.
Run: python scripts/offense_grade.py   (append-history like the gradebook)
"""
from __future__ import annotations
import json
import os
import time
import urllib.request
from collections import defaultdict

BASE = os.environ.get("RH_DASH_BASE",
                      "https://gracious-inspiration-production.up.railway.app")
DEAD_PATH = os.path.join("scratchpad", "_dead_tokens.json")
HIST = os.path.join("scratchpad", "_offense_history.jsonl")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "offense"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _dead():
    try:
        return set(json.load(open(DEAD_PATH)).get("dead") or [])
    except Exception:
        return set()


def main():
    dead = _dead()
    bots = list((_get(f"{BASE}/api/rh-paper").get("bots") or {}).keys())
    # signal -> bucket("present"/"absent") -> {n, fid, dead}
    sig = defaultdict(lambda: defaultdict(
        lambda: {"n": 0, "fid": 0.0, "dead": 0, "days": set()}))
    for b in bots:
        rows = (_get(f"{BASE}/api/rh-paper?bot={b}&raw=1").get("rows")) or []
        pos = {}
        for r in rows:
            k = r.get("pool")
            if r.get("ev") == "buy":
                pos[k] = {"ff": r.get("flow_flags"),
                          "da": r.get("dist_active"),
                          "tok": r.get("token"),
                          "usd": abs(r.get("usd") or 25.0),
                          "day": str(r.get("ts"))[:10], "sp": []}
            elif (r.get("ev") == "sell"
                  and isinstance(r.get("pnl_usd"), (int, float))
                  and k in pos):
                pos[k]["sp"].append(r["pnl_usd"])
        for k, v in pos.items():
            if not v["sp"]:
                continue
            fid = -v["usd"] if v["tok"] in dead else sum(v["sp"])
            ff = v["ff"] or {}
            checks = {
                "dist_active": (v["da"] == 1) if v["da"] is not None else None,
                "recycled_flow": (ff.get("wash_n", 0) > 0
                                  or ff.get("rt_buy_usd", 0) > 0)
                if v["ff"] is not None else None,
            }
            for name, present in checks.items():
                if present is None:
                    continue
                bkt = sig[name]["present" if present else "absent"]
                bkt["n"] += 1
                bkt["fid"] += fid
                bkt["days"].add(v["day"])
                if v["tok"] in dead:
                    bkt["dead"] += 1

    print("=== OFFENSE-SIGNAL GRADE (fidelity-honest, from entry stamps) ===")
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "signals": {}}
    for name in ("dist_active", "recycled_flow"):
        print(f"\n{name}:")
        rec = {}
        for bk in ("present", "absent"):
            d = sig[name][bk]
            n = d["n"]
            per = d["fid"] / n if n else None
            corpse = d["dead"] / n if n else None
            print(f"  {bk:8} n={n:>4} days={len(d['days'])} "
                  f"fid=${d['fid']:+9.2f} "
                  f"{'$%+.3f/e' % per if per is not None else 'n/a':>10} "
                  f"corpse={('%.0f%%' % (corpse*100)) if corpse is not None else 'n/a'}")
            rec[bk] = {"n": n, "days": len(d["days"]),
                       "fid": round(d["fid"], 2),
                       "per_entry": round(per, 4) if per is not None else None,
                       "corpse_rate": round(corpse, 3) if corpse is not None
                       else None}
        p, a = sig[name]["present"], sig[name]["absent"]
        if p["n"] >= 30 and a["n"] >= 30:
            sep = (p["fid"]/p["n"]) - (a["fid"]/a["n"])
            verdict = ("SIGNAL CONFIRMS (present worse)" if sep < -0.15
                       else "no separation / inverts" if sep > -0.05
                       else "weak")
            print(f"  -> n>=30 both: present-minus-absent ${sep:+.3f}/e "
                  f"[{verdict}]")
            rec["verdict"] = verdict
        else:
            print(f"  -> below n>=30 (present {p['n']}, absent {a['n']}) — "
                  f"accruing")
        out["signals"][name] = rec
    with open(HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    print(f"\n-> appended {HIST}")


if __name__ == "__main__":
    main()
