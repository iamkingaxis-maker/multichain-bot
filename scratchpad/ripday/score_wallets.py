"""LOCAL ONLY: per-wallet per-token P&L from harvested io tapes.

Reads all scratchpad/ripday/tape_*.jsonl. For each (wallet, token):
  - chronological walk; realized net counted only from the wallet's FIRST
    COVERED BUY onward (sells before any buy = pre-window position, excluded
    from 'covered' net but tallied separately).
Outputs:
  wallet_pnl.json  - per wallet: per-token stats + cross-token summary
  winners_prelim.json - wallets net-positive (covered) on >=2 tokens
"""
import glob, json, os
from collections import defaultdict

OUT = "scratchpad/ripday"

tapes = sorted(glob.glob(os.path.join(OUT, "tape_*.jsonl")))
# wallet -> token -> stats
W = defaultdict(dict)
tok_sym = {}

for tp in tapes:
    rows = []
    for line in open(tp, encoding="ascii", errors="replace"):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    if not rows:
        continue
    rows.sort(key=lambda r: r["ts"])
    tok = rows[0]["token"]
    tok_sym[tok] = rows[0].get("sym")
    per = defaultdict(lambda: {"buy_usd": 0.0, "sell_usd": 0.0, "n_buys": 0,
                               "n_sells": 0, "first_ts": None, "last_ts": None,
                               "first_kind": None, "sell_before_buy_usd": 0.0,
                               "covered_sell_usd": 0.0, "seen_buy": False})
    for r in rows:
        mk = r.get("maker") or ""
        if not mk:
            continue
        s = per[mk]
        v = r.get("volume_usd") or 0.0
        if s["first_ts"] is None:
            s["first_ts"] = r["ts"]; s["first_kind"] = r["kind"]
        s["last_ts"] = r["ts"]
        if r["kind"] == "buy":
            s["buy_usd"] += v; s["n_buys"] += 1; s["seen_buy"] = True
        else:
            s["sell_usd"] += v; s["n_sells"] += 1
            if s["seen_buy"]:
                s["covered_sell_usd"] += v
            else:
                s["sell_before_buy_usd"] += v
    for mk, s in per.items():
        if s["n_buys"] + s["n_sells"] == 0:
            continue
        s.pop("seen_buy")
        s["net_usd"] = round(s["sell_usd"] - s["buy_usd"], 2)
        # covered net: sells after first covered buy minus buys
        s["covered_net_usd"] = round(s["covered_sell_usd"] - s["buy_usd"], 2)
        for k in ("buy_usd", "sell_usd", "covered_sell_usd", "sell_before_buy_usd"):
            s[k] = round(s[k], 2)
        W[mk][tok] = s

# summarize
summary = {}
for mk, toks in W.items():
    # only meaningful tokens: wallet actually bought >= $20 there
    traded = {t: s for t, s in toks.items() if s["buy_usd"] >= 20}
    pos = [t for t, s in traded.items() if s["covered_net_usd"] > 0]
    neg = [t for t, s in traded.items() if s["covered_net_usd"] < 0
           and s["n_sells"] > 0]
    open_bags = [t for t, s in traded.items() if s["n_sells"] == 0]
    tot_cov = round(sum(s["covered_net_usd"] for s in traded.values()
                        if s["n_sells"] > 0), 2)
    summary[mk] = {
        "n_tokens_seen": len(toks),
        "n_tokens_traded": len(traded),
        "n_pos": len(pos), "n_neg": len(neg), "n_open_bags": len(open_bags),
        "covered_net_closed_usd": tot_cov,
        "pos_tokens": pos, "neg_tokens": neg,
        "tokens": toks,
    }

json.dump({"n_wallets": len(summary), "tok_sym": tok_sym, "wallets": summary},
          open(os.path.join(OUT, "wallet_pnl.json"), "w"))

# preliminary winners: net-positive covered on >=2 tokens
prelim = {mk: s for mk, s in summary.items() if s["n_pos"] >= 2}
# rank by (n_pos, covered net)
ranked = sorted(prelim.items(), key=lambda kv: (-kv[1]["n_pos"], -kv[1]["covered_net_closed_usd"]))
out = []
for mk, s in ranked:
    out.append({"wallet": mk, "n_pos": s["n_pos"], "n_neg": s["n_neg"],
                "n_open_bags": s["n_open_bags"],
                "covered_net_closed_usd": s["covered_net_closed_usd"],
                "pos_tokens": [{"token": t, "sym": tok_sym.get(t),
                                "net": s["tokens"][t]["covered_net_usd"],
                                "buy_usd": s["tokens"][t]["buy_usd"]}
                               for t in s["pos_tokens"]]})
json.dump({"bar": "covered_net>0 on >=2 tokens, buy_usd>=20/token",
           "n": len(out), "winners": out},
          open(os.path.join(OUT, "winners_prelim.json"), "w"), indent=1)

n3 = sum(1 for r in out if r["n_pos"] >= 3)
print("tapes: %d | wallets with any maker activity: %d" % (len(tapes), len(summary)))
print("prelim winners >=2 pos tokens: %d  (>=3: %d)" % (len(out), n3))
for r in out[:15]:
    print("  %s n_pos=%d net=$%.0f open=%d" % (r["wallet"][:10], r["n_pos"],
          r["covered_net_closed_usd"], r["n_open_bags"]))
