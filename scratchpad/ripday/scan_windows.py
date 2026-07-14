"""Scan _full_trades.json for sol_pc_h6 stamps -> locate rip windows (sol_pc_h6>1.5)."""
import json
from datetime import datetime, timezone
from collections import defaultdict

d = json.load(open("_full_trades.json"))
hours = defaultdict(list)
tokens_by_hour = defaultdict(set)
makers_hours = 0
n_meta = 0
tmin, tmax = None, None
for r in d:
    if r.get("type") != "buy":
        continue
    em = r.get("entry_meta") or {}
    s6 = em.get("sol_pc_h6")
    ts = r.get("time")
    if ts:
        dt = datetime.fromisoformat(str(ts))
        if tmin is None or dt < tmin: tmin = dt
        if tmax is None or dt > tmax: tmax = dt
    if s6 is None or ts is None:
        continue
    n_meta += 1
    dt = datetime.fromisoformat(str(ts))
    key = dt.strftime("%Y-%m-%d %H")
    hours[key].append(float(s6))
    tokens_by_hour[key].add((str(em.get("pc_h24"))[:6], r.get("token") or "", (r.get("address") or "")[:10]))
    if em.get("top_buy_makers"):
        makers_hours += 1

print("buys with sol_pc_h6:", n_meta, "| with top_buy_makers:", makers_hours)
print("data span:", tmin, "->", tmax)
print("\nUTC-hour      n   med_s6  max_s6  tag")
for k in sorted(hours):
    v = sorted(hours[k])
    med = v[len(v) // 2]
    tag = "RIP" if med > 1.5 else ("green" if med > 0.5 else "")
    print(f"{k}:00  n={len(v):4d}  med={med:+6.2f}  max={max(v):+6.2f}  {tag}")

# dump tokens bought during RIP hours
print("\n== tokens bought during med sol_pc_h6>1.5 hours ==")
for k in sorted(hours):
    v = sorted(hours[k])
    med = v[len(v) // 2]
    if med > 1.5:
        toks = sorted(tokens_by_hour[k])
        line = f"{k}:00  " + ", ".join(f"{s}({a})" for _, s, a in toks[:12])
        print(line.encode("ascii", "replace").decode("ascii"))
