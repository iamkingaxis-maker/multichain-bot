# Our fleet since 2026-07-01: per-token outcomes, hold, exit reasons, bleed list
import json, os, collections, statistics as st

RIP = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(RIP, "our_trades_20260703.json")))
rec = [x for x in d if x.get("time", "") >= "2026-07-01"]
sells = [x for x in rec if x["type"] == "sell"]
buys = [x for x in rec if x["type"] == "buy"]

# dedup fleet jerseys: same (address, entry_price, exit_price) across bots = same underlying signal
# keep per-bot for bot lens but token lens dedups by (address, round(entry,8), round(exit,8))
per_tok = collections.defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0, "holds": [], "reasons": [],
                                           "pnl_pcts": [], "sym": "", "pair": "", "bots": set()})
dedup = {}
for s in sells:
    key = (s["address"], round(s.get("entry_price") or 0, 10), round(s.get("exit_price") or 0, 10), round(s.get("hold_secs") or 0))
    if key in dedup:
        dedup[key]["bots"].add(s.get("bot_id"))
        continue
    dedup[key] = {"s": s, "bots": {s.get("bot_id")}}

print("raw sells:", len(sells), "deduped unique exits:", len(dedup))
uniq = []
for v in dedup.values():
    s = v["s"]
    uniq.append(s)
    t = per_tok[s["address"]]
    t["pnl"] += s.get("pnl") or 0
    t["n"] += 1
    if (s.get("pnl_pct") or 0) > 0:
        t["wins"] += 1
    t["holds"].append(s.get("hold_secs") or 0)
    t["reasons"].append((s.get("reason") or "")[:30])
    t["pnl_pcts"].append(s.get("pnl_pct") or 0)
    t["sym"] = s.get("token", "")
    t["pair"] = s.get("pair_address", "")
    t["bots"] |= v["bots"]

pcts = [s.get("pnl_pct") or 0 for s in uniq]
holds = [s.get("hold_secs") or 0 for s in uniq]
print("unique exits: n=%d mean_pnl_pct=%.2f median=%.2f win_rate=%.1f%%" % (
    len(uniq), st.mean(pcts), st.median(pcts), 100 * sum(1 for p in pcts if p > 0) / len(pcts)))
print("hold_secs: median=%.0f p75=%.0f p90=%.0f" % (st.median(holds), sorted(holds)[int(.75 * len(holds))], sorted(holds)[int(.9 * len(holds))]))
print("reach +6%%: %.1f%% of unique exits (peak_pnl_pct>=6)" % (100 * sum(1 for s in uniq if (s.get("peak_pnl_pct") or 0) >= 6) / len(uniq)))

reason_c = collections.Counter()
for s in uniq:
    r = (s.get("reason") or "").split(" ")[0].split("=")[0][:24]
    reason_c[r] += 1
print("\nexit reasons:", reason_c.most_common(12))

rows = sorted(per_tok.items(), key=lambda kv: kv[1]["pnl"])
print("\n--- WORST tokens (our bleed) ---")
for a, t in rows[:15]:
    print("%-10s pnl=%8.1f n=%2d win=%d/%d medhold=%4.0fs pair=%s" % (t["sym"][:10], t["pnl"], t["n"], t["wins"], t["n"], st.median(t["holds"]), t["pair"][:12]))
print("\n--- BEST tokens ---")
for a, t in rows[-10:]:
    print("%-10s pnl=%8.1f n=%2d win=%d/%d medhold=%4.0fs pair=%s" % (t["sym"][:10], t["pnl"], t["n"], t["wins"], t["n"], st.median(t["holds"]), t["pair"][:12]))

json.dump({a: {"sym": t["sym"], "pair": t["pair"], "pnl": round(t["pnl"], 1), "n": t["n"], "wins": t["wins"],
               "med_hold": st.median(t["holds"]) if t["holds"] else 0}
           for a, t in per_tok.items()}, open(os.path.join(RIP, "our_token_outcomes.json"), "w"), indent=1)

# young lane vs rest
ya = [s for s in sells if s.get("bot_id") == "badday_young_absorb"]
rest = [s for s in sells if s.get("bot_id") != "badday_young_absorb"]
for name, grp in [("young_absorb", ya), ("rest_family", rest)]:
    if not grp:
        continue
    p = [s.get("pnl_pct") or 0 for s in grp]
    print("\n%s: n=%d mean=%.2f med=%.2f win=%.1f%% sum_pnl=%.1f" % (
        name, len(p), st.mean(p), st.median(p), 100 * sum(1 for x in p if x > 0) / len(p),
        sum(s.get("pnl") or 0 for s in grp)))
