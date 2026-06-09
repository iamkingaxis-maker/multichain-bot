"""Watchlist wallet ACTIVITY check — detect rotation (operators abandon wallets).

One getSignatures call per watchlist wallet -> hours since last on-chain activity.
A previously-active wallet silent >24-48h = rotation signal -> swap in a bench wallet.

Usage: python scripts/wallet_activity_check.py [stale_hours=24]
"""
from __future__ import annotations
import json, sys, time, subprocess
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RPCS = ["https://solana.leorpc.com/?api_key=FREE", "https://api.mainnet-beta.solana.com"]
BENCH = [  # vetted SELECTOR-class replacements, ready to swap
    "9fcMp3GNGnd3qTn4krE399UYEsUNfkVsXQvo8np47NGW",  # twin of 45Sn4KL1 (use if twin rotates)
]


def last_activity(addr):
    for rpc in RPCS:
        for _ in range(2):
            out = subprocess.run(["curl", "-s", "--max-time", "8", "-X", "POST", rpc,
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                                   "params": [addr, {"limit": 1}]})],
                capture_output=True, text=True, errors="replace").stdout
            try:
                r = json.loads(out).get("result")
                if r is not None:
                    if not r:
                        return None  # no history at all
                    return r[0].get("blockTime")
            except Exception:
                pass
            time.sleep(0.3)
    return "rpc_fail"


def main():
    stale_h = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
    wl = json.load(open("config/follow_watchlist.json"))
    now = datetime.now(timezone.utc).timestamp()
    stale = []
    print(f"{'wallet':14s} {'last activity':>16s}  status")
    for w in wl:
        bt = last_activity(w)
        if bt == "rpc_fail":
            print(f"  {w[:12]:12s} {'RPC-fail':>16s}  ?")
        elif bt is None:
            print(f"  {w[:12]:12s} {'none':>16s}  EMPTY?")
            stale.append(w)
        else:
            h = (now - bt) / 3600
            flag = "STALE — rotation?" if h > stale_h else "active"
            if h > stale_h:
                stale.append(w)
            print(f"  {w[:12]:12s} {h:13.1f}h ago  {flag}")
        time.sleep(0.4)
    print(f"\nstale (> {stale_h:.0f}h): {len(stale)}")
    for w in stale:
        print(f"  {w}")
    if stale:
        print(f"bench available: {len(BENCH)} -> {[b[:12] for b in BENCH]}")
        print("protocol: confirm -> swap in bench -> commit/deploy -> verify fire rate")


if __name__ == "__main__":
    main()
