# Revival adjudication — data collection (analysis only, one-shot pulls).
# 1) fresh io.dexscreener trade sweep of revival+pond pairs -> _adj_fresh_trades.jsonl
# 2) DexScreener pairs batch -> _adj_ds_now.json (current px/liq/vol/txns)
# 3) GT hourly bars (limit=160) -> _adj_gt_hour/{pair12}.json
import asyncio, json, os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, r"C:\Users\jcole\multichain-bot")
RIP = r"C:\Users\jcole\multichain-bot\scratchpad\ripday"

REVIVAL = {
 "B9c9dVwSNvM8q2v1CdmymXtsWBWS7U1VjG2Z3aLjLzpX": "TATE",
 "2rC2rhyHDQE3SsRd2kMmTbZJj4bu7c912ZqRgYisxJsb": "GOON",
 "8qDidAKuyNYKaR4dh2ZFZZVG5gBTUfyJcwQPgwt9FS1Y": "manlet",
 "55HjYFDTgkP2dMJ7AzykXoP3FgrnW9DyQewWQ3Sg7Qdo": "Hobbes",
 "6YismmnYSgxCw9aWgf3h2GxMr1GDkvWq7oo76RjQ1i6v": "Udin",
 "7DvWfEjcg6Dxq2jj5x5W9pMdci2XQWaVtCXUpBrXM66z": "Martolexx",
 "Gerz5Vw4sxUZkR4dAtnYT5ChgX47GCYLE5Z3oeZinyFJ": "NEIL",
 "C2X7vGNyeja4TSHs9DRbVjSek7Z3h2kPRoWM7YQFrSPq": "QQQ",
 "HFu4wM862Fyw9AjVa75CRM818zVuXpUmMYmguYfxixx4": "Zeus",
 "2cz2GC3UkwKaFomL8uHAcSgRW6gLHBDx5viRMPVykics": "CHANCE",
 "9QwpxCSEssoY6NacsYivMptPeYqXa8Hwd2oFpJd4vrXp": "FOMO",
 "AtPXzwvpNuXzeJUcFZGWZ6cK14NpE6UwmmbBTqnDc8un": "MITCH",
 "DCmraCrumoQ1geBcYoRQmbw2qjCtveMmr8C2G1wbZ49": "TMB",
}
EXTRA_POND = {}
# resolve the 2 non-revival 07-04 pond pairs from the grid (full ids)
g = json.load(open(os.path.join(RIP, "_revival_grid.json")))
for p in g:
    if p.startswith("8SEg9BokNM79") or p.startswith("CKB2oH5RWERU"):
        EXTRA_POND[p] = p[:6]
PAIRS = dict(REVIVAL)
PAIRS.update(EXTRA_POND)
print("pairs:", len(PAIRS))

# ---------- 1) fresh trade sweep ----------
from feeds.dexscreener_client import DexScreenerClient

async def sweep():
    cl = DexScreenerClient()
    out = open(os.path.join(RIP, "_adj_fresh_trades.jsonl"), "w", encoding="ascii")
    tot = 0
    for pair, sym in PAIRS.items():
        try:
            trades = await cl.fetch_recent_trades(pair, limit=250)
        except Exception as e:
            print("  sweep ERR", sym, repr(e)); trades = []
        for t in trades:
            out.write(json.dumps({"pair": pair, "sym": sym, **t}) + "\n")
        tot += len(trades)
        if trades:
            tss = sorted(t["ts"] for t in trades)
            print("  %-10s n=%d span %s -> %s" % (sym, len(trades), tss[0], tss[-1]))
        else:
            print("  %-10s n=0" % sym)
        await asyncio.sleep(2.0)
    out.close()
    print("sweep total", tot)

asyncio.run(sweep())

# ---------- 2) DexScreener pairs batch ----------
from curl_cffi import requests as cr
sess = cr.Session(impersonate="chrome")
plist = list(PAIRS.keys())
res = []
for i in range(0, len(plist), 30):
    chunk = ",".join(plist[i:i+30])
    r = sess.get("https://api.dexscreener.com/latest/dex/pairs/solana/" + chunk, timeout=30)
    d = r.json()
    res.extend(d.get("pairs") or d.get("pair") or [])
    time.sleep(2)
json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "pairs": res},
          open(os.path.join(RIP, "_adj_ds_now.json"), "w"))
print("ds pairs:", len(res))

# ---------- 3) GT hourly bars ----------
os.makedirs(os.path.join(RIP, "_adj_gt_hour"), exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (research; contact none)", "Accept": "application/json"}
for pair in plist:
    fp = os.path.join(RIP, "_adj_gt_hour", pair[:12] + ".json")
    url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/ohlcv/hour"
           "?aggregate=1&limit=160&currency=usd&token=base" % pair)
    ok = False
    for att in range(4):
        try:
            r = sess.get(url, headers=H, timeout=30)
            if r.status_code == 429:
                print("  429", pair[:8], "sleep", 15 * (att + 1)); time.sleep(15 * (att + 1)); continue
            j = r.json()
            bl = j["data"]["attributes"]["ohlcv_list"]
            json.dump(bl, open(fp, "w"))
            print("  gt %-8s bars=%d newest=%s" % (PAIRS[pair], len(bl),
                  datetime.fromtimestamp(bl[0][0], tz=timezone.utc).isoformat()))
            ok = True; break
        except Exception as e:
            print("  gt ERR", pair[:8], repr(e)); time.sleep(8)
    if not ok:
        print("  gt FAIL", pair[:8])
    time.sleep(3.2)
print("DONE", datetime.now(timezone.utc).isoformat())
