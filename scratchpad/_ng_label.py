"""Build labeled entry records for the never-green DS decode.

Join each SELL to its prior BUY per token (per bot). Label:
  NEVER_GREEN: peak_pnl_pct<=0.5 and hold_secs<=300
  BOUNCED:     peak_pnl_pct>=3
  OTHER:       everything else
Scrub rule: drop sells with pnl_pct>0 and hold_secs<10.
Sells since 2026-07-02 only.
"""
import json
from datetime import datetime, timezone

CUTOFF = datetime(2026, 7, 2, tzinfo=timezone.utc)

def ts(s):
    return datetime.fromisoformat(s)

out = []
for bot in ["badday_flush", "badday_young_absorb"]:
    rows = json.load(open(f"scratchpad/_ng_trades_{bot}.json"))
    rows.sort(key=lambda r: r["time"])
    last_buy = {}  # token address -> buy row
    n_scrub = n_nolabel = 0
    for r in rows:
        if r["type"] == "buy":
            last_buy[r["address"]] = r
        elif r["type"] == "sell":
            t = ts(r["time"])
            buy = last_buy.get(r["address"])
            # only fully-closed legs count once; partial sells (sell_fraction<1)
            # keep the buy live. Use fully_closed flag when present.
            fully = r.get("fully_closed")
            if t < CUTOFF:
                continue
            pnl = r.get("pnl_pct")
            hold = r.get("hold_secs")
            peak = r.get("peak_pnl_pct")
            if pnl is None or hold is None or peak is None:
                n_nolabel += 1
                continue
            if pnl > 0 and hold < 10:
                n_scrub += 1
                continue
            if peak <= 0.5 and hold <= 300:
                lab = "NEVER_GREEN"
            elif peak >= 3:
                lab = "BOUNCED"
            else:
                lab = "OTHER"
            rec = {
                "bot": bot,
                "token": r["token"],
                "address": r["address"],
                "pair": r.get("pair_address") or (buy or {}).get("pair_address"),
                "sell_time": r["time"],
                "hold_secs": hold,
                "pnl_pct": pnl,
                "peak_pnl_pct": peak,
                "label": lab,
                "fully_closed": fully,
                "sell_fraction": r.get("sell_fraction"),
                "reason": r.get("reason"),
            }
            if buy is not None:
                rec["buy_time"] = buy["time"]
                rec["entry_price"] = buy.get("entry_price")
                rec["entry_meta"] = buy.get("entry_meta") or {}
                rec["buy_ts"] = ts(buy["time"]).timestamp()
            else:
                # reconstruct buy time from sell time - hold
                rec["buy_time"] = None
                rec["buy_ts"] = t.timestamp() - hold
                rec["entry_meta"] = {}
            out.append(rec)
    print(bot, "scrubbed:", n_scrub, "nolabel:", n_nolabel)

# Dedup: multiple partial sells of the same buy leg -> keep the FIRST sell per
# (bot, address, buy_ts) — label/hold/peak refer to the same entry.
seen = set()
dedup = []
for r in sorted(out, key=lambda r: r["sell_time"]):
    k = (r["bot"], r["address"], round(r["buy_ts"], 0))
    if k in seen:
        continue
    seen.add(k)
    dedup.append(r)

from collections import Counter
print("total sell legs:", len(out), "dedup entries:", len(dedup))
for bot in ["badday_flush", "badday_young_absorb"]:
    c = Counter(r["label"] for r in dedup if r["bot"] == bot)
    print(bot, dict(c))
# per-day counts
c2 = Counter((r["bot"], r["sell_time"][:10], r["label"]) for r in dedup)
for k in sorted(c2):
    print(k, c2[k])
# never-green pp cost check
for bot in ["badday_flush", "badday_young_absorb"]:
    ng = [r for r in dedup if r["bot"] == bot and r["label"] == "NEVER_GREEN"]
    print(bot, "NG legs:", len(ng), "pp:", round(sum(r["pnl_pct"] for r in ng), 1))
# entry_meta None coverage
nb = sum(1 for r in dedup if r.get("entry_meta") and r["entry_meta"].get("unique_buyers_n") is None)
print("entries with buyers=None:", nb, "with buy row missing:", sum(1 for r in dedup if r["buy_time"] is None))

json.dump(dedup, open("scratchpad/_ng_entries.json", "w"), indent=1)
print("wrote scratchpad/_ng_entries.json")
