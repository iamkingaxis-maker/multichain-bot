"""Classify wallets: IN-POND / ADJACENT / OUT vs our mcap/liq/age ranges."""
import json, time

tok = json.load(open("scratchpad/_toptrader_tokens.json"))
wmeta = {w["address"]: w for w in json.load(open("scratchpad/_toptrader_wallets.json"))}

MAJORS = {"So11111111111111111111111111111111111111112"}

rows = []
for line in open("scratchpad/_toptrader_activity.jsonl"):
    d = json.loads(line)
    a, trades = d["wallet"], d["trades"]
    buys = [t for t in trades if t["side"] == "buy" and t["mint"] not in MAJORS]
    n = len(buys)
    if n == 0:
        rows.append((a, 0, 0, 0, 0, 0, "NO-BUYS")); continue
    pond = adj = out = young_unres = 0
    liq_band = 0
    liq_known = 0
    for t in buys:
        try:
            mc = float(t["price_usd"]) * float(t["supply"])
        except (TypeError, ValueError):
            mc = None
        info = tok.get(t["mint"])
        age_d = None
        if info and info.get("created"):
            age_d = (t["ts"] - info["created"] / 1000) / 86400
        if info and info.get("liq") is not None:
            liq_known += 1
            if 10_000 <= info["liq"] <= 60_000:
                liq_band += 1
        in_mc = mc is not None and 100_000 <= mc <= 5_000_000
        in_age = age_d is not None and age_d < 7
        if in_mc and (in_age or (info is None and mc <= 5_000_000)):
            pond += 1
        elif info is None and (mc is None or mc < 100_000):
            young_unres += 1  # dead pump.fun token, sub-100k = below our floor
        elif mc is not None and mc > 5_000_000:
            adj += 1
        else:
            out += 1
    w = wmeta[a]
    pct = pond / n
    if pct >= 0.25:
        cls = "IN-POND"
    elif (adj / n) >= 0.5:
        cls = "ADJACENT"
    else:
        cls = "OUT/MIXED"
    rows.append((a, n, pond, adj, out + young_unres, liq_band / max(liq_known, 1), cls,
                 w["realized_profit_7d"], w["winrate_7d"], w["txs_7d"]))

rows.sort(key=lambda r: -(r[2] / max(r[1], 1)))
print(f"{'wallet':<10}{'buys':>5}{'pond':>6}{'pond%':>7}{'adj':>5}{'other':>6}{'liqband%':>9}  class      real7d$   wr    txs7d")
for r in rows:
    if len(r) < 8:
        print(f"{r[0][:8]:<10} {r[6]}"); continue
    a, n, p, adj, oth, lb, cls, rp, wr, tx = r
    print(f"{a[:8]:<10}{n:>5}{p:>6}{p/n:>7.0%}{adj:>5}{oth:>6}{lb:>9.0%}  {cls:<9}{rp:>9.0f}  {wr:>4.2f}{tx:>8}")

npond = sum(1 for r in rows if len(r) >= 8 and r[6] == "IN-POND")
print(f"\nIN-POND: {npond}/{len(rows)}")
json.dump([{"wallet": r[0], "class": r[6] if len(r) >= 8 else "NO-BUYS"} for r in rows],
          open("scratchpad/_toptrader_class.json", "w"), indent=1)
