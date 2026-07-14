"""Standing daily wallet-BEHAVIOR decode, 2026-07-06.
Fresh window: ts >= 2026-07-05T12:00Z. Local tapes only, zero egress.
No price field in tapes -> price proxy = cumulative signed USD flow (constant-product
AMM: price is monotone in cumulative net flow). Wallet P&L = USD-out - USD-in per pair
(union-counted over ALL legs). Scrub rule: delta>0 AND hold<10s dropped.
"""
import json, glob, os
from datetime import datetime, timezone
from collections import defaultdict

TAPE_DIR = r"C:\Users\jcole\multichain-bot\scratchpad\ripday\live_tapes"
CUT = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc).timestamp()

def pct(v, p):
    v = sorted(v)
    return v[min(len(v)-1, int(p/100*(len(v)-1)))] if v else float("nan")

def q(v):
    return f"p25={pct(v,25):.1f} med={pct(v,50):.1f} p75={pct(v,75):.1f}"

# ---------- load ----------
pairs = {}          # pair -> list of (ts, kind, maker, usd)
sym_of = {}
dupes = 0
files_fresh = 0
for f in glob.glob(os.path.join(TAPE_DIR, "tape_*.jsonl")):
    recs, seen = [], set()
    for line in open(f, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = datetime.fromisoformat(d["ts"]).timestamp()
        if ts < CUT:
            continue
        key = (d["ts"], d["maker"], d["kind"], d["volume_usd"])
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        recs.append((ts, d["kind"], d["maker"], float(d["volume_usd"])))
        sym_of[d["pair"]] = d.get("sym", "?")
    if recs:
        p = os.path.basename(f)[5:-6]
        recs.sort(key=lambda r: r[0])
        pairs[p] = recs
        files_fresh += 1

ntr = sum(len(v) for v in pairs.values())
all_ts = [r[0] for v in pairs.values() for r in v]
print(f"fresh pairs={len(pairs)} trades={ntr} dupes_dropped={dupes}")
print(f"window: {datetime.fromtimestamp(min(all_ts),timezone.utc).isoformat()} .. "
      f"{datetime.fromtimestamp(max(all_ts),timezone.utc).isoformat()}")

# drop thin pairs (can't detect structure)
pairs = {p: v for p, v in pairs.items() if len(v) >= 200 and
         sum(r[3] for r in v) >= 5000}
print(f"analyzable pairs (>=200 trades, >=$5k gross): {len(pairs)}")

# ---------- price proxy + flush troughs per pair ----------
# cum netflow series; trough = local min where drawdown from prior peak >= D
# and rebound after >= 0.35*D, D = max($400, 1.5% of gross volume)
troughs = {}   # pair -> list of trough ts
for p, recs in pairs.items():
    gross = sum(r[3] for r in recs)
    D = max(400.0, 0.015 * gross)
    cum, series = 0.0, []
    for ts, kind, mk, usd in recs:
        cum += usd if kind == "buy" else -usd
        series.append((ts, cum))
    tr, peak, peak_i, low, low_i, in_dd = [], series[0][1], 0, None, None, False
    for i, (ts, c) in enumerate(series):
        if c > peak:
            peak, peak_i = c, i
            if in_dd:
                in_dd = False
            low, low_i = None, None
        if low is None or c < low:
            low, low_i = c, i
        # confirmed trough: dd >= D and current rebound off low >= 0.35*D
        if peak - low >= D and c - low >= 0.35 * D and low_i is not None:
            tr.append(series[low_i][0])
            # reset to hunt the next flush
            peak, peak_i = c, i
            low, low_i = c, i
    troughs[p] = sorted(set(tr))
npt = [len(v) for v in troughs.values()]
print(f"troughs: total={sum(npt)} pairs_with>=1={sum(1 for x in npt if x)} "
      f"per-pair {q([float(x) for x in npt])}")

# ---------- wallet-pair episodes (union-counted) ----------
episodes = []   # dicts
for p, recs in pairs.items():
    by_w = defaultdict(list)
    for r in recs:
        by_w[r[2]].append(r)
    for w, legs in by_w.items():
        buys = [(ts, usd) for ts, k, _, usd in legs if k == "buy"]
        sells = [(ts, usd) for ts, k, _, usd in legs if k == "sell"]
        bu, su = sum(u for _, u in buys), sum(u for _, u in sells)
        delta = su - bu
        hold = (max(t for t, _ in sells) - min(t for t, _ in buys)) if buys and sells else None
        episodes.append(dict(pair=p, w=w, nb=len(buys), ns=len(sells),
                             bu=bu, su=su, delta=delta, hold=hold,
                             buys=buys, sells=sells))

# round-trippers: >=1 buy and >=1 sell, sell volume covers >=50% of buy volume
rt = [e for e in episodes if e["nb"] >= 1 and e["ns"] >= 1 and e["su"] >= 0.5*e["bu"]]
# scrub rule
scrubbed = [e for e in rt if not (e["delta"] > 0 and e["hold"] is not None and e["hold"] < 10)]
nscrub = len(rt) - len(scrubbed)
win = [e for e in scrubbed if e["delta"] > 0]
los = [e for e in scrubbed if e["delta"] < 0]
uw = lambda es: len({e["w"] for e in es})
up = lambda es: len({e["pair"] for e in es})
print(f"\nepisodes={len(episodes)} round-trip={len(rt)} scrubbed_out={nscrub}")
print(f"winners: ep={len(win)} wallets={uw(win)} pairs={up(win)} | "
      f"losers: ep={len(los)} wallets={uw(los)} pairs={up(los)}")
print(f"winner delta$ {q([e['delta'] for e in win])} | loser {q([e['delta'] for e in los])}")

# ---------- Q1: buy timing vs trough ----------
def dt_to_trough(pair, ts):
    """signed secs from NEAREST trough within +/-1800s; + = after trough."""
    best = None
    for t in troughs.get(pair, []):
        d = ts - t
        if abs(d) <= 1800 and (best is None or abs(d) < abs(best)):
            best = d
    return best

def timing(es):
    out = []
    for e in es:
        for ts, usd in e["buys"]:
            d = dt_to_trough(e["pair"], ts)
            if d is not None:
                out.append((d, e["w"], e["pair"]))
    return out

tw, tl = timing(win), timing(los)
print("\n--- Q1 buy_ts - trough_ts (sec, nearest trough +/-30min) ---")
for name, tt in (("WIN", tw), ("LOS", tl)):
    v = [x[0] for x in tt]
    nw2, np2 = len({x[1] for x in tt}), len({x[2] for x in tt})
    if not v:
        print(name, "no data")
        continue
    pre = sum(1 for x in v if x < 0)/len(v)
    m1 = sum(1 for x in v if 0 <= x < 60)/len(v)
    m5 = sum(1 for x in v if 60 <= x < 300)/len(v)
    m30 = sum(1 for x in v if x >= 300)/len(v)
    print(f"{name}: n_buys={len(v)} wallets={nw2} pairs={np2} "
          f"p10={pct(v,10):.0f} p25={pct(v,25):.0f} med={pct(v,50):.0f} "
          f"p75={pct(v,75):.0f} p90={pct(v,90):.0f}")
    print(f"     before_low={pre:.0%} 0-60s={m1:.0%} 1-5min={m5:.0%} >5min={m30:.0%}")
# HL-confirm zone: buys 60-300s after trough — win share within that zone
zone_w = sum(1 for x in tw if 60 <= x[0] < 300)
zone_l = sum(1 for x in tl if 60 <= x[0] < 300)
first_w = sum(1 for x in tw if 0 <= x[0] < 60)
first_l = sum(1 for x in tl if 0 <= x[0] < 60)
pre_w = sum(1 for x in tw if x[0] < 0)
pre_l = sum(1 for x in tl if x[0] < 0)
def sh(a, b): return f"{a}/{a+b}={a/max(a+b,1):.0%}W"
print(f"winner share of buys: pre-low {sh(pre_w,pre_l)} | first-60s {sh(first_w,first_l)} | "
      f"60-300s {sh(zone_w,zone_l)}")

# ---------- Q2: buy size ----------
print("\n--- Q2 buy size at entry ($) ---")
for name, es in (("WIN", win), ("LOS", los)):
    per_ep_med = [pct([u for _, u in e["buys"]], 50) for e in es]
    first_buy = [e["buys"][0][1] for e in es if e["buys"]]
    tot = [e["bu"] for e in es]
    print(f"{name}: ep={len(es)} wallets={uw(es)} | per-ep median buy {q(per_ep_med)} | "
          f"first buy {q(first_buy)} | total deployed {q(tot)}")
# separator check at prior thresholds
big_w = sum(1 for e in win if pct([u for _,u in e['buys']],50) >= 300)
big_l = sum(1 for e in los if pct([u for _,u in e['buys']],50) >= 300)
sm_w = sum(1 for e in win if pct([u for _,u in e['buys']],50) < 300)
sm_l = sum(1 for e in los if pct([u for _,u in e['buys']],50) < 300)
print(f"medbuy>=300: WR={big_w/max(big_w+big_l,1):.0%} (n={big_w+big_l}) | "
      f"medbuy<300: WR={sm_w/max(sm_w+sm_l,1):.0%} (n={sm_w+sm_l})")

# ---------- Q3: exit shape of winners ----------
print("\n--- Q3 exit shape ---")
def shape(e):
    if e["ns"] == 1:
        return "single"
    first_frac = e["sells"][0][1] / e["su"]
    span = e["sells"][-1][0] - e["sells"][0][0]
    if span < 5:
        return "burst"      # multi-fill single decision
    return "peel" if first_frac <= 0.7 else "big-then-dust"
for name, es in (("WIN", win), ("LOS", los)):
    c = defaultdict(int)
    for e in es:
        c[shape(e)] += 1
    n = len(es)
    print(f"{name} (n={n}): " + " ".join(f"{k}={v}({v/n:.0%})" for k, v in
          sorted(c.items(), key=lambda x: -x[1])))
# among multi-sell winners: peel spacing + first-slice size
mw = [e for e in win if shape(e) == "peel"]
if mw:
    ff = [e["sells"][0][1]/e["su"]*100 for e in mw]
    sp = [(e["sells"][-1][0]-e["sells"][0][0])/60 for e in mw]
    print(f"winner peels: n={len(mw)} wallets={uw(mw)} first-slice% {q(ff)} "
          f"peel-span-min {q(sp)}")
# WR by shape
print("WR by shape:", end=" ")
for s in ("single", "peel", "burst", "big-then-dust"):
    a = sum(1 for e in win if shape(e) == s)
    b = sum(1 for e in los if shape(e) == s)
    if a+b >= 20:
        print(f"{s}={a/(a+b):.0%}(n={a+b})", end="  ")
print()

# ---------- Q4: exploratory — new families ----------
print("\n--- Q4 exploration ---")
feats = []
for e in scrubbed:
    nb, ns = e["nb"], e["ns"]
    scale_in = nb >= 3 and (e["buys"][-1][0]-e["buys"][0][0]) > 60
    dca_down = False
    if scale_in:
        # buys spaced across a falling proxy? approximate: later buys near/after a trough
        ds = [dt_to_trough(e["pair"], ts) for ts, _ in e["buys"]]
        ds = [d for d in ds if d is not None]
        dca_down = len(ds) >= 2 and any(d < 0 for d in ds) and any(d > 0 for d in ds)
    hold_m = e["hold"]/60 if e["hold"] is not None else None
    feats.append(dict(e=e, scale_in=scale_in, straddle=dca_down, hold_m=hold_m,
                      win=e["delta"] > 0))
def wr(sel):
    s = [f for f in feats if sel(f)]
    if not s: return "n=0"
    w = sum(1 for f in s if f["win"])
    return (f"WR={w/len(s):.0%} n={len(s)} wallets={len({f['e']['w'] for f in s})} "
            f"pairs={len({f['e']['pair'] for f in s})} "
            f"med_delta={pct([f['e']['delta'] for f in s],50):+.0f}$")
print("scale-in (>=3 buys over >60s):", wr(lambda f: f["scale_in"]))
print("single-buy:                   ", wr(lambda f: f["e"]["nb"] == 1))
print("straddle-the-low (buys both sides of a trough):", wr(lambda f: f["straddle"]))
for lo, hi, lab in ((0, 2, "hold<2m"), (2, 15, "2-15m"), (15, 60, "15-60m"),
                    (60, 240, "1-4h"), (240, 1e9, ">4h")):
    print(f"hold {lab}:", wr(lambda f, lo=lo, hi=hi: f["hold_m"] is not None and lo <= f["hold_m"] < hi))
# hour-of-day of winner first buys
hc = defaultdict(int)
for e in win:
    h = int(datetime.fromtimestamp(e["buys"][0][0], timezone.utc).hour) if e["buys"] else None
    if h is not None: hc[h] += 1
print("winner first-buy hour UTC:", " ".join(f"{h:02d}:{hc[h]}" for h in sorted(hc)))
# big-loser behavior contrast (what kills): top-quartile losers
bl = sorted(los, key=lambda e: e["delta"])[:max(len(los)//4, 1)]
print(f"worst-quartile losers (n={len(bl)}): med buys/ep={pct([float(e['nb']) for e in bl],50):.0f} "
      f"med total-in {q([e['bu'] for e in bl])} med hold-min "
      f"{q([e['hold']/60 for e in bl if e['hold'] is not None])}")
