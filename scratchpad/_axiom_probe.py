"""Probe Axiom leaderboard endpoints (no auth) + fallbacks (gmgn, kolscan)."""
import json, time, sys
from curl_cffi import requests as cr

sess = cr.Session(impersonate="chrome", timeout=20)

def probe(name, url, headers=None, method="GET", body=None):
    try:
        if method == "POST":
            r = sess.post(url, headers=headers or {}, json=body or {})
        else:
            r = sess.get(url, headers=headers or {})
        ct = r.headers.get("content-type", "")
        snippet = r.text[:300].replace("\n", " ")
        print(f"[{name}] {r.status_code} ct={ct[:40]} len={len(r.text)}")
        print(f"    {snippet}")
    except Exception as e:
        print(f"[{name}] EXC {type(e).__name__}: {e}")
    time.sleep(1.5)

# --- Axiom probes ---
AX_HDR = {"Origin": "https://axiom.trade", "Referer": "https://axiom.trade/"}
probe("axiom-api6-leaderboard", "https://api6.axiom.trade/leaderboard", AX_HDR)
probe("axiom-api-top-traders", "https://api6.axiom.trade/top-traders", AX_HDR)
probe("axiom-api8-leaderboard", "https://api8.axiom.trade/leaderboard", AX_HDR)
probe("axiom-app-leaderboard-page", "https://axiom.trade/leaderboard", {})
probe("axiom-api-lb-v1", "https://api6.axiom.trade/leaderboard-rank?timePeriod=7d", AX_HDR)

# --- gmgn fallback probes ---
GM_HDR = {"Referer": "https://gmgn.ai/", "Accept": "application/json"}
probe("gmgn-rank-wallets-7d",
      "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d?orderby=pnl_7d&direction=desc",
      GM_HDR)
probe("gmgn-rank-wallets-7d-tags",
      "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d?tag=smart_degen&orderby=pnl_7d&direction=desc",
      GM_HDR)

# --- kolscan fallback ---
probe("kolscan-leaderboard", "https://kolscan.io/leaderboard", {})
print("DONE")
