"""Harvest recent trade activity for top wallets (checkpointed, resumable)."""
import json, time, os, sys
from curl_cffi import requests as cr

OUT = "scratchpad/_toptrader_activity.jsonl"
NOW = time.time()
CUTOFF = NOW - 5 * 86400   # last 5 days
MAX_TRADES = 140           # per wallet cap (7 pages)
DEADLINE = time.time() + 480  # stay under bash timeout; resume on rerun

lb = json.load(open("scratchpad/_gmgn_leaderboard.json"))

# wallet selection: axiom-tagged wallets from pnl7d top-100, then smart_degen fill
seen, wallets = set(), []
for src in ("pnl7d", "smart_degen"):
    for w in lb[src]:
        a = w["address"]
        if a in seen:
            continue
        tags = w.get("tags") or []
        if "axiom" not in tags:
            continue
        seen.add(a)
        wallets.append({"address": a, "src": src, "tags": tags,
                        "realized_profit_7d": float(w["realized_profit_7d"] or 0),
                        "winrate_7d": float(w["winrate_7d"] or 0),
                        "txs_7d": w["txs_7d"],
                        "avg_cost_7d": float(w["avg_cost_7d"] or 0),
                        "avg_holding_period_7d": float(w["avg_holding_period_7d"] or 0)})
        if len(wallets) >= 60:
            break
    if len(wallets) >= 60:
        break
json.dump(wallets, open("scratchpad/_toptrader_wallets.json", "w"), indent=1)

done = set()
if os.path.exists(OUT):
    for line in open(OUT):
        try:
            done.add(json.loads(line)["wallet"])
        except Exception:
            pass
print(f"{len(wallets)} wallets selected; {len(done)} already harvested")

sess = cr.Session(impersonate="chrome", timeout=20,
                  headers={"Referer": "https://gmgn.ai/", "Accept": "application/json"})

fout = open(OUT, "a")
for wi, w in enumerate(wallets):
    a = w["address"]
    if a in done:
        continue
    if time.time() > DEADLINE:
        print("DEADLINE hit — rerun to resume")
        break
    trades, cursor, pages = [], None, 0
    while len(trades) < MAX_TRADES and pages < 7:
        url = f"https://gmgn.ai/api/v1/wallet_activity/sol?wallet={a}&limit=20"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            r = sess.get(url)
            d = r.json().get("data", {})
        except Exception as e:
            print(f"  {a[:8]} EXC {e}"); time.sleep(3); break
        acts = d.get("activities") or []
        if not acts:
            break
        for t in acts:
            if t["timestamp"] < CUTOFF:
                acts = None
                break
            trades.append({
                "ts": t["timestamp"], "side": t["event_type"],
                "mint": t["token"]["address"], "sym": t["token"].get("symbol"),
                "supply": t["token"].get("total_supply"),
                "usd": t.get("cost_usd"), "buy_cost_usd": t.get("buy_cost_usd"),
                "price_usd": t.get("price_usd"),
                "launchpad": t.get("launchpad_platform") or t.get("launchpad"),
            })
        pages += 1
        if acts is None:
            break
        cursor = d.get("next")
        if not cursor:
            break
        time.sleep(1.1)
    fout.write(json.dumps({"wallet": a, "trades": trades}) + "\n")
    fout.flush()
    print(f"[{wi+1}/{len(wallets)}] {a[:8]} trades={len(trades)}")
    time.sleep(1.1)
fout.close()
print("BATCH DONE")
