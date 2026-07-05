#!/usr/bin/env python3
"""
live_trade_watch.py — poll railway logs for LIVE trade activity (T1 probe).

Emits one line per NEW live-trade event (dedup across polls via a seen-set
of line hashes; bounded). Designed to run under the session Monitor so every
live fill/failure wakes the operator. Patterns cover the live money path:
Ultra order/execute, live buy/sell records, the probe bot by name, and the
live-swap logger. Poll ~45s; railway CLI returns the recent window.
"""
import hashlib
import subprocess
import sys
import time

PATTERNS = ("young_absorb_live", "[Ultra]", "Live sell", "Live buy",
            "LIVE-SWAP", "LIVE BUY", "LIVE SELL")
NOISE = ("GET /", "POST /", "HTTP/1.1")   # dashboard access-log lines
seen = set()

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print("# live-trade watch up — polling railway logs for live money-path events")
while True:
    try:
        out = subprocess.run("railway logs", shell=True, capture_output=True,
                             text=True, timeout=60, encoding="utf-8",
                             errors="replace").stdout or ""
        for line in out.splitlines():
            if not any(p in line for p in PATTERNS):
                continue
            if any(n in line for n in NOISE):
                continue
            h = hashlib.md5(line.strip().encode()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            print(f"LIVE-EVENT {line.strip()[:300]}", flush=True)
        if len(seen) > 4000:
            seen.clear()
    except Exception as e:
        print(f"# poll err {str(e)[:60]}", flush=True)
    time.sleep(45)
