# Quantify the 3 candidate deltas + young-lane corroboration
import json, os, glob, bisect, statistics as st, collections, sys
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding="utf-8")

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger2_wallets.json")))
win = json.load(open(os.path.join(RIP, "winners_current.json")))
winners = set(win["winners"].keys())
realized_winners = {w for w, s in win["winners"].items() if s["realized"] > 0}

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# ============ 1) OUR velocity-bail churn ============
our = [x for x in json.load(open(os.path.join(RIP, "our_trades_20260703.json"))) if x.get("time", "") >= "2026-07-01"]
sells = [x for x in our if x["type"] == "sell"]
dedup = {}
for s in sells:
    k = (s["address"], round(s.get("entry_price") or 0, 12), round(s.get("exit_price") or 0, 12), round(s.get("hold_secs") or 0))
    dedup.setdefault(k, s)
uniq = list(dedup.values())
vb = [s for s in uniq if "velocity-bail" in (s.get("reason") or "")]
fast = [s for s in uniq if (s.get("hold_secs") or 0) <= 30]
print("=== OUR CHURN (unique exits since 07-01, n=%d) ===" % len(uniq))
print("velocity-bail: n=%d (%.0f%%), mean pnl%%=%.2f, sum pnl=$%.0f" % (
    len(vb), 100 * len(vb) / len(uniq), st.mean(s.get("pnl_pct") or 0 for s in vb), sum(s.get("pnl") or 0 for s in vb)))
print("hold<=30s exits: n=%d (%.0f%%), mean pnl%%=%.2f, sum pnl=$%.0f" % (
    len(fast), 100 * len(fast) / len(uniq), st.mean(s.get("pnl_pct") or 0 for s in fast), sum(s.get("pnl") or 0 for s in fast)))
neg_sum = sum(s.get("pnl") or 0 for s in uniq if (s.get("pnl") or 0) < 0)
print("total negative pnl: $%.0f ; velocity-bail share of it: %.0f%%" % (neg_sum, 100 * sum(s.get("pnl") or 0 for s in vb if (s.get("pnl") or 0) < 0) / neg_sum))

# our re-entry structure per token: entries per token per bot vs winners' campaign
per_tok_n = collections.Counter()
for s in uniq:
    per_tok_n[s["address"]] += 1

# ============ 2) winner campaign structure ============
def campaign(pool, label):
    spans, nbs, re_after_sell = [], [], 0
    tot_eps = 0
    for w in pool:
        for e in led.get(w, []):
            if e["buy_usd"] < 20 or not e["buy_ts"]:
                continue
            tot_eps += 1
            nbs.append(e["n_buys"])
            b = sorted(e["buy_ts"])
            spans.append((iso2ep(b[-1]) - iso2ep(b[0])) / 60)
            if e["sell_ts"]:
                s0 = min(e["sell_ts"])
                if any(x > s0 for x in e["buy_ts"]):
                    re_after_sell += 1
    spans.sort()
    q = lambda v, f: v[min(len(v) - 1, int(f * len(v)))] if v else float("nan")
    print("%s: eps=%d buys/ep med=%d mean=%.1f | buy-span(first->last buy) med=%.0fm p75=%.0fm p90=%.0fm | re-buys after a sell: %.0f%% of eps" % (
        label, tot_eps, st.median(nbs), st.mean(nbs), st.median(spans), q(spans, .75), q(spans, .9), 100 * re_after_sell / tot_eps))

print("\n=== CAMPAIGN STRUCTURE ===")
campaign(winners, "WINNERS ")
campaign(realized_winners, "REALIZED")
print("OUR: entries per token med=%d mean=%.1f (unique exits per token; each entry a fresh fixed-size slot, no averaging)" % (
    st.median(per_tok_n.values()), st.mean(per_tok_n.values())))

# ============ 3) winner convergence waves ============
# for each winner buy: how many OTHER winner wallets bought same pair within +-15m
wbuys = collections.defaultdict(list)  # pair -> [(ep, wallet)]
for w in winners:
    for e in led.get(w, []):
        if e["buy_usd"] < 20:
            continue
        for bts in e["buy_ts"]:
            wbuys[e["pair"]].append((iso2ep(bts), w))
conv = []
for p, lst in wbuys.items():
    lst.sort()
    eps_ = [x[0] for x in lst]
    for ep, w in lst:
        lo = bisect.bisect_left(eps_, ep - 900)
        hi = bisect.bisect_right(eps_, ep + 900)
        others = len(set(x[1] for x in lst[lo:hi]) - {w})
        conv.append(others)
print("\n=== CONVERGENCE: distinct OTHER winner wallets buying same pair within ±15m of a winner buy ===")
print("n=%d med=%d mean=%.1f | >=2 others: %.0f%% | >=3: %.0f%%" % (
    len(conv), st.median(conv), st.mean(conv), 100 * sum(1 for c in conv if c >= 2) / len(conv), 100 * sum(1 for c in conv if c >= 3) / len(conv)))

# ============ 4) hour-of-day full histogram ============
hrs_w = collections.Counter()
usd_w = collections.Counter()
for w in winners:
    for e in led.get(w, []):
        if e["buy_usd"] < 20:
            continue
        for bts in e["buy_ts"]:
            h = int(bts[11:13])
            hrs_w[h] += 1
tot = sum(hrs_w.values())
print("\n=== WINNER BUY HOURS UTC (n=%d) ===" % tot)
print(" ".join("%02d:%4.1f%%" % (h, 100 * hrs_w.get(h, 0) / tot) for h in range(24)))
dead = sum(hrs_w.get(h, 0) for h in range(4, 14))
print("share in UTC 04-13 (our observed dead zone): %.1f%%" % (100 * dead / tot))

# ============ 5) young-lane check: winner episodes by token age ============
ages = {}
try:
    tm = json.load(open(os.path.join(RIP, "token_meta.json")))
    for p, v in tm.items():
        if v.get("pool_created_at"):
            ages[p] = datetime.fromisoformat(v["pool_created_at"].replace("Z", "+00:00")).timestamp()
except Exception:
    pass
for f in ["_pair_created_cache.json", "_pair_created_cache2.json"]:
    try:
        for p, v in json.load(open(os.path.join(RIP, f))).items():
            if v:
                ages.setdefault(p, float(v))
    except Exception:
        pass

bands = {"<2h": (0, 2), "2-6h": (2, 6), "6-24h": (6, 24), "24-72h": (24, 72), ">72h": (72, 1e9)}
def age_econ(pool, label):
    rows = collections.defaultdict(lambda: [0, 0.0, 0.0])  # band -> [n, net, buy]
    for w in pool:
        for e in led.get(w, []):
            if e["buy_usd"] < 20 or not e["first_buy"]:
                continue
            a = ages.get(e["pair"])
            if not a:
                continue
            h = (iso2ep(e["first_buy"]) - a) / 3600
            for b, (lo, hi) in bands.items():
                if lo <= h < hi:
                    rows[b][0] += 1
                    rows[b][1] += e["net"]
                    rows[b][2] += e["buy_usd"]
    print(label)
    for b in bands:
        n, net, bu = rows[b]
        if n:
            print("  %-7s eps=%3d  net=$%8.1f  ret_on_buy=%+.1f%%" % (b, n, net, 100 * net / bu if bu else 0))

print("\n=== EPISODE ECONOMICS BY TOKEN AGE AT FIRST BUY ===")
age_econ(winners, "WINNERS:")
# base multi humans
base_wallets = []
for w, eps in led.items():
    if w in winners:
        continue
    n = sum(1 for e in eps if e["buy_usd"] >= 20)
    tot_t = sum(e["n_buys"] + e["n_sells"] for e in eps)
    if n >= 3 and len(eps) < 25 and tot_t < 400:
        base_wallets.append(w)
age_econ(base_wallets, "BASE MULTI:")
