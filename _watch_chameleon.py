"""Chameleon watch (2026-06-12 night): poll the API every 5min, emit one line
ONLY when something changes — tune state, qualifying archetypes, new trades,
stream health problems. Designed as a Monitor event stream (quiet when idle)."""
import json
import time
import urllib.request

BASE = "https://gracious-inspiration-production.up.railway.app"


def get(path, timeout=30):
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))


prev = {"tune": None, "pending": None, "quals": None, "sells": None,
        "buys": None, "stale": False, "down": 0, "scored": -1}
down_since = None

while True:
    try:
        ms = get("/api/meta-sensor")
        if down_since:
            print(f"API RECOVERED after {time.time()-down_since:.0f}s")
            down_since = None
        cham = (ms.get("chameleon") or {}).get("meta_chameleon") or {}
        tune = json.dumps(cham.get("tune"), sort_keys=True)
        pend = json.dumps((cham.get("pending") or {}).get("archetype"))
        if tune != prev["tune"]:
            print(f"TUNE CHANGED -> archetype={cham.get('archetype')} tune={cham.get('tune')} "
                  f"wallets={json.dumps((cham.get('geometry') or {}).get('wallets'))}")
            prev["tune"] = tune
        if pend != prev["pending"]:
            if (cham.get("pending") or {}).get("archetype"):
                print(f"TUNE QUEUED (book busy) -> {cham['pending']['archetype']} {cham['pending'].get('tune')}")
            prev["pending"] = pend
        w6 = (ms.get("windows") or {}).get("6h") or {}
        quals = sorted(a for a, r in w6.items()
                       if a != "all" and r.get("n", 0) >= 8 and r.get("wr", 0) >= 0.60)
        qs = ",".join(quals) or "-"
        if qs != prev["quals"]:
            board = {a: f"wr={r['wr']:.0%} n={r['n']}" for a, r in w6.items()}
            print(f"BOARD: qualifying=[{qs}] | 6h board={json.dumps(board)}")
            prev["quals"] = qs
        scored = ms.get("scored_24h") or 0
        if prev["scored"] >= 0 and scored >= prev["scored"] + 100:
            print(f"SENSOR: {scored} episodes scored (24h) — board filling")
        if prev["scored"] < 0 or scored >= prev["scored"] + 100:
            prev["scored"] = scored
        age = ms.get("last_ingest_age_secs", ms.get("last_score_age_secs"))
        stale = age is not None and age > 1800
        if stale and not prev["stale"]:
            print(f"WARNING: sensor INGEST stale — last trade seen {age/60:.0f}min ago")
        prev["stale"] = stale

        tr = get("/api/trades?limit=600")
        rows = tr if isinstance(tr, list) else tr.get("trades", [])
        mine = [t for t in rows if t.get("bot_id") == "meta_chameleon"]
        buys = sum(1 for t in mine if t.get("type") == "buy")
        sells = [t for t in mine if t.get("type") == "sell"]
        key = (buys, len(sells))
        if prev["buys"] is not None and key != (prev["buys"], prev["sells"]):
            net = sum(float(t.get("pnl") or 0) for t in sells)
            last = sells[0] if sells else None
            extra = ""
            if last and (prev["sells"] or 0) < len(sells):
                extra = (f" | last close: {last.get('token')} {float(last.get('pnl') or 0):+.2f} "
                         f"({last.get('kind')}) arch={last.get('chameleon_archetype')}")
            print(f"TRADES: {buys} buys, {len(sells)} closes, net ${net:+.2f}{extra}")
        prev["buys"], prev["sells"] = buys, len(sells)
    except Exception as e:
        if down_since is None:
            down_since = time.time()
            print(f"API unreachable ({type(e).__name__}) — deploy cutover or outage; watching for recovery")
        elif time.time() - down_since > 600:
            print(f"API STILL DOWN after {(time.time()-down_since)/60:.0f}min — needs attention")
            down_since = time.time() - 300   # re-alert every ~5min after
    time.sleep(300)
