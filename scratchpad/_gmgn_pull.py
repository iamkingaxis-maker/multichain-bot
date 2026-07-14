"""Pull gmgn leaderboard(s) + probe per-wallet activity endpoints."""
import json, time
from curl_cffi import requests as cr

sess = cr.Session(impersonate="chrome", timeout=20,
                  headers={"Referer": "https://gmgn.ai/", "Accept": "application/json"})

def get(url):
    r = sess.get(url)
    time.sleep(1.6)
    return r

out = {}
for tag, url in [
    ("pnl7d", "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d?orderby=pnl_7d&direction=desc"),
    ("smart_degen", "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d?tag=smart_degen&orderby=pnl_7d&direction=desc"),
    ("pump_smart", "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d?tag=pump_smart&orderby=pnl_7d&direction=desc"),
]:
    r = get(url)
    if r.status_code == 200:
        d = r.json().get("data", {}).get("rank", [])
        out[tag] = d
        print(f"{tag}: {len(d)} wallets")
    else:
        print(f"{tag}: HTTP {r.status_code}")

json.dump(out, open("scratchpad/_gmgn_leaderboard.json", "w"))

# show fields of first record
first = out["pnl7d"][0]
print("FIELDS:", sorted(first.keys()))
print(json.dumps(first, indent=1)[:1200])

# probe per-wallet activity endpoints with the first wallet
w = first["address"]
print("\nPROBE wallet:", w)
for name, url in [
    ("wallet_activity", f"https://gmgn.ai/defi/quotation/v1/wallet_activity/sol?wallet={w}&limit=20"),
    ("wallet_holdings", f"https://gmgn.ai/defi/quotation/v1/wallet/sol/holdings/{w}?limit=20&orderby=last_active_timestamp&direction=desc"),
    ("smartmoney_stat", f"https://gmgn.ai/defi/quotation/v1/smartmoney/sol/walletNew/{w}?period=7d"),
    ("api_v1_activity", f"https://gmgn.ai/api/v1/wallet_activity/sol?wallet={w}&limit=20"),
]:
    r = get(url)
    print(f"[{name}] {r.status_code} len={len(r.text)}")
    print("   ", r.text[:250].replace("\n", " "))
print("DONE")
