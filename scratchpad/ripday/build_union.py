"""LOCAL: final candidate-wallet union across all identity sources.
Writes candidate_wallets_union.json ranked for the decoder fan-out.
"""
import glob, json, os

OUT = "scratchpad/ripday"

pnl = json.load(open(os.path.join(OUT, "wallet_pnl.json")))
prelim = {w["wallet"]: w for w in json.load(open(os.path.join(OUT, "winners_prelim.json")))["winners"]}
cf = {w["wallet"]: w for w in json.load(open(os.path.join(OUT, "candidates_fulltrades.json")))["wallets"]}
gd = {w["wallet"]: w for w in json.load(open(os.path.join(OUT, "greenday_winners.json")))["wallets"]}
try:
    known = json.load(open(os.path.join(OUT, "known_wallets_in_tape.json")))
    wl = set(known.get("watchlist_in_tape") or [])
except Exception:
    wl = set()

union = {}
def ent(w):
    return union.setdefault(w, {"wallet": w, "sources": [], "tape": None,
                                "fulltrades": None, "greenday": None,
                                "on_watchlist": w in wl})

for w, s in pnl["wallets"].items():
    if s["n_tokens_traded"] >= 1 and (s["n_pos"] >= 1 or s["covered_net_closed_usd"] > 0):
        e = ent(w); e["sources"].append("io_tape")
        e["tape"] = {"n_tokens_traded": s["n_tokens_traded"], "n_pos": s["n_pos"],
                     "n_neg": s["n_neg"], "n_open_bags": s["n_open_bags"],
                     "covered_net_closed_usd": s["covered_net_closed_usd"]}
for w, s in cf.items():
    if s["n_tokens"] >= 2:
        e = ent(w); e["sources"].append("fulltrades_recurrent")
        e["fulltrades"] = {"n_tokens": s["n_tokens"], "vol_usd": s["vol_usd"]}
for w, s in gd.items():
    if s["winner"]:
        e = ent(w); e["sources"].append("greenday_winner")
        e["greenday"] = {"trips": s["trips"], "wr_pct": s["wr_pct"], "net_sol": s["net_sol"]}
for w in wl:
    e = ent(w)
    if "watchlist" not in e["sources"]:
        e["sources"].append("watchlist")

def score(e):
    t = e.get("tape") or {}
    return (t.get("n_pos", 0), len(e["sources"]), t.get("covered_net_closed_usd", 0))

ranked = sorted(union.values(), key=score, reverse=True)
n2 = sum(1 for e in ranked if (e.get("tape") or {}).get("n_pos", 0) >= 2)
n3 = sum(1 for e in ranked if (e.get("tape") or {}).get("n_pos", 0) >= 3)
multi = sum(1 for e in ranked if len(e["sources"]) >= 2)
json.dump({"n": len(ranked), "n_tape_pos2": n2, "n_tape_pos3": n3,
           "n_multi_source": multi, "wallets": ranked},
          open(os.path.join(OUT, "candidate_wallets_union.json"), "w"), indent=1)
print("union: %d wallets | tape-pos>=2: %d | tape-pos>=3: %d | multi-source: %d"
      % (len(ranked), n2, n3, multi))
for e in ranked[:12]:
    print("  %s %s %s" % (e["wallet"][:12], e["sources"], e.get("tape")))
