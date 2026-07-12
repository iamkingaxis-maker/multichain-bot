"""Reconstruct closed RH paper trips from partial-sell log + measure variance levers."""
import json, statistics, sys
from collections import defaultdict, Counter

PATH = "scratchpad/robinhood_tapes/rh_paper_trades.jsonl"
ENTRY_USD = 25.0

rows = []
with open(PATH) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("ts", "")[:4] == "1970":
            continue
        rows.append(d)

# index buys by pool (chronological) so we can match a trip's entry
buys_by_pool = defaultdict(list)
for d in rows:
    if d.get("ev") == "buy":
        buys_by_pool[d.get("pool")].append(d)

def ts_of(d):
    return d.get("ts", "")

# rug_signals stamps keyed by pool (for catastrophe/rug-gate shadow)
rug_by_pool = defaultdict(list)
for d in rows:
    if d.get("ev") == "rug_signals":
        rug_by_pool[d.get("pool")].append(d)

# group sells by (bot_id, pool), split into trips at fully==True
sells_by_key = defaultdict(list)
for d in rows:
    if d.get("ev") == "sell":
        sells_by_key[(d.get("bot_id"), d.get("pool"))].append(d)

trips = []
for (bot, pool), sells in sells_by_key.items():
    sells.sort(key=ts_of)
    cur = []
    for s in sells:
        cur.append(s)
        if s.get("fully"):
            # close a trip
            pnl_usd = sum(x.get("pnl_usd", 0.0) or 0.0 for x in cur)
            first_ts = ts_of(cur[0])
            last_ts = ts_of(cur[-1])
            # match entry buy: latest buy on pool before first sell
            entry = None
            for b in buys_by_pool.get(pool, []):
                if ts_of(b) <= first_ts:
                    entry = b
                else:
                    break
            kinds = [x.get("kind") for x in cur]
            trips.append({
                "bot": bot, "pool": pool,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_usd / ENTRY_USD * 100.0,
                "first_sell_ts": first_ts, "last_sell_ts": last_ts,
                "entry_ts": ts_of(entry) if entry else None,
                "dip_pct": entry.get("dip_pct") if entry else None,
                "liq": entry.get("liq") if entry else None,
                "n_slices": len(cur),
                "kinds": kinds,
                "terminal": kinds[-1],
                "has_hard_stop": "HARD_STOP" in kinds,
                "has_tp1": "TP1" in kinds,
                "day": last_ts[:10],
                "rug_stamped": pool in rug_by_pool,
            })
            cur = []

# hold seconds
from datetime import datetime
def parse(t):
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except Exception:
        return None
for t in trips:
    a = parse(t["entry_ts"]); b = parse(t["last_sell_ts"])
    t["hold_s"] = (b - a).total_seconds() if a and b else None

def stats(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return dict(n=len(vals), mean=vals[0] if vals else 0, stdev=0, dnstd=0)
    mean = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    downs = [min(v, 0.0) for v in vals]
    dnstd = statistics.pstdev(downs)
    return dict(n=len(vals), mean=round(mean, 3), stdev=round(sd, 3),
                dnstd=round(dnstd, 3),
                p05=round(sorted(vals)[max(0, int(0.05 * len(vals)))], 2),
                worst=round(min(vals), 2), best=round(max(vals), 2))

if __name__ == "__main__":
    print("TOTAL closed trips:", len(trips))
    print("By bot:", dict(Counter(t["bot"] for t in trips).most_common()))
    allp = [t["pnl_pct"] for t in trips]
    print("ALL per-trip pnl_pct:", stats(allp))
    # dump for reuse
    with open("scratchpad/variance_reduction/_rh_trips.json", "w") as f:
        json.dump(trips, f)
    print("wrote _rh_trips.json")
