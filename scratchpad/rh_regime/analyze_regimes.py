"""RH regime analysis — two-window discipline over mined trips.

Reads scratchpad/rh_regime/{trips.jsonl.gz, windows.json}; prints per-axis
tables and writes rulebook_v1.json. Every axis is interacted with POOL AGE
BAND (young <6h / mid 6-24h / aged >24h) per AxiS's required hypothesis.

Splits:
  chrono : W1 = 2026-07-01..05 (human era + early), W2 = 2026-07-06..11 (incl bot era)
  parity : odd vs even day-of-month (era-balanced robustness)
A candidate rule ships as a GATE only if its direction holds in BOTH chrono
halves AND BOTH parity halves; everything else ships as a stamp.
"""
import gzip
import json
import os
import statistics
from collections import defaultdict

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_regime"
WIN_S = 1800

W = json.load(open(os.path.join(OUT, "windows.json")))["windows"]
WI = {int(k): v for k, v in W.items()}
# cols: n, buy_eth, sell_eth, buy_n, sell_n, distinct_pools, new_pools, eth_usd


def buy_share(wk):
    v = WI.get(wk)
    if not v:
        return None
    tot = v[1] + v[2]
    return v[1] / tot if tot > 0 else None


def distinct_pools(wk):
    v = WI.get(wk)
    return v[5] if v else None


def eth_usd(wk):
    v = WI.get(wk)
    return v[7] if v else None


def eth_move(wk, back_wins):
    a, b = eth_usd(wk - 1), eth_usd(wk - 1 - back_wins)
    if a and b:
        return (a / b - 1) * 100
    return None


trips = []
with gzip.open(os.path.join(OUT, "trips.jsonl.gz"), "rt",
               encoding="utf-8") as f:
    for ln in f:
        t = json.loads(ln)
        if t["day"] < "2026-07-01":
            continue
        trips.append(t)

for t in trips:
    a = t["age_h"]
    t["band"] = (None if a is None else
                 "young" if a < 6 else "mid" if a < 24 else "aged")
    t["chrono"] = "W1" if t["day"] <= "2026-07-05" else "W2"
    t["parity"] = "odd" if int(t["day"][8:10]) % 2 == 1 else "even"
    t["bshare"] = buy_share(t["win"] - 1)     # PRIOR window (decision-time)
    t["dpools"] = distinct_pools(t["win"] - 1)
    t["eth1h"] = eth_move(t["win"], 2)
    t["eth24h"] = eth_move(t["win"], 48)

dips = [t for t in trips if t["kind"] == "dip" and t["band"]]
pops = [t for t in trips if t["kind"] == "pop" and t["band"]]
print(f"trips: dip={len(dips)} pop={len(pops)} "
      f"days {min(t['day'] for t in trips)}..{max(t['day'] for t in trips)}")


def cell(rows):
    if not rows:
        return None
    rets = [r["ret20"] for r in rows]
    return {"n": len(rows),
            "win_pct": round(100 * sum(1 for x in rets if x > 0) / len(rets), 1),
            "med_ret": round(statistics.median(rets), 2),
            "rug_pct": round(100 * sum(r["rug"] for r in rows) / len(rows), 1),
            "stale_pct": round(100 * sum(1 for r in rows
                                         if r["res"] == "stale") / len(rows), 1)}


HOUR_BLOCKS = {"22-01": (22, 23, 0, 1), "02-07": (2, 3, 4, 5, 6, 7),
               "08-10": (8, 9, 10), "11-13": (11, 12, 13),
               "14-18": (14, 15, 16, 17, 18), "19-21": (19, 20, 21)}


def hour_block(h):
    for k, hs in HOUR_BLOCKS.items():
        if h in hs:
            return k


def bshare_bin(x):
    if x is None:
        return None
    return ("<50" if x < 0.50 else "50-55" if x < 0.55 else
            "55-62" if x < 0.62 else ">62")


def npph_bin(x):
    return "bot" if x >= 200 else "human"


def tercile_binner(rows, key):
    vals = sorted(r[key] for r in rows if r[key] is not None)
    if len(vals) < 30:
        return lambda r: None
    lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]

    def b(r):
        x = r[key]
        if x is None:
            return None
        return "lo" if x < lo else "mid" if x < hi else "hi"
    b.cuts = (lo, hi)
    return b


def table(rows, axis_fn, axis_name, splits=("chrono", "parity"),
          store=None):
    print(f"\n=== {axis_name} x age band (dip trips) ===")
    out = defaultdict(dict)
    for split in splits:
        halves = sorted({r[split] for r in rows})
        for band in ("young", "mid", "aged"):
            for half in halves:
                sub = [r for r in rows if r["band"] == band
                       and r[split] == half]
                base = cell(sub)
                groups = defaultdict(list)
                for r in sub:
                    g = axis_fn(r)
                    if g is not None:
                        groups[g].append(r)
                for g in sorted(groups):
                    c = cell(groups[g])
                    out[f"{band}|{half}"][str(g)] = c
                    d = (c["win_pct"] - base["win_pct"]) if base else 0
                    print(f"  {split:6} {half:4} {band:5} {str(g):>6}: "
                          f"n={c['n']:5d} win={c['win_pct']:5.1f}% "
                          f"(base {base['win_pct']:5.1f} d={d:+5.1f}) "
                          f"med={c['med_ret']:+6.2f} rug={c['rug_pct']:4.1f}%")
    if store is not None:
        store[axis_name] = {k: v for k, v in out.items()}
    return out


R = {}
table(dips, lambda r: hour_block(r["hour"]), "hour_block", store=R)
table(dips, lambda r: r["hour"], "hour", splits=("chrono",), store=R)
table(dips, lambda r: npph_bin(r["npph"]), "discovery_regime", store=R)
table(dips, lambda r: bshare_bin(r["bshare"]), "buy_share_prior30m", store=R)
dp = tercile_binner(dips, "dpools")
table(dips, dp, "distinct_pools_prior30m", store=R)
if hasattr(dp, "cuts"):
    print(f"  distinct_pools cuts: {dp.cuts}")
e1 = tercile_binner(dips, "eth1h")
table(dips, e1, "eth_1h_move", store=R)
if hasattr(e1, "cuts"):
    print(f"  eth1h cuts: {e1.cuts}")
e24 = tercile_binner(dips, "eth24h")
table(dips, e24, "eth_24h_move", store=R)
if hasattr(e24, "cuts"):
    print(f"  eth24h cuts: {e24.cuts}")

print("\n=== POP follow-through: hour_block x band ===")
table(pops, lambda r: hour_block(r["hour"]), "pop_hour_block", store=R)
table(pops, lambda r: npph_bin(r["npph"]), "pop_discovery_regime", store=R)

# base rates per band per half (the young-flat test needs these)
print("\n=== base rates: band x half ===")
bases = {}
for split in ("chrono", "parity"):
    for band in ("young", "mid", "aged"):
        for half in sorted({r[split] for r in dips}):
            c = cell([r for r in dips if r["band"] == band
                      and r[split] == half])
            bases[f"{band}|{half}"] = c
            if c:
                print(f"  {split:6} {half:4} {band:5}: n={c['n']:5d} "
                      f"win={c['win_pct']}% med={c['med_ret']:+.2f} "
                      f"rug={c['rug_pct']}%")
R["base_rates"] = bases

json.dump(R, open(os.path.join(OUT, "rulebook_v1_tables.json"), "w"),
          indent=1)
print("\nwrote rulebook_v1_tables.json")
