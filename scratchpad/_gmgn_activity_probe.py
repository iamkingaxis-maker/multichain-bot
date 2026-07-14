"""Inspect wallet_activity schema + pagination."""
import json, time
from curl_cffi import requests as cr

sess = cr.Session(impersonate="chrome", timeout=20,
                  headers={"Referer": "https://gmgn.ai/", "Accept": "application/json"})
w = "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f"
r = sess.get(f"https://gmgn.ai/api/v1/wallet_activity/sol?wallet={w}&limit=100")
d = r.json()["data"]
acts = d.get("activities", [])
print("keys:", list(d.keys()))
print("n:", len(acts))
print(json.dumps(acts[0], indent=1))
print("event_types:", {a.get("event_type") for a in acts})
ts = [a["timestamp"] for a in acts]
print("ts range:", min(ts), max(ts), "(span hours:", (max(ts)-min(ts))/3600, ")")
# pagination probe
nxt = d.get("next")
print("next:", str(nxt)[:80])
if nxt:
    time.sleep(1.5)
    r2 = sess.get(f"https://gmgn.ai/api/v1/wallet_activity/sol?wallet={w}&limit=100&cursor={nxt}")
    d2 = r2.json()["data"]
    print("page2 n:", len(d2.get("activities", [])), "next:", str(d2.get("next"))[:60])
