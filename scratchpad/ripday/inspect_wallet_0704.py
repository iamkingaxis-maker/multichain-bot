# Inspect a wallet's day episodes from ledger3 (aggregator sanity + episode detail).
import json, os, sys

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))
pref = sys.argv[1]
day = sys.argv[2] if len(sys.argv) > 2 else None
for w, eps in led.items():
    if not w.startswith(pref):
        continue
    print("wallet:", w, " pairs_seen(full window):", len(eps),
          " trades:", sum(e["n_buys"] + e["n_sells"] for e in eps))
    for e in sorted(eps, key=lambda x: x["first_buy"] or ""):
        if day and e["day"] != day:
            continue
        print(" %s %-12s buy=%8.1f nb=%2d ns=%2d realized=%+9.1f unreal=%+8.1f frac_sold=%.2f cap=%s no_px=%s pxm=%d" % (
            e["first_buy"][11:16] if e["first_buy"] else "?", (e["sym"] or e["pair"][:10])[:12],
            e["buy_usd"], e["n_buys"], e["n_sells"], e["realized"], e["unreal"],
            e["frac_sold"], e["capped_preinv"], e.get("no_px"), e["px_missing"]))
        print("   buys:", [round(x, 1) for x in e["buy_usd_list"]][:12])
        print("   sells:", [round(x, 1) for x in e["sell_usd_list"]][:12])
