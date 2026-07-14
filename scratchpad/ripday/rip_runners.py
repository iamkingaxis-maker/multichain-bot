"""From recorder events: runners (peak>=25%) whose event fired while sol_pc_h6>1.5."""
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
tss = sorted(e.get("event_ts") for e in ev if e.get("event_ts"))
print("events:", len(ev))
if tss:
    f = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%m-%d %H:%M")
    print("event_ts span:", f(tss[0]), "->", f(tss[-1]))

rip = {}
for e in ev:
    s6 = e.get("sol_pc_h6")
    pk = e.get("peak_pct")
    if not isinstance(s6, (int, float)) or s6 <= 1.5:
        continue
    if not isinstance(pk, (int, float)) or pk < 25:
        continue
    tok = e.get("token_address") or e.get("pair_address")
    cur = rip.get(tok)
    if cur is None or pk > cur["peak"]:
        rip[tok] = {"pair": e.get("pair_address"), "peak": pk, "sym": e.get("symbol"),
                    "ts": e.get("event_ts"), "sol_pc_h6": s6, "liq": e.get("liq_usd"),
                    "mcap": e.get("mcap"), "pc_h1": e.get("pc_h1"),
                    "n_trades": e.get("n_recent_trades_seen"), "buyers": e.get("unique_buyers_n")}
ranked = sorted(rip.items(), key=lambda kv: -kv[1]["peak"])
print(f"\nRIP-WINDOW runners (event sol_pc_h6>1.5, peak>=25%): {len(ranked)}")
for tok, r in ranked[:35]:
    when = datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%m-%d %H:%M") if r["ts"] else "?"
    line = (f"  {str(r['sym'])[:12]:12s} peak={r['peak']:7.0f}% at={when} s6=+{r['sol_pc_h6']:.1f} "
            f"liq={r['liq'] or 0:9.0f} tok={tok}")
    print(line.encode("ascii", "replace").decode("ascii"))
json.dump(rip, open("scratchpad/ripday/rip_runners.json", "w"), indent=1)
print("wrote scratchpad/ripday/rip_runners.json")
