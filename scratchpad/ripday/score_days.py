# Q1 per-day: winnability + matched-realized winners per day (ledger3, day = first-buy date)
import json, os, statistics as st, collections

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))
MIN_BUY = 20.0
DAYS = ["2026-07-01", "2026-07-02", "2026-07-03"]

# full-window stats for aggregator flagging
full = {}
for w, eps in led.items():
    full[w] = {"pairs_seen": len(eps), "trades": sum(e["n_buys"] + e["n_sells"] for e in eps)}

def day_stats(day):
    wallets = {}
    all_eps = []
    for w, eps in led.items():
        des = [e for e in eps if e["day"] == day and e["buy_usd"] >= MIN_BUY and not e.get("no_px")]
        des_seen = [e for e in eps if e["day"] == day]
        if not des_seen:
            continue
        n_trades = sum(e["n_buys"] + e["n_sells"] for e in des_seen)
        s = {"n_pairs": len(des), "n_pairs_seen": len(des_seen), "n_trades": n_trades}
        if des:
            s.update({
                "net": sum(e["net"] for e in des), "realized": sum(e["realized"] for e in des),
                "unreal": sum(e["unreal"] for e in des), "buy_usd": sum(e["buy_usd"] for e in des),
                "n_pos": sum(1 for e in des if e["net"] > 0), "n_neg": sum(1 for e in des if e["net"] <= 0),
                "n_pos_real": sum(1 for e in des if e["realized"] > 0),
                "capped": sum(1 for e in des if e["capped_preinv"]),
            })
            all_eps += des
        # bot flag: day-level churn OR full-window monster
        bot = None
        if s["n_pairs_seen"] >= 25: bot = "day_on_everything(%d)" % s["n_pairs_seen"]
        elif n_trades >= 400: bot = "day_trades(%d)" % n_trades
        elif s["n_pairs_seen"] >= 10 and n_trades / max(1, s["n_pairs_seen"]) >= 30: bot = "churn_spray"
        elif full[w]["pairs_seen"] >= 40: bot = "full_on_everything(%d)" % full[w]["pairs_seen"]
        elif full[w]["trades"] >= 800: bot = "full_trades(%d)" % full[w]["trades"]
        s["bot"] = bot
        wallets[w] = s
    return wallets, all_eps

winner_sets = {}
for day in DAYS:
    wallets, all_eps = day_stats(day)
    traded = {w: s for w, s in wallets.items() if s.get("n_pairs", 0) >= 1}
    multi = {w: s for w, s in traded.items() if s["n_pairs"] >= 3}
    multi_h = {w: s for w, s in multi.items() if not s["bot"]}
    pos_eps = sum(1 for e in all_eps if e["net"] > 0)
    pos_eps_r = sum(1 for e in all_eps if e["realized"] > 0)
    closed = [e for e in all_eps if e["frac_sold"] >= 0.8]
    print("\n======== %s ========" % day)
    print("episodes(buy>=20)=%d  wallets=%d  multi(>=3 pairs)=%d  human multi=%d" % (
        len(all_eps), len(traded), len(multi), len(multi_h)))
    if all_eps:
        print("BASE: net>0 %.1f%%  realized>0 %.1f%%  median net=%.2f  median net/buy=%.1f%%  closed(frac>=0.8)=%d" % (
            100 * pos_eps / len(all_eps), 100 * pos_eps_r / len(all_eps),
            st.median(e["net"] for e in all_eps),
            100 * st.median(e["net"] / e["buy_usd"] for e in all_eps), len(closed)))
    if multi_h:
        nets = [s["net"] for s in multi_h.values()]
        reals = [s["realized"] for s in multi_h.values()]
        print("multi-human: net>0 %d/%d (%.1f%%)  realized>0 %d (%.1f%%)  median net=%.1f" % (
            sum(1 for n in nets if n > 0), len(nets), 100 * sum(1 for n in nets if n > 0) / len(nets),
            sum(1 for r in reals if r > 0), 100 * sum(1 for r in reals if r > 0) / len(reals), st.median(nets)))
    winners = {w: s for w, s in multi_h.items() if s["net"] > 0 and s["n_pos"] >= 3}
    strict = {w: s for w, s in winners.items() if s["net"] >= 50 and s["n_pos"] > s["n_neg"]}
    core = {w: s for w, s in winners.items() if s["realized"] > 0}
    # realized-first winner def: >=3 pairs, realized>0, >=2 realized-positive episodes
    rwin = {w: s for w, s in multi_h.items() if s["realized"] > 0 and s["n_pos_real"] >= 2}
    print("WINNERS(net)=%d  strict=%d  realized-core(net-winner & realized>0)=%d  REALIZED-winners(real>0,>=2 pos-real eps)=%d" % (
        len(winners), len(strict), len(core), len(rwin)))
    top = sorted(multi_h.items(), key=lambda kv: -kv[1]["realized"])[:8]
    print("top by MATCHED REALIZED:")
    for w, s in top:
        print("  %s.. pairs=%d pos=%d/%d realized=%+.1f unreal=%+.1f net=%+.1f buy=%.0f cap=%d" % (
            w[:8], s["n_pairs"], s["n_pos"], s["n_neg"], s["realized"], s["unreal"], s["net"], s["buy_usd"], s["capped"]))
    winner_sets[day] = {"winners": sorted(winners), "core": sorted(core), "rwin": sorted(rwin)}

# overlap / rotation
print("\n======== winner-set overlap (net-winners) ========")
for a in DAYS:
    for b in DAYS:
        if a < b:
            sa, sb = set(winner_sets[a]["winners"]), set(winner_sets[b]["winners"])
            print("%s vs %s: %d & %d, overlap %d" % (a[-2:], b[-2:], len(sa), len(sb), len(sa & sb)))
json.dump(winner_sets, open(os.path.join(RIP, "winners_by_day.json"), "w"), indent=1)
print("saved winners_by_day.json")
