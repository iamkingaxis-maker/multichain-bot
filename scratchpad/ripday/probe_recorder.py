"""Probe universe-recorder: ts coverage + runners (peak>=25%) with events during rip windows."""
import gzip, io, json, urllib.request
from datetime import datetime, timezone

BASE = "https://gracious-inspiration-production.up.railway.app"

def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)

ev = _get(f"{BASE}/api/universe-recorder?limit=5000")
print("events:", len(ev))
if ev:
    print("keys:", sorted(ev[0].keys()))
    tss = [e.get("ts") or e.get("time") or e.get("timestamp") for e in ev]
    tss = [t for t in tss if t]
    if tss:
        print("ts span:", min(tss), "->", max(tss))

# runners with peak>=25
best = {}
for e in ev:
    pk = e.get("peak_pct")
    if not isinstance(pk, (int, float)) or pk < 25:
        continue
    tok = e.get("token_address") or e.get("pair_address")
    if not e.get("pair_address"):
        continue
    cur = best.get(tok)
    ts = e.get("ts") or e.get("time") or e.get("timestamp") or ""
    if cur is None or pk > cur[1]:
        best[tok] = (e.get("pair_address"), pk, e.get("symbol"), ts)
ranked = sorted(best.items(), key=lambda kv: -kv[1][1])[:40]
print(f"\nrunners peak>=25%: {len(best)}, top 40:")
for tok, (pair, pk, sym, ts) in ranked:
    line = f"  {str(sym)[:12]:12s} peak={pk:7.0f}% ts={str(ts)[:19]} tok={tok} pair={pair}"
    print(line.encode("ascii", "replace").decode("ascii"))
json.dump({t: {"pair": p, "peak": pk, "sym": s, "ts": ts} for t, (p, pk, s, ts) in best.items()},
          open("scratchpad/ripday/recorder_runners.json", "w"), indent=1)
print("wrote scratchpad/ripday/recorder_runners.json")
