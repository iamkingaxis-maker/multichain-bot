"""Rip-day harvest driver — the ONLY network process.

Phases:
  0. Refresh recorder (one call) -> add any new sol_pc_h6>1.5 peak>=25 runners.
  1. io.dexscreener trade-log sweep over ALL runner pairs (rip + contrast set),
     dedup-append to scratchpad/ripday/tape_{pair8}.jsonl.
  2. GT minute OHLC per rip runner covering its run window (before_ts=event+4h,
     limit=1000 => ~16.6h ending 4h after event). 2.7s pacing, retry-429.
  3. io re-sweep (active pairs only) x2 more, spaced, to accumulate fresh tape.
  4. GT pool meta (multi, 30/call) + SOL/USD minute paged back to 06-24.

Everything ascii. Append-only tape files, atomic-ish index rewrites.
"""
import asyncio, gzip, io, json, os, sys, time, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, ".")
from feeds.dexscreener_client import DexScreenerClient
from curl_cffi import requests as cr

OUT = "scratchpad/ripday"
os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, "harvest_log.txt"), "a", buffering=1)

def log(msg):
    line = "%s %s" % (datetime.now(timezone.utc).strftime("%H:%M:%S"), msg)
    print(line)
    LOG.write(line + "\n")

RIP = json.load(open(os.path.join(OUT, "rip_runners.json")))
REC = json.load(open(os.path.join(OUT, "recorder_runners.json")))

# 06-30 pump-day contrast monsters (ran OUTSIDE sol-rip windows)
CONTRAST = [
    "CzigqJ9h9mnfXq3G3jKBVcApKu8Kh8JtZGxarzY2pump",  # SUPERMAN
    "9gAVAtdnsrniW3GCwsvDeRPf5eJDjtT9S5bu1j7tpump",  # dog
    "4U4U8oXwDyVXGeTffMXds4NAgBgLFwq3wNvTCRTSpump",  # TJR
    "hZ5KWowmySGbKVuGDhRTsPPrYCRfzk7BxLKwpg7pump",   # Vulland
    "86CFcbZBJAqGVnfgnLNcw3tPmfaTigAR2UxbUPYTpump",  # LUKE
]

# ---------- phase 0: recorder refresh ----------
def refresh_recorder():
    try:
        req = urllib.request.Request(
            "https://gracious-inspiration-production.up.railway.app/api/universe-recorder?limit=5000",
            headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        ev = json.loads(raw)
    except Exception as e:
        log("recorder refresh FAILED: %r (continuing with disk lists)" % e)
        return 0
    added = 0
    for e in ev:
        s6 = e.get("sol_pc_h6"); pk = e.get("peak_pct")
        tok = e.get("token_address"); pair = e.get("pair_address")
        ts = e.get("event_ts")
        if not (isinstance(s6, (int, float)) and s6 > 1.5): continue
        if not (isinstance(pk, (int, float)) and pk >= 25): continue
        if not tok or not pair: continue
        cur = RIP.get(tok)
        if cur is None or pk > cur.get("peak", 0):
            RIP[tok] = {"pair": pair, "peak": pk, "sym": e.get("symbol"),
                        "ts": ts, "sol_pc_h6": s6,
                        "liq": e.get("liq_usd"), "mcap": e.get("mcap_usd"),
                        "pc_h1": e.get("pc_h1")}
            if cur is None: added += 1
    json.dump(RIP, open(os.path.join(OUT, "rip_runners_live.json"), "w"), indent=1)
    log("recorder refresh: rip runners now %d (+%d new)" % (len(RIP), added))
    return added

# ---------- io tape sweep ----------
TAPE_KEYS = {}   # pair -> set of dedup keys
INDEX_PATH = os.path.join(OUT, "tape_index.json")
INDEX = json.load(open(INDEX_PATH)) if os.path.exists(INDEX_PATH) else {}

def tape_path(pair):
    return os.path.join(OUT, "tape_%s.jsonl" % pair[:8])

def load_tape_keys(pair):
    if pair in TAPE_KEYS:
        return TAPE_KEYS[pair]
    keys = set()
    p = tape_path(pair)
    if os.path.exists(p):
        for line in open(p, encoding="ascii", errors="replace"):
            try:
                t = json.loads(line)
                keys.add((t["ts"], t.get("maker", ""), t["volume_usd"], t["kind"]))
            except Exception:
                pass
    TAPE_KEYS[pair] = keys
    return keys

async def sweep(targets, tag):
    """targets: list of (token, pair, sym). Appends new trades to tapes."""
    cl = DexScreenerClient()
    t0 = time.time()
    total_new = 0
    fails = 0
    for i, (tok, pair, sym) in enumerate(targets):
        try:
            trades = await cl.fetch_recent_trades(pair, limit=250)
        except Exception as e:
            log("  %s fetch ERR %s: %r" % (tag, str(sym)[:10], e))
            trades = []
        if not trades:
            fails += 1
            if fails >= 8 and i < 12:
                log("  %s: early fail streak, circuit likely open; sleep 65s" % tag)
                await asyncio.sleep(65)
                fails = 0
        else:
            fails = 0
            keys = load_tape_keys(pair)
            new = []
            for t in trades:
                k = (t["ts"], t.get("maker", ""), t["volume_usd"], t["kind"])
                if k in keys: continue
                keys.add(k)
                new.append({"token": tok, "pair": pair, "sym": sym, **t})
            if new:
                with open(tape_path(pair), "a", encoding="ascii") as f:
                    for t in new:
                        f.write(json.dumps(t) + "\n")
                total_new += len(new)
            tss = sorted(t["ts"] for t in trades)
            idx = INDEX.setdefault(pair, {"token": tok, "sym": sym,
                                          "file": os.path.basename(tape_path(pair)),
                                          "n_trades": 0, "sweeps": 0})
            idx["n_trades"] = len(keys)
            idx["sweeps"] = (idx.get("sweeps") or 0) + 1
            idx["newest"] = tss[-1]
            idx["oldest"] = min(idx.get("oldest", tss[0]), tss[0])
            if i % 20 == 0:
                json.dump(INDEX, open(INDEX_PATH, "w"), indent=1)
        await asyncio.sleep(1.8)
    json.dump(INDEX, open(INDEX_PATH, "w"), indent=1)
    log("%s done: %d pairs, +%d new trades, %.0fs" % (tag, len(targets), total_new, time.time() - t0))

def all_targets():
    seen = set(); out = []
    # rip runners first, newest event first
    for tok, r in sorted(RIP.items(), key=lambda kv: -(kv[1].get("ts") or 0)):
        if r.get("pair") and r["pair"] not in seen:
            seen.add(r["pair"]); out.append((tok, r["pair"], r.get("sym")))
    # contrast monsters + rest of recorder set
    for tok in CONTRAST:
        r = REC.get(tok)
        if r and r.get("pair") and r["pair"] not in seen:
            seen.add(r["pair"]); out.append((tok, r["pair"], r.get("sym")))
    for tok, r in REC.items():
        if r.get("pair") and r["pair"] not in seen:
            seen.add(r["pair"]); out.append((tok, r["pair"], r.get("sym")))
    return out

def active_targets():
    """Pairs whose newest tape trade is <3h old (still accumulating), plus all 07-01 rip events."""
    now = datetime.now(timezone.utc)
    out = []
    for tok, r in sorted(RIP.items(), key=lambda kv: -(kv[1].get("ts") or 0)):
        pair = r.get("pair")
        if not pair: continue
        idx = INDEX.get(pair)
        keep = False
        ts = r.get("ts") or 0
        if ts and ts > 1782864000:  # 07-01 00:00 UTC
            keep = True
        if idx and idx.get("newest"):
            try:
                age_h = (now - datetime.fromisoformat(idx["newest"])).total_seconds() / 3600.0
                if age_h < 3.0: keep = True
            except Exception:
                pass
        if keep:
            out.append((tok, pair, r.get("sym")))
    return out

# ---------- GT ----------
GTS = cr.Session(impersonate="chrome")
GT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
              "Accept": "application/json"}

def gt(url, tries=4):
    for t in range(tries):
        try:
            r = GTS.get(url, timeout=25, headers=GT_HEADERS)
            if r.status_code == 200:
                return r.json()
            time.sleep(9 if r.status_code == 429 else 3)
        except Exception:
            time.sleep(4)
    return None

def gt_ohlc_for(tok, pair, sym, event_ts, before_off=4 * 3600):
    path = os.path.join(OUT, "ohlc_%s.json" % tok[:8])
    if os.path.exists(path):
        return "cached"
    before = int(event_ts + before_off) if event_ts else int(time.time())
    url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/"
           "ohlcv/minute?aggregate=1&limit=1000&currency=usd&before_timestamp=%d" % (pair, before))
    j = gt(url); time.sleep(2.7)
    bars = (((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    if not bars:
        # fallback: resolve top pool by token (io pair may differ from GT pool)
        j2 = gt("https://api.geckoterminal.com/api/v2/networks/solana/tokens/%s/pools" % tok)
        time.sleep(2.7)
        if j2 and j2.get("data"):
            best = max(j2["data"], key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
            gp = best.get("id", "").replace("solana_", "")
            if gp and gp != pair:
                url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/"
                       "ohlcv/minute?aggregate=1&limit=1000&currency=usd&before_timestamp=%d" % (gp, before))
                j = gt(url); time.sleep(2.7)
                bars = (((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
                pair = gp
    rec = {"token": tok, "pair": pair, "sym": sym, "event_ts": event_ts,
           "before_ts": before, "n_bars": len(bars),
           "bars": sorted(bars, key=lambda b: b[0])}
    json.dump(rec, open(path, "w"))
    return len(bars)

def gt_pool_meta(pairs):
    metas = {}
    for i in range(0, len(pairs), 30):
        chunk = pairs[i:i + 30]
        url = "https://api.geckoterminal.com/api/v2/networks/solana/pools/multi/%s" % ",".join(chunk)
        j = gt(url); time.sleep(2.7)
        for d in (j or {}).get("data") or []:
            a = d.get("attributes") or {}
            pid = d.get("id", "").replace("solana_", "")
            rel = ((d.get("relationships") or {}).get("dex") or {}).get("data") or {}
            metas[pid] = {
                "name": a.get("name"), "pool_created_at": a.get("pool_created_at"),
                "reserve_usd": a.get("reserve_in_usd"), "fdv_usd": a.get("fdv_usd"),
                "market_cap_usd": a.get("market_cap_usd"), "dex": rel.get("id"),
                "price_usd": a.get("base_token_price_usd"),
                "vol_h24": (a.get("volume_usd") or {}).get("h24"),
                "txns": a.get("transactions"),
            }
        log("  pool meta chunk %d-%d: %d resolved" % (i, i + len(chunk), len(metas)))
    json.dump(metas, open(os.path.join(OUT, "token_meta.json"), "w"), indent=1)
    return metas

def gt_sol_minute(back_to=1782259200):
    """SOL/USD minute bars paged back to 06-24 00:00 UTC."""
    path = os.path.join(OUT, "sol_usd_minute.json")
    pool = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"  # Raydium SOL/USDC
    allb = {}
    before = int(time.time())
    for page in range(14):
        url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/"
               "ohlcv/minute?aggregate=1&limit=1000&currency=usd&before_timestamp=%d" % (pool, before))
        j = gt(url); time.sleep(2.7)
        bars = (((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not bars and page == 0:
            # resolve dynamically
            j2 = gt("https://api.geckoterminal.com/api/v2/networks/solana/tokens/So11111111111111111111111111111111111111112/pools")
            time.sleep(2.7)
            if j2 and j2.get("data"):
                best = max(j2["data"], key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
                pool = best.get("id", "").replace("solana_", "")
                continue
        if not bars:
            break
        for b in bars:
            allb[b[0]] = b
        oldest = min(b[0] for b in bars)
        log("  sol minute page %d: %d bars, oldest %s" % (page, len(bars),
            datetime.fromtimestamp(oldest, timezone.utc).strftime("%m-%d %H:%M")))
        if oldest <= back_to:
            break
        before = oldest
    rows = [allb[k] for k in sorted(allb)]
    json.dump({"pool": pool, "n_bars": len(rows), "bars": rows}, open(path, "w"))
    log("sol minute: %d bars total" % len(rows))

# ---------- main ----------
def main():
    log("=== harvest driver start ===")
    refresh_recorder()

    targets = all_targets()
    log("sweep1: %d pairs (full universe)" % len(targets))
    asyncio.run(sweep(targets, "sweep1"))

    # GT OHLC: 07-01 runners first (freshest windows), then older rips, then contrast
    rip_sorted = sorted(RIP.items(), key=lambda kv: -(kv[1].get("ts") or 0))
    log("GT ohlc batch1: 07-01 runners")
    n1 = 0
    for tok, r in rip_sorted:
        if (r.get("ts") or 0) > 1782864000 and r.get("pair"):
            gt_ohlc_for(tok, r["pair"], r.get("sym"), r.get("ts")); n1 += 1
    log("GT ohlc batch1 done (%d tokens)" % n1)

    act = active_targets()
    log("sweep2: %d active pairs" % len(act))
    asyncio.run(sweep(act, "sweep2"))

    log("GT ohlc batch2: older rip runners + contrast monsters")
    n2 = 0
    for tok, r in rip_sorted:
        if (r.get("ts") or 0) <= 1782864000 and r.get("pair"):
            gt_ohlc_for(tok, r["pair"], r.get("sym"), r.get("ts") or 1782950400); n2 += 1
    for tok in CONTRAST:
        r = REC.get(tok)
        if r and r.get("pair"):
            # 06-30 pump day: window ended by 07-01 00:00 UTC
            gt_ohlc_for(tok, r["pair"], r.get("sym"), 1782849600); n2 += 1  # before=06-30 20:00+4h
    log("GT ohlc batch2 done (%d tokens)" % n2)

    log("GT pool meta")
    pairs = [r["pair"] for _, r in RIP.items() if r.get("pair")]
    pairs += [REC[t]["pair"] for t in CONTRAST if t in REC and REC[t].get("pair")]
    gt_pool_meta(sorted(set(pairs)))

    log("GT sol/usd minute history")
    gt_sol_minute()

    act = active_targets()
    log("sweep3: %d active pairs" % len(act))
    asyncio.run(sweep(act, "sweep3"))

    log("waiting 15min for tape to accumulate, then final sweep")
    time.sleep(900)
    act = active_targets()
    log("sweep4: %d active pairs" % len(act))
    asyncio.run(sweep(act, "sweep4"))

    log("=== harvest driver COMPLETE ===")

if __name__ == "__main__":
    main()
