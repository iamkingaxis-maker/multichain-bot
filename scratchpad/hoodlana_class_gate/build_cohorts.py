"""Build winner cohort + universe sample from _full_trades.json."""
import json, os
BASE = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(BASE)

t = json.load(open(os.path.join(SP, "_full_trades.json")))
t.sort(key=lambda x: x.get("time") or "")

# net pnl per mint across all sells (sells carry realized pnl USD directly)
net = {}
sells_n = {}
for x in t:
    if x.get("type") == "sell" and x.get("address"):
        a = x["address"]
        try:
            net[a] = net.get(a, 0.0) + float(x.get("pnl") or 0.0)
            sells_n[a] = sells_n.get(a, 0) + 1
        except Exception:
            pass

winners = sorted([a for a, v in net.items() if v > 0])
print(f"closed mints: {len(net)}  winners(net>0): {len(winners)}")

ds = json.load(open(os.path.join(SP, "rug_forensics", "death_split.json")))
alive = set(ds["alive"])
w_alive = [a for a in winners if a in alive]
w_dead_or_unknown = [a for a in winners if a not in alive]
w_dead = [a for a in w_dead_or_unknown if a in set(ds["dead"])]
w_unknown = [a for a in w_dead_or_unknown if a not in set(ds["dead"])]
print(f"winners alive: {len(w_alive)}  dead: {len(w_dead)}  not-in-split: {len(w_unknown)}")

# universe: 50 most recent distinct buy mints regardless of outcome
seen, uni = set(), []
for x in reversed(t):
    if x.get("type") == "buy" and x.get("address") and x["address"] not in seen:
        seen.add(x["address"])
        uni.append({"mint": x["address"], "token": x.get("token"), "buy_time": x.get("time")})
        if len(uni) >= 50:
            break
print(f"universe sample: {len(uni)}  newest buy: {uni[0]['buy_time']}  oldest: {uni[-1]['buy_time']}")

out = {
    "winners_all": winners,
    "winners_alive": w_alive,
    "winners_dead": w_dead,
    "winners_not_in_split": w_unknown,
    "net_pnl_usd": {a: round(net[a], 2) for a in winners},
    "universe_recent50": uni,
}
json.dump(out, open(os.path.join(BASE, "cohorts.json"), "w"), indent=1)
print("saved cohorts.json")
