# -*- coding: ascii -*-
"""Adversarial verification of the post60_nf retrace continuation separator.
Forward-only reconstruction from live tapes. Zero egress.
Claim: post60_nf>0 (AUC 0.619); 2-factor with nf_last60_pre>-100 -> 72.5% CONT
       at n=142/78 tokens vs 60.5% base.
"""
import json, os, glob, math, random
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(RIP, "live_tapes")

# flow-trough (decode) params
D_FLOOR = 400.0
D_FRAC = 0.015      # 1.5% of gross window volume
REBOUND = 0.35      # rebound frac of D confirms
PEAK_LOOKBACK = 3600.0  # 60 min trailing peak
PRE_S = 60.0
POST_S = 60.0
H_OUT = 600.0       # outcome horizon after decision instant (10 min)

def load_tape(path):
    seen = set(); tr = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except: continue
        key = (r.get("ts"), r.get("maker"), r.get("volume_usd"), r.get("kind"))
        if key in seen: continue
        seen.add(key)
        try:
            ep = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp()
        except: continue
        k = r.get("kind"); v = float(r.get("volume_usd") or 0.0); mk = r.get("maker") or ""
        sv = v if k == "buy" else -v
        tr.append((ep, sv, v, mk, k))
    tr.sort()
    return tr

def nf_at(cum, times, t):
    # cumulative net flow at/just before time t (times sorted). linear step.
    import bisect
    i = bisect.bisect_right(times, t) - 1
    if i < 0: return 0.0
    return cum[i]

def gross_window(times, vols, t0, t1):
    import bisect
    a = bisect.bisect_left(times, t0); b = bisect.bisect_right(times, t1)
    return sum(vols[a:b])

def detect_troughs(tr, causal):
    """Return list of trough dicts. If causal: decision-time running-min + confirmed
    rebound, t0 must remain running-min through t0+POST. If oracle: global min of
    flush segment (uses forward data)."""
    if len(tr) < 20: return []
    times = [x[0] for x in tr]
    svs = [x[1] for x in tr]
    vols = [x[2] for x in tr]
    cum = []
    s = 0.0
    for v in svs:
        s += v; cum.append(s)
    n = len(tr)
    troughs = []
    i = 0
    cooldown_until = -1
    # trailing peak tracking
    while i < n:
        t = times[i]
        if t < cooldown_until:
            i += 1; continue
        # trailing peak of cum over [t-LOOKBACK, t]
        import bisect
        a = bisect.bisect_left(times, t - PEAK_LOOKBACK)
        if i - a < 5:
            i += 1; continue
        peak = max(cum[a:i+1])
        gross = gross_window(times, vols, t - PEAK_LOOKBACK, t)
        D = max(D_FLOOR, D_FRAC * gross)
        drawdown = peak - cum[i]
        if drawdown >= D:
            # armed: find the low. causal walk forward until rebound >= REBOUND*D above running min
            lo_i = i; lo = cum[i]
            j = i
            fired = None
            while j < n:
                if cum[j] < lo:
                    lo = cum[j]; lo_i = j
                if cum[j] - lo >= REBOUND * D:
                    fired = j
                    break
                # abandon if goes way below (keeps dumping far) -> still a trough eventually
                j += 1
            if fired is None:
                # never rebounded within tape -> tape ends dumping; skip
                i += 1; continue
            t0 = times[lo_i]
            if causal:
                # decision instant = t0 + POST_S; require t0 is running min through then
                dec_t = t0 + POST_S
                b = bisect.bisect_right(times, dec_t)
                seg = cum[lo_i:b]
                if seg and min(seg) < lo - 1e-6:
                    # a lower low appeared within POST window -> t0 not the trough, advance
                    i = lo_i + 1; continue
            troughs.append({"lo_i": lo_i, "t0": t0})
            cooldown_until = t0 + 1800.0
            i = lo_i + 1
        else:
            i += 1
    return troughs, times, cum, tr

def flow_outcome(times, cum, t0):
    """CONT if NF rises >=R above decision-NF before dropping >=R below, within H.
    decision instant = t0+POST. R scaled to local D-ish (use post/pre magnitude)."""
    import bisect
    dec_t = t0 + POST_S
    dNF = nf_at2(cum, times, dec_t)
    # reference scale R: 1.5% gross over the outcome window's preceding hour
    R = max(D_FLOOR, D_FRAC * gross_scale(times, t0))
    a = bisect.bisect_right(times, dec_t)
    b = bisect.bisect_right(times, dec_t + H_OUT)
    up = False; dn = False
    for k in range(a, b):
        d = cum[k] - dNF
        if d >= 0.5 * R: return 1  # continuation
        if d <= -0.5 * R: return 0  # top / further dump
    # neither: judge by end
    if b > a:
        return 1 if cum[b-1] - dNF > 0 else 0
    return None

_gross_cache = {}
def gross_scale(times, t0):
    import bisect
    a = bisect.bisect_left(times, t0 - PEAK_LOOKBACK); b = bisect.bisect_right(times, t0)
    return _vol_sum[a:b_marker] if False else None

def nf_at2(cum, times, t):
    import bisect
    i = bisect.bisect_right(times, t) - 1
    return cum[i] if i >= 0 else 0.0

# ---- simpler: recompute everything per tape with vols available ----

def process_tape(path, causal):
    tr = load_tape(path)
    if len(tr) < 20: return []
    times = [x[0] for x in tr]; svs = [x[1] for x in tr]
    vols = [x[2] for x in tr]; makers = [x[3] for x in tr]
    cum = []; s = 0.0
    for v in svs: s += v; cum.append(s)
    n = len(tr)
    import bisect
    def gwin(t0, t1):
        a = bisect.bisect_left(times, t0); b = bisect.bisect_right(times, t1)
        return sum(vols[a:b])
    def nfat(t):
        i = bisect.bisect_right(times, t) - 1
        return cum[i] if i >= 0 else 0.0
    rows = []
    i = 0; cooldown = -1
    while i < n:
        t = times[i]
        if t < cooldown: i += 1; continue
        a = bisect.bisect_left(times, t - PEAK_LOOKBACK)
        if i - a < 5: i += 1; continue
        peak = max(cum[a:i+1])
        gross = gwin(t - PEAK_LOOKBACK, t)
        D = max(D_FLOOR, D_FRAC * gross)
        if peak - cum[i] >= D:
            lo = cum[i]; lo_i = i; j = i; fired = None
            while j < n:
                if cum[j] < lo: lo = cum[j]; lo_i = j
                if cum[j] - lo >= REBOUND * D: fired = j; break
                j += 1
            if fired is None: i += 1; continue
            t0 = times[lo_i]
            if causal:
                dec_t = t0 + POST_S
                b = bisect.bisect_right(times, dec_t)
                if b > lo_i and min(cum[lo_i:b]) < lo - 1e-6:
                    i = lo_i + 1; continue
            # need tape to span [t0-PRE, t0+POST+H_OUT]
            if times[0] > t0 - PRE_S or times[-1] < t0 + POST_S + H_OUT:
                i = lo_i + 1; cooldown = t0 + 1800; continue
            pre60 = nfat(t0) - nfat(t0 - PRE_S)
            post60 = nfat(t0 + POST_S) - nfat(t0)
            # outcome (flow-based, independent horizon AFTER decision instant)
            dec_t = t0 + POST_S; dNF = nfat(dec_t)
            R = max(D_FLOOR, D_FRAC * gwin(t0 - PEAK_LOOKBACK, t0))
            aa = bisect.bisect_right(times, dec_t); bb = bisect.bisect_right(times, dec_t + H_OUT)
            outcome = None
            for k in range(aa, bb):
                d = cum[k] - dNF
                if d >= 0.5 * R: outcome = 1; break
                if d <= -0.5 * R: outcome = 0; break
            if outcome is None:
                outcome = 1 if (bb > aa and cum[bb-1] - dNF > 0) else 0
            # wallets in post window (for whale drop)
            pa = bisect.bisect_left(times, t0); pb = bisect.bisect_right(times, t0 + POST_S)
            post_trades = [(svs[k], makers[k]) for k in range(pa, pb)]
            rows.append({"pair8": os.path.basename(path)[5:13], "t0": t0,
                         "pre60": pre60, "post60": post60, "outcome": outcome,
                         "R": R, "post_trades": post_trades})
            cooldown = t0 + 1800; i = lo_i + 1
        else:
            i += 1
    return rows

def auc(scores, labels):
    pairs = list(zip(scores, labels))
    pos = [s for s, l in pairs if l == 1]; neg = [s for s, l in pairs if l == 0]
    if not pos or not neg: return float("nan")
    c = 0.0
    for p in pos:
        for q in neg:
            if p > q: c += 1
            elif p == q: c += 0.5
    return c / (len(pos) * len(neg))

def summarize(rows, tag):
    n = len(rows)
    if n == 0:
        print("%s: no rows" % tag); return
    tokens = len(set(r["pair8"] for r in rows))
    base = sum(r["outcome"] for r in rows) / n
    sc = [r["post60"] for r in rows]; lb = [r["outcome"] for r in rows]
    a = auc(sc, lb)
    # single factor post60>0
    p1 = [r for r in rows if r["post60"] > 0]
    r1 = sum(x["outcome"] for x in p1)/len(p1) if p1 else float("nan")
    # 2-factor
    p2 = [r for r in rows if r["post60"] > 0 and r["pre60"] > -100]
    r2 = sum(x["outcome"] for x in p2)/len(p2) if p2 else float("nan")
    tok2 = len(set(r["pair8"] for r in p2))
    print("%s: n=%d tokens=%d base_CONT=%.1f%% AUC(post60)=%.3f" % (tag, n, tokens, 100*base, a))
    print("   post60>0: n=%d CONT=%.1f%%   2factor(post60>0 & pre60>-100): n=%d/%dtok CONT=%.1f%%" %
          (len(p1), 100*r1 if p1 else float('nan'), len(p2), tok2, 100*r2 if p2 else float('nan')))
    return rows

def whale_drop(rows, tag):
    """Recompute post60 dropping the single largest-abs signed trade per event; also
    global: drop events dominated by one wallet. Report AUC & 2factor after drop."""
    dr = []
    for r in rows:
        pt = r["post_trades"]
        if not pt:
            dr.append(dict(r)); continue
        # drop the largest |signed| trade in post window
        idx = max(range(len(pt)), key=lambda k: abs(pt[k][0]))
        drop_v = pt[idx][0]
        nr = dict(r); nr["post60"] = r["post60"] - drop_v
        dr.append(nr)
    n = len(dr); base = sum(x["outcome"] for x in dr)/n
    a = auc([x["post60"] for x in dr], [x["outcome"] for x in dr])
    p2 = [x for x in dr if x["post60"] > 0 and x["pre60"] > -100]
    r2 = sum(x["outcome"] for x in p2)/len(p2) if p2 else float("nan")
    print("%s [drop top-1 post-wallet trade/event]: AUC=%.3f  2factor n=%d CONT=%.1f%%" %
          (tag, a, len(p2), 100*r2 if p2 else float('nan')))

def oos_split(rows, tag):
    toks = sorted(set(r["pair8"] for r in rows))
    random.seed(7); random.shuffle(toks)
    half = set(toks[:len(toks)//2])
    A = [r for r in rows if r["pair8"] in half]; B = [r for r in rows if r["pair8"] not in half]
    for nm, g in (("splitA", A), ("splitB", B)):
        if not g: continue
        a = auc([x["post60"] for x in g], [x["outcome"] for x in g])
        p2 = [x for x in g if x["post60"] > 0 and x["pre60"] > -100]
        base = sum(x["outcome"] for x in g)/len(g)
        r2 = sum(x["outcome"] for x in p2)/len(p2) if p2 else float("nan")
        print("%s %s: n=%d tok=%d base=%.1f%% AUC=%.3f 2f n=%d CONT=%.1f%%" %
              (tag, nm, len(g), len(set(r['pair8'] for r in g)), 100*base, a, len(p2),
               100*r2 if p2 else float('nan')))

def main():
    paths = sorted(glob.glob(os.path.join(LIVE, "tape_*.jsonl")))
    for causal in (False, True):
        allrows = []
        for p in paths:
            try: allrows += process_tape(p, causal)
            except Exception as e:
                pass
        tag = "CAUSAL" if causal else "ORACLE"
        print("\n===== %s trough, flow outcome =====" % tag)
        summarize(allrows, tag)
        whale_drop(allrows, tag)
        oos_split(allrows, tag)
        # temporal OOS: first half of events by time vs second
        allrows.sort(key=lambda r: r["t0"])
        mid = len(allrows)//2
        for nm, g in (("timeEARLY", allrows[:mid]), ("timeLATE", allrows[mid:])):
            a = auc([x["post60"] for x in g],[x["outcome"] for x in g])
            p2=[x for x in g if x["post60"]>0 and x["pre60"]>-100]
            base=sum(x["outcome"] for x in g)/len(g) if g else float('nan')
            r2=sum(x["outcome"] for x in p2)/len(p2) if p2 else float('nan')
            print("   %s: n=%d base=%.1f%% AUC=%.3f 2f n=%d CONT=%.1f%%"%(nm,len(g),100*base,a,len(p2),100*r2 if p2 else float('nan')))

if __name__ == "__main__":
    main()
