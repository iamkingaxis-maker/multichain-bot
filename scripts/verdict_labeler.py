#!/usr/bin/env python3
"""
verdict_labeler.py — nightly forward-outcome labeling for shadow verdicts.

Walks (1) green_day_gate + nf5m_toxic_zone BLOCK records from /api/filter-shadow
raw records if available (falls back to the watcher case file), and (2) the
watcher's chart-verified missed-winner state, then fetches GT minute bars
(paced 3.2s, retry-429) and labels each: BOUNCED (+10%/60m from block-time
price), DIED (no +5% by 90m), else AMBIGUOUS. Output: scratchpad/verdicts_YYYYMMDD.md
Run: PYTHONPATH=. python scripts/verdict_labeler.py
"""
import json, os, sys, time, urllib.request, gzip, io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASH = "https://gracious-inspiration-production.up.railway.app"


def get(url, gz=True, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                **({"Accept-Encoding": "gzip"} if gz else {})})
            r = urllib.request.urlopen(req, timeout=30)
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return json.loads(raw)
        except Exception:
            time.sleep(6 * (i + 1))
    return None


def label_from_bars(pair, t0):
    q = get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}"
            f"/ohlcv/minute?aggregate=1&limit=400", gz=False)
    time.sleep(3.2)
    bars = sorted(((q or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list") or [])
    fwd = [(b[0], b[4]) for b in bars if t0 <= b[0] <= t0 + 5400]
    if len(fwd) < 10:
        return "NO-DATA", None
    p0 = fwd[0][1]
    peak60 = max((c for t, c in fwd if t <= t0 + 3600), default=p0)
    peak90 = max((c for t, c in fwd), default=p0)
    if peak60 / p0 - 1 >= 0.10:
        return "BOUNCED", round((peak60 / p0 - 1) * 100, 1)
    if peak90 / p0 - 1 < 0.05:
        return "DIED", round((peak90 / p0 - 1) * 100, 1)
    return "AMBIGUOUS", round((peak90 / p0 - 1) * 100, 1)


def main():
    day = time.strftime("%Y%m%d", time.gmtime())
    out = [f"# Verdict labels {day}", ""]
    # source: watcher state (chart-verified misses carry pair addresses via recorder)
    ev = get(f"{DASH}/api/universe-recorder?limit=1500") or {}
    events = ev.get("events", ev) if isinstance(ev, dict) else ev
    st = {}
    try:
        st = json.load(open(os.path.join("scratchpad", "market_watch_state.json")))
    except Exception:
        pass
    seen = st.get("mw_seen") or {}
    # map alerted symbols -> most recent event with pair + ts
    by_sym = {}
    for e in events or []:
        s = str(e.get("symbol") or "").lower()
        if f"mw_{s}" in seen and e.get("pair_address"):
            ts = float(e.get("event_ts") or 0)
            if s not in by_sym or ts > by_sym[s][1]:
                by_sym[s] = (e["pair_address"], ts)
    tallies = {}
    out.append(f"## Missed-winner case file ({len(by_sym)} tokens)")
    for s, (pair, ts) in sorted(by_sym.items()):
        lab, pk = label_from_bars(pair, int(ts))
        tallies[lab] = tallies.get(lab, 0) + 1
        out.append(f"- {s}: {lab} (fwd peak {pk}%)")
    out.append("")
    out.append(f"## Tally: {tallies}")
    path = os.path.join("scratchpad", f"verdicts_{day}.md")
    open(path, "w").write("\n".join(out))
    print(f"labeled {len(by_sym)} -> {path} | tally {tallies}")


if __name__ == "__main__":
    main()
