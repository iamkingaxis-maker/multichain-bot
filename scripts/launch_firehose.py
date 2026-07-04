#!/usr/bin/env python3
"""
launch_firehose.py — LAUNCH-WATCH v1 (PumpPortal firehose feed).

Replaces the GT new_pools polling half of market_watch.launch_watch_tick (v0
saw ~45% of the launch stream and 429-throttled all day) with the free
PumpPortal WebSocket firehose (wss://pumpportal.fun/api/data), which pushes
EVERY pump.fun launch + every Raydium migration in real time (~15-25/min).

Funnel (same 4-window-validated predicate as v0 — 100% recall on every
traction cohort, 110/110 production pools):
  1. STAGE   every new-token event (mint+ts, in-memory, bounded). Migration
             events staged too (already-bonded = high-value candidates).
  2. LIQ     at age 12-25min (migrations: 5-25min), resolve the token's best
             pair via DexScreener batch lookup (<=30 mints/call) and require
             liquidity >= $10k. This prefilter passes ~10-15%, keeping GT
             comfortably inside its budget. Rechecked every ~3min inside the
             window (liq can arrive late), dropped past 25min.
  3. DECIDE  ONE GeckoTerminal minute-bar call per survivor (3s pacing,
             429-backoff, User-Agent): bars printing >=8 of last 15min AND
             vol15 >= $5k. One shot per token, then done — exactly v0.
  4. PIN     qualifiers get POSTed into the trading bot's user-watchlist
             (same endpoint/auth as market_watch.inject_watchlist, 24h
             client-side TTL) and appended to scratchpad/launch_watch.jsonl
             (one audit stream shared with v0).

Run (long-running, from repo root, under the session Monitor):
    cd C:\\Users\\jcole\\multichain-bot
    python scripts/launch_firehose.py

Env vars:
    MW_DASH_USER / MW_DASH_PASS  Basic auth for the bot's watchlist write
                                 endpoints (without them injection 401s —
                                 pins are still logged/printed, fail-open).
    LF_DEBUG=1                   verbose staging/decision tracing to stderr.

stdout is a monitored event stream — ONE line per qualifier:
    LAUNCH-PIN <sym> bars15=<n> vol15=$<v> liq=$<l>
plus one heartbeat line per hour:
    HB staged=<n> checked=<n> pinned=<n> ws_msgs=<n> reconn=<n>

State: scratchpad/launch_firehose_state.json (injected-TTL bookkeeping +
seen-mint dedupe). Staged set is in-memory only; a restart just loses the
current 25-minute window. Resilient: ws reconnect w/ backoff, malformed
events skipped, DS/GT errors fail-open (skip token, never die).
"""
import json
import os
import sys
import time
import gzip
import io as _io
import threading
import urllib.request
import urllib.error

DASH = "https://gracious-inspiration-production.up.railway.app"
STATE_PATH = os.path.join("scratchpad", "launch_firehose_state.json")
AUDIT_PATH = os.path.join("scratchpad", "launch_watch.jsonl")
WS_URL = "wss://pumpportal.fun/api/data"

MIN_AGE_S = int(os.environ.get("LF_MIN_AGE_S", 12 * 60))       # window open (creates)
MIN_AGE_MIG_S = int(os.environ.get("LF_MIN_AGE_MIG_S", 5 * 60))  # migrations: sooner
MAX_AGE_S = int(os.environ.get("LF_MAX_AGE_S", 25 * 60))        # drop unchecked past this
LIQ_MIN_USD = 10_000
BARS15_MIN = 8
VOL15_MIN_USD = 5_000
DS_BATCH = 30                # DexScreener mints per lookup call
DS_RECHECK_S = 180           # re-try liq prefilter at most every 3min/token
GT_PACE_S = 3.1              # GeckoTerminal pacing
STAGED_CAP = 6000
SEEN_CAP = 4000

DEBUG = os.environ.get("LF_DEBUG") == "1"


def dbg(msg):
    if DEBUG:
        print(f"# {msg}", file=sys.stderr, flush=True)


def _dash_auth_header():
    """Basic-auth header for the bot's write endpoints (MW_DASH_USER/PASS env,
    injected at launch — never persisted). Same contract as market_watch."""
    import base64
    u, pw = os.environ.get("MW_DASH_USER", ""), os.environ.get("MW_DASH_PASS", "")
    if not u or not pw:
        return {}
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{pw}".encode()).decode()}


def get(url, timeout=25, headers=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip", **(headers or {})})
    r = urllib.request.urlopen(req, timeout=timeout)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=_io.BytesIO(raw)).read()
    return json.loads(raw)


def load_state():
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {}


def save_state(s):
    try:
        json.dump(s, open(STATE_PATH, "w"))
    except Exception:
        pass


def inject_watchlist(state, addr, sym):
    """POST a qualified find into the bot's user-watchlist (force-include in
    every discovery/enrichment cycle — gates still decide entries). Client-side
    24h TTL bookkeeping in our own state file. Fail-open."""
    try:
        inj = state.setdefault("injected", {})
        if addr in inj:
            return "dup"
        body = json.dumps({"address": addr}).encode()
        req = urllib.request.Request(f"{DASH}/api/user-watchlist/add", data=body,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "lf/1",
                                              **_dash_auth_header()})
        urllib.request.urlopen(req, timeout=15)
        inj[addr] = time.time()
        return "ok"
    except urllib.error.HTTPError as e:
        dbg(f"inject {sym} HTTP {e.code}")
        return f"http_{e.code}"
    except Exception as e:
        dbg(f"inject {sym} err {str(e)[:60]}")
        return "err"


def expire_injections(state):
    """Remove our own injections after the 24h TTL. Fail-open."""
    try:
        inj = state.get("injected") or {}
        for addr, ts in list(inj.items()):
            if time.time() - ts > 24 * 3600:
                body = json.dumps({"address": addr}).encode()
                req = urllib.request.Request(f"{DASH}/api/user-watchlist/remove",
                                             data=body,
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "lf/1",
                                                      **_dash_auth_header()})
                try:
                    urllib.request.urlopen(req, timeout=15)
                except Exception:
                    pass
                del inj[addr]
    except Exception:
        pass


# ---------------------------------------------------------------- firehose --

class Firehose(threading.Thread):
    """PumpPortal ws reader: stages every new-token / migration event into a
    shared bounded dict. Reconnects with capped exponential backoff. Never
    raises out of run()."""
    daemon = True

    def __init__(self, staged, seen, lock, stats):
        super().__init__(daemon=True)
        self.staged, self.seen, self.lock, self.stats = staged, seen, lock, stats

    def _handle(self, raw):
        try:
            ev = json.loads(raw)
        except Exception:
            return
        if not isinstance(ev, dict):
            return
        self.stats["ws_msgs"] += 1
        mint = str(ev.get("mint") or "").strip()
        if not mint or len(mint) < 30:
            return  # sub-acks / malformed
        tx = str(ev.get("txType") or "")
        mig = tx == "migrate" or "migrat" in str(ev.get("pool") or "").lower() \
            or (tx != "create" and "migrat" in raw[:200].lower())
        if tx != "create" and not mig:
            return
        sym = str(ev.get("symbol") or "").strip() or mint[:8]
        with self.lock:
            if mint in self.staged or mint in self.seen:
                if mig and mint in self.staged:      # upgrade: bonded while staged
                    self.staged[mint]["mig"] = True
                return
            self.staged[mint] = {"t": time.time(), "sym": sym[:12], "mig": bool(mig),
                                 "ds_last": 0.0}
            self.stats["staged_total"] += 1
            if len(self.staged) > STAGED_CAP:        # bound: drop oldest
                for k in sorted(self.staged, key=lambda k: self.staged[k]["t"])[:STAGED_CAP // 4]:
                    self.staged.pop(k, None)
        dbg(f"stage {'MIG ' if mig else ''}{sym} {mint[:10]}")

    def run(self):
        from websockets.sync.client import connect
        backoff = 1
        while True:
            try:
                with connect(WS_URL, open_timeout=20) as ws:
                    ws.send(json.dumps({"method": "subscribeNewToken"}))
                    try:
                        ws.send(json.dumps({"method": "subscribeMigration"}))
                    except Exception:
                        pass
                    backoff = 1
                    while True:
                        raw = ws.recv(timeout=120)
                        try:
                            self._handle(raw)
                        except Exception:
                            pass
            except Exception as e:
                self.stats["reconnects"] += 1
                dbg(f"ws drop ({str(e)[:60]}) — reconnect in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------- decision --

def ds_liq_lookup(mints):
    """Batch DexScreener token lookup -> {mint: (pair_addr, sym, liq_usd)} for
    the best (max-liq) pair per mint. SPOOF GUARD (found live 2026-07-04): the
    firehose carries fake 'create' events bearing MAJORS' mints (saw USDC) —
    without a pair-age cap the funnel would pin established tokens. Only pairs
    created within the last 2h count. Fail-open: {} on error."""
    out = {}
    try:
        d = get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(mints))
        for pr in (d or []):
            try:
                base = pr.get("baseToken") or {}
                mint = str(base.get("address") or "")
                if mint not in mints:
                    continue
                cts = pr.get("pairCreatedAt")
                if cts and time.time() - float(cts) / 1000 > 2 * 3600:
                    continue  # old pool = spoofed mint, not a launch
                liq = float(((pr.get("liquidity") or {}).get("usd")) or 0)
                if mint not in out or liq > out[mint][2]:
                    out[mint] = (str(pr.get("pairAddress") or ""),
                                 str(base.get("symbol") or "")[:12], liq)
            except Exception:
                continue
    except Exception as e:
        dbg(f"DS err {str(e)[:60]}")
    return out


def gt_participation(pair, gt_state):
    """ONE GT minute-bar call -> (bars15, vol15) or None on error/429.
    3s pacing + 429 backoff via gt_state {'next_ok': ts}."""
    wait = gt_state.get("next_ok", 0) - time.time()
    if wait > 0:
        time.sleep(min(wait, 90))
    try:
        q = get("https://api.geckoterminal.com/api/v2/networks/solana/"
                f"pools/{pair}/ohlcv/minute?limit=25",
                headers={"User-Agent": "launch-firehose/1 (axis local)"})
        gt_state["next_ok"] = time.time() + GT_PACE_S
        bars = (((q.get("data") or {}).get("attributes") or {})
                .get("ohlcv_list") or [])
        cut = time.time() - 15 * 60
        printed = sum(1 for b in bars if float(b[0]) >= cut)
        vol15 = sum(float(b[5] or 0) for b in bars if float(b[0]) >= cut)
        return printed, vol15
    except urllib.error.HTTPError as e:
        gt_state["next_ok"] = time.time() + (60 if e.code == 429 else GT_PACE_S)
        dbg(f"GT HTTP {e.code} {pair[:10]}")
        return None
    except Exception as e:
        gt_state["next_ok"] = time.time() + GT_PACE_S
        dbg(f"GT err {str(e)[:60]}")
        return None


def decide_cycle(staged, seen, lock, state, stats, gt_state):
    """One decision pass: DS liq prefilter on window-age tokens, then the GT
    participation check on survivors. Everything fail-open."""
    now = time.time()
    with lock:
        # expire past-window tokens
        for m in [m for m, i in staged.items() if now - i["t"] > MAX_AGE_S]:
            staged.pop(m, None)
            seen[m] = now
        due = [m for m, i in staged.items()
               if now - i["t"] >= (MIN_AGE_MIG_S if i["mig"] else MIN_AGE_S)
               and now - i["ds_last"] >= DS_RECHECK_S]
        due.sort(key=lambda m: staged[m]["t"])
        batches = [due[i:i + DS_BATCH] for i in range(0, min(len(due), 3 * DS_BATCH), DS_BATCH)]
        for m in due[:3 * DS_BATCH]:
            staged[m]["ds_last"] = now

    survivors = []
    for bi, batch in enumerate(batches):
        if bi:
            time.sleep(1.5)  # be gentle to DS
        res = ds_liq_lookup(batch)
        stats["checked"] += len(batch)
        for mint in batch:
            pair, sym, liq = res.get(mint, ("", "", 0.0))
            if liq >= LIQ_MIN_USD and pair:
                survivors.append((mint, pair, sym, liq))
            # else: stays staged; rechecked in DS_RECHECK_S or dropped at 25min
    dbg(f"decide: due={len(due)} survivors={len(survivors)}")

    for mint, pair, sym, liq in survivors:
        with lock:
            info = staged.pop(mint, None)   # one GT shot per token, then done
            seen[mint] = now
        if info is None:
            continue
        sym = sym or info["sym"]
        r = gt_participation(pair, gt_state)
        if r is None:
            continue  # fail-open: GT throttled/err — skip, don't die
        bars15, vol15 = r
        if bars15 >= BARS15_MIN and vol15 >= VOL15_MIN_USD:
            stats["pinned"] += 1
            status = inject_watchlist(state, mint, f"LF:{sym}")
            try:
                with open(AUDIT_PATH, "a") as f:
                    f.write(json.dumps({"ts": time.time(), "mint": mint, "pair": pair,
                                        "sym": sym, "bars15": bars15,
                                        "vol15": round(vol15), "liq": round(liq),
                                        "src": "fh", "mig": info["mig"],
                                        "inject": status}) + "\n")
            except Exception:
                pass
            print(f"LAUNCH-PIN {sym} bars15={bars15} vol15=${round(vol15)} "
                  f"liq=${round(liq)}", flush=True)
        else:
            dbg(f"reject {sym} bars15={bars15} vol15={round(vol15)} liq={round(liq)}")

    with lock:
        if len(seen) > SEEN_CAP:
            for k in sorted(seen, key=seen.get)[:SEEN_CAP // 2]:
                seen.pop(k, None)


def main():
    os.makedirs("scratchpad", exist_ok=True)
    state = load_state()
    staged, lock = {}, threading.Lock()
    seen = {k: float(v) for k, v in (state.get("seen") or {}).items()}
    stats = {"ws_msgs": 0, "reconnects": 0, "staged_total": 0,
             "checked": 0, "pinned": 0}
    gt_state = {"next_ok": 0.0}
    Firehose(staged, seen, lock, stats).start()

    last_hb = time.time()
    last_save = time.time()
    last_expire = 0.0
    while True:
        try:
            decide_cycle(staged, seen, lock, state, stats, gt_state)
        except Exception as e:
            dbg(f"decide_cycle err {str(e)[:80]}")
        now = time.time()
        if now - last_expire > 3600:
            last_expire = now
            expire_injections(state)
        if now - last_save > 300:
            last_save = now
            with lock:
                state["seen"] = dict(seen)
            save_state(state)
        if now - last_hb > 3600:
            last_hb = now
            with lock:
                n_staged = len(staged)
            print(f"HB staged={n_staged} checked={stats['checked']} "
                  f"pinned={stats['pinned']} ws_msgs={stats['ws_msgs']} "
                  f"reconn={stats['reconnects']}", flush=True)
            stats["checked"] = stats["pinned"] = 0
            stats["ws_msgs"] = stats["reconnects"] = 0
        time.sleep(30)


if __name__ == "__main__":
    main()
