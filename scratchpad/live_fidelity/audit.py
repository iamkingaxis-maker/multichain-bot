"""Live-vs-paper fidelity + friction audit 07-12. Read-only analysis."""
import json, collections, statistics as st
from datetime import datetime, timezone

D = r"C:\Users\jcole\multichain-bot\scratchpad\live_fidelity"

swaps = json.load(open(f"{D}/live_swaps.json"))["recent"]
trades = json.load(open(f"{D}/trades_full.json"))

recs = [r for r in swaps if r["ts"] >= "2026-07-09"]

def era_of(ts):
    if ts < "2026-07-11T02:46":  return "A_prepause"
    if ts < "2026-07-11T23:50":  return "B_resume_probe"
    if ts < "2026-07-12T08:00":  return "C_$100"
    return "D_$22.5"

for r in recs:
    r["era"] = era_of(r["ts"])

# ---------- timeline ----------
print("=== TIMELINE (all live swaps since 07-09) ===")
for r in recs:
    fee = None
    ro = r.get("raw_order_response") or {}
    if isinstance(ro, dict):
        fee = ro.get("prioritizationFeeLamports")
    print(f"{r['ts'][:19]} {r['era']:15s} {r['side']:4s} {str(r.get('bot_id')):24s} "
          f"{str(r.get('token_symbol')):12s} ${r.get('size_usd') or 0:6.1f} "
          f"slip={r.get('fill_vs_mid_slippage_pct')} lat={r.get('total_latency_ms')} "
          f"liq={r.get('liquidity_usd')} ok={r.get('success')} fee_lam={fee}")

# ---------- round-trip pairing ----------
# buys have bot_id; sells have bot_id None -> pair by token_address FIFO
print("\n=== ROUND TRIPS ===")
buys  = [r for r in recs if r["side"] == "buy" and r.get("success")]
sells = [r for r in recs if r["side"] == "sell" and r.get("success")]
trips = []
sells_by_tok = collections.defaultdict(list)
for s in sells:
    sells_by_tok[s["token_address"]].append(s)
used = set()
for b in buys:
    legs = []
    for s in sells_by_tok.get(b["token_address"], []):
        if id(s) in used: continue
        if s["ts"] > b["ts"]:
            legs.append(s); used.add(id(s))
    trips.append({"buy": b, "sells": legs})

unmatched_sells = [s for s in sells if id(s) not in used]
print(f"buys={len(buys)} sells={len(sells)} unmatched_sells={len(unmatched_sells)}")
for s in unmatched_sells:
    print("  UNMATCHED SELL", s["ts"][:19], s.get("token_symbol"), s.get("size_usd"))

LAMPORTS = 1e9
SOLUSD = 78.0  # approx 07-11/12; only used for fee-in-pp conversion

def trip_row(t):
    b = t["buy"]; legs = t["sells"]
    entry_slip = b.get("fill_vs_mid_slippage_pct")
    size = b.get("size_usd") or 0.0
    # size-weighted exit slip
    if legs:
        w = [(l.get("size_usd") or 1.0) for l in legs]
        es = [l.get("fill_vs_mid_slippage_pct") for l in legs]
        if all(e is not None for e in es):
            exit_slip = sum(a*x for a, x in zip(w, es)) / sum(w)
        else:
            exit_slip = None
    else:
        exit_slip = None
    # fees: prioritization fee on each leg (lamports) -> pp of size
    fee_lam = 0
    nfee = 0
    for leg in [b] + legs:
        ro = leg.get("raw_order_response") or {}
        f = ro.get("prioritizationFeeLamports") if isinstance(ro, dict) else None
        if f:
            fee_lam += f; nfee += 1
    fee_usd = fee_lam / LAMPORTS * SOLUSD
    fee_pp = (fee_usd / size * 100) if size else None
    total_friction = None
    if entry_slip is not None and exit_slip is not None and fee_pp is not None:
        total_friction = entry_slip + exit_slip + fee_pp
    return {
        "era": b["era"], "ts": b["ts"], "bot": b.get("bot_id"),
        "sym": b.get("token_symbol"), "tok": b["token_address"],
        "size": size, "entry_slip": entry_slip, "exit_slip": exit_slip,
        "n_sell_legs": len(legs), "fee_pp": fee_pp, "fee_usd": fee_usd,
        "friction_pp": total_friction,
        "liq": b.get("liquidity_usd"),
        "lat_ms": b.get("total_latency_ms"),
        "closed": bool(legs),
        "sell_ts": legs[-1]["ts"] if legs else None,
    }

rows = [trip_row(t) for t in trips]
for r in rows:
    print(f"{r['ts'][:19]} {r['era']:15s} {str(r['bot']):24s} {str(r['sym']):12s} "
          f"${r['size']:6.1f} entry={r['entry_slip']} exit={r['exit_slip']} "
          f"legs={r['n_sell_legs']} fee_pp={None if r['fee_pp'] is None else round(r['fee_pp'],2)} "
          f"fric={None if r['friction_pp'] is None else round(r['friction_pp'],2)} closed={r['closed']}")

def med_p90(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return (None, None, 0)
    p90 = xs[min(len(xs)-1, int(round(0.9*(len(xs)-1))))]
    return (round(st.median(xs), 2), round(p90, 2), len(xs))

print("\n=== FRICTION BY ERA (closed trips) ===")
for era in sorted(set(r["era"] for r in rows)):
    er = [r for r in rows if r["era"] == era and r["closed"]]
    e_m = med_p90([r["entry_slip"] for r in er])
    x_m = med_p90([r["exit_slip"] for r in er])
    f_m = med_p90([r["fee_pp"] for r in er])
    t_m = med_p90([r["friction_pp"] for r in er])
    print(f"{era:15s} n={len(er):2d}  entry med/p90={e_m}  exit={x_m}  fee_pp={f_m}  TOTAL={t_m}")

# prewarm split within era D (and late C?) at 07-12T10:00
print("\n=== PREWARM SPLIT (entry slip, all eras 07-12, before/after 10:00 UTC) ===")
d12 = [r for r in rows if r["ts"][:10] == "2026-07-12"]
pre  = [r["entry_slip"] for r in d12 if r["ts"] < "2026-07-12T10:00"]
post = [r["entry_slip"] for r in d12 if r["ts"] >= "2026-07-12T10:00"]
print("pre :", sorted(round(x,2) for x in pre if x is not None), med_p90(pre))
print("post:", sorted(round(x,2) for x in post if x is not None), med_p90(post))

json.dump(rows, open(f"{D}/trips.json", "w"), indent=1)
print("\nsaved trips.json")
