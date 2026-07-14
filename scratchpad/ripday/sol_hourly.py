"""One GT call: SOL/USDC hourly OHLC (limit=1000 ~ 41d) -> rolling 6h pct, find rip windows."""
import json, time
from datetime import datetime, timezone
from curl_cffi import requests as cr

S = cr.Session(impersonate="chrome")
# Raydium SOL/USDC main pool (from t5_recon pattern - resolve via token pools)
j = S.get("https://api.geckoterminal.com/api/v2/networks/solana/tokens/So11111111111111111111111111111111111111112/pools",
          timeout=25, headers={"User-Agent": "Mozilla/5.0"}).json()
best = max(j["data"], key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
pair = best["id"].replace("solana_", "")
print("SOL pool:", pair)
time.sleep(3)
oj = S.get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/ohlcv/hour?aggregate=1&limit=1000",
           timeout=25, headers={"User-Agent": "Mozilla/5.0"}).json()
rows = sorted(oj["data"]["attributes"]["ohlcv_list"], key=lambda r: r[0])
print("bars:", len(rows), "span:",
      datetime.fromtimestamp(rows[0][0], timezone.utc), "->",
      datetime.fromtimestamp(rows[-1][0], timezone.utc))
json.dump(rows, open("scratchpad/ripday/sol_hourly.json", "w"))
# rolling 6h pct on closes
out = []
for i in range(6, len(rows)):
    ts, c = rows[i][0], rows[i][4]
    c6 = rows[i - 6][4]
    pc6 = (c / c6 - 1) * 100
    out.append((ts, pc6, c))
print("\nhours with pc6 > 1.5 (since 06-24):")
cut = datetime(2026, 6, 24, tzinfo=timezone.utc).timestamp()
for ts, pc6, c in out:
    if ts >= cut and pc6 > 1.5:
        print(datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:00"), f"pc6=+{pc6:.2f} close={c:.2f}")
