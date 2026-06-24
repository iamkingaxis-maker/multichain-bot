"""Continuous wallet-decode miner (Track B, autonomous mission 2026-06-15).
Runs UNATTENDED in the background until the deadline (or _mission_stop appears).
Decodes a growing queue of candidate wallets (pond harvest + discovery + roster),
parses each profile, flags COPYABLE ones (diverse + realized + overlapping + +asymmetry),
appends to _mission_mined.jsonl. Periodically re-harvests the fleet's OWN pond (new
winners' early buyers) so the queue keeps growing -> continuous mining. Single process
+ 5s pacing = rate-limit-safe (GT/DexScreener). My loop turns READ _mission_mined.jsonl,
run the deliberate entry-feature mining on the flagged-copyable wallets, and synthesize
new triggers. The miner only WRITES; the loop only READS (no race)."""
import json, os, sys, re, time, glob, subprocess, datetime

DEADLINE = "2026-06-15T20:27:48Z"
OUT = "_mission_mined.jsonl"
SEEN = "_mission_seen_wallets.json"
LOG = "_mission_miner.log"
STOP = "_mission_stop"
PY = sys.executable
RE_HARVEST_EVERY = 40   # wallets


def now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def past_deadline():
    return now() >= DEADLINE


def log(msg):
    line = "%s %s" % (now(), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_seen():
    try:
        return set(json.load(open(SEEN)))
    except Exception:
        return set()


def save_seen(s):
    try:
        json.dump(sorted(s), open(SEEN, "w"))
    except Exception:
        pass


def valid(w):
    return isinstance(w, str) and 30 < len(w) < 50 and w.isalnum()


def build_queue():
    """Pond candidates first (most actionable/copyable), then discovery wallets
    LOW-net-SOL first (retail-size = more likely copyable than the MM whales),
    then any roster file. Dedup, preserve priority order."""
    q = []
    try:
        for c in json.load(open("_our_pond_candidates.json")):
            w = c.get("wallet") or c.get("address") if isinstance(c, dict) else c
            if valid(w):
                q.append(w)
    except Exception:
        pass
    try:
        d = json.load(open("_wallet_discovery_log.json"))
        agg = {}
        for _ts, m in d.items():
            if isinstance(m, dict):
                for w, sc in m.items():
                    if valid(w):
                        agg[w] = max(agg.get(w, 0.0), float(sc) if isinstance(sc, (int, float)) else 0.0)
        for w in sorted(agg, key=lambda w: agg[w]):   # low score (retail) first
            q.append(w)
    except Exception:
        pass
    for pat in ("_roster*.json", "roster*.json", "_wallet_roster*.json", "models/*roster*.json"):
        for f in glob.glob(pat):
            try:
                data = json.load(open(f))
                cand = data if isinstance(data, list) else (list(data.keys()) if isinstance(data, dict) else [])
                for w in cand:
                    if valid(w):
                        q.append(w)
            except Exception:
                pass
    seen = set()
    out = []
    for w in q:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def decode(w):
    try:
        r = subprocess.run([PY, "scripts/wallet_decode.py", w],
                           capture_output=True, text=True, timeout=200)
        return r.stdout or ""
    except Exception as e:
        return "ERR %s" % e


def parse(w, out):
    p = {"wallet": w, "t": now()}
    m = re.search(r"(\d+) tokens, (\d+) closed, (\d+) open", out)
    if m:
        p["tokens"], p["closed"], p["open"] = int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(r"SIZING: median ([\d.]+) SOL/token \| (\w[\w ]*)", out)
    if m:
        p["sol_per_tok"], p["sizing"] = float(m.group(1)), m.group(2).strip()
    m = re.search(r"WR (\d+)% \| win med ([+-]?[\d.]+)% \| loss med ([+-]?[\d.]+)% \| best ([+-]?[\d.]+)%", out)
    if m:
        p["wr"], p["win_med"], p["loss_med"], p["best"] = int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
    m = re.search(r"our scanner saw (\d+)/(\d+).*?we traded (\d+)/", out)
    if m:
        p["ov_saw"], p["ov_tot"], p["ov_traded"] = int(m.group(1)), int(m.group(2)), int(m.group(3))
    # COPYABLE = diverse + realized closes + some overlap (followable) + positive asymmetry
    p["copyable"] = bool(
        p.get("closed", 0) >= 8 and p.get("tokens", 0) >= 8
        and p.get("ov_saw", 0) >= 2 and p.get("wr") is not None
        and p.get("win_med", 0) > abs(p.get("loss_med", 0))
    )
    return p


def main():
    seen = load_seen()
    log("MINER START | seen=%d | deadline=%s" % (len(seen), DEADLINE))
    n_done = 0
    n_copy = 0
    since_harvest = 0
    while not past_deadline() and not os.path.exists(STOP):
        queue = [w for w in build_queue() if w not in seen]
        if not queue:
            log("queue empty (all seen) -> re-harvest + wait")
            try:
                subprocess.run([PY, "scripts/harvest_our_pond.py"], capture_output=True, text=True, timeout=420)
            except Exception as e:
                log("harvest err: %s" % e)
            time.sleep(90)
            continue
        for w in queue:
            if past_deadline() or os.path.exists(STOP):
                break
            out = decode(w)
            prof = parse(w, out)
            try:
                with open(OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(prof) + "\n")
            except Exception as e:
                log("write err: %s" % e)
            seen.add(w)
            save_seen(seen)
            n_done += 1
            if prof.get("copyable"):
                n_copy += 1
                log("COPYABLE %s tokens=%s closed=%s WR=%s win=%s loss=%s ov=%s/%s" % (
                    w[:12], prof.get("tokens"), prof.get("closed"), prof.get("wr"),
                    prof.get("win_med"), prof.get("loss_med"), prof.get("ov_saw"), prof.get("ov_tot")))
            if n_done % 10 == 0:
                log("progress: decoded=%d copyable=%d (last=%s)" % (n_done, n_copy, w[:10]))
            since_harvest += 1
            if since_harvest >= RE_HARVEST_EVERY:
                since_harvest = 0
                log("periodic re-harvest (fleet's new winners)")
                try:
                    subprocess.run([PY, "scripts/harvest_our_pond.py"], capture_output=True, text=True, timeout=420)
                except Exception as e:
                    log("harvest err: %s" % e)
            time.sleep(5)   # rate-limit pacing (single process)
    log("MINER STOP | decoded=%d copyable=%d | deadline=%s stop_file=%s" % (
        n_done, n_copy, past_deadline(), os.path.exists(STOP)))


if __name__ == "__main__":
    main()
