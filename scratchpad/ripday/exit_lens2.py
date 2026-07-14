# exit_lens2.py -- harden exit/size decode: trust flags, sell-into-strength, PnL-weighted holds
import json, glob, os
import statistics as st
from datetime import datetime

RD = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(RD, "exit_lens_rows.json"), encoding="utf-8"))

# token peak (recorder, pct vs event) for plausibility cross-check
peaks = {}
for fn in ("rip_runners_live.json", "rip_runners.json", "recorder_runners.json"):
    p = os.path.join(RD, fn)
    if os.path.exists(p):
        for mint, d in json.load(open(p, encoding="utf-8")).items():
            if mint not in peaks and isinstance(d, dict) and d.get("peak") is not None:
                peaks[mint] = d["peak"]

# tape span per token for coverage flag
idx = json.load(open(os.path.join(RD, "tape_index.json"), encoding="utf-8"))
tok_span = {}
for pair, d in idx.items():
    tok_span[d["token"]] = (d["oldest"], d["newest"], d["n_trades"])

TIER_A = ["kEFiAX3jo5NmemysQov342TZ9mGh6yp92GDRjhA8XDf", "J1sfMsbxGNXDPMUPXyGs5D6oCEe7fSYgdPMRyVzZuZUW"]
TIER_B = ["DJocqRPK2uKWvmR5WnWcd7m8fDw6az1L54R4UuH3GrGN", "7JCe3GHwkEr3feHgtLXnmuJ1yB3A7coSeyynxTBgdG8k",
          "DF8tRgFkt1JSuqqtVmG2maiEY92mfFWBHNpMeRBK4fEo", "4MB2yiq54PHkJ11YPoZGYgVzew9zFRRms41PAFoXaevg",
          "CAP9q6SmwGufYWTKupGA5uGzkjqirkRRuz6YEGhumjyi", "8P1msjLVVaZdwtHke9Ly9GSkQzDtwEjAr9UDyTiJBJuP"]
TIER_C = ["AgmLJBMDCqWynYnQiPCuj9ewsNNsBJXyzoUhD9LJzN51", "FYX5JQ2kP7TD8gWb9WP1tjmwWWUAzi8edEZTr5Z8F1ck",
          "8zkgFGVZrDLieViwqiXFCydSX6WL5hsxmUu55yBdsNsZ", "2tgUbS9UMoQD6GkDZBiqKYCURnGrSb6ocYwRABrSJUvY"]

def tier_of(w):
    return "A" if w in TIER_A else ("B" if w in TIER_B else "C")

# ---- trust flag on tail winners: cash ROI must be <= token full-run peak (recorder pct) * 1.5 ----
print("=== TAIL WINNERS (cash ROI > 25%, closed) -- plausibility vs token peak ===")
print(f"{'T':1} {'wallet':>12} {'sym':>10} {'cashROI%':>9} {'tokPeak%':>9} {'plaus':>6} {'hold1':>6} {'holdL':>6} {'nS':>3} {'buy$':>6}")
tails = [r for r in rows if not r["open_bag"] and r["roi_cash"] is not None and r["roi_cash"] > 25]
for r in sorted(tails, key=lambda r: -r["roi_cash"]):
    pk = peaks.get(r["tok"])
    plaus = "?" if pk is None else ("OK" if r["roi_cash"] <= pk * 1.5 else "INFL")
    sym = "".join(ch if ord(ch) < 128 else "?" for ch in r["sym"])[:10]
    print(f"{tier_of(r['w'])} {r['w'][:12]:>12} {sym:>10} {r['roi_cash']:9.1f} "
          f"{(f'{pk:9.1f}' if pk is not None else '       --')} {plaus:>6} "
          f"{(f'{r([])}' if False else f'{r['h_first']:6.0f}' if r['h_first'] is not None else '    --')} "
          f"{(f'{r['h_last']:6.0f}' if r['h_last'] is not None else '    --')} {r['n_sells']:3d} {r['buy_usd']:6.0f}")

# ---- PnL-weighted hold (closed positions, plausible only) ----
print()
print("=== PnL-WEIGHTED HOLD (tier A+B closed, cash) ===")
sel = [r for r in rows if tier_of(r["w"]) in "AB" and not r["open_bag"] and r["h_last"] is not None]
pos = [r for r in sel if (r["sell_usd"] - r["buy_usd"]) > 0]
tot_pnl = sum(r["sell_usd"] - r["buy_usd"] for r in pos)
wh = sum((r["sell_usd"] - r["buy_usd"]) * r["h_last"] for r in pos) / tot_pnl if tot_pnl else None
wh1 = sum((r["sell_usd"] - r["buy_usd"]) * r["h_first"] for r in pos) / tot_pnl if tot_pnl else None
print(f"winning closed positions n={len(pos)} total gross +${tot_pnl:.0f}")
print(f"PnL-weighted hold to LAST sell = {wh:.0f} min | to FIRST sell = {wh1:.0f} min")
print(f"unweighted median hold last = {st.median([r['h_last'] for r in pos]):.0f} min")
frac_60 = sum((r["sell_usd"] - r["buy_usd"]) for r in pos if r["h_last"] >= 60) / tot_pnl
frac_120 = sum((r["sell_usd"] - r["buy_usd"]) for r in pos if r["h_last"] >= 120) / tot_pnl
print(f"share of winner $$ realized on holds >=60m: {frac_60*100:.0f}%  >=120m: {frac_120*100:.0f}%")

# losses side: how fast do A+B wallets cut losers?
neg = [r for r in sel if (r["sell_usd"] - r["buy_usd"]) <= 0]
if neg:
    print(f"losing closed positions n={len(neg)} med hold last={st.median([r['h_last'] for r in neg]):.0f}m "
          f"med ROI={st.median([r['roi_cash'] for r in neg]):.1f}%")

# open bags = the hidden loss channel
print()
print("=== OPEN BAGS (n_sells=0) per tier ===")
for t in "ABC":
    ob = [r for r in rows if tier_of(r["w"]) == t and r["open_bag"]]
    cl = [r for r in rows if tier_of(r["w"]) == t and not r["open_bag"]]
    print(f"tier {t}: open bags n={len(ob)} buy$={sum(r['buy_usd'] for r in ob):7.0f} "
          f"| closed n={len(cl)} buy$={sum(r['buy_usd'] for r in cl):7.0f} "
          f"net closed=${sum(r['sell_usd']-r['buy_usd'] for r in cl):7.0f}")

# ---- sell-into-strength: sells with OHLC coverage, price +10m after sell vs sell px ----
ohlc = {}
for f in glob.glob(os.path.join(RD, "ohlc_*.json")):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if d.get("bars"):
        ohlc[d["token"]] = d["bars"]

def px_at(mint, ep, tol=180):
    bars = ohlc.get(mint)
    if not bars:
        return None
    best = None
    for b in bars:
        if b[0] <= ep:
            best = b
        else:
            break
    if best is None or ep - best[0] > tol:
        return None
    return best[4]

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# reload raw trades for A+B wallets to inspect individual sells
TARGETS = set(TIER_A + TIER_B + TIER_C)
sells = []
buys_by = {}
for f in glob.glob(os.path.join(RD, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        t = json.loads(line)
        if t["maker"] not in TARGETS:
            continue
        ep = iso2ep(t["ts"])
        if t["kind"] == "sell":
            sells.append((t["maker"], t["token"], ep, t["volume_usd"]))
        else:
            buys_by.setdefault((t["maker"], t["token"]), []).append(ep)

print()
print("=== SELL-INTO-STRENGTH (sells w/ OHLC: px +10m after sell vs sell px) ===")
for t in ("AB", "C"):
    up = dn = 0
    deltas = []
    pre = []
    for w, tok, ep, usd in sells:
        if tier_of(w) not in t:
            continue
        fb = min(buys_by.get((w, tok), [9e18]))
        if ep < fb:
            continue  # uncovered sell
        p0 = px_at(tok, ep)
        p1 = px_at(tok, ep + 600, tol=240)
        pm = px_at(tok, ep - 600, tol=240)
        if p0 and p1:
            d = (p1 / p0 - 1) * 100
            deltas.append(d)
            up += d > 0
            dn += d <= 0
        if p0 and pm:
            pre.append((p0 / pm - 1) * 100)
    if deltas:
        print(f"tier {t}: n_sells_covered={len(deltas)} | px kept RISING after sell: {up} ({up/(up+dn)*100:.0f}%) "
              f"| med px move +10m after sell = {st.median(deltas):+.1f}% | med px move 10m BEFORE sell = {st.median(pre):+.1f}% (n={len(pre)})")
    else:
        print(f"tier {t}: n=0")

# ---- our-exit counterfactual, cash arithmetic on A+B closed winners ----
print()
print("=== OUR EXIT vs THEIRS (cash arithmetic, A+B closed winners, plausible ROIs only) ===")
print("ourA = tp1 +6% sell 75% + remainder at their blended exit (optimistic for us)")
print("ourB = tp1 +13% sell 30% + remainder at their blended exit")
tot_them = tot_a = tot_b = 0.0
n = 0
for r in pos:
    roi = r["roi_cash"] / 100
    pk = peaks.get(r["tok"])
    if roi > 0.25 and pk is not None and r["roi_cash"] > pk * 1.5:
        continue  # skip inflated
    if roi <= 0:
        continue
    n += 1
    them = r["buy_usd"] * roi
    # if their blended ROI >= 6%, the path passed +6%; our tp1 books 75% at +6
    if roi >= 0.06:
        oa = r["buy_usd"] * (0.75 * 0.06 + 0.25 * roi)
    else:
        oa = them
    if roi >= 0.13:
        ob_ = r["buy_usd"] * (0.30 * 0.13 + 0.70 * roi)
    else:
        ob_ = them
    tot_them += them; tot_a += oa; tot_b += ob_
print(f"n={n} closed plausible winners | THEM gross=+${tot_them:.0f} | ourA=+${tot_a:.0f} ({tot_a/tot_them*100:.0f}%) "
      f"| ourB=+${tot_b:.0f} ({tot_b/tot_them*100:.0f}%)")
print("NOTE: remainder priced at THEIR blended exit = optimistic; a 10%-trail would usually stop the remainder earlier.")
