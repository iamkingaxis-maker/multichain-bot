# REVIVAL-POND Part B: base rates of the predicate on all >72h pairs, hourly grid, bars-only.
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))

def iso2ep(s): return datetime.fromisoformat(s).timestamp()

bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try: d = json.load(open(f))
    except Exception: continue
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
pairs_all = {e["pair"] for eps in led.values() for e in eps}
p12 = {p[:12]: p for p in pairs_all}
for dd in ("_gt_bars", "_gt_bars_b"):
    for f in glob.glob(os.path.join(RIP, dd, "*.json")):
        p = p12.get(os.path.basename(f).split(".")[0])
        if not p: continue
        try: b = json.load(open(f))
        except Exception: continue
        bl = b if isinstance(b, list) else (b.get("bars") or [])
        if bl: bars_by_pair.setdefault(p, []).extend(bl)
for p in bars_by_pair:
    u = {int(b[0]): b for b in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda x: x[0])

age_src = {}
try:
    tm = json.load(open(os.path.join(RIP, "token_meta.json")))
    for p, v in tm.items():
        if v.get("pool_created_at"):
            age_src[p] = datetime.fromisoformat(v["pool_created_at"].replace("Z", "+00:00")).timestamp()
except Exception: pass
for fn in ("_pair_created_cache.json", "_pair_created_cache2.json"):
    try:
        pc = json.load(open(os.path.join(RIP, fn)))
        for p, v in pc.items():
            if v: age_src.setdefault(p, float(v))
    except Exception: pass

sym = {}
for eps in led.values():
    for e in eps: sym.setdefault(e["pair"], e.get("sym"))

# revival tokens from Part A (07-03 winner >72h tokens) for recall check
rev_eps = json.load(open(os.path.join(RIP, "_revival_eps_0703.json")))
rev_toks = {r["pair"] for r in rev_eps}
first_win_entry = {}
for r in sorted(rev_eps, key=lambda r: r["ep0"]):
    first_win_entry.setdefault(r["pair"], float(r["ep0"]))

# ---- hourly volume series per pair ----
def hourly_series(p):
    hv = collections.defaultdict(float)
    for b in bars_by_pair[p]:
        hv[int(b[0] // 3600)] += b[5]
    return hv

P = dict(PEAK48=25000.0, DORM=0.35, VNOW=5000.0, RAMP=1.5, BASE=0.55)

def eval_pair(p):
    """returns list of (t_hour_epoch, matched, fwd_ret_pct, feats) for age>72h hours"""
    bl = bars_by_pair.get(p)
    if not bl or p not in age_src: return []
    ts = [b[0] for b in bl]
    hv = hourly_series(p)
    hours = sorted(hv.keys())
    if not hours: return []
    out = []
    h0, h1 = hours[0], hours[-1]
    for H in range(h0 + 12, h1 - 5):  # need >=12h lookback, 6h forward runway
        t = (H + 1) * 3600  # decision at close of hour H
        if (t - age_src[p]) / 3600.0 <= 72: continue
        look = [hv.get(h, 0.0) for h in range(max(h0, H - 48), H)]
        if len(look) < 12: continue
        prior24 = [hv.get(h, 0.0) for h in range(max(h0, H - 24), H)]
        avg24 = sum(prior24) / len(prior24)
        peak48 = max(look)
        v_now = hv.get(H, 0.0)
        if v_now < 500: continue  # activity floor for the eval universe
        # px at close of hour H, high over lookback for base check
        i = bisect.bisect_right(ts, t) - 1
        if i < 0 or t - ts[i] > 3600: continue
        px = bl[i][4]
        lo48 = bisect.bisect_left(ts, t - 48 * 3600)
        hi48 = max(b[2] for b in bl[lo48:i + 1])
        # forward 6h max high
        j = bisect.bisect_right(ts, t + 6 * 3600)
        if j <= i + 1: continue
        fwd_hi = max(b[2] for b in bl[i + 1:j])
        fwd = 100.0 * (fwd_hi / px - 1)
        dorm = avg24 / peak48 if peak48 else None
        ramp = v_now / avg24 if avg24 else None
        matched = (peak48 >= P["PEAK48"] and dorm is not None and dorm <= P["DORM"]
                   and v_now >= P["VNOW"] and ramp is not None and ramp >= P["RAMP"]
                   and px >= P["BASE"] * hi48)
        out.append((t, matched, fwd, dict(peak48=peak48, dorm=dorm, ramp=ramp, v_now=v_now,
                                          base=px / hi48)))
    return out

ev_all = {}
for p in pairs_all:
    r = eval_pair(p)
    if r: ev_all[p] = r

def day_of(t): return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")

# --- event level ---
m_ev = [(p, t, f) for p, rr in ev_all.items() for (t, m, f, _) in rr if m]
u_ev = [(p, t, f) for p, rr in ev_all.items() for (t, m, f, _) in rr if not m]
def hit(evs): return sum(1 for _, _, f in evs if f >= 15.0)
print("EVENT level: matched n=%d hit15_6h=%.1f%% | unmatched n=%d hit15_6h=%.1f%%" %
      (len(m_ev), 100.0 * hit(m_ev) / len(m_ev) if m_ev else 0,
       len(u_ev), 100.0 * hit(u_ev) / len(u_ev) if u_ev else 0))

# --- token level dedup: first matched event per token; unmatched tokens = first active event ---
tok_first_m, tok_first_u = {}, {}
for p, rr in ev_all.items():
    for (t, m, f, _) in rr:
        if m:
            if p not in tok_first_m: tok_first_m[p] = (t, f)
        else:
            if p not in tok_first_u: tok_first_u[p] = (t, f)
mt = list(tok_first_m.items())
ut = [(p, v) for p, v in tok_first_u.items() if p not in tok_first_m]
print("TOKEN level: matched n=%d hit15=%.1f%% medfwd=%+.1f | never-matched(active >72h) n=%d hit15=%.1f%% medfwd=%+.1f" %
      (len(mt), 100.0 * sum(1 for _, (t, f) in mt if f >= 15) / len(mt) if mt else 0,
       st.median([f for _, (t, f) in mt]) if mt else 0,
       len(ut), 100.0 * sum(1 for _, (t, f) in ut if f >= 15) / len(ut) if ut else 0,
       st.median([f for _, (t, f) in ut]) if ut else 0))

# per-day matched token counts (dedup within day)
per_day = collections.defaultdict(set)
for p, t, f in m_ev: per_day[day_of(t)].add(p)
print("matched tokens per day:", {d: len(s) for d, s in sorted(per_day.items())})
# per-day token-level hit using first match within that day
for d in sorted(per_day):
    firsts = {}
    for p, t, f in sorted(m_ev, key=lambda x: x[1]):
        if day_of(t) == d and p not in firsts: firsts[p] = f
    n = len(firsts); h = sum(1 for f in firsts.values() if f >= 15)
    print("  %s: n_tok=%d hit15_6h=%.0f%% medfwd=%+.1f" % (d, n, 100.0 * h / n if n else 0,
          st.median(list(firsts.values())) if firsts else 0))

# recall on the 13 winner revival tokens: did predicate fire on/before first winner entry (same day)?
rec = 0; det = []
for p in rev_toks:
    t0 = first_win_entry[p]
    fired = [t for (t, m, f, _) in ev_all.get(p, []) if m and t <= t0 + 900]
    ok = bool(fired)
    rec += ok
    det.append("%s:%s%s" % ((sym.get(p) or p[:6]).encode("ascii", "replace").decode(),
                            "HIT" if ok else "miss",
                            ("(dt=%.1fh)" % ((t0 - max(fired)) / 3600) if fired else "")))
print("RECALL on 13 winner revival tokens (predicate fired at/before first winner entry): %d/13" % rec)
print("  " + " | ".join(det))

# split check: pooled hit by day-half
h1 = [f for p, t, f in m_ev if day_of(t) <= "2026-07-02"]
h2 = [f for p, t, f in m_ev if day_of(t) >= "2026-07-03"]
print("SPLIT events: 07-01/02 n=%d hit=%.0f%% | 07-03/04 n=%d hit=%.0f%%" %
      (len(h1), 100.0 * sum(1 for f in h1 if f >= 15) / len(h1) if h1 else 0,
       len(h2), 100.0 * sum(1 for f in h2 if f >= 15) / len(h2) if h2 else 0))
json.dump({p: [(t, m, f) for (t, m, f, _) in rr] for p, rr in ev_all.items()},
          open(os.path.join(RIP, "_revival_grid.json"), "w"))
