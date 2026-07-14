"""Probe bars retro coverage for a pond buy from ~3 days ago."""
import json, time, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from curl_cffi import requests as cr
from feeds.dexscreener_chart_format import parse_chart_bars

SOL = "So11111111111111111111111111111111111111112"
cls = {r["wallet"]: r["class"] for r in json.load(open("scratchpad/_toptrader_class.json"))}
tok = json.load(open("scratchpad/_toptrader_tokens.json"))

# find an in-pond buy 2-4 days old on a resolved token
target = None
now = time.time()
for line in open("scratchpad/_toptrader_activity.jsonl"):
    d = json.loads(line)
    if cls.get(d["wallet"]) != "IN-POND":
        continue
    for t in d["trades"]:
        if t["side"] == "buy" and tok.get(t["mint"]) and 2 < (now - t["ts"]) / 86400 < 4:
            target = t
            break
    if target:
        break
print("target:", target["mint"], datetime.fromtimestamp(target["ts"], timezone.utc))

sess = cr.Session(impersonate="chrome", timeout=20,
                  headers={"Origin": "https://dexscreener.com", "Referer": "https://dexscreener.com/"})
r = sess.get(f"https://api.dexscreener.com/tokens/v1/solana/{target['mint']}")
p = r.json()[0]
pair, dexid = p["pairAddress"], p["dexId"].lower()
slug = {"raydium": "solamm", "pumpswap": "pumpfundex", "pumpfun": "pumpfundex"}.get(dexid, dexid)
print("pair:", pair, "dex:", dexid, "slug:", slug)
buy_ms = target["ts"] * 1000
time.sleep(1.5)
for tag, u in [
    ("res1-to", f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}?res=1&cb=120&q={SOL}&to={int(buy_ms)}"),
    ("res5-deep", f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}?res=5&cb=1500&q={SOL}"),
]:
    r = sess.get(u)
    bars = parse_chart_bars(r.content) if r.status_code == 200 else []
    if bars:
        f, l = bars[0]["ts_ms"], bars[-1]["ts_ms"]
        print(f"{tag}: HTTP {r.status_code} n={len(bars)} "
              f"first={datetime.fromtimestamp(f/1000, timezone.utc)} "
              f"last={datetime.fromtimestamp(l/1000, timezone.utc)} "
              f"covers_buy={'YES' if f <= buy_ms <= l else 'NO'}")
    else:
        print(f"{tag}: HTTP {r.status_code} n=0 bytes={len(r.content)}")
    time.sleep(1.5)
