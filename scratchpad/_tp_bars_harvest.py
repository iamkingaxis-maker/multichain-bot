"""Harvest GT minute bars [entry, entry+6h] per position. Checkpointed/resumable."""
import json, time, os, sys, urllib.request, urllib.error
from datetime import datetime, timezone

OUT = "scratchpad/_tp_bars.jsonl"
DEADLINE = time.time() + 520
PACE = 3.0

pos = json.load(open("scratchpad/_tp_positions.json"))
for p in pos:
    p["pid"] = f"{p['bot']}|{p['addr']}|{p['entry_time']}"
    p["entry_ts"] = datetime.fromisoformat(p["entry_time"]).timestamp()

done = set()
if os.path.exists(OUT):
    for line in open(OUT):
        try:
            done.add(json.loads(line)["pid"])
        except Exception:
            pass

# order: winners first (biggest peak first), then losers
pos.sort(key=lambda p: (not p["winner"], -p["peak"]))
todo = [p for p in pos if p["pid"] not in done]
print(f"positions={len(pos)} done={len(done)} todo={len(todo)}")

# per-pair bar cache accumulated this run (ascending ts list)
paircache = {}  # pair -> {ts: bar}

def gt_fetch(pair, before, limit=520):
    url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}"
           f"/ohlcv/minute?aggregate=1&before_timestamp={int(before)}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 tp-peel-replay",
                                               "Accept": "application/json"})
    backoff = 20
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
            return d["data"]["attributes"]["ohlcv_list"], None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if time.time() + backoff > DEADLINE:
                    return None, "deadline_429"
                time.sleep(backoff); backoff *= 2
                continue
            return None, f"http_{e.code}"
        except Exception as e:
            return None, f"exc_{type(e).__name__}"
    return None, "429_exhausted"

fout = open(OUT, "a")
n_fetch = 0
for i, p in enumerate(todo):
    if time.time() > DEADLINE:
        print("DEADLINE — rerun to resume"); break
    t0, t1 = p["entry_ts"] - 120, p["entry_ts"] + 6 * 3600
    cache = paircache.setdefault(p["pair"], {})
    have = [ts for ts in cache if t0 <= ts <= t1]
    # coverage test: need a bar at/near entry and bars reaching near t1
    covered = bool(have) and min(have) <= p["entry_ts"] + 300 and max(have) >= t1 - 900
    if not covered:
        ol, err = gt_fetch(p["pair"], t1 + 120)
        n_fetch += 1
        time.sleep(PACE)
        if err == "deadline_429":
            print("429 near deadline — rerun to resume"); break
        if ol is None:
            fout.write(json.dumps({"pid": p["pid"], "err": err, "bars": []}) + "\n")
            fout.flush()
            print(f"[{i}] {p['token']} ERR {err}")
            continue
        for b in ol:
            cache[b[0]] = b
        have = [ts for ts in cache if t0 <= ts <= t1]
        # if fetched window doesn't reach back to entry (dense token), page once more
        if have and min(have) > p["entry_ts"] + 300:
            ol2, err2 = gt_fetch(p["pair"], min(have))
            n_fetch += 1
            time.sleep(PACE)
            if ol2:
                for b in ol2:
                    cache[b[0]] = b
                have = [ts for ts in cache if t0 <= ts <= t1]
    bars = sorted((cache[ts] for ts in have), key=lambda b: b[0])
    first = datetime.fromtimestamp(bars[0][0], timezone.utc).isoformat() if bars else None
    cov_entry = bool(bars) and bars[0][0] <= p["entry_ts"] + 300
    fout.write(json.dumps({"pid": p["pid"], "bars": bars, "cov_entry": cov_entry}) + "\n")
    fout.flush()
    tok = (p["token"] or "?").encode("ascii", "replace").decode()[:12]
    print(f"[{i}] {p['bot'][7:]:>16} {tok:<12} peak={p['peak']:6.1f} "
          f"bars={len(bars)} cov_entry={cov_entry}")
fout.close()
print(f"run done: fetched={n_fetch}")
