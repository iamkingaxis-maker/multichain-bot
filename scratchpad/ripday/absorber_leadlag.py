# -*- coding: ascii -*-
"""
ABSORBER-WALLET LEAD-LAG study (2026-07-03).

Question: when an absorber-ledger wallet buys at a flush low, does the bounce
follow with actionable LEAD TIME?  Compare head-to-head vs the anonymous
composition signal (n_buyers>=3 AND max_print>=50 in the trough window).

Reuses absorption_decode2.py definitions verbatim (import).
No network.  Read-only except stdout.
"""
import json, os, glob, sys, importlib.util
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ad2", os.path.join(RIP, "absorption_decode2.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)

ANON_X = 50.0   # max_print threshold
ANON_Y = 3      # n_buyers threshold
CAND_MIN_B = 3
CAND_ASSOC = 0.85


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def dist(xs):
    return "n=%3d  med %7.1f  p25 %7.1f  p75 %7.1f  min %7.1f  max %7.1f" % (
        len(xs), pct(xs, .5), pct(xs, .25), pct(xs, .75),
        min(xs) if xs else float("nan"), max(xs) if xs else float("nan"))


def build_events():
    """Rebuild in-band labeled events + raw window buys (chronological)."""
    events = []
    for path in sorted(glob.glob(os.path.join(RIP, "ohlc2_*.json"))):
        pair8 = os.path.basename(path)[6:-5]
        try:
            pair, bars = A.load_bars(path)
        except Exception:
            continue
        if len(bars) < 5:
            continue
        evs = A.find_flushes(bars)
        if not evs:
            continue
        trades = A.load_tape(pair8)
        for ev in evs:
            label, min_dd = A.label_event(bars, ev)
            if label not in ("BOUNCED", "DIED"):
                continue
            low_i = ev["low_i"]
            low_t, low_c = bars[low_i]
            feats = A.tape_features(trades, low_t)
            if feats is None or feats == "GAP":
                continue
            # in-band, non-rug only
            if not (ev["depth"] >= A.BAND_MIN and min_dd > A.RUG_DD):
                continue
            w0, w1 = low_t - A.TAPE_PRE_S, low_t + A.TAPE_POST_S
            win_buys = [tr for tr in trades
                        if w0 <= tr[0] <= w1 and tr[1] == "buy"
                        and not tr[3].startswith(A.KILL_PREFIXES)]
            win_buys.sort()
            # bounce confirmation ts: first bar close >= low*1.10 within 60m
            t_conf = None
            for t, c in bars[low_i + 1:]:
                if t > low_t + A.BOUNCE_S:
                    break
                if c >= low_c * (1 + A.BOUNCE_PCT):
                    t_conf = t
                    break
            # earlier action point: first bar close >= low*1.05 (bounce start)
            t_start = None
            for t, c in bars[low_i + 1:]:
                if t > low_t + A.BOUNCE_S:
                    break
                if c >= low_c * (1 + 0.05):
                    t_start = t
                    break
            events.append({"pair8": pair8, "label": label, "low_t": low_t,
                           "low_c": low_c, "depth": ev["depth"],
                           "t_conf": t_conf, "t_start": t_start,
                           "win_buys": win_buys,
                           "n_buyers": feats["n_buyers"],
                           "max_print": feats["max_print"]})
    return events


def anon_first_cross(win_buys):
    """Chronological first ts where distinct buyers >= Y and running max print >= X."""
    seen = set()
    mx = 0.0
    for ts, kind, usd, mk in win_buys:
        seen.add(mk)
        mx = max(mx, usd)
        if len(seen) >= ANON_Y and mx >= ANON_X:
            return ts
    return None


def candidates_from(assoc, exclude_pair=None):
    out = set()
    for mk, w in assoc.items():
        b = set(w["bounced_pairs"]) - ({exclude_pair} if exclude_pair else set())
        d = set(w["died_pairs"]) - ({exclude_pair} if exclude_pair else set())
        if len(b) >= CAND_MIN_B and len(b) / max(len(b) + len(d), 1) >= CAND_ASSOC:
            out.add(mk)
    return out


def rate(grp, key):
    n = len(grp)
    k = sum(1 for e in grp if key(e))
    return "%3d/%3d = %5.1f%%" % (k, n, 100.0 * k / n if n else float("nan"))


def main():
    events = build_events()
    inb = events
    b_ev = [e for e in inb if e["label"] == "BOUNCED"]
    d_ev = [e for e in inb if e["label"] == "DIED"]
    print("=== EVENT SET (in-band, non-rug, tape-joined) ===")
    print("events=%d  BOUNCED=%d (%d pairs)  DIED=%d (%d pairs)" % (
        len(inb), len(b_ev), len(set(e["pair8"] for e in b_ev)),
        len(d_ev), len(set(e["pair8"] for e in d_ev))))

    # -------- fresh association table (from current rows; union per pair)
    assoc = {}
    for e in inb:
        for ts, kind, usd, mk in e["win_buys"]:
            if not mk:
                continue
            w = assoc.setdefault(mk, {"bounced_pairs": [], "died_pairs": []})
            key = "bounced_pairs" if e["label"] == "BOUNCED" else "died_pairs"
            if e["pair8"] not in w[key]:
                w[key].append(e["pair8"])
    cand_global = candidates_from(assoc)
    # persisted ledger candidates for reference
    try:
        led = json.load(open(os.path.join(RIP, "absorber_ledger.json")))
        cand_ledger = candidates_from(led)
    except Exception:
        cand_ledger = set()
    print("\nabsorber candidates: fresh-rebuild=%d  persisted-ledger=%d  overlap=%d" % (
        len(cand_global), len(cand_ledger), len(cand_global & cand_ledger)))
    for mk in sorted(cand_global | cand_ledger):
        w = assoc.get(mk, {"bounced_pairs": [], "died_pairs": []})
        print("  %s  fresh b=%d d=%d  [%s%s]" % (
            mk[:24], len(w["bounced_pairs"]), len(w["died_pairs"]),
            "G" if mk in cand_global else "-", "L" if mk in cand_ledger else "-"))

    # -------- per-event signal times
    for e in inb:
        e["t_anon"] = anon_first_cross(e["win_buys"])
        cand_lopo = candidates_from(assoc, exclude_pair=e["pair8"])
        abs_g = [tr for tr in e["win_buys"] if tr[3] in cand_global]
        abs_l = [tr for tr in e["win_buys"] if tr[3] in cand_lopo]
        e["t_abs_g"] = abs_g[0][0] if abs_g else None
        e["t_abs_l"] = abs_l[0][0] if abs_l else None

    def block(name, tkey):
        print("\n=== %s ===" % name)
        fired_b = [e for e in b_ev if e[tkey] is not None]
        fired_d = [e for e in d_ev if e[tkey] is not None]
        fired = fired_b + fired_d
        print("coverage of BOUNCED: %s   fires on DIED (FP rate): %s" % (
            rate(b_ev, lambda e: e[tkey] is not None),
            rate(d_ev, lambda e: e[tkey] is not None)))
        if fired:
            prec = 100.0 * len(fired_b) / len(fired)
            print("precision when fired: %d/%d = %.1f%%  (base bounce rate %.1f%%)" % (
                len(fired_b), len(fired), prec, 100.0 * len(b_ev) / len(inb)))
            # pair-level dedup: any-fire per pair
            pb = set(e["pair8"] for e in fired_b)
            pd = set(e["pair8"] for e in fired_d)
            print("pair-dedup fired: bounced-pairs=%d died-pairs=%d (overlap %d)" % (
                len(pb), len(pd), len(pb & pd)))
        # lead times on bounced fires
        lead_conf = [(e["t_conf"] - e[tkey]) / 60.0 for e in fired_b if e["t_conf"]]
        lead_start = [(e["t_start"] - e[tkey]) / 60.0 for e in fired_b if e["t_start"]]
        off_low = [(e[tkey] - e["low_t"]) / 60.0 for e in fired_b]
        print("lead to +10%% confirm bar (min): %s" % dist(lead_conf))
        print("lead to +5%% start bar   (min): %s" % dist(lead_start))
        print("signal ts - flush-low ts (min): %s" % dist(off_low))
        neg = sum(1 for x in lead_conf if x <= 0)
        print("fires AT/AFTER confirm bar (lead<=0): %d/%d" % (neg, len(lead_conf)))
        # half split by sorted pair list
        pairs = sorted(set(e["pair8"] for e in inb))
        h1 = set(pairs[:len(pairs) // 2])
        for hname, hset in (("half-A", h1), ("half-B", set(pairs) - h1)):
            hb = [e for e in b_ev if e["pair8"] in hset]
            hd = [e for e in d_ev if e["pair8"] in hset]
            fb = [e for e in hb if e[tkey] is not None]
            fd = [e for e in hd if e[tkey] is not None]
            tot = len(fb) + len(fd)
            print("  %s: cover %s  FP %s  precision %s" % (
                hname, rate(hb, lambda e: e[tkey] is not None),
                rate(hd, lambda e: e[tkey] is not None),
                ("%d/%d=%.0f%%" % (len(fb), tot, 100.0 * len(fb) / tot)) if tot else "--"))
        return fired_b

    fb_g = block("ABSORBER (global candidate set -- CIRCULAR, upper bound)", "t_abs_g")
    fb_l = block("ABSORBER (leave-one-pair-out candidate set -- honest)", "t_abs_l")
    fb_a = block("ANONYMOUS COMPOSITION (n_buyers>=%d & max_print>=$%.0f, chronological cross)"
                 % (ANON_Y, ANON_X), "t_anon")

    # -------- head-to-head on events where BOTH fire
    print("\n=== HEAD-TO-HEAD (bounced events where both LOPO-absorber and anon fire) ===")
    both = [e for e in b_ev if e["t_abs_l"] is not None and e["t_anon"] is not None]
    if both:
        delta = [(e["t_anon"] - e["t_abs_l"]) / 60.0 for e in both]
        print("anon_ts - absorber_ts (min, + = absorber earlier): %s" % dist(delta))
        earlier = sum(1 for x in delta if x > 0)
        print("absorber strictly earlier: %d/%d" % (earlier, len(both)))
    only_abs = [e for e in b_ev if e["t_abs_l"] is not None and e["t_anon"] is None]
    only_anon = [e for e in b_ev if e["t_abs_l"] is None and e["t_anon"] is not None]
    print("bounced fired-by: both=%d  absorber-only=%d  anon-only=%d  neither=%d" % (
        len(both), len(only_abs), len(only_anon),
        len(b_ev) - len(both) - len(only_abs) - len(only_anon)))
    # died joint
    d_both = [e for e in d_ev if e["t_abs_l"] is not None and e["t_anon"] is not None]
    d_abs = [e for e in d_ev if e["t_abs_l"] is not None]
    d_anon = [e for e in d_ev if e["t_anon"] is not None]
    print("died fired-by: absorber=%d  anon=%d  both=%d  (of %d died)" % (
        len(d_abs), len(d_anon), len(d_both), len(d_ev)))

    # does absorber ADD to anon? conditional precision
    print("\n=== ADDITIVITY: bounce rate among anon-fired events, split by absorber presence ===")
    anon_fired = [e for e in inb if e["t_anon"] is not None]
    for nm, grp in (("anon+absorberLOPO", [e for e in anon_fired if e["t_abs_l"] is not None]),
                    ("anon only        ", [e for e in anon_fired if e["t_abs_l"] is None])):
        n = len(grp)
        b = sum(1 for e in grp if e["label"] == "BOUNCED")
        print("  %s  n=%3d  pairs=%2d  bounce=%5.1f%%" % (
            nm, n, len(set(e["pair8"] for e in grp)), 100.0 * b / n if n else float("nan")))


if __name__ == "__main__":
    main()
