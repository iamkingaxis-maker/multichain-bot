"""Join OUR paper fleet outcomes to the regime axes (corroboration only, n small).
Position-level: sum sell legs per (pool, buy_ts); band from the buy row's age_h.
"""
import json
import os
from collections import defaultdict

ROOT = r"C:\Users\jcole\multichain-bot"
TAPES = os.path.join(ROOT, "scratchpad", "robinhood_tapes")

buys = {}
for ln in open(os.path.join(TAPES, "rh_paper_trades.jsonl"), encoding="utf-8"):
    try:
        r = json.loads(ln)
    except ValueError:
        continue
    if r.get("ev") == "buy" and r.get("ts", "").startswith("2026"):
        buys[(r["pool"], r["ts"][:19])] = r

import calendar
import time

reg_ts = {}
for ln in open(os.path.join(ROOT, "scratchpad", "rh_history",
                            "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(ln)
    reg_ts[d["pool"]] = d["ts"]


def iso_epoch(s):
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


closed = json.load(open(os.path.join(ROOT, "scratchpad",
                                     "_rh_paper_closed.json")))
pos = defaultdict(lambda: {"pnl": 0.0, "legs": 0})
for c in closed:
    bts = (c.get("buy_ts") or "")[:19]
    key = (c["pool"], bts)
    pos[key]["pnl"] += c.get("pnl_usd") or 0.0
    pos[key]["legs"] += 1
    pos[key]["hour"] = int(bts[11:13]) if len(bts) >= 13 else None

HB = {"22-01": (22, 23, 0, 1), "02-07": (2, 3, 4, 5, 6, 7),
      "08-10": (8, 9, 10), "11-13": (11, 12, 13),
      "14-18": (14, 15, 16, 17, 18), "19-21": (19, 20, 21)}


def hb(h):
    for k, hs in HB.items():
        if h in hs:
            return k


agg = defaultdict(lambda: [0, 0, 0.0])   # (block, band) -> [n, wins, pnl]
unmatched = 0
for (pool, bts), v in pos.items():
    b = buys.get((pool, bts))
    age = b.get("age_h") if b else None
    if age is None:                      # pre-07-11 ledger rows lack age_h:
        ep, ct = iso_epoch(bts), reg_ts.get(pool)   # registry creation ts
        if ep and ct:
            age = (ep - ct) / 3600.0
    if age is None:
        unmatched += 1
    band = (None if age is None else
            "young" if age < 6 else "mid" if age < 24 else "aged")
    k = (hb(v["hour"]) if v["hour"] is not None else None, band)
    agg[k][0] += 1
    agg[k][1] += v["pnl"] > 0
    agg[k][2] += v["pnl"]

print(f"positions={len(pos)} unmatched_age={unmatched}")
for k in sorted(agg, key=str):
    n, w, p = agg[k]
    print(f"  block={str(k[0]):>6} band={str(k[1]):>5}: n={n:3d} "
          f"win={100*w/max(n,1):5.1f}% pnl=${p:+8.2f} (${p/max(n,1):+.2f}/trip)")
