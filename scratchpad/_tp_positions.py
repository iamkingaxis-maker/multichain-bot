"""Reconstruct closed positions since 2026-07-01 per bot; label winners (peak>=3)."""
import json
from collections import defaultdict

BOTS = ["badday_flush", "badday_young_absorb", "badday_adolescent_absorb"]
SINCE = "2026-07-01"

all_pos = []
for bot in BOTS:
    ts = json.load(open(f"scratchpad/_tp_trades_{bot}.json"))
    ts = sorted(ts, key=lambda t: t.get("time", ""))
    open_rounds = {}  # addr -> position dict
    scrubbed = 0
    for t in ts:
        addr = t.get("address")
        if t.get("type") == "buy":
            # a new buy on same addr while a round is open = restart (previous should be closed)
            open_rounds[addr] = {
                "bot": bot, "addr": addr, "token": t.get("token"),
                "pair": t.get("pair_address"), "entry_price": t.get("entry_price"),
                "entry_time": t.get("time"), "amount_usd": t.get("amount_usd"),
                "sells": [],
            }
        elif t.get("type") == "sell":
            r = open_rounds.get(addr)
            if r is None:
                continue  # sell with no buy in window (position opened pre-window)
            pnl = t.get("pnl_pct") or 0.0
            hold = t.get("hold_secs") or 0.0
            if pnl > 0 and hold < 10:
                scrubbed += 1
                # scrub rule: phantom fast green fill — drop the SELL leg entirely
                # but the round continues; mark so fraction accounting is honest
                r["scrubbed_legs"] = r.get("scrubbed_legs", 0) + 1
            else:
                r["sells"].append({
                    "pnl": pnl, "frac": t.get("sell_fraction"),
                    "peak": t.get("peak_pnl_pct"), "hold": hold,
                    "reason": (t.get("reason") or "")[:60],
                    "time": t.get("time"),
                    "fully_closed": t.get("fully_closed"),
                })
            if t.get("fully_closed"):
                if r["sells"] or r.get("scrubbed_legs"):
                    all_pos.append(open_rounds.pop(addr))
                else:
                    open_rounds.pop(addr)
    print(f"{bot}: closed rounds={len([p for p in all_pos if p['bot']==bot])} scrubbed_legs={scrubbed} still_open={len(open_rounds)}")

# filter to entries since 07-01 and fully covered
pos = [p for p in all_pos if (p["entry_time"] or "") >= SINCE]
for p in pos:
    fr = sum(s["frac"] or 0 for s in p["sells"])
    p["fracsum"] = round(fr, 3)
    p["realized_pp"] = sum((s["pnl"] or 0) * (s["frac"] or 0) for s in p["sells"])
    p["peak"] = max([s["peak"] or 0 for s in p["sells"]], default=0)
    p["hold_max"] = max([s["hold"] or 0 for s in p["sells"]], default=0)
    p["winner"] = p["peak"] >= 3

print(f"\npositions since {SINCE}: {len(pos)}")
for bot in BOTS:
    bp = [p for p in pos if p["bot"] == bot]
    w = [p for p in bp if p["winner"]]
    l = [p for p in bp if not p["winner"]]
    tot = sum(p["realized_pp"] for p in bp)
    print(f"{bot}: n={len(bp)} winners={len(w)} losers={len(l)} "
          f"realized_total={tot:+.1f}pp  winner_pp={sum(p['realized_pp'] for p in w):+.1f} "
          f"loser_pp={sum(p['realized_pp'] for p in l):+.1f}")
    bad = [p for p in bp if not (0.9 <= p["fracsum"] <= 1.1)]
    if bad:
        print(f"  WARN fracsum!=1 on {len(bad)} rounds: {[p['fracsum'] for p in bad][:10]}")

json.dump(pos, open("scratchpad/_tp_positions.json", "w"), indent=1)
print("wrote scratchpad/_tp_positions.json")
