"""Dig HARD for the drawdown's winners (AxiS: every drawdown has winners).
The recurrence funnel (>=3 distinct runners) is too strict for a single window
(even AgmLJBMD hits only 2-3/window). This drops the bar to 2, then computes
each buyer's REALIZED P&L with wallet_decode's proven parser (the funnel's
validator zeroed AgmLJBMD) and ranks the actual net-positive winners."""
import sys, os, asyncio, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
import find_daily_positive_wallets as f
import wallet_decode as wd
f.MIN_HITS = 1  # widest: validate P&L on every buyer, not just recurrent ones

runners = f.runners_from_recorder()
print(f"runners: {len(runners)}", flush=True)
rec = asyncio.run(f.harvest(runners))   # {wallet: set(runner tokens)}, >=2 hits
watch = set(__import__("json").load(open("config/follow_watchlist.json")))
rec = {w: t for w, t in rec.items() if w not in watch}
print(f"buyers (>=1 runner): {len(rec)}", flush=True)

cands = sorted(rec.items(), key=lambda kv: -len(kv[1]))[:70]   # widest: top 70 buyers (recurrent first, then singles)
rows = []
for w, toks in cands:
    try:
        tok = wd.trade_map(w, 120)
    except Exception as e:
        print(f"  {w[:10]} decode-fail {e}", flush=True); continue
    net = trips = wins = 0.0
    holds = []
    for m, r in tok.items():
        if r["buys"] and r["sells"] and r["spent"]:
            ret = r["recv"] / r["spent"] - 1
            net += (r["recv"] - r["spent"]); trips += 1; wins += (1 if ret > 0 else 0)
    if trips >= 3:
        rows.append((w, len(toks), int(trips), wins/trips, net))
    time.sleep(0.1)
rows.sort(key=lambda x: -x[4])
print(f"\n=== DRAWDOWN WINNERS (net-positive, >=3 closed trips) ===", flush=True)
print(f"{'wallet':46s}{'hits':>5s}{'trips':>6s}{'WR':>5s}{'netSOL':>9s}", flush=True)
for w, h, t, wr, net in rows:
    flag = "  <-- WINNER" if net > 0 else ""
    print(f"{w:46s}{h:5d}{t:6d}{wr*100:4.0f}%{net:+9.3f}{flag}", flush=True)
winners = [r for r in rows if r[4] > 0]
print(f"\n{len(winners)} net-positive winners of {len(rows)} validated", flush=True)
import json
json.dump([{"wallet": w, "hits": h, "trips": t, "wr": round(wr, 3), "net_sol": round(net, 4)}
           for w, h, t, wr, net in winners], open("_drawdown_winners_ranked.json", "w"), indent=1)
