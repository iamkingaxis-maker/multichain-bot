# Same-token head-to-head: on tokens BOTH we and winners traded, how do entries/exits differ?
import json, os, glob, bisect, statistics as st, collections, sys
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding="utf-8")

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger2_wallets.json")))
win = json.load(open(os.path.join(RIP, "winners_current.json")))
winners = set(win["winners"].keys())
realized_winners = {w for w, s in win["winners"].items() if s["realized"] > 0}
ours_tok = json.load(open(os.path.join(RIP, "our_token_outcomes.json")))
our_trades = [x for x in json.load(open(os.path.join(RIP, "our_trades_20260703.json"))) if x.get("time", "") >= "2026-07-01"]

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# bars
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    d = json.load(open(f))
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
pairs_all = set()
for w, eps in led.items():
    for e in eps:
        pairs_all.add(e["pair"])
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

def px_at(p, ep):
    ts = bar_ts.get(p)
    if not ts:
        return None
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0 or ep - ts[i] > 1800:
        return None
    return bars_by_pair[p][i][4]

# our trades per pair (full pair via prefix match)
our_by_pair = collections.defaultdict(list)
for x in our_trades:
    pa = x.get("pair_address") or ""
    full = p12m.get(pa[:12]) if pa else None
    if full:
        our_by_pair[full].append(x)

# head-to-head tokens: our pnl<=-20 (bleed) with winner episodes
rows = []
for p, tl in our_by_pair.items():
    w_eps = [(w, e) for w in winners for e in led.get(w, []) if e["pair"] == p and e["buy_usd"] >= 20]
    if not w_eps:
        continue
    our_sells = [x for x in tl if x["type"] == "sell"]
    if not our_sells:
        continue
    our_pnl = sum(x.get("pnl") or 0 for x in our_sells)
    sym = tl[0].get("token", "")
    # our first entry ts (from buys or sells' time - hold)
    our_buys = [x for x in tl if x["type"] == "buy"]
    our_entry_eps = [iso2ep(x["time"]) for x in our_buys] or [iso2ep(x["time"]) - (x.get("hold_secs") or 0) for x in our_sells]
    w_net = sum(e["net"] for _, e in w_eps)
    rows.append((p, sym, our_pnl, w_net, our_entry_eps, w_eps))

print("head-to-head tokens (we traded + winner episodes): n=%d" % len(rows))
print("%-12s %8s %8s %6s %6s  %s" % ("sym", "our_pnl", "win_net", "n_weps", "dt_med(m)", "win_entry_vs_our_entry_px_pct"))
h2h = []
for p, sym, opnl, wnet, our_eps, w_eps in sorted(rows, key=lambda r: r[2]):
    o0 = min(our_eps)
    dts, pxdel = [], []
    for w, e in w_eps:
        if not e["buy_ts"]:
            continue
        we0 = iso2ep(e["buy_ts"][0])
        dts.append((we0 - o0) / 60)
        p1, p2 = px_at(p, we0), px_at(p, o0)
        if p1 and p2:
            pxdel.append(100 * (p1 / p2 - 1))
    if dts:
        h2h.append({"sym": sym, "our_pnl": opnl, "w_net": wnet, "dt_med_m": st.median(dts), "px_delta_med": st.median(pxdel) if pxdel else None})
        print("%-12s %8.1f %8.1f %6d %8.0f  %s" % (sym[:12], opnl, wnet, len(w_eps), st.median(dts),
              ("%.1f%%" % st.median(pxdel)) if pxdel else "?"))

dts_all = [r["dt_med_m"] for r in h2h]
pxs = [r["px_delta_med"] for r in h2h if r["px_delta_med"] is not None]
print("\nacross tokens: winner-first-buy minus OUR-first-buy: median %.0f min (p25 %.0f, p75 %.0f); winner entry px vs our entry px: median %.1f%%" % (
    st.median(dts_all), sorted(dts_all)[len(dts_all)//4], sorted(dts_all)[3*len(dts_all)//4], st.median(pxs) if pxs else float("nan")))

# ---- PEACE + RUSH case studies ----
for want in ["PEACE", "RUSH", "popeyes"]:
    cand = [p for p, s, *_ in rows if s == want]
    if not cand:
        continue
    p = cand[0]
    print("\n===== CASE %s (%s) =====" % (want, p[:12]))
    tl = our_by_pair[p]
    o_s = [x for x in tl if x["type"] == "sell"]
    seen = set()
    print("-- OUR trades (deduped) --")
    for x in sorted(o_s, key=lambda x: x["time"]):
        k = (round(x.get("entry_price") or 0, 12), round(x.get("exit_price") or 0, 12))
        if k in seen:
            continue
        seen.add(k)
        print("  sell %s pnl%%=%6.1f hold=%5.0fs reason=%s" % (x["time"][5:19], x.get("pnl_pct") or 0, x.get("hold_secs") or 0, (x.get("reason") or "")[:28]))
    print("-- WINNER episodes --")
    for w, e in [(w, e) for w in winners for e in led.get(w, []) if e["pair"] == p and e["buy_usd"] >= 20]:
        b0 = e["buy_ts"][0] if e["buy_ts"] else "?"
        slast = e["sell_ts"][-1] if e["sell_ts"] else "?"
        hold = (iso2ep(slast) - iso2ep(b0)) / 60 if (e["buy_ts"] and e["sell_ts"]) else None
        print("  %s.. buy=%7.1f nb=%d ns=%d first_buy=%s last_sell=%s hold=%s net=%7.1f" % (
            w[:8], e["buy_usd"], e["n_buys"], e["n_sells"], b0[5:19] if b0 != "?" else "?",
            slast[5:19] if slast != "?" else "?", ("%.0fm" % hold) if hold is not None else "open", e["net"]))
    # price path summary around our entries
    bl = bars_by_pair.get(p)
    if bl:
        print("-- price path (hourly closes) --")
        step = max(1, len(bl) // 24)
        line = []
        for i in range(0, len(bl), step):
            b = bl[i]
            line.append("%s:%0.2e" % (datetime.fromtimestamp(b[0], tz=timezone.utc).strftime("%d%H"), b[4]))
        print("  " + " ".join(line))

# ---- realized-winner per-episode hold + result (clean set) ----
print("\n--- realized winners: episode economics ---")
hh, nets = [], []
for w in realized_winners:
    for e in led.get(w, []):
        if e["buy_usd"] < 20:
            continue
        if e["buy_ts"] and e["sell_ts"]:
            s_after = [s for s in e["sell_ts"] if s >= e["buy_ts"][0]]
            if s_after:
                hh.append((iso2ep(s_after[-1]) - iso2ep(e["buy_ts"][0])) / 60)
        nets.append(e["net"] / e["buy_usd"] * 100 if e["buy_usd"] else 0)
hh.sort(); nets.sort()
q = lambda v, f: v[min(len(v) - 1, int(f * len(v)))]
print("hold_to_last_sell_m: n=%d p25=%.0f med=%.0f p75=%.0f p90=%.0f" % (len(hh), q(hh, .25), st.median(hh), q(hh, .75), q(hh, .9)))
print("episode net%%: n=%d p10=%.0f p25=%.0f med=%.0f p75=%.0f p90=%.0f mean=%.0f | >0: %.0f%%" % (
    len(nets), q(nets, .1), q(nets, .25), st.median(nets), q(nets, .75), q(nets, .9), st.mean(nets),
    100 * sum(1 for x in nets if x > 0) / len(nets)))
