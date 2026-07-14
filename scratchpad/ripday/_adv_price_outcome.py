# -*- coding: ascii -*-
"""post60_nf separator vs INDEPENDENT price outcome (BOUNCED=CONT / DIED=TOP).
Reuses absorption_decode2 flush detection + labels on ohlc2 bars, joins tape netflow.
Forward-only: post60 uses [low, low+60]; decision instant = low+60; outcome uses bars
strictly AFTER (BOUNCED within 60m / DIED within 90m) -> no overlap with post60 window
except the 60s absorption window itself, which is the signal not the label.
"""
import json, os, glob, bisect, random
from datetime import datetime, timezone
import importlib.util

RIP = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ad2", os.path.join(RIP, "absorption_decode2.py"))
ad2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(ad2)

PRE_S = 60.0; POST_S = 60.0

def build_nf(trades):
    times = [t[0] for t in trades]
    cum = []; s = 0.0
    for tr in trades:
        s += (tr[2] if tr[1] == "buy" else -tr[2]); cum.append(s)
    return times, cum

def nfat(times, cum, t):
    i = bisect.bisect_right(times, t) - 1
    return cum[i] if i >= 0 else 0.0

def auc(scores, labels):
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    if not pos or not neg: return float("nan")
    c = 0.0
    for p in pos:
        for q in neg:
            c += 1 if p > q else (0.5 if p == q else 0)
    return c / (len(pos) * len(neg))

def main():
    ohlc = sorted(glob.glob(os.path.join(RIP, "ohlc2_*.json")))
    rows = []
    for path in ohlc:
        pair8 = os.path.basename(path)[6:-5]
        try: pair, bars = ad2.load_bars(path)
        except: continue
        if len(bars) < 5: continue
        evs = ad2.find_flushes(bars)
        if not evs: continue
        trades = ad2.load_tape(pair8)
        if len(trades) < 10: continue
        times, cum = build_nf(trades)
        for ev in evs:
            label, min_dd = ad2.label_event(bars, ev)
            if label not in ("BOUNCED", "DIED"): continue
            # in-band non-rug filter (same as decode's tradeable band)
            if ev["depth"] < ad2.BAND_MIN: continue
            if min_dd <= ad2.RUG_DD: continue
            low_t = bars[ev["low_i"]][0]
            if times[0] > low_t - PRE_S or times[-1] < low_t + POST_S:
                continue
            pre60 = nfat(times, cum, low_t) - nfat(times, cum, low_t - PRE_S)
            post60 = nfat(times, cum, low_t + POST_S) - nfat(times, cum, low_t)
            # post-window trades for whale drop
            pa = bisect.bisect_left(times, low_t); pb = bisect.bisect_right(times, low_t + POST_S)
            post_trades = [(trades[k][2] if trades[k][1] == "buy" else -trades[k][2],
                            trades[k][3]) for k in range(pa, pb)]
            rows.append({"pair8": pair8, "low_t": low_t, "pre60": pre60, "post60": post60,
                         "cont": 1 if label == "BOUNCED" else 0, "depth": ev["depth"],
                         "post_trades": post_trades})
    n = len(rows)
    toks = len(set(r["pair8"] for r in rows))
    base = sum(r["cont"] for r in rows) / n if n else float("nan")
    a = auc([r["post60"] for r in rows], [r["cont"] for r in rows])
    print("=== PRICE outcome (BOUNCED=CONT / DIED=TOP), in-band non-rug ===")
    print("n=%d tokens=%d base_CONT=%.1f%% AUC(post60)=%.3f" % (n, toks, 100*base, a))
    for thr in (0, 100, 250):
        p1 = [r for r in rows if r["post60"] > thr]
        r1 = sum(r["cont"] for r in p1)/len(p1) if p1 else float("nan")
        print("  post60>%d: n=%d tok=%d CONT=%.1f%%" % (thr, len(p1),
              len(set(x['pair8'] for x in p1)), 100*r1 if p1 else float('nan')))
    p2 = [r for r in rows if r["post60"] > 0 and r["pre60"] > -100]
    r2 = sum(r["cont"] for r in p2)/len(p2) if p2 else float("nan")
    print("  2factor(post60>0 & pre60>-100): n=%d/%dtok CONT=%.1f%% (base %.1f%%)" %
          (len(p2), len(set(x['pair8'] for x in p2)), 100*r2 if p2 else float('nan'), 100*base))
    # AUC of pre60 alone, and of imbalance
    ap = auc([r["pre60"] for r in rows], [r["cont"] for r in rows])
    print("  AUC(pre60)=%.3f  AUC(post60+pre60 sum)=%.3f" % (ap,
          auc([r["post60"]+r["pre60"] for r in rows], [r["cont"] for r in rows])))

    # WHALE DROP: drop largest |signed| post trade per event
    dr = []
    for r in rows:
        pt = r["post_trades"]
        if pt:
            idx = max(range(len(pt)), key=lambda k: abs(pt[k][0]))
            nr = dict(r); nr["post60"] = r["post60"] - pt[idx][0]; dr.append(nr)
        else: dr.append(dict(r))
    ad_ = auc([r["post60"] for r in dr], [r["cont"] for r in dr])
    p2d = [r for r in dr if r["post60"] > 0 and r["pre60"] > -100]
    r2d = sum(r["cont"] for r in p2d)/len(p2d) if p2d else float("nan")
    print("  [drop top-1 post trade/event] AUC=%.3f  2factor n=%d CONT=%.1f%%" %
          (ad_, len(p2d), 100*r2d if p2d else float('nan')))

    # DROP TOP TOKENS: remove the 2 tokens contributing most events
    from collections import Counter
    cnt = Counter(r["pair8"] for r in rows)
    top2 = [t for t, _ in cnt.most_common(2)]
    g = [r for r in rows if r["pair8"] not in top2]
    ag = auc([r["post60"] for r in g], [r["cont"] for r in g])
    p2g = [r for r in g if r["post60"] > 0 and r["pre60"] > -100]
    r2g = sum(r["cont"] for r in p2g)/len(p2g) if p2g else float("nan")
    baseg = sum(r["cont"] for r in g)/len(g)
    print("  [drop top-2 tokens %s] n=%d AUC=%.3f base=%.1f%% 2factor n=%d CONT=%.1f%%" %
          (top2, len(g), ag, 100*baseg, len(p2g), 100*r2g if p2g else float('nan')))

    # OOS token split
    tks = sorted(set(r["pair8"] for r in rows)); random.seed(11); random.shuffle(tks)
    half = set(tks[:len(tks)//2])
    for nm, gg in (("A", [r for r in rows if r["pair8"] in half]),
                   ("B", [r for r in rows if r["pair8"] not in half])):
        if not gg: continue
        ax = auc([r["post60"] for r in gg], [r["cont"] for r in gg])
        p2x = [r for r in gg if r["post60"] > 0 and r["pre60"] > -100]
        bx = sum(r["cont"] for r in gg)/len(gg)
        r2x = sum(r["cont"] for r in p2x)/len(p2x) if p2x else float("nan")
        print("  OOS split %s: n=%d tok=%d base=%.1f%% AUC=%.3f 2factor n=%d CONT=%.1f%%" %
              (nm, len(gg), len(set(r['pair8'] for r in gg)), 100*bx, ax, len(p2x),
               100*r2x if p2x else float('nan')))

if __name__ == "__main__":
    main()
