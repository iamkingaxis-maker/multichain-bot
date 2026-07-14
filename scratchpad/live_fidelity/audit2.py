"""Proper trip construction: trades-feed live sells (bot_id + booked pnl) joined
to swap-log legs by tx signature. Wallet-truth per trip from lamport flows."""
import json, collections, statistics as st
from datetime import datetime, timezone, timedelta

D = r"C:\Users\jcole\multichain-bot\scratchpad\live_fidelity"
swaps = [r for r in json.load(open(f"{D}/live_swaps.json"))["recent"] if r["ts"] >= "2026-07-09"]
trades = json.load(open(f"{D}/trades_full.json"))

def era_of(ts):
    if ts < "2026-07-11T02:46":  return "A_prepause"
    if ts < "2026-07-11T23:50":  return "B_resume"
    if ts < "2026-07-12T08:00":  return "C_$100"
    return "D_$22.5"

def pt(s):
    return datetime.fromisoformat(s)

sell_swaps = {r["tx_signature"]: r for r in swaps if r["side"] == "sell" and r.get("success")}
buy_swaps  = [r for r in swaps if r["side"] == "buy" and r.get("success")]

lsells = [r for r in trades if r.get("live_signature")]
# join each trades-sell to its swap record
for r in lsells:
    sig = r["live_signature"]
    r["_swap"] = next((sw for s2, sw in sell_swaps.items() if s2 == sig or s2.startswith(sig)), None)
    r["_buy_ts"] = pt(r["time"]) - timedelta(seconds=r["hold_secs"])

# group sells into trips by (bot, address, buy_ts within 5s)
lsells.sort(key=lambda r: r["_buy_ts"])
trips = []
for r in lsells:
    for t in trips:
        if (t["bot"] == r["bot_id"] and t["addr"] == r["address"]
                and abs((t["buy_ts"] - r["_buy_ts"]).total_seconds()) < 10):
            t["sells"].append(r); break
    else:
        trips.append({"bot": r["bot_id"], "addr": r["address"], "sym": r["token"],
                      "buy_ts": r["_buy_ts"], "sells": [r]})

# match swap buy leg: same bot+token, ts closest to buy_ts (within 120s)
used_buys = set()
for t in trips:
    cands = [b for b in buy_swaps if b.get("bot_id") == t["bot"]
             and b["token_address"] == t["addr"] and id(b) not in used_buys]
    if cands:
        best = min(cands, key=lambda b: abs((pt(b["ts"]) - t["buy_ts"]).total_seconds()))
        dt = abs((pt(best["ts"]) - t["buy_ts"]).total_seconds())
        if dt < 300:
            t["buy"] = best; used_buys.add(id(best)); t["buy_dt"] = round(dt, 1)
        else:
            t["buy"] = None; t["buy_dt"] = dt
    else:
        t["buy"] = None; t["buy_dt"] = None

orphan_buys = [b for b in buy_swaps if id(b) not in used_buys]

LAM = 1e9
SIG_FEE = 5000  # lamports per tx

def fee_lam(leg):
    ro = leg.get("raw_order_response") or {}
    f = (ro.get("prioritizationFeeLamports") if isinstance(ro, dict) else None) or 0
    return f + SIG_FEE

out = []
print("=== TRIPS (trades-joined) ===")
for t in sorted(trips, key=lambda t: t["buy_ts"]):
    b = t["buy"]
    legs = [s["_swap"] for s in t["sells"] if s["_swap"]]
    size = b.get("size_usd") if b else None
    entry_slip = b.get("fill_vs_mid_slippage_pct") if b else None
    # proceeds-weighted exit slip
    w = [s.get("live_proceeds_usd") or 1.0 for s in t["sells"]]
    es = [s["_swap"].get("fill_vs_mid_slippage_pct") if s["_swap"] else None for s in t["sells"]]
    exit_slip = (sum(a*x for a, x in zip(w, es)) / sum(w)) if all(e is not None for e in es) and es else None
    booked_pnl_usd = sum(s["pnl"] for s in t["sells"])
    booked_pnl_pct = (booked_pnl_usd / size * 100) if size else None
    # wallet truth: SOL out on buy, SOL in on sells, minus fees
    sol_out = (b["in_amount"] / LAM) if b else None
    sol_in = 0.0
    ok_sol = True
    for s in t["sells"]:
        sw = s["_swap"]
        if sw and sw.get("out_amount"):
            sol_in += sw["out_amount"] / LAM
        else:
            ok_sol = False
    fees_lam = (fee_lam(b) if b else 0) + sum(fee_lam(s["_swap"]) for s in t["sells"] if s["_swap"])
    sol_net = (sol_in - sol_out - fees_lam / LAM) if (b and ok_sol) else None
    fee_pp = (fees_lam / LAM * 78.0 / size * 100) if size else None
    era = era_of(b["ts"]) if b else era_of(t["buy_ts"].isoformat())
    row = {"era": era, "buy_ts": t["buy_ts"].isoformat()[:19], "bot": t["bot"], "sym": t["sym"],
           "addr": t["addr"],
           "size": size, "entry_slip": entry_slip, "exit_slip": None if exit_slip is None else round(exit_slip, 3),
           "n_legs": len(t["sells"]), "fee_pp": None if fee_pp is None else round(fee_pp, 3),
           "friction_pp": None if (entry_slip is None or exit_slip is None or fee_pp is None)
                          else round(entry_slip + exit_slip + fee_pp, 2),
           "booked_pnl_pct": None if booked_pnl_pct is None else round(booked_pnl_pct, 2),
           "booked_pnl_usd": round(booked_pnl_usd, 3),
           "sol_net": None if sol_net is None else round(sol_net, 6),
           "sol_net_pct": None if (sol_net is None or not sol_out) else round(sol_net / sol_out * 100, 2),
           "hold_secs": round(max(s["hold_secs"] for s in t["sells"])),
           "liq": b.get("liquidity_usd") if b else None,
           "buy_dt": t.get("buy_dt")}
    out.append(row)
    print(f"{row['buy_ts']} {row['era']:10s} {row['bot']:22s} {str(row['sym']):12s} ${row['size'] or 0:6.1f} "
          f"entry={row['entry_slip']} exit={row['exit_slip']} legs={row['n_legs']} fee={row['fee_pp']} "
          f"fric={row['friction_pp']} booked={row['booked_pnl_pct']}% sol_net%={row['sol_net_pct']} hold={row['hold_secs']}s dt={row['buy_dt']}")

print(f"\ntrips={len(trips)}  orphan buys (no sell in trades feed): {len(orphan_buys)}")
for b in orphan_buys:
    print("  ORPHAN BUY", b["ts"][:19], b.get("bot_id"), b.get("token_symbol"), b.get("size_usd"))

def med_p90(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return (None, None, 0)
    p90 = xs[min(len(xs)-1, int(round(0.9*(len(xs)-1))))]
    return (round(st.median(xs), 2), round(p90, 2), len(xs))

print("\n=== BY ERA ===")
for era in sorted(set(r["era"] for r in out)):
    er = [r for r in out if r["era"] == era]
    print(f"{era:10s} n={len(er)}")
    print(f"  entry_slip  med/p90 {med_p90([r['entry_slip'] for r in er])}")
    print(f"  exit_slip   med/p90 {med_p90([r['exit_slip'] for r in er])}")
    print(f"  fee_pp      med/p90 {med_p90([r['fee_pp'] for r in er])}")
    print(f"  friction_pp med/p90 {med_p90([r['friction_pp'] for r in er])}")
    print(f"  booked_pnl% med/p90 {med_p90([r['booked_pnl_pct'] for r in er])}  sum$={round(sum(r['booked_pnl_usd'] for r in er),2)}")
    print(f"  sol_net_pct med {med_p90([r['sol_net_pct'] for r in er])}  sum_sol={round(sum(r['sol_net'] or 0 for r in er),6)}")

print("\n=== PREWARM SPLIT 07-12 (entry slip per trip buy) ===")
d12 = [r for r in out if r["buy_ts"] >= "2026-07-12"]
pre  = sorted(r["entry_slip"] for r in d12 if r["buy_ts"] < "2026-07-12T10:00" and r["entry_slip"] is not None)
post = sorted(r["entry_slip"] for r in d12 if r["buy_ts"] >= "2026-07-12T10:00" and r["entry_slip"] is not None)
print("pre :", [round(x,2) for x in pre], med_p90(pre))
print("post:", [round(x,2) for x in post], med_p90(post))

json.dump(out, open(f"{D}/trips2.json", "w"), indent=1)
print("saved trips2.json")
