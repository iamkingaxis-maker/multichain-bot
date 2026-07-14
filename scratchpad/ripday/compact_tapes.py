"""LOCAL: dedup + repair tape_*.jsonl in place (concurrent-append safety).
Drops malformed lines and exact dupes on (ts, maker, volume_usd, kind); rewrites
sorted by ts ascending. Rebuilds tape_index.json.
"""
import glob, json, os

OUT = "scratchpad/ripday"
idx = {}
tot_in = tot_out = 0
for tp in sorted(glob.glob(os.path.join(OUT, "tape_*.jsonl"))):
    seen = set(); rows = []
    for line in open(tp, encoding="ascii", errors="replace"):
        tot_in += 1
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
            k = (t["ts"], t.get("maker", ""), t["volume_usd"], t["kind"])
            assert t["kind"] in ("buy", "sell") and "token" in t and "pair" in t
        except Exception:
            continue
        if k in seen:
            continue
        seen.add(k); rows.append(t)
    if not rows:
        os.remove(tp)
        continue
    rows.sort(key=lambda r: r["ts"])
    tmp = tp + ".tmp"
    with open(tmp, "w", encoding="ascii") as f:
        for t in rows:
            f.write(json.dumps(t) + "\n")
    os.replace(tmp, tp)
    tot_out += len(rows)
    idx[rows[0]["pair"]] = {"token": rows[0]["token"], "sym": rows[0].get("sym"),
                            "file": os.path.basename(tp), "n_trades": len(rows),
                            "sweeps": None, "oldest": rows[0]["ts"],
                            "newest": rows[-1]["ts"]}
json.dump(idx, open(os.path.join(OUT, "tape_index.json"), "w"), indent=1)
print("compacted: %d lines -> %d unique across %d tapes" % (tot_in, tot_out, len(idx)))
