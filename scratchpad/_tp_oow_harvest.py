"""Out-of-window harvest: page in-pond wallets' gmgn activity back to 2026-06-23.

Decode window was ~06-30..07-05. Out-of-window = trades with ts < OOW_END.
Checkpointed per wallet.
"""
import json, time, os
from datetime import datetime, timezone
from curl_cffi import requests as cr

OUT = "scratchpad/_tp_oow_activity.jsonl"
OOW_END = datetime(2026, 6, 30, tzinfo=timezone.utc).timestamp()   # decode window start
OOW_START = datetime(2026, 6, 23, tzinfo=timezone.utc).timestamp()  # 7d earlier
MAX_PAGES = 40
DEADLINE = time.time() + 520

wallets = json.load(open("scratchpad/_inpond_wallets.json"))
done = set()
if os.path.exists(OUT):
    for line in open(OUT):
        try:
            done.add(json.loads(line)["wallet"])
        except Exception:
            pass
print(f"{len(wallets)} in-pond wallets; {len(done)} done")

sess = cr.Session(impersonate="chrome", timeout=20,
                  headers={"Referer": "https://gmgn.ai/", "Accept": "application/json"})
fout = open(OUT, "a")
for wi, a in enumerate(wallets):
    if a in done:
        continue
    if time.time() > DEADLINE:
        print("DEADLINE — rerun"); break
    trades, cursor, pages, oldest = [], None, 0, None
    err = None
    while pages < MAX_PAGES:
        url = f"https://gmgn.ai/api/v1/wallet_activity/sol?wallet={a}&limit=20"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            r = sess.get(url)
            d = r.json().get("data", {})
        except Exception as e:
            err = str(e)[:60]; time.sleep(4); break
        acts = d.get("activities") or []
        if not acts:
            break
        stop = False
        for t in acts:
            ts = t["timestamp"]
            oldest = ts if oldest is None else min(oldest, ts)
            if ts < OOW_START:
                stop = True
                continue
            if ts >= OOW_END:
                continue   # in decode window — skip, we only want out-of-window
            trades.append({
                "ts": ts, "side": t["event_type"],
                "mint": t["token"]["address"], "sym": t["token"].get("symbol"),
                "usd": t.get("cost_usd"), "buy_cost_usd": t.get("buy_cost_usd"),
                "price_usd": t.get("price_usd"),
            })
        pages += 1
        if stop:
            break
        cursor = d.get("next")
        if not cursor:
            break
        time.sleep(1.2)
    reached = oldest is not None and oldest < OOW_END
    fout.write(json.dumps({"wallet": a, "trades": trades, "pages": pages,
                           "oldest_ts": oldest, "reached_oow": reached,
                           "err": err}) + "\n")
    fout.flush()
    old_s = datetime.fromtimestamp(oldest, timezone.utc).isoformat()[:16] if oldest else "-"
    print(f"[{wi+1}/{len(wallets)}] {a[:8]} pages={pages} oow_trades={len(trades)} "
          f"oldest={old_s} reached_oow={reached} err={err}")
    time.sleep(1.2)
fout.close()
print("BATCH DONE")
