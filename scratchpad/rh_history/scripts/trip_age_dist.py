"""Aged-pool racer threshold-setting: hold + return distributions of the
AUDITED day-robust winners (same cohort definition as hist_decode.py),
split by entry pool age, so TP ladder / time box / age floor come from data.
Read-only over scratchpad/rh_history + robinhood_tapes local files.
"""
import sys, json, os, glob, time, collections, statistics
from datetime import datetime, timezone

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
TAPES = r"C:\Users\jcole\multichain-bot\scratchpad\robinhood_tapes"

REG = {}
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    REG[d["pool"]] = d

def pt(ts):
    return datetime.fromisoformat(ts).timestamp()

def load_file(fp):
    rows = []
    for ln in open(fp, encoding="utf-8"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("kind") not in ("buy", "sell") or not d.get("pair"):
            continue
        d["t"] = pt(d["ts"])
        rows.append(d)
    return rows

trades_by_pool = {}
hist_pool12 = set()
for fp in glob.glob(os.path.join(OUT, "hist_*.jsonl")):
    hist_pool12.add(os.path.basename(fp)[5:-6])
    rows = load_file(fp)
    if rows:
        rows.sort(key=lambda r: (r["t"], r.get("block", 0)))
        trades_by_pool[rows[0]["pair"]] = rows
for fp in glob.glob(os.path.join(TAPES, "tape_*.jsonl")):
    p12 = os.path.basename(fp)[5:-6]
    if p12 in hist_pool12:
        continue
    rows = load_file(fp)
    if rows:
        pair = rows[0]["pair"]
        if pair in trades_by_pool:
            continue
        rows.sort(key=lambda r: r["t"])
        trades_by_pool[pair] = rows

MP = {}
maker_pools = collections.defaultdict(set)
for p, rows in trades_by_pool.items():
    for d in rows:
        m = d["maker"]
        if not m:
            continue
        k = (m, p)
        r = MP.get(k)
        if r is None:
            r = MP[k] = {"b": 0.0, "s": 0.0, "nb": 0, "ns": 0, "fb": None,
                         "ls": None, "bs": [], "entries": []}
            maker_pools[m].add(p)
        if d["kind"] == "buy":
            r["b"] += d["volume_usd"]; r["nb"] += 1
            r["bs"].append(d["volume_usd"])
            if r["fb"] is None:
                r["fb"] = d["t"]
            r["entries"].append((d["t"], d.get("px") or 0.0, d["volume_usd"]))
        else:
            r["s"] += d["volume_usd"]; r["ns"] += 1
            r["ls"] = d["t"]

def classify(r):
    if r["ns"] == 0: return "open"
    if r["b"] == 0: return "sell_only"
    if r["s"] >= 0.7 * r["b"]: return "closed"
    return "partial"

def day_of(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")

for k, r in MP.items():
    r["net"] = r["s"] - r["b"]
    r["cls"] = classify(r)

maker_realized = collections.defaultdict(list)
for (m, p), r in MP.items():
    if r["cls"] in ("closed", "sell_only"):
        maker_realized[m].append((p, r["net"], r["cls"], day_of(r["ls"]), r))

winners = {}
for m, lst in maker_realized.items():
    pos_pools = [x for x in lst if x[1] > 1.0]
    tot = sum(x[1] for x in lst)
    day_net = collections.defaultdict(float)
    for p, net, cls, day, r in lst:
        day_net[day] += net
    pos_days = sum(1 for v in day_net.values() if v > 0)
    has_sellonly = any(x[2] == "sell_only" for x in lst)
    mixed = has_sellonly and any(x[2] == "closed" for x in lst)
    cls3 = ("pure_sell_only" if has_sellonly and not mixed
            else "mixed" if mixed else "pure_ontape")
    if len(pos_pools) >= 3 and tot > 0 and cls3 == "pure_ontape" and pos_days >= 2:
        winners[m] = True

print(f"audited day-robust winners: {len(winners)}")

def q(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(p * len(s))))
    return s[i]

# per-ledger (maker,pool) trips of the audited winners, split by entry pool age
BANDS = [("age<1h", 0, 1), ("1-6h", 1, 6), ("6-24h", 6, 24), (">24h", 24, 1e9)]
stats = {b[0]: {"holds": [], "ret": [], "net": [], "n_win": 0, "n_loss": 0}
         for b in BANDS}
for m in winners:
    for p, net, cls, day, r in maker_realized[m]:
        if cls != "closed" or not r["fb"] or not r["ls"] or r["b"] <= 0:
            continue
        creation = REG.get(p, {}).get("ts")
        if not creation:
            continue
        age_h = (r["fb"] - creation) / 3600.0
        hold_m = (r["ls"] - r["fb"]) / 60.0
        ret_pct = (r["s"] / r["b"] - 1.0) * 100.0
        for name, lo, hi in BANDS:
            if lo <= age_h < hi:
                st = stats[name]
                st["holds"].append(hold_m)
                st["ret"].append(ret_pct)
                st["net"].append(net)
                if net > 0:
                    st["n_win"] += 1
                else:
                    st["n_loss"] += 1
                break

for name, lo, hi in BANDS:
    st = stats[name]
    n = len(st["holds"])
    if not n:
        print(f"{name}: n=0")
        continue
    wr = st["n_win"] / n * 100
    print(f"\n{name}: n_trips={n} win%={wr:.0f} sum_net=${sum(st['net']):+,.0f}")
    print(f"  hold_m  p25={q(st['holds'],.25):.1f} p50={q(st['holds'],.5):.1f} "
          f"p75={q(st['holds'],.75):.1f} p90={q(st['holds'],.9):.1f}")
    print(f"  ret_pct p25={q(st['ret'],.25):+.1f} p50={q(st['ret'],.5):+.1f} "
          f"p75={q(st['ret'],.75):+.1f} p90={q(st['ret'],.9):+.1f}")
    wins = [x for x in st["ret"] if x > 0]
    if wins:
        print(f"  WINNING trips ret p25={q(wins,.25):+.1f} p50={q(wins,.5):+.1f} "
              f"p75={q(wins,.75):+.1f} p90={q(wins,.9):+.1f} (n={len(wins)})")
    wh = [h for h, x in zip(st["holds"], st["ret"]) if x > 0]
    if wh:
        print(f"  WINNING trips hold_m p25={q(wh,.25):.1f} p50={q(wh,.5):.1f} "
              f"p75={q(wh,.75):.1f} p90={q(wh,.9):.1f}")
