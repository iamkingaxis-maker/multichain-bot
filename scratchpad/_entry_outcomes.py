"""Per-wallet entry styles + outcome join (matched realized per wallet+mint)."""
import json
from collections import defaultdict

scored = json.load(open("scratchpad/_entry_scored.json"))
cls = {r["wallet"]: r["class"] for r in json.load(open("scratchpad/_toptrader_class.json"))}
mirror = {w for w in cls if w.startswith("YupUTKEj")}

# rebuild sells for realized join
sells = defaultdict(list)   # (w, mint) -> [(proceeds, basis)]
buys_all = defaultdict(list)
for line in open("scratchpad/_toptrader_activity.jsonl"):
    d = json.loads(line)
    w = d["wallet"]
    if cls.get(w) != "IN-POND" or w in mirror:
        continue
    for t in d["trades"]:
        if t["side"] == "sell":
            try:
                sells[(w, t["mint"])].append((float(t["usd"]), float(t["buy_cost_usd"])))
            except (TypeError, ValueError):
                pass
        else:
            try:
                buys_all[(w, t["mint"])].append(float(t["usd"] or 0))
            except (TypeError, ValueError):
                pass

def pct(v, p):
    v = sorted(v)
    return v[int(p / 100 * (len(v) - 1))] if v else None

# per-wallet style
byw = defaultdict(list)
for r in scored:
    byw[r["w"]].append(r)
print(f"{'wallet':<10}{'n':>4}{'dip%':>6}{'brk%':>6}{'med_dd':>8}{'med_age_h':>10}")
for w, rs in sorted(byw.items(), key=lambda x: -len(x[1])):
    dd = [r["dd60"] * 100 for r in rs]
    ages = [r["age_h"] for r in rs if r["age_h"] is not None]
    print(f"{w[:8]:<10}{len(rs):>4}{sum(1 for x in dd if x <= -15)/len(dd):>6.0%}"
          f"{sum(1 for x in dd if x >= -5)/len(dd):>6.0%}{pct(dd,50):>8.1f}"
          f"{(pct(ages,50) or 0):>10.1f}")

# outcome join: first scored buy per (w,mint) sets style; realized = sum(proceeds-basis)
ep = {}
for r in scored:
    k = (r["w"], r["mint"])
    if k not in ep or r["ts"] < ep[k]["ts"]:
        ep[k] = r
groups = {"DIP": [], "BREAKOUT": [], "MID": []}
for k, r in ep.items():
    ss = sells.get(k)
    if not ss:
        continue
    proceeds = sum(x[0] for x in ss)
    basis = sum(x[1] for x in ss)
    if basis <= 0:
        continue
    retpct = (proceeds - basis) / basis * 100
    ddp = r["dd60"] * 100
    style = "DIP" if ddp <= -15 else ("BREAKOUT" if ddp >= -5 else "MID")
    groups[style].append(retpct)
print("\nepisode matched realized by entry style (sold portion only):")
for s, v in groups.items():
    if not v:
        continue
    print(f"  {s:<9} n={len(v):>3} win%={sum(1 for x in v if x>0)/len(v):.0%} "
          f"med={pct(v,50):+.1f}% p25={pct(v,25):+.1f}% p75={pct(v,75):+.1f}%")

# unsold-episode check (loss never realized bias)
nosell = sum(1 for k in ep if not sells.get(k))
print(f"\nepisodes with NO sells in window (unrealized/possibly bags): {nosell}/{len(ep)}")

# hour-of-day w/o mirror, in-pond buys only
from collections import Counter
hc = Counter((r["ts"] % 86400) // 3600 for r in scored)
print("pond-buy hours UTC:", " ".join(f"{h:02d}:{hc.get(h,0)}" for h in range(24)))
