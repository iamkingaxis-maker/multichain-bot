"""LOCAL ONLY: candidate wallet identities from prior artifacts.

Outputs (scratchpad/ripday/):
  candidates_fulltrades.json - rip-hour (sol_pc_h6>1.5) buy-side makers from
      _full_trades.json entry_meta.top_buy_makers, with cross-token recurrence.
  greenday_winners.json      - 06-29 validated net-positive winners parsed from
      _greenday_winners_out.txt.
  rip_artifact_buys.json     - all_winner_buys.json buys restricted to rip hours.
"""
import json, os, re
from datetime import datetime, timezone

OUT = "scratchpad/ripday"

# rip windows (UTC epoch ranges) from recon
def _ep(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())

WINDOWS = [
    ("2026-06-24 22:00", "2026-06-25 01:00"),
    ("2026-06-25 05:00", "2026-06-25 09:00"),
    ("2026-06-25 19:00", "2026-06-25 22:00"),
    ("2026-06-26 00:00", "2026-06-26 01:00"),
    ("2026-06-26 06:00", "2026-06-26 10:00"),
    ("2026-06-26 15:00", "2026-06-26 21:00"),
    ("2026-06-27 15:00", "2026-06-27 16:00"),
    ("2026-06-28 08:00", "2026-06-28 13:00"),
    ("2026-06-29 03:00", "2026-06-29 04:00"),
    ("2026-06-29 17:00", "2026-06-29 22:00"),
    ("2026-06-30 18:00", "2026-06-30 19:40"),
    ("2026-07-01 03:00", "2026-07-01 05:00"),
    ("2026-07-01 13:00", "2026-07-01 19:00"),
]
WIN_EP = [(_ep(a), _ep(b)) for a, b in WINDOWS]

def in_window(ep):
    return any(a <= ep <= b for a, b in WIN_EP)

# ---- 1. _full_trades.json rip-hour makers ----
d = json.load(open("_full_trades.json"))
wallets = {}   # addr -> {tokens: {tok: vol}, n_buys, vol_usd}
n_rip_buys = 0
for r in d:
    if r.get("type") != "buy":
        continue
    em = r.get("entry_meta") or {}
    s6 = em.get("sol_pc_h6")
    if not isinstance(s6, (int, float)) or s6 <= 1.5:
        continue
    n_rip_buys += 1
    tok = r.get("token")
    for m in em.get("top_buy_makers") or []:
        a = m.get("addr")
        if not a:
            continue
        w = wallets.setdefault(a, {"tokens": {}, "n_buys": 0, "vol_usd": 0.0})
        w["tokens"][tok] = w["tokens"].get(tok, 0.0) + (m.get("volume_usd") or 0.0)
        w["n_buys"] += m.get("n_buys") or 1
        w["vol_usd"] += m.get("volume_usd") or 0.0

rows = []
for a, w in wallets.items():
    rows.append({"wallet": a, "n_tokens": len(w["tokens"]),
                 "n_buys": w["n_buys"], "vol_usd": round(w["vol_usd"], 2),
                 "tokens": sorted(w["tokens"], key=lambda t: -w["tokens"][t])})
rows.sort(key=lambda r: (-r["n_tokens"], -r["vol_usd"]))
json.dump({"n_rip_hour_buys": n_rip_buys, "n_wallets": len(rows),
           "recurrent_2plus": sum(1 for r in rows if r["n_tokens"] >= 2),
           "wallets": rows},
          open(os.path.join(OUT, "candidates_fulltrades.json"), "w"), indent=1)
print("fulltrades: %d rip-hour buys, %d wallets, %d recurrent>=2" %
      (n_rip_buys, len(rows), sum(1 for r in rows if r["n_tokens"] >= 2)))

# ---- 2. greenday winners parse ----
gw = []
try:
    txt = open("_greenday_winners_out.txt", encoding="utf-8", errors="replace").read()
    # lines with wallet addresses + netSOL; capture base58-ish 32-44 char tokens with numbers on the line
    for line in txt.splitlines():
        m = re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", line)
        if not m:
            continue
        nums = re.findall(r"[-+]?\d+\.\d+", line)
        gw.append({"line": line.strip(), "addrs": m, "nums": nums})
except FileNotFoundError:
    pass
json.dump(gw, open(os.path.join(OUT, "greenday_winners_rawlines.json"), "w"), indent=1)
print("greenday raw lines with addrs: %d" % len(gw))

# ---- 3. all_winner_buys.json rip-hour restriction ----
try:
    awb = json.load(open("scratchpad/all_winner_buys.json"))
    out = {}
    tot = 0
    for wal, buys in awb.items():
        keep = [b for b in buys if in_window(b.get("bt") or 0)]
        if keep:
            out[wal] = keep
            tot += len(keep)
    json.dump(out, open(os.path.join(OUT, "rip_artifact_buys.json"), "w"), indent=1)
    print("artifact buys in rip hours: %d wallets, %d buys" % (len(out), tot))
except FileNotFoundError:
    print("all_winner_buys.json missing")
