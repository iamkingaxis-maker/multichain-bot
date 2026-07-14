# For each of OUR velocity-bail exits since 07-01: did the token recover (bars)?
import json, os, glob, bisect, statistics as st, collections, sys
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8")

RIP = os.path.dirname(os.path.abspath(__file__))

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    d = json.load(open(f))
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
led = json.load(open(os.path.join(RIP, "ledger2_wallets.json")))
pairs_all = set(e["pair"] for eps in led.values() for e in eps)
p12m = {p[:12]: p for p in pairs_all}
for f in glob.glob(os.path.join(RIP, "_gt_bars", "*.json")):
    p = p12m.get(os.path.basename(f).split(".")[0])
    if not p:
        continue
    b = json.load(open(f))
    if isinstance(b, list) and b:
        bars_by_pair.setdefault(p, []).extend(b)
for p in bars_by_pair:
    u = {int(x[0]): x for x in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda x: x[0])
bar_ts = {p: [b[0] for b in bars_by_pair[p]] for p in bars_by_pair}

our = [x for x in json.load(open(os.path.join(RIP, "our_trades_20260703.json"))) if x.get("time", "") >= "2026-07-01"]
sells = [x for x in our if x["type"] == "sell"]
dedup = {}
for s in sells:
    k = (s["address"], round(s.get("entry_price") or 0, 12), round(s.get("exit_price") or 0, 12), round(s.get("hold_secs") or 0))
    dedup.setdefault(k, s)
uniq = list(dedup.values())
vb = [s for s in uniq if "velocity-bail" in (s.get("reason") or "")]

rows = []
for s in vb:
    pa = (s.get("pair_address") or "")
    p = p12m.get(pa[:12])
    if not p or p not in bars_by_pair:
        continue
    ep = iso2ep(s["time"])
    ts = bar_ts[p]
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0 or ep - ts[i] > 1800:
        continue
    bl = bars_by_pair[p]
    exit_px = bl[i][4]
    entry_px = s.get("entry_price") or 0
    if not exit_px or exit_px <= 0 or entry_px <= 0:
        continue
    for horizon, tag in [(3600, "60m"), (7200, "120m")]:
        pass
    k60 = bisect.bisect_right(ts, ep + 3600)
    k120 = bisect.bisect_right(ts, ep + 7200)
    fwd60 = bl[i + 1:k60]
    fwd120 = bl[i + 1:k120]
    if not fwd60:
        continue
    # measure vs OUR bar-based exit px (removes paper-vs-bar basis)
    mx60 = 100 * (max(b[2] for b in fwd60) / exit_px - 1)
    mn60 = 100 * (min(b[3] for b in fwd60) / exit_px - 1)
    mx120 = 100 * (max(b[2] for b in fwd120) / exit_px - 1) if fwd120 else mx60
    rows.append({"sym": s.get("token", ""), "pnl_pct": s.get("pnl_pct") or 0,
                 "mx60": mx60, "mn60": mn60, "mx120": mx120})

print("velocity-bails with bar coverage: n=%d / %d" % (len(rows), len(vb)))
mx = sorted(r["mx60"] for r in rows)
mn = sorted(r["mn60"] for r in rows)
mx2 = sorted(r["mx120"] for r in rows)
q = lambda v, f: v[min(len(v) - 1, int(f * len(v)))]
print("fwd MAX 60m from bail px : p25=%.1f med=%.1f p75=%.1f mean=%.1f" % (q(mx, .25), st.median(mx), q(mx, .75), st.mean(mx)))
print("fwd MIN 60m from bail px : p25=%.1f med=%.1f p75=%.1f mean=%.1f" % (q(mn, .25), st.median(mn), q(mn, .75), st.mean(mn)))
print("fwd MAX 120m             : med=%.1f" % st.median(mx2))
rec6 = 100 * sum(1 for r in rows if r["mx60"] >= 6) / len(rows)
rec6b = 100 * sum(1 for r in rows if r["mx60"] >= 5.5) / len(rows)  # ~recover entry+1.5 after -4 bail
worse7 = 100 * sum(1 for r in rows if r["mn60"] <= -7) / len(rows)
print("bails where token went +6%% above bail px within 60m: %.0f%% | fell another -7%%: %.0f%%" % (rec6, worse7))
print("=> bail-was-right rate (fell -7 more before any +6 recovery unknown ordering) approx via mn<=-7 and mx<6: %.0f%%" % (
    100 * sum(1 for r in rows if r["mn60"] <= -7 and r["mx60"] < 6) / len(rows)))
print("=> bail-was-wrong rate (recovered >=+6 and never fell -7 more): %.0f%%" % (
    100 * sum(1 for r in rows if r["mx60"] >= 6 and r["mn60"] > -7) / len(rows)))

# simulate: if instead of bailing at -4 we held with -12 hard floor and +6 TP (chronological)
def sim(rows_src):
    wins = loss = flat = 0
    pnl = 0.0
    for r in rows_src:
        # chronological approx not available from aggregates; conservative: if mn60<=-12 count loss first when both
        if r["mn60"] <= -12 and r["mx60"] >= 6:
            loss += 1
            pnl += -8  # -12 floor vs bail px = additional -8 vs -4 bail
        elif r["mx60"] >= 6:
            wins += 1
            pnl += 10  # +6 vs bail px ~= +10 vs the -4 bail counterfactual
        elif r["mn60"] <= -12:
            loss += 1
            pnl += -8
        else:
            flat += 1
            pnl += (r["mx60"] * 0.3)
    print("counterfactual(hold w/ -12 floor, +6 TP, worst-case ordering): wins=%d loss=%d flat=%d approx pp-delta vs bail=%+.0f" % (wins, loss, flat, pnl))
sim(rows)
