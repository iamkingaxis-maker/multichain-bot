"""Behavioral decode of IN-POND wallets from activity data."""
import json
from collections import defaultdict

cls = {r["wallet"]: r["class"] for r in json.load(open("scratchpad/_toptrader_class.json"))}
tok = json.load(open("scratchpad/_toptrader_tokens.json"))
MAJORS = {"So11111111111111111111111111111111111111112"}

data = {}
for line in open("scratchpad/_toptrader_activity.jsonl"):
    d = json.loads(line)
    if cls.get(d["wallet"]) == "IN-POND":
        data[d["wallet"]] = d["trades"]
data = {w: t for w, t in data.items() if len([x for x in t if x["side"] == "buy"]) >= 10}
print("in-pond wallets with >=10 buys:", len(data))

# --- union check: overlapping token sets between wallets ---
sets = {w: {t["mint"] for t in tr} for w, tr in data.items()}
ws = list(sets)
for i in range(len(ws)):
    for j in range(i + 1, len(ws)):
        inter = len(sets[ws[i]] & sets[ws[j]])
        uni = len(sets[ws[i]] | sets[ws[j]])
        if inter / uni > 0.3:
            print(f"UNION? {ws[i][:8]} ~ {ws[j][:8]} jaccard={inter/uni:.2f} shared={inter}")

def pctile(v, p):
    v = sorted(v)
    return v[int(p / 100 * (len(v) - 1))] if v else None

# --- per-wallet + pooled stats ---
pooled = defaultdict(list)
print(f"\n{'wallet':<10}{'buys':>5}{'medbuy$':>8}{'p90buy$':>8}{'sells':>6}{'sellWR':>7}"
      f"{'medhold_m':>10}{'reentry%':>9}{'scalein%':>9}{'medret%':>8}")
for w, tr in sorted(data.items()):
    tr = sorted(tr, key=lambda t: t["ts"])
    buys = [t for t in tr if t["side"] == "buy" and t["mint"] not in MAJORS]
    sells = [t for t in tr if t["side"] == "sell" and t["mint"] not in MAJORS]
    bsz = [float(t["usd"]) for t in buys if t["usd"]]
    # matched realized per sell: proceeds(cost_usd) vs gmgn basis(buy_cost_usd)
    rets, wins = [], 0
    for s in sells:
        try:
            pr, ba = float(s["usd"]), float(s["buy_cost_usd"])
        except (TypeError, ValueError):
            continue
        if ba <= 0:
            continue
        r = (pr - ba) / ba * 100
        rets.append(r)
        if r > 0:
            wins += 1
    # hold: sell ts - most recent prior buy ts same mint
    lastbuy, holds = {}, []
    first_low, reent, scale = {}, 0, 0
    bought_once = defaultdict(int)
    sold_mints = set()
    for t in tr:
        m = t["mint"]
        if t["side"] == "buy":
            if m in sold_mints:
                reent += 1
            if bought_once[m] >= 1 and m not in sold_mints:
                scale += 1
            bought_once[m] += 1
            lastbuy[m] = t["ts"]
        else:
            sold_mints.add(m)
            if m in lastbuy:
                holds.append((t["ts"] - lastbuy[m]) / 60)
    hours = [((t["ts"] % 86400) // 3600) for t in buys]
    nb = len(buys)
    print(f"{w[:8]:<10}{nb:>5}{pctile(bsz,50):>8.0f}{pctile(bsz,90):>8.0f}{len(rets):>6}"
          f"{wins/max(len(rets),1):>7.0%}{pctile(holds,50) or 0:>10.1f}"
          f"{reent/max(nb,1):>9.0%}{scale/max(nb,1):>9.0%}{pctile(rets,50) or 0:>8.1f}")
    pooled["buy_usd"] += bsz
    pooled["rets"] += rets
    pooled["holds"] += holds
    pooled["hours"] += hours

print("\n--- POOLED (in-pond cohort) ---")
b = pooled["buy_usd"]
print(f"buys n={len(b)} p10={pctile(b,10):.0f} p25={pctile(b,25):.0f} med={pctile(b,50):.0f} "
      f"p75={pctile(b,75):.0f} p90={pctile(b,90):.0f} p99={pctile(b,99):.0f}")
r = pooled["rets"]
print(f"sell-level matched returns n={len(r)} win%={sum(1 for x in r if x>0)/len(r):.0%} "
      f"p10={pctile(r,10):.1f} p25={pctile(r,25):.1f} med={pctile(r,50):.1f} "
      f"p75={pctile(r,75):.1f} p90={pctile(r,90):.1f} p99={pctile(r,99):.1f}")
h = pooled["holds"]
print(f"hold(min, last-buy->sell) n={len(h)} p10={pctile(h,10):.1f} p25={pctile(h,25):.1f} "
      f"med={pctile(h,50):.1f} p75={pctile(h,75):.1f} p90={pctile(h,90):.1f}")
hh = pooled["hours"]
from collections import Counter
hc = Counter(hh)
print("buy hour-of-day UTC:", " ".join(f"{h:02d}:{hc.get(h,0)}" for h in range(24)))
json.dump(list(data.keys()), open("scratchpad/_inpond_wallets.json", "w"))
