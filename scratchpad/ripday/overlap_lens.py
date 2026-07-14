# Q2e/Q3: winner activity on OUR tokens (esp. bleed tokens); realized-winner subset behavior;
# winner sell context (into strength vs weakness); age bimodality; hour-of-day
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger2_wallets.json")))
win = json.load(open(os.path.join(RIP, "winners_current.json")))
winners = set(win["winners"].keys())
ours = json.load(open(os.path.join(RIP, "our_token_outcomes.json")))

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# realized-positive winner subset
realized_winners = {w for w, s in win["winners"].items() if s["realized"] > 0}
print("winners:", len(winners), "| realized>0 subset:", len(realized_winners), sorted(realized_winners)[:20])

# ---- bars ----
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    d = json.load(open(f))
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
pairs_all = set()
for w, eps in led.items():
    for e in eps:
        pairs_all.add(e["pair"])
p12 = {p[:12]: p for p in pairs_all}
for f in glob.glob(os.path.join(RIP, "_gt_bars", "*.json")):
    p = p12.get(os.path.basename(f).split(".")[0])
    if not p:
        continue
    b = json.load(open(f))
    if isinstance(b, list) and b:
        bars_by_pair.setdefault(p, []).extend(b)
for p in bars_by_pair:
    u = {int(x[0]): x for x in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda x: x[0])
bar_ts = {p: [b[0] for b in bars_by_pair[p]] for p in bars_by_pair}

def px_at(p, ep):
    ts = bar_ts.get(p)
    if not ts:
        return None
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0 or ep - ts[i] > 1800:
        return None
    return bars_by_pair[p][i][4]

def ctx60(p, ep):
    ts = bar_ts.get(p)
    if not ts:
        return None
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0 or ep - ts[i] > 1800:
        return None
    j0 = bisect.bisect_left(ts, ep - 3600)
    prior = bars_by_pair[p][j0:i + 1]
    px = bars_by_pair[p][i][4]
    hi = max(b[2] for b in prior)
    lo = min(b[3] for b in prior)
    return px, hi, lo

# ---- our pairs mapping (pair12 from our_token_outcomes) ----
our_pairs = {}   # full pair -> our outcome rec
for a, t in ours.items():
    if t.get("pair"):
        our_pairs[t["pair"]] = t
our_full = {}
for p in pairs_all:
    if p in our_pairs:
        our_full[p] = our_pairs[p]
print("our tokens with tape coverage: %d / %d" % (len(our_full), len(our_pairs)))

bleed = {p: t for p, t in our_full.items() if t["pnl"] <= -20}
green = {p: t for p, t in our_full.items() if t["pnl"] >= 10}
print("bleed tokens covered:", len(bleed), "| green tokens covered:", len(green))

# winner buy USD share on our bleed vs green vs rest
def wallet_side(pool):
    stats = collections.Counter()
    for w in pool:
        for e in led.get(w, []):
            if e["buy_usd"] < 20:
                continue
            p = e["pair"]
            cat = "bleed" if p in bleed else ("our_green" if p in green else ("our_flat" if p in our_full else "not_ours"))
            stats[cat + "_usd"] += e["buy_usd"]
            stats[cat + "_eps"] += 1
    return stats

ws = wallet_side(winners)
# baseline = multi-pair non-winner humans
base_wallets = []
for w, eps in led.items():
    if w in winners:
        continue
    n = sum(1 for e in eps if e["buy_usd"] >= 20)
    tot = sum(e["n_buys"] + e["n_sells"] for e in eps)
    if n >= 3 and len(eps) < 25 and tot < 400:
        base_wallets.append(w)
bs = wallet_side(base_wallets)
print("\nbuy-USD allocation (share of wallet-pool buy USD):")
for pool, s in [("WINNERS", ws), ("BASE_MULTI", bs)]:
    tot = sum(v for k, v in s.items() if k.endswith("_usd"))
    print(pool, {k: "%.1f%%" % (100 * v / tot) for k, v in s.items() if k.endswith("_usd")}, "tot_usd=%d" % tot)

# per bleed token: winner participation
print("\n--- our bleed tokens: winner buy activity ---")
for p, t in sorted(bleed.items(), key=lambda kv: kv[1]["pnl"]):
    wb = sum(e["buy_usd"] for w in winners for e in led.get(w, []) if e["pair"] == p and e["buy_usd"] >= 20)
    wn = sum(1 for w in winners for e in led.get(w, []) if e["pair"] == p and e["buy_usd"] >= 20)
    net = sum(e["net"] for w in winners for e in led.get(w, []) if e["pair"] == p and e["buy_usd"] >= 20)
    print("%-10s our_pnl=%7.1f  winner_eps=%2d winner_buy_usd=%7.0f winner_net=%8.1f" % (t["sym"][:10], t["pnl"], wn, wb, net))

# ---- winner SELL context: selling into strength? ----
def sell_ctx(pool, label):
    rows = []
    for w in pool:
        for e in led.get(w, []):
            if e["buy_usd"] < 20 or not e["sell_ts"] or not e["buy_ts"]:
                continue
            p = e["pair"]
            b0 = iso2ep(e["buy_ts"][0])
            pxb = px_at(p, b0)
            for stx in e["sell_ts"]:
                if stx < e["buy_ts"][0]:
                    continue
                ep = iso2ep(stx)
                c = ctx60(p, ep)
                if not c or not pxb:
                    continue
                px, hi, lo = c
                rows.append({"vs_entry": 100 * (px / pxb - 1),
                             "pos_range": (px - lo) / (hi - lo) if hi > lo else 0.5})
    if rows:
        ve = sorted(r["vs_entry"] for r in rows)
        pr = sorted(r["pos_range"] for r in rows)
        q = lambda v, f: v[min(len(v) - 1, int(f * len(v)))]
        print("%s sells n=%d | px vs first-entry: p25=%.1f med=%.1f p75=%.1f | pos in 60m range: p25=%.2f med=%.2f p75=%.2f | sold_above_entry=%.0f%% | sold_in_top_third=%.0f%%" % (
            label, len(rows), q(ve, .25), st.median(ve), q(ve, .75), q(pr, .25), st.median(pr), q(pr, .75),
            100 * sum(1 for x in ve if x > 0) / len(ve), 100 * sum(1 for x in pr if x > .67) / len(pr)))

print()
sell_ctx(winners, "WINNERS   ")
sell_ctx(realized_winners, "REALIZED_W")
import random
random.seed(3)
sell_ctx(random.sample(base_wallets, min(600, len(base_wallets))), "BASE_MULTI")

# ---- age bimodality + hour of day for winner buys ----
beh = json.load(open(os.path.join(RIP, "behavior_buys.json")))
for label, rows in [("WIN", beh["win_buys"]), ("BASE", beh["base_buys"])]:
    ages = [r["age_h"] for r in rows if r.get("age_h") is not None]
    if ages:
        y = sum(1 for a in ages if a < 2)
        y6 = sum(1 for a in ages if a < 6)
        y24 = sum(1 for a in ages if a < 24)
        print("%s buys: age<2h %.0f%% | <6h %.0f%% | <24h %.0f%% | n=%d" % (label, 100 * y / len(ages), 100 * y6 / len(ages), 100 * y24 / len(ages), len(ages)))
    hrs = collections.Counter(int(r["ts"][11:13]) for r in rows)
    tot = sum(hrs.values())
    print("%s buy hours (UTC):" % label, {h: "%.0f%%" % (100 * c / tot) for h, c in sorted(hrs.items()) if c / tot >= 0.05})

# ---- sanity: top realized winner detail ----
print("\n--- top realized winners detail ---")
det = sorted([(w, win["winners"][w]) for w in realized_winners], key=lambda kv: -kv[1]["realized"])
for w, s in det[:8]:
    print("\n%s realized=%.1f net=%.1f pairs=%d" % (w, s["realized"], s["net"], s["n_pairs"]))
    for e in sorted(led[w], key=lambda e: -abs(e["net"])):
        if e["buy_usd"] < 20:
            continue
        print("   %-10s buy=%7.1f sellA=%7.1f real=%7.1f unre=%7.1f net=%7.1f nb=%d ns=%d cap=%s first=%s" % (
            e["sym"][:10], e["buy_usd"], e["sell_usd_after"], e["realized"], e["unreal"], e["net"],
            e["n_buys"], e["n_sells"], e["capped_preinv"], (e["first_buy"] or "")[5:16]))
