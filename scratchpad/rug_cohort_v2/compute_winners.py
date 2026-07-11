"""Step 3a: winners from the union of trade caches.
Dedupe sells on (bot_id, address, time, round(pnl,6)); sum realized pnl.
anybot = ANY (bot,mint) net>0; strict = mint net>0 across all bots.
Output: winners.json {anybot: [...], strict: [...], mint_net: {...}}
"""
import json, gzip, os, glob

REPO = r"C:\Users\jcole\multichain-bot"
SP = os.path.join(REPO, "scratchpad")
V2 = os.path.join(SP, "rug_cohort_v2")

CANDS = [os.path.join(SP, f) for f in (
    "_full_trades.json", "_ev_trades.json", "_tcond_trades.json",
    "_trades_fresh.json", "_trades_full.json", "_trades_full_2026_07_06.json",
    "_trades_now.json", "_trades_new.json", "_vf_trades.json",
)] + [os.path.join(V2, "_trades_today.json")] \
  + glob.glob(os.path.join(REPO, "analysis", "legacy_data", "*trades*.json")) \
  + [os.path.join(REPO, "analysis", "legacy_data", "all.json")] \
  + glob.glob(os.path.join(REPO, "analysis", "winloss_8hr", "*trades*.json")) \
  + [os.path.join(REPO, "analysis", "_prune_mine", "_overall_trades.json"),
     os.path.join(REPO, "analysis", "_research", "trades_full.json"),
     os.path.join(REPO, "analysis", "2026-06", "data", "_crash_trades.json"),
     os.path.join(REPO, "analysis", "2026-06", "data", "_nf_trades.json")]

seen = set()
mint_net = {}
botmint_net = {}
for p in CANDS:
    if not os.path.exists(p):
        continue
    try:
        with (gzip.open(p, "rt", encoding="utf-8") if p.endswith(".gz")
              else open(p, encoding="utf-8")) as f:
            d = json.load(f)
    except Exception:
        continue
    trades = d if isinstance(d, list) else (d.get("trades") or [])
    for t in trades:
        if not isinstance(t, dict):
            continue
        if (t.get("type") or t.get("kind")) != "sell":
            continue
        m = t.get("address")
        pnl = t.get("pnl")
        if not m or pnl is None:
            continue
        key = (t.get("bot_id"), m, str(t.get("time")), round(float(pnl), 6))
        if key in seen:
            continue
        seen.add(key)
        mint_net[m] = mint_net.get(m, 0.0) + float(pnl)
        bk = (t.get("bot_id"), m)
        botmint_net[bk] = botmint_net.get(bk, 0.0) + float(pnl)

anybot = sorted({m for (b, m), v in botmint_net.items() if v > 0})
strict = sorted([m for m, v in mint_net.items() if v > 0])
json.dump({"anybot": anybot, "strict": strict,
           "mint_net": {m: round(v, 4) for m, v in mint_net.items()}},
          open(os.path.join(V2, "winners.json"), "w"))
print(f"distinct sell rows={len(seen)} mints_with_sells={len(mint_net)} "
      f"winners_anybot={len(anybot)} winners_strict={len(strict)}")
