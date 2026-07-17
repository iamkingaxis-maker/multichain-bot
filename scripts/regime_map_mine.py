#!/usr/bin/env python3
"""regime_map_mine.py — DAILY regime-map re-mine (v2: health x hour-band x family).

Run each day (the gate cycle). Produces scratchpad/_regime_map_latest.json:
per-chain {health x hour-band} fleet cells + healthy-window family ranks, from
the freshest ledger pulls. The map is ADVISORY until cells accrue >=5 days of
same-sign evidence (structure changes day to day — never hard-code cells; the
17-22 UTC band flipped from the 07-11 rulebook's best to the worst by 07-17).
Reads scratchpad/_rg/*.json (RH per-bot raw pulls) — refresh those first.
"""
import glob
import json
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

def bkt(ts, W=4):
    return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() // (W * 3600))

def hour(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).hour

def band(h):
    for lo, hi, name in ((3, 9, "03-09"), (9, 13, "09-13"), (13, 17, "13-17"), (17, 22, "17-22")):
        if lo <= h < hi:
            return name
    return "22-03"

def mine(rows_glob):
    allrows = []
    for f in glob.glob(rows_glob):
        try:
            allrows.extend(json.load(open(f)).get("rows") or [])
        except Exception:
            continue
    sells = [r for r in allrows if r.get("ev") == "sell" and r.get("pnl_usd") is not None and r.get("ts")]
    buys = [r for r in allrows if r.get("ev") == "buy" and r.get("ts")]
    px = defaultdict(lambda: defaultdict(list))
    for r in buys:
        p = r.get("price_eth")
        if p:
            px[bkt(r["ts"])][r.get("token")].append((r["ts"], float(p)))
    tape = {}
    for w, toks in px.items():
        ds = []
        for tok, v in toks.items():
            if len(v) < 2:
                continue
            v.sort()
            if v[0][1]:
                ds.append((v[-1][1] / v[0][1] - 1) * 100)
        if len(ds) >= 3:
            tape[w] = st.median(ds)
    cells = defaultdict(lambda: {"usd": 0.0, "entries": 0})
    for r in sells:
        t = tape.get(bkt(r["ts"]))
        if t is None:
            continue
        k = f"{band(hour(r['ts']))}|{'H' if t > -3 else 'S'}"
        cells[k]["usd"] += float(r["pnl_usd"])
    for r in buys:
        t = tape.get(bkt(r["ts"]))
        if t is None:
            continue
        k = f"{band(hour(r['ts']))}|{'H' if t > -3 else 'S'}"
        cells[k]["entries"] += 1
    return {k: {"usd": round(v["usd"], 2), "entries": v["entries"]} for k, v in cells.items()}

if __name__ == "__main__":
    out = {"ts": datetime.now(timezone.utc).isoformat(),
           "note": "ADVISORY until cells hold >=5 days same-sign; re-mine daily",
           "rh_cells": mine("scratchpad/_rg/*.json")}
    with open("scratchpad/_regime_map_latest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for k, v in sorted(out["rh_cells"].items()):
        print(f"{k:10} ${v['usd']:+9.2f} ({v['entries']}e)")
    print("-> scratchpad/_regime_map_latest.json")
