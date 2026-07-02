#!/usr/bin/env python3
"""
rip_tape_recorder.py — capture COMPLETE wallet trade tapes during SOL-rip windows.

WHY (rip-day mine, 2026-07-01): io.dexscreener trade logs hold only the last
~100 trades, so by the time a rip window is analyzed the entry legs are gone
and winner-scoring is forgeable by truncation. This recorder starts sweeping
runner pairs THE MOMENT sol h6 crosses the rip threshold, so the NEXT rip
window is fully covered and the FLUSH-ABSORB validation becomes decisive.

Run locally (single process, paced — respects all rate limits):
    PYTHONPATH=. python scripts/rip_tape_recorder.py
Output: scratchpad/ripday/live_tapes/tape_{pair8}.jsonl (+ recorder.log)

Idle cost ~1 tiny request/5min. Active cost ~1 req/2.5s only while SOL rips.
"""
import asyncio
import json
import os
import sys
import time
import gzip
import io as _io
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RIP_THRESHOLD = float(os.environ.get("RIP_TAPE_SOL_H6", "1.5"))
SOL_PAIR = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"  # Raydium SOL/USDC
DASH = "https://gracious-inspiration-production.up.railway.app"
OUT_DIR = os.path.join("scratchpad", "ripday", "live_tapes")
IDLE_SLEEP = 300          # 5 min between SOL checks when calm
SWEEP_INTERVAL = 600      # re-sweep runner tapes every 10 min while ripping
PAIR_PACING = 2.5         # seconds between pair fetches
MIN_PEAK_PCT = 25.0


def _get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=timeout)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=_io.BytesIO(raw)).read()
    return json.loads(raw)


def sol_h6():
    try:
        d = _get_json(f"https://api.dexscreener.com/latest/dex/pairs/solana/{SOL_PAIR}")
        pc = (d.get("pair") or (d.get("pairs") or [{}])[0]).get("priceChange") or {}
        return float(pc.get("h6"))
    except Exception:
        return None


def fresh_runners():
    """Recent recorder events with peak>=MIN_PEAK_PCT -> {pair: symbol}."""
    out = {}
    try:
        d = _get_json(f"{DASH}/api/universe-recorder?limit=500")
        events = d.get("events", d) if isinstance(d, dict) else d
        cutoff = time.time() - 8 * 3600
        for e in (events or []):
            try:
                if float(e.get("peak_pct") or 0) < MIN_PEAK_PCT:
                    continue
                ts = e.get("event_ts") or e.get("ts") or 0
                if isinstance(ts, str):
                    continue
                if float(ts) < cutoff:
                    continue
                p = e.get("pair_address")
                if p:
                    out[p] = e.get("symbol") or e.get("token_symbol") or "?"
            except Exception:
                continue
    except Exception as ex:
        log(f"runner fetch err: {ex}")
    return out


def log(msg):
    line = f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT_DIR, "recorder.log"), "a") as f:
        f.write(line + "\n")


async def sweep(pairs):
    from feeds.dexscreener_client import DexScreenerClient
    cli = DexScreenerClient(cache_ttl=5, rate_per_min=90)
    n_new_total = 0
    for pair, sym in pairs.items():
        try:
            trades = await cli.fetch_recent_trades(pair, limit=300)
        except Exception as ex:
            log(f"  {sym} fetch err: {ex}")
            trades = []
        if trades:
            path = os.path.join(OUT_DIR, f"tape_{pair[:8]}.jsonl")
            seen = set()
            if os.path.exists(path):
                for ln in open(path, encoding="utf-8"):
                    try:
                        t = json.loads(ln)
                        seen.add((t.get("ts"), t.get("maker"),
                                  t.get("volume_usd"), t.get("kind")))
                    except Exception:
                        pass
            n_new = 0
            with open(path, "a", encoding="utf-8") as f:
                for t in trades:
                    key = (t.get("ts"), t.get("maker"),
                           t.get("volume_usd"), t.get("kind"))
                    if key in seen:
                        continue
                    seen.add(key)
                    row = dict(t)
                    row["pair"] = pair
                    row["sym"] = sym
                    f.write(json.dumps(row) + "\n")
                    n_new += 1
            n_new_total += n_new
        await asyncio.sleep(PAIR_PACING)
    return n_new_total


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"rip tape recorder up (threshold sol_h6>{RIP_THRESHOLD})")
    ripping = False
    while True:
        s6 = sol_h6()
        if s6 is None:
            log("sol h6 unreadable; retry in 5min")
            time.sleep(IDLE_SLEEP)
            continue
        # ALWAYS-ON sweeping (2026-07-02, absorption decode v2): tape-window
        # coverage was the new bottleneck (56% of labeled flushes lost to tape
        # gaps; died-class accrual ~5 pairs/day). Sweeping every cycle — not
        # just SOL rips — triples flush coverage at zero Railway cost (all
        # calls originate locally). The rip flag now only tags the log.
        if s6 > RIP_THRESHOLD and not ripping:
            log(f"=== RIP WINDOW OPEN (sol_h6={s6:+.2f}) ===")
            ripping = True
        elif s6 <= RIP_THRESHOLD and ripping:
            log(f"=== rip window closed (sol_h6={s6:+.2f}) ===")
            ripping = False
        runners = fresh_runners()
        log(f"sol_h6={s6:+.2f} rip={ripping} runners={len(runners)}")
        if runners:
            n = asyncio.run(sweep(runners))
            log(f"sweep done: +{n} new trades")
        time.sleep(SWEEP_INTERVAL)


if __name__ == "__main__":
    main()
