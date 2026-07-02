#!/usr/bin/env python3
"""
market_watch.py — the automated 'AxiS eyes': periodic DexScreener/fleet check
that prints a line ONLY when something needs attention. Run under a Monitor so
each printed line becomes a notification.

Checks every CYCLE_SECS (default 720 = 12 min), all calls local (no Railway
egress except one tiny gzip /api/bots + /api/trades?limit=60 per cycle):
  MISSED-WINNER  token in our mcap band (50k-1B) up >=30%/h1 with vol, that we
                 hold no position in and haven't bought in 6h
  REGIME-FLIP    SOL h1/h6 crossing gate thresholds (rip open/close, crash)
  BUY-STALL      fleet buys in the last 2h < 25% of the trailing 12h/6 rate
                 (only during non-sleep hours UTC 08-03)
  FLEET-DARK     open positions unreadable / API down 2 cycles in a row
Quiet = healthy. State kept in scratchpad/market_watch_state.json.
"""
import json
import os
import time
import gzip
import io as _io
import urllib.request
from datetime import datetime, timezone

CYCLE_SECS = int(os.environ.get("MW_CYCLE_SECS", "720"))
DASH = "https://gracious-inspiration-production.up.railway.app"
STATE = os.path.join("scratchpad", "market_watch_state.json")
SOL_PAIR = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=timeout)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=_io.BytesIO(raw)).read()
    return json.loads(raw)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(s):
    try:
        json.dump(s, open(STATE, "w"))
    except Exception:
        pass


def emit(tag, msg):
    print(f"[{tag}] {msg}", flush=True)


def main():
    state = load_state()
    fails = 0
    while True:
        now = datetime.now(timezone.utc)
        hr = now.hour
        try:
            # --- SOL regime ---
            try:
                d = get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{SOL_PAIR}")
                pc = (d.get("pair") or (d.get("pairs") or [{}])[0]).get("priceChange") or {}
                h1, h6 = float(pc.get("h1") or 0), float(pc.get("h6") or 0)
                prev = state.get("sol_zone")
                zone = ("rip" if h6 > 1.5 else "crash" if h6 < -3 or h1 < -2
                        else "green" if h6 > 0 else "red")
                if prev and zone != prev and ("rip" in (zone, prev) or "crash" in (zone, prev)):
                    emit("REGIME-FLIP", f"SOL {prev} -> {zone} (h1={h1:+.2f} h6={h6:+.2f})")
                state["sol_zone"] = zone
            except Exception:
                pass

            # --- fleet state ---
            bots = get(f"{DASH}/api/bots")
            bots = bots.get("bots", bots) if isinstance(bots, dict) else bots
            open_n = sum((b.get("open_position_count") or 0) for b in bots or [])
            tr = get(f"{DASH}/api/trades?limit=200")
            arr = tr.get("trades", tr) if isinstance(tr, dict) else tr
            buys = [t for t in arr if t.get("type") == "buy"]
            fails = 0
            # buy-stall (only when awake: UTC 08-03 trading window)
            awake = not (3 <= hr < 8)
            if awake and buys:
                import datetime as dt
                nowts = time.time()
                def n_since(hours):
                    cut = (now - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
                    return sum(1 for t in buys if str(t.get("time", "")) > cut)
                last2, last12 = n_since(2), n_since(12)
                base_rate = last12 / 6.0
                if base_rate >= 2 and last2 < 0.25 * base_rate:
                    key = f"stall_{now.strftime('%Y%m%d%H')}"
                    if not state.get(key):
                        state[key] = 1
                        emit("BUY-STALL", f"buys last2h={last2} vs 12h-rate {base_rate:.1f}/2h "
                                          f"(open={open_n}) — check the funnel")
            our_tokens = {str(t.get("token")).lower() for t in buys[-120:]}

            # --- missed winners in our band ---
            try:
                d = get("https://api.dexscreener.com/latest/dex/search?q=SOL&chainId=solana")
                seen_alerts = state.setdefault("mw_seen", {})
                for p in (d.get("pairs") or [])[:80]:
                    if p.get("chainId") != "solana":
                        continue
                    mc = p.get("marketCap") or p.get("fdv") or 0
                    h1p = float((p.get("priceChange") or {}).get("h1") or 0)
                    vol1 = float((p.get("volume") or {}).get("h1") or 0)
                    sym = str((p.get("baseToken") or {}).get("symbol") or "?")
                    if (50_000 <= float(mc) <= 1e9 and h1p >= 30 and vol1 >= 50_000
                            and sym.lower() not in our_tokens):
                        k = f"mw_{sym.lower()}"
                        if time.time() - seen_alerts.get(k, 0) > 6 * 3600:
                            seen_alerts[k] = time.time()
                            emit("MISSED-WINNER", f"{sym} +{h1p:.0f}%/h1 vol1h=${vol1/1e3:.0f}k "
                                                  f"mc=${float(mc)/1e6:.2f}M — in band, no position")
            except Exception:
                pass
        except Exception as e:
            fails += 1
            if fails == 2:
                emit("FLEET-DARK", f"API unreadable 2 cycles: {str(e)[:80]}")
        save_state(state)
        time.sleep(CYCLE_SECS)


if __name__ == "__main__":
    main()
