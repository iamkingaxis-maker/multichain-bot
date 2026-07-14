"""Refinement pass: finer trough-timing buckets, wallet-level whale separator,
combo family candidates. Same data/method as _decode_0706.py."""
import json, glob, os
from datetime import datetime, timezone
from collections import defaultdict

TAPE_DIR = r"C:\Users\jcole\multichain-bot\scratchpad\ripday\live_tapes"
CUT = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc).timestamp()

def pct(v, p):
    v = sorted(v)
    return v[min(len(v)-1, int(p/100*(len(v)-1)))] if v else float("nan")

pairs = {}
for f in glob.glob(os.path.join(TAPE_DIR, "tape_*.jsonl")):
    recs, seen = [], set()
    for line in open(f, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = datetime.fromisoformat(d["ts"]).timestamp()
        if ts < CUT:
            continue
        key = (d["ts"], d["maker"], d["kind"], d["volume_usd"])
        if key in seen:
            continue
        seen.add(key)
        recs.append((ts, d["kind"], d["maker"], float(d["volume_usd"])))
    if recs:
        recs.sort(key=lambda r: r[0])
        pairs[os.path.basename(f)[5:-6]] = recs
pairs = {p: v for p, v in pairs.items() if len(v) >= 200 and sum(r[3] for r in v) >= 5000}

troughs = {}
for p, recs in pairs.items():
    gross = sum(r[3] for r in recs)
    D = max(400.0, 0.015 * gross)
    cum, series = 0.0, []
    for ts, kind, mk, usd in recs:
        cum += usd if kind == "buy" else -usd
        series.append((ts, cum))
    tr, peak, low, low_i = [], series[0][1], None, None
    for i, (ts, c) in enumerate(series):
        if c > peak:
            peak = c
            low, low_i = None, None
        if low is None or c < low:
            low, low_i = c, i
        if peak - low >= D and c - low >= 0.35 * D and low_i is not None:
            tr.append(series[low_i][0])
            peak, low, low_i = c, c, i
    troughs[p] = sorted(set(tr))

episodes = []
for p, recs in pairs.items():
    by_w = defaultdict(list)
    for r in recs:
        by_w[r[2]].append(r)
    for w, legs in by_w.items():
        buys = [(ts, usd) for ts, k, _, usd in legs if k == "buy"]
        sells = [(ts, usd) for ts, k, _, usd in legs if k == "sell"]
        bu, su = sum(u for _, u in buys), sum(u for _, u in sells)
        hold = (max(t for t, _ in sells) - min(t for t, _ in buys)) if buys and sells else None
        episodes.append(dict(pair=p, w=w, nb=len(buys), ns=len(sells), bu=bu, su=su,
                             delta=su - bu, hold=hold, buys=buys, sells=sells))

rt = [e for e in episodes if e["nb"] >= 1 and e["ns"] >= 1 and e["su"] >= 0.5*e["bu"]]
sc = [e for e in rt if not (e["delta"] > 0 and e["hold"] is not None and e["hold"] < 10)]
win = [e for e in sc if e["delta"] > 0]
los = [e for e in sc if e["delta"] < 0]
base = len(win)/ (len(win)+len(los))
print(f"base episode WR={base:.1%} (win={len(win)} los={len(los)})")

def dt_to_trough(pair, ts):
    best = None
    for t in troughs.get(pair, []):
        d = ts - t
        if abs(d) <= 1800 and (best is None or abs(d) < abs(best)):
            best = d
    return best

# ---- fine timing buckets, count + USD-weighted ----
buck = [(-1800,-300,"-30..-5m"),(-300,-60,"-5..-1m"),(-60,0,"-60..0s"),
        (0,60,"0..60s"),(60,120,"60..120s"),(120,300,"2..5m"),(300,600,"5..10m"),(600,1800,"10..30m")]
cnt = {b[2]: [0,0,0.0,0.0] for b in buck}   # winN, losN, winUSD, losUSD
wall = {b[2]: [set(), set()] for b in buck}
for es, i in ((win,0),(los,1)):
    for e in es:
        for ts, usd in e["buys"]:
            d = dt_to_trough(e["pair"], ts)
            if d is None:
                continue
            for lo, hi, lab in buck:
                if lo <= d < hi:
                    cnt[lab][i] += 1
                    cnt[lab][2+i] += usd
                    wall[lab][i].add(e["w"])
                    break
print("\nfine timing buckets (buys near a flush trough):")
print(f"{'bucket':<10}{'nW':>6}{'nL':>6}{'WR':>6}{'usdWR':>7}{'walletsW':>9}{'walletsL':>9}")
for lo, hi, lab in buck:
    nw, nl, uw_, ul = cnt[lab]
    if nw+nl < 30:
        continue
    print(f"{lab:<10}{nw:>6}{nl:>6}{nw/(nw+nl):>6.0%}{uw_/(uw_+ul):>7.0%}"
          f"{len(wall[lab][0]):>9}{len(wall[lab][1]):>9}")

# ---- wallet-level whale separator (across pairs, closer to prior method) ----
by_wallet = defaultdict(list)
for e in sc:
    by_wallet[e["w"]].append(e)
wlv = []
for w, es in by_wallet.items():
    tot = sum(e["delta"] for e in es)
    allbuys = [u for e in es for _, u in e["buys"]]
    if len(allbuys) >= 3:
        wlv.append((tot, pct(allbuys, 50), len(es)))
wpos = [x for x in wlv if x[0] > 0]
wneg = [x for x in wlv if x[0] < 0]
print(f"\nwallet-level (>=3 buys): pos={len(wpos)} neg={len(wneg)}")
print(f"pos med-buy$: p25={pct([x[1] for x in wpos],25):.0f} med={pct([x[1] for x in wpos],50):.0f} p75={pct([x[1] for x in wpos],75):.0f}")
print(f"neg med-buy$: p25={pct([x[1] for x in wneg],25):.0f} med={pct([x[1] for x in wneg],50):.0f} p75={pct([x[1] for x in wneg],75):.0f}")
# big-money wallets only (medbuy >= 150)
bpos = [x for x in wpos if x[1] >= 150]; bneg = [x for x in wneg if x[1] >= 150]
print(f"medbuy>=150 wallets: WR={len(bpos)/max(len(bpos)+len(bneg),1):.0%} n={len(bpos)+len(bneg)}")
bpos = [x for x in wpos if x[1] >= 373]; bneg = [x for x in wneg if x[1] >= 373]
print(f"medbuy>=373 wallets: WR={len(bpos)/max(len(bpos)+len(bneg),1):.0%} n={len(bpos)+len(bneg)}")

# ---- combo family: flush-minute sniper who peels ----
def shape(e):
    if e["ns"] == 1: return "single"
    if e["sells"][-1][0]-e["sells"][0][0] < 5: return "burst"
    return "peel" if e["sells"][0][1]/e["su"] <= 0.7 else "big-then-dust"

def famtest(name, sel):
    s = [e for e in sc if sel(e)]
    w = [e for e in s if e["delta"] > 0]
    l = [e for e in s if e["delta"] < 0]
    nw2 = len({e["w"] for e in s}); np2 = len({e["pair"] for e in s})
    if not s:
        print(f"{name}: n=0"); return
    print(f"{name}: ep={len(s)} wallets={nw2} pairs={np2} WR={len(w)/max(len(w)+len(l),1):.0%} "
          f"med_delta={pct([e['delta'] for e in s],50):+.1f}$ "
          f"med_ret%={pct([e['delta']/e['bu']*100 for e in s if e['bu']>0],50):+.1f}")

def first_buy_dt(e):
    return dt_to_trough(e["pair"], e["buys"][0][0]) if e["buys"] else None

print("\ncombo families:")
famtest("A flush-sniper+peel (1st buy 0-120s post-trough, peel exit, hold>=15m)",
        lambda e: (lambda d: d is not None and 0 <= d < 120)(first_buy_dt(e))
                  and shape(e) == "peel" and e["hold"] and e["hold"] >= 900)
famtest("B flush-sniper any exit (1st buy 0-120s post-trough)",
        lambda e: (lambda d: d is not None and 0 <= d < 120)(first_buy_dt(e)))
famtest("C peel+hold>=1h (any entry)",
        lambda e: shape(e) == "peel" and e["hold"] and e["hold"] >= 3600)
famtest("D pre-low knife (1st buy 0-300s BEFORE trough)",
        lambda e: (lambda d: d is not None and -300 <= d < 0)(first_buy_dt(e)))
famtest("E HL-zone (1st buy 60-300s post-trough)",
        lambda e: (lambda d: d is not None and 60 <= d < 300)(first_buy_dt(e)))
famtest("F HL-zone + peel",
        lambda e: (lambda d: d is not None and 60 <= d < 300)(first_buy_dt(e))
                  and shape(e) == "peel")
famtest("G sniper 0-60s + peel",
        lambda e: (lambda d: d is not None and 0 <= d < 60)(first_buy_dt(e))
                  and shape(e) == "peel")
# size-conditioned peel (does peel hold for meaningful size?)
famtest("H peel, total-in>=$100",
        lambda e: shape(e) == "peel" and e["bu"] >= 100)
famtest("I single, total-in>=$100",
        lambda e: shape(e) == "single" and e["bu"] >= 100)
famtest("J peel, total-in>=$100, entry 0-120s post-trough",
        lambda e: shape(e) == "peel" and e["bu"] >= 100
                  and (lambda d: d is not None and 0 <= d < 120)(first_buy_dt(e)))
