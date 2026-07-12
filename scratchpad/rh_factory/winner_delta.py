"""WINNER-DELTA on <1h pools (candidate factory mine #1).

The 91 audited day-robust winner wallets ran 88% win / +$9,128 on <1h-old
pools while OUR scalps are net red there (and mostly BLOCKED there: every
dip scalp carries min_pool_age_h=1.0). Reconstruct what the winners' <1h
entries looked like AT ENTRY TIME vs the repeat pure-ontape LOSERS' <1h
entries on the SAME tapes, and vs our own paper entries — the separating
signature = candidate #1's gates.

Cohort definitions are verbatim from rh_history/scripts/hist_decode.py
(winners: net>+$1 in >=3 pools, tot>0, pure_ontape, pos_days>=2; losers:
net<-$1 in >=3 pools, tot<0, pure_ontape). Features per entry:
  dip600   px vs prior-600s high (pct; None if no prior px)
  age_s    seconds from pool creation (registry ts)
  nf120    net USD inflow, prior 120s (strictly before the entry)
  b30/s30  buy/sell USD, prior 30s;  nb30 buy count
  dbuy120  DISTINCT buyer makers, prior 120s (tapes carry makers)
  arc      px vs pool's FIRST taped px (pct)
  athdd    px vs pool's running max px (pct)
  vol_pre  cum USD volume before entry;  nsw_pre swaps before entry
Output: rh_factory/winner_delta.json + console tables.
"""
import bisect
import collections
import glob
import json
import os
import statistics
from datetime import datetime, timezone

HIST = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
TAPES = r"C:\Users\jcole\multichain-bot\scratchpad\robinhood_tapes"
OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_factory"

REG = {}
for line in open(os.path.join(HIST, "pools_registry.jsonl"), encoding="utf-8"):
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
hist12 = set()
for fp in glob.glob(os.path.join(HIST, "hist_*.jsonl")):
    hist12.add(os.path.basename(fp)[5:-6])
    rows = load_file(fp)
    if rows:
        rows.sort(key=lambda r: (r["t"], r.get("block", 0)))
        trades_by_pool[rows[0]["pair"]] = rows
for fp in glob.glob(os.path.join(TAPES, "tape_*.jsonl")):
    if os.path.basename(fp)[5:-6] in hist12:
        continue
    rows = load_file(fp)
    if rows:
        pair = rows[0]["pair"]
        if pair in trades_by_pool:
            continue
        rows.sort(key=lambda r: r["t"])
        trades_by_pool[pair] = rows
print(f"[load] {len(trades_by_pool)} pools")

# per-pool arrays
pool_arr = {}
for p, rows in trades_by_pool.items():
    ts = [r["t"] for r in rows]
    px = [r.get("px") or 0.0 for r in rows]
    sv = [r["volume_usd"] if r["kind"] == "buy" else -r["volume_usd"]
          for r in rows]
    cum = []
    c = 0.0
    for v in sv:
        c += v
        cum.append(c)
    vol = []
    c = 0.0
    for r in rows:
        c += r["volume_usd"]
        vol.append(c)
    pool_arr[p] = (ts, px, cum, vol, rows)

# ── (maker,pool) ledgers + cohorts (verbatim hist_decode definitions) ───────
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
                         "ls": None, "entries": []}
            maker_pools[m].add(p)
        if d["kind"] == "buy":
            r["b"] += d["volume_usd"]
            r["nb"] += 1
            if r["fb"] is None:
                r["fb"] = d["t"]
            r["entries"].append((d["t"], d.get("px") or 0.0, d["volume_usd"]))
        else:
            r["s"] += d["volume_usd"]
            r["ns"] += 1
            r["ls"] = d["t"]


def classify(r):
    if r["ns"] == 0:
        return "open"
    if r["b"] == 0:
        return "sell_only"
    if r["s"] >= 0.7 * r["b"]:
        return "closed"
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

winners, losers = {}, {}
for m, lst in maker_realized.items():
    pos = [x for x in lst if x[1] > 1.0]
    neg = [x for x in lst if x[1] < -1.0]
    tot = sum(x[1] for x in lst)
    day_net = collections.defaultdict(float)
    for p, net, cls, day, r in lst:
        day_net[day] += net
    pos_days = sum(1 for v in day_net.values() if v > 0)
    has_so = any(x[2] == "sell_only" for x in lst)
    mixed = has_so and any(x[2] == "closed" for x in lst)
    cls3 = ("pure_sell_only" if has_so and not mixed
            else "mixed" if mixed else "pure_ontape")
    if len(pos) >= 3 and tot > 0 and cls3 == "pure_ontape" and pos_days >= 2:
        winners[m] = True
    elif len(neg) >= 3 and tot < 0 and cls3 == "pure_ontape":
        losers[m] = True
print(f"[cohort] audited day-robust winners={len(winners)} "
      f"pure-ontape repeat losers={len(losers)}")


# ── entry-time features ──────────────────────────────────────────────────────
def feats(p, t, pxe):
    ts, px, cum, vol, rows = pool_arr[p]
    j = bisect.bisect_left(ts, t)          # strictly before entry
    if j <= 0:
        return None
    i600 = bisect.bisect_left(ts, t - 600.0)
    i120 = bisect.bisect_left(ts, t - 120.0)
    i30 = bisect.bisect_left(ts, t - 30.0)
    prior = [px[k] for k in range(i600, j) if px[k] > 0]
    dip = (pxe / max(prior) - 1) * 100.0 if (prior and pxe) else None
    nf120 = cum[j - 1] - (cum[i120 - 1] if i120 > 0 else 0.0)
    b30 = s30 = 0.0
    nb30 = 0
    for k in range(i30, j):
        r = rows[k]
        if r["kind"] == "buy":
            b30 += r["volume_usd"]
            nb30 += 1
        else:
            s30 += r["volume_usd"]
    dbuy = len({rows[k]["maker"] for k in range(i120, j)
                if rows[k]["kind"] == "buy" and rows[k]["maker"]})
    first_px = next((x for x in px if x > 0), None)
    run_max = max((px[k] for k in range(0, j) if px[k] > 0), default=None)
    arc = ((pxe / first_px - 1) * 100.0
           if (first_px and pxe) else None)
    athdd = ((pxe / run_max - 1) * 100.0 if (run_max and pxe) else None)
    creation = REG.get(p, {}).get("ts")
    age_s = (t - creation) if creation else None
    return {"dip600": dip, "age_s": age_s, "nf120": nf120,
            "b30": b30, "s30": s30, "nb30": nb30, "dbuy120": dbuy,
            "arc": arc, "athdd": athdd,
            "vol_pre": vol[j - 1], "nsw_pre": j}


def collect(makers, label):
    ents = []
    for m in makers:
        for p, net, cls, day, r in maker_realized[m]:
            if cls != "closed":
                continue
            creation = REG.get(p, {}).get("ts")
            if not creation:
                continue
            trip_age_h = (r["fb"] - creation) / 3600.0
            if not (0 <= trip_age_h < 1.0):
                continue
            for (te, pxe, sz) in r["entries"]:
                f = feats(p, te, pxe)
                if f is None:
                    continue
                f["net_trip"] = net
                f["size"] = sz
                f["pool"] = p
                f["day"] = day
                ents.append(f)
    print(f"[{label}] <1h-band entries: {len(ents)} "
          f"({len({e['pool'] for e in ents})} pools, "
          f"{len({e['day'] for e in ents})} days)")
    return ents


def q(vals, p):
    vals = [v for v in vals if v is not None]
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(p * len(s))))]


def dist_table(we, le, keys):
    print(f"\n{'feature':9s} | {'WINNER p25/p50/p75':>28s} | "
          f"{'LOSER p25/p50/p75':>28s}")
    out = {}
    for k in keys:
        wv = [e[k] for e in we if e[k] is not None]
        lv = [e[k] for e in le if e[k] is not None]
        out[k] = {"w": [q(wv, .25), q(wv, .5), q(wv, .75)],
                  "l": [q(lv, .25), q(lv, .5), q(lv, .75)]}
        print(f"{k:9s} | {q(wv,.25):8.1f} {q(wv,.5):8.1f} {q(wv,.75):8.1f} | "
              f"{q(lv,.25):8.1f} {q(lv,.5):8.1f} {q(lv,.75):8.1f}")
    return out


W = collect(winners, "winners")
L = collect(losers, "losers")
KEYS = ["dip600", "age_s", "nf120", "b30", "s30", "nb30", "dbuy120",
        "arc", "athdd", "vol_pre", "nsw_pre"]
tables = dist_table(W, L, KEYS)

# share views the gates care about
for label, E in (("WINNER", W), ("LOSER", L)):
    n = len(E)
    if not n:
        continue
    dips = [e["dip600"] for e in E if e["dip600"] is not None]
    print(f"\n[{label}] n={n}")
    print(f"  strength (nf120>0): "
          f"{sum(1 for e in E if e['nf120'] > 0)}/{n}")
    print(f"  dip<=-3%: {sum(1 for d in dips if d <= -3)}/{len(dips)}  "
          f"dip<=-12%: {sum(1 for d in dips if d <= -12)}/{len(dips)}  "
          f"dip>=0 (at/above high): {sum(1 for d in dips if d >= 0)}/{len(dips)}")
    print(f"  age<=10m: {sum(1 for e in E if (e['age_s'] or 9e9) <= 600)}/{n}  "
          f"age 10-30m: {sum(1 for e in E if 600 < (e['age_s'] or 9e9) <= 1800)}/{n}  "
          f"age 30-60m: {sum(1 for e in E if 1800 < (e['age_s'] or 9e9) <= 3600)}/{n}")
    print(f"  dbuy120>=3: {sum(1 for e in E if e['dbuy120'] >= 3)}/{n}  "
          f"nf120>=$150: {sum(1 for e in E if e['nf120'] >= 150)}/{n}  "
          f"vol_pre>=$5k: {sum(1 for e in E if e['vol_pre'] >= 5000)}/{n}")
    print(f"  athdd>=-15: {sum(1 for e in E if (e['athdd'] is not None and e['athdd'] >= -15))}"
          f"/{sum(1 for e in E if e['athdd'] is not None)}  "
          f"arc>0: {sum(1 for e in E if (e['arc'] is not None and e['arc'] > 0))}"
          f"/{sum(1 for e in E if e['arc'] is not None)}")

# ── our paper fleet's entries for contrast (stamped features) ────────────────
ours = []
led = os.path.join(TAPES, "rh_paper_trades.jsonl")
if os.path.exists(led):
    for ln in open(led, encoding="utf-8"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("ev") == "buy":
            ours.append(d)
u1h = [d for d in ours if (d.get("age_h") or 99) < 1.0]
print(f"\n[ours] paper buys={len(ours)}; <1h-band buys={len(u1h)} "
      f"(bots: {collections.Counter(d.get('bot_id') or 'legacy' for d in u1h)})")
print(f"[ours] all-buys dip_pct p25/p50/p75: "
      f"{q([d.get('dip_pct') for d in ours],.25):.1f} "
      f"{q([d.get('dip_pct') for d in ours],.5):.1f} "
      f"{q([d.get('dip_pct') for d in ours],.75):.1f} | age_h p50 "
      f"{q([d.get('age_h') for d in ours],.5):.2f}")

json.dump({"winner_entries": len(W), "loser_entries": len(L),
           "winner_pools": len({e['pool'] for e in W}),
           "loser_pools": len({e['pool'] for e in L}),
           "tables": tables,
           "our_1h_buys": len(u1h)},
          open(os.path.join(OUT, "winner_delta.json"), "w"), indent=1)
print("\nwrote winner_delta.json")
