"""Bad-regime pockets study (2026-06-10): what STILL performs on bad days?

Day quality = fleet-wide CT-day WR (all sells, n>=30). BAD = WR < 55%.
For every entry segment (mcap/age/liq/dip-depth/pc_h24/trigger/vol), measure
WR + $/tr + distinct tokens ON BAD DAYS ONLY, and require consistency across
two time folds (first vs second half of bad days) to call a pocket real.
Universe: ALL fleet sells with a linked buy carrying entry_meta.
"""
import json, sys, collections, statistics
from datetime import datetime, timedelta
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

tr = json.load(open("_trades_cache.json"))

def ct_day(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
    except Exception:
        return None

# fleet day WR
day_pnls = collections.defaultdict(list)
for t in tr:
    if t.get("type") == "sell" and "cancelled" not in (t.get("reason") or "").lower():
        d = ct_day(t.get("time"))
        if d:
            day_pnls[d].append(float(t.get("pnl") or 0))
day_wr = {d: sum(1 for p in ps if p > 0) / len(ps)
          for d, ps in day_pnls.items() if len(ps) >= 30}
bad_days = sorted(d for d, w in day_wr.items() if w < 0.55)
good_days = sorted(d for d, w in day_wr.items() if w >= 0.55)
print(f"days scored={len(day_wr)} | BAD={len(bad_days)} {bad_days}")
print(f"GOOD={len(good_days)}")

# link sells -> buys (same token+bot, latest prior buy)
bb = collections.defaultdict(list)
for t in tr:
    if t.get("type") == "buy" and t.get("entry_meta"):
        bb[((t.get("pair_address") or t.get("address") or "").lower(),
            t.get("bot_id") or "")].append(t)
for k in bb:
    bb[k].sort(key=lambda b: b.get("time", ""))

rows = []  # (day, pnl, em, buy_record, token)
for t in tr:
    if t.get("type") != "sell" or "cancelled" in (t.get("reason") or "").lower():
        continue
    d = ct_day(t.get("time"))
    if d not in day_wr:
        continue
    k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    c = [b for b in bb.get(k, []) if (b.get("time") or "") < (t.get("time") or "")]
    if not c:
        continue
    rows.append((d, float(t.get("pnl") or 0), c[-1].get("entry_meta") or {}, c[-1],
                 t.get("address") or t.get("token")))
bad_rows = [r for r in rows if r[0] in bad_days]
good_rows = [r for r in rows if r[0] in good_days]
print(f"linked closes: bad-days={len(bad_rows)} good-days={len(good_rows)}")
bad_base_wr = sum(1 for r in bad_rows if r[1] > 0) / len(bad_rows)
bad_base_d = statistics.mean(r[1] for r in bad_rows)
print(f"bad-day baseline: WR={bad_base_wr:.0%} ${bad_base_d:+.2f}/tr\n")

# fold split of bad days for consistency
half = len(bad_days) // 2
foldA, foldB = set(bad_days[:half]), set(bad_days[half:])

def seg_stats(rows_, label_fn):
    segs = collections.defaultdict(lambda: {"A": [0, 0, 0.0, set()], "B": [0, 0, 0.0, set()]})
    for d, pnl, em, buy, tok in rows_:
        lbl = label_fn(em, buy)
        if lbl is None:
            continue
        f = "A" if d in foldA else "B"
        s = segs[lbl][f]
        s[0] += 1
        s[1] += pnl > 0
        s[2] += pnl
        s[3].add(tok)
    return segs

def show(title, label_fn, min_n=25):
    segs = seg_stats(bad_rows, label_fn)
    print(f"── {title} (bad days; need both folds green-ish) ──")
    for lbl in sorted(segs):
        A, B = segs[lbl]["A"], segs[lbl]["B"]
        n = A[0] + B[0]
        if n < min_n or A[0] < 8 or B[0] < 8:
            continue
        wr = (A[1] + B[1]) / n
        dpt = (A[2] + B[2]) / n
        wA = A[1] / A[0]; wB = B[1] / B[0]
        dA = A[2] / A[0]; dB = B[2] / B[0]
        toks = len(A[3] | B[3])
        flag = " ⭐" if (dA > 0 and dB > 0 and wr > bad_base_wr + 0.05 and toks >= 8) else ""
        print(f"  {str(lbl):26s} n={n:5d} WR={wr:4.0%} ${dpt:+6.2f}/tr "
              f"[A:{wA:.0%}/${dA:+5.2f} B:{wB:.0%}/${dB:+5.2f}] tok={toks}{flag}")
    print()

def mcap_band(em, buy):
    v = buy.get("entry_market_cap_usd") or em.get("mcap")
    if not isinstance(v, (int, float)) or v <= 0:
        return None
    for lo, hi, lbl in ((0, 1e5, "<100k"), (1e5, 5e5, "100-500k"), (5e5, 1e6, "500k-1M"),
                        (1e6, 5e6, "1-5M"), (5e6, 1e7, "5-10M"), (1e7, 1e12, ">10M")):
        if lo <= v < hi:
            return lbl

def age_band(em, buy):
    v = buy.get("entry_age_hours") or em.get("age_hours")
    if not isinstance(v, (int, float)) or v <= 0:
        return None
    for lo, hi, lbl in ((0, 1, "<1h"), (1, 6, "1-6h"), (6, 24, "6-24h"),
                        (24, 72, "1-3d"), (72, 168, "3-7d"), (168, 1e9, ">7d")):
        if lo <= v < hi:
            return lbl

def dip_band(em, buy):
    v = em.get("shape_90m_drawdown_from_max_pct")
    if not isinstance(v, (int, float)):
        return None
    for lo, hi, lbl in ((-999, -30, "<=-30"), (-30, -20, "-30..-20"),
                        (-20, -16, "-20..-16"), (-16, -10, "-16..-10"), (-10, 999, ">-10")):
        if lo <= v < hi:
            return lbl

def liq_band(em, buy):
    v = em.get("liquidity_usd") or em.get("liq")
    if not isinstance(v, (int, float)) or v <= 0:
        return None
    for lo, hi, lbl in ((0, 5e4, "<50k"), (5e4, 1.5e5, "50-150k"),
                        (1.5e5, 5e5, "150-500k"), (5e5, 1e12, ">500k")):
        if lo <= v < hi:
            return lbl

def pc24_band(em, buy):
    v = em.get("pc_h24")
    if not isinstance(v, (int, float)):
        return None
    for lo, hi, lbl in ((-999, -20, "<-20%"), (-20, 0, "-20..0"), (0, 50, "0..+50"),
                        (50, 200, "+50..200"), (200, 1e9, ">+200")):
        if lo <= v < hi:
            return lbl

def nf60_band(em, buy):
    v = em.get("net_flow_60s_usd")
    if not isinstance(v, (int, float)):
        return None
    return ">=100" if v >= 100 else ("0..100" if v >= 0 else "<0")

def trigger_lbl(em, buy):
    trigs = em.get("triggers_fired") or buy.get("triggers_fired")
    if isinstance(trigs, str):
        trigs = [trigs]
    if not trigs:
        return None
    return tuple(sorted(trigs))[:1][0]  # primary trigger

show("ENTRY MCAP", mcap_band)
show("TOKEN AGE", age_band)
show("DIP DEPTH (90m drawdown)", dip_band)
show("LIQUIDITY", liq_band)
show("PC_H24 AT ENTRY", pc24_band)
show("NET FLOW 60s", nf60_band)
show("PRIMARY TRIGGER", trigger_lbl, min_n=30)

# the money question: do the GOOD-day pockets flip or hold on bad days?
print("── cross-check: best bad-day mcap/age cells on GOOD days ──")
for fn, name in ((mcap_band, "mcap"), (age_band, "age"), (dip_band, "dip")):
    segs_g = seg_stats(good_rows, fn)
    for lbl in sorted(segs_g):
        A, B = segs_g[lbl]["A"], segs_g[lbl]["B"]
        # folds built from bad_days; good rows mostly land in one fold — pool them
        n = A[0] + B[0]
        if n < 25:
            continue
        wr = (A[1] + B[1]) / n
        dpt = (A[2] + B[2]) / n
        print(f"  good-days {name} {str(lbl):14s} n={n:5d} WR={wr:4.0%} ${dpt:+6.2f}/tr")
