#!/usr/bin/env python3
"""Extract labeled winner positions (monster peak>=40 vs regular peak 8-20) from _tr.json."""
import json
from datetime import datetime, timezone

d = json.load(open("_tr.json", encoding="utf-8"))
tr = d.get("trades", d) if isinstance(d, dict) else d

def pt(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))

# build positions: per (bot? no — per address), buy opens, sells until position closed.
# Simpler robust grouping: per address, sort by time; a buy after a "fully closed" state opens new pos.
rows = sorted(tr, key=lambda x: x.get("time") or "")
pos = {}       # address -> current open position dict
closed = []
for t in rows:
    a = t.get("address")
    if not a:
        continue
    if t.get("type") == "buy":
        if a not in pos:
            pos[a] = {"sym": t.get("token"), "addr": a, "pair": t.get("pair_address"),
                      "entry_ts": t.get("time"), "entry_price": t.get("entry_price"),
                      "bot": t.get("bot_id"), "sells": [], "buys": 1,
                      "entry_meta": t.get("entry_meta") or {}}
        else:
            pos[a]["buys"] += 1
    elif t.get("type") == "sell":
        p = pos.get(a)
        if not p:
            continue
        p["sells"].append(t)
        r = str(t.get("reason") or "")
        # heuristic: full exit reasons close the position
        if not any(k in r for k in ("TP1",)) or "fully" in r:
            # trail/stop/final sells close; TP1 partial keeps open
            if "TP1" not in r:
                pos.pop(a, None)
                closed.append(p)
# whatever remains open with sells — include too (peak known so far)
for p in pos.values():
    if p["sells"]:
        closed.append(p)

out = []
for p in closed:
    peaks = [s.get("peak_pnl_pct") for s in p["sells"] if s.get("peak_pnl_pct") is not None]
    if not peaks:
        continue
    peak = max(peaks)
    last = p["sells"][-1]
    out.append({
        "sym": p["sym"], "addr": p["addr"], "pair": p["pair"], "bot": p.get("bot"),
        "entry_ts": p["entry_ts"], "last_sell_ts": last.get("time"),
        "peak": round(peak, 2),
        "final_pnl": round(last.get("pnl_pct") or 0, 2),
        "n_sells": len(p["sells"]),
    })

cutoff = "2026-07-04"
out = [o for o in out if str(o["entry_ts"]) >= cutoff]
mon = sorted([o for o in out if o["peak"] >= 40], key=lambda x: -x["peak"])
reg = sorted([o for o in out if 8 <= o["peak"] <= 20], key=lambda x: -x["peak"])
print(f"positions since {cutoff}: {len(out)}; monsters>=40: {len(mon)}; regular 8-20: {len(reg)}")

def show(lst, name, cap=40):
    print(f"\n== {name} (n={len(lst)}) ==")
    for c in lst[:cap]:
        print(f"{str(c['sym']):<14} peak={c['peak']:>7.1f} entry={str(c['entry_ts'])[:16]} "
              f"exitts={str(c['last_sell_ts'])[:16]} final={c['final_pnl']:>6.1f} addr={c['addr']} pair={c['pair']}")
show(mon, "MONSTERS")
show(reg, "REGULARS")

json.dump({"monsters": mon, "regulars": reg}, open("scratchpad/_runner_labels.json", "w"), indent=1)

for name in ("mogdog", "SMOLE", "Bullscan"):
    hits = [t for t in tr if str(t.get("token") or "").lower() == name.lower()]
    if hits:
        h = hits[0]
        print(f"\nNAMED {name}: rows={len(hits)} addr={h.get('address')} pair={h.get('pair_address')} "
              f"first={h.get('time','')[:16]} type={h.get('type')}")
    else:
        print(f"\nNAMED {name}: not found in _tr.json")
