# Q1: who wins now — winner selection with base rates + bot/aggregator flagging
import json, os, statistics as st, collections

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger2_wallets.json")))

MIN_BUY = 20.0

wallets = {}
for w, eps in led.items():
    traded = [e for e in eps if e["buy_usd"] >= MIN_BUY]
    if not traded:
        continue
    n_trades = sum(e["n_buys"] + e["n_sells"] for e in eps)
    net = sum(e["net"] for e in traded)
    realized = sum(e["realized"] for e in traded)
    unreal = sum(e["unreal"] for e in traded)
    pos = [e for e in traded if e["net"] > 0]
    neg = [e for e in traded if e["net"] <= 0]
    buy_usd = sum(e["buy_usd"] for e in traded)
    px_miss = sum(e["px_missing"] for e in eps)
    wallets[w] = {
        "n_pairs": len(traded), "n_pairs_seen": len(eps), "n_trades": n_trades,
        "buy_usd": round(buy_usd, 1), "net": round(net, 1), "realized": round(realized, 1),
        "unreal": round(unreal, 1), "n_pos": len(pos), "n_neg": len(neg),
        "px_missing": px_miss,
        "capped": sum(1 for e in traded if e["capped_preinv"]),
    }

# --- bot / aggregator flags ---
def bot_flag(w, s):
    if s["n_pairs_seen"] >= 25:
        return "on_everything(%d pairs)" % s["n_pairs_seen"]
    if s["n_trades"] >= 400:
        return "extreme_trade_count(%d)" % s["n_trades"]
    # churn spray: many trades/pair on many pairs
    if s["n_pairs_seen"] >= 10 and s["n_trades"] / max(1, s["n_pairs_seen"]) >= 30:
        return "churn_spray"
    return None

for w, s in wallets.items():
    s["bot"] = bot_flag(w, s)

multi = {w: s for w, s in wallets.items() if s["n_pairs"] >= 3}
multi_h = {w: s for w, s in multi.items() if not s["bot"]}
print("wallets with buys>=%s on >=1 pair: %d" % (MIN_BUY, len(wallets)))
print("multi (>=3 pairs): %d ; after bot-flag exclusion: %d" % (len(multi), len(multi_h)))

# --- base rates ---
all_eps = [e for w, eps in led.items() for e in eps if e["buy_usd"] >= MIN_BUY]
pos_eps = sum(1 for e in all_eps if e["net"] > 0)
print("\nBASE RATE (all wallet-token episodes buy>=%d): n=%d, net>0: %.1f%%, median net/episode=%.2f, mean=%.2f" % (
    MIN_BUY, len(all_eps), 100 * pos_eps / len(all_eps),
    st.median(e["net"] for e in all_eps), st.mean(e["net"] for e in all_eps)))
# per-episode return on buy
rets = [e["net"] / e["buy_usd"] for e in all_eps if e["buy_usd"] > 0]
print("episode net/buy_usd: median=%.1f%% mean=%.1f%%" % (100 * st.median(rets), 100 * st.mean(rets)))

nets = [s["net"] for s in multi_h.values()]
print("\nmulti-token human wallets: net>0: %d/%d (%.1f%%), median net=%.1f" % (
    sum(1 for n in nets if n > 0), len(nets), 100 * sum(1 for n in nets if n > 0) / len(nets), st.median(nets)))

# --- winner set: >=3 pairs, overall net>0, and majority pairs positive ---
winners = {w: s for w, s in multi_h.items() if s["net"] > 0 and s["n_pos"] >= 3}
# strict: also net >= 50 to kill noise
winners_strict = {w: s for w, s in winners.items() if s["net"] >= 50 and s["n_pos"] > s["n_neg"]}
print("\nWINNERS (>=3 pos pairs, net>0, human): %d" % len(winners))
print("WINNERS strict (net>=$50, pos>neg): %d" % len(winners_strict))

rank = sorted(winners.items(), key=lambda kv: -kv[1]["net"])
print("\n%-44s %5s %4s/%4s %9s %9s %9s %6s %s" % ("wallet", "pairs", "pos", "neg", "net", "realzd", "unreal", "trades", "flags"))
for w, s in rank[:40]:
    print("%-44s %5d %4d/%4d %9.1f %9.1f %9.1f %6d cap=%d pxm=%d" % (
        w, s["n_pairs"], s["n_pos"], s["n_neg"], s["net"], s["realized"], s["unreal"], s["n_trades"], s["capped"], s["px_missing"]))

# bot-flagged big fish for the record
print("\n--- bot-flagged high-net (EXCLUDED) ---")
botr = sorted([kv for kv in multi.items() if kv[1]["bot"] and kv[1]["net"] > 0], key=lambda kv: -kv[1]["net"])
for w, s in botr[:10]:
    print("%-44s net=%9.1f pairs=%d trades=%d %s" % (w, s["net"], s["n_pairs_seen"], s["n_trades"], s["bot"]))

json.dump({"winners": {w: s for w, s in winners.items()},
           "winners_strict": list(winners_strict),
           "base_rate_episode_pos_pct": round(100 * pos_eps / len(all_eps), 1),
           "n_multi_human": len(multi_h)},
          open(os.path.join(RIP, "winners_current.json"), "w"), indent=1)
print("\nsaved winners_current.json")
