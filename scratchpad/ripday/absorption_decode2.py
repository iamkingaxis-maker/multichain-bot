# -*- coding: ascii -*-
"""
FLUSH-ABSORPTION decode v2 -- reruns daily as tapes grow.

Data (all under scratchpad/ripday/):
  ohlc2_{pair8}.json               fresh minute bars {pair, n_bars, span, bars:[[epoch_s,o,h,l,c,vol],...]}
  tape_{pair8}.jsonl               harvest tapes (one trade/line)
  live_tapes/tape_{pair8}.jsonl    live tapes (may overlap harvest -> dedup)

Method:
  1. Flush events from ohlc2 closes: drop <= -15% vs rolling 60m close-high,
     with the high bar <= 30min before the trigger bar; 30min cooldown per pair.
  2. Label vs flush-low close:
       BOUNCED = some close >= low*1.10 within 60m of the low
       DIED    = no close >= low*1.05 within 90m AND close at +90m < low*1.02
       middle  = EXCLUDED (ambiguous); insufficient bars -> coverage-fail
  3. Tape join: window [low-10m, low+5m]; requires tape span to cover it.
  4. In-band test: depth in [-60%,-15%], exclude rug-class (<= -90% dd within
     90m of the low). Composition table + threshold sweep + day split.

Run: cd C:\\Users\\jcole\\multichain-bot && PYTHONPATH=. python scratchpad/ripday/absorption_decode2.py
No network. Read-only except stdout.
"""
import json, os, glob, sys
from datetime import datetime, timezone

RIP = os.path.join(os.path.dirname(os.path.abspath(__file__)))
KILL_PREFIXES = ("DJocqRPK", "7JCe3GHw", "DF8tRgFk", "AgmLJBMD", "2tgUbS9")

FLUSH_DROP = -0.15
ROLL_HIGH_S = 3600
FAST_S = 1800          # high->trigger max gap
LOW_SCAN_S = 1800      # trigger->low search window
COOLDOWN_S = 1800
BOUNCE_PCT = 0.10
BOUNCE_S = 3600
DIE_PCT = 0.05
DIE_S = 5400
DIE_CLOSE_PCT = 0.02
TAPE_PRE_S = 600
TAPE_POST_S = 300
BAND_MIN = -0.60       # depth floor (tradeable band)
RUG_DD = -0.90         # dd within 90m of low -> rug-class


def load_bars(path):
    d = json.load(open(path))
    bars = [(int(b[0]), float(b[4])) for b in d["bars"]]  # (ts, close)
    bars.sort()
    return d["pair"], bars


def find_flushes(bars):
    """Return list of flush events: dict(trigger_i, low_i, roll_high, depth)."""
    events = []
    n = len(bars)
    cooldown_until = -1
    i = 0
    while i < n:
        t, c = bars[i]
        if t < cooldown_until:
            i += 1
            continue
        # rolling 60m close-high over prior bars
        hi, hi_t = None, None
        j = i - 1
        while j >= 0 and bars[j][0] >= t - ROLL_HIGH_S:
            if hi is None or bars[j][1] > hi:
                hi, hi_t = bars[j][1], bars[j][0]
            j -= 1
        if hi is None or hi <= 0:
            i += 1
            continue
        drop = c / hi - 1.0
        if drop <= FLUSH_DROP and (t - hi_t) <= FAST_S:
            # flush low = min close in [t, t+LOW_SCAN_S]
            low_i = i
            k = i
            while k < n and bars[k][0] <= t + LOW_SCAN_S:
                if bars[k][1] < bars[low_i][1]:
                    low_i = k
                k += 1
            depth = bars[low_i][1] / hi - 1.0
            events.append({"trigger_i": i, "low_i": low_i,
                           "roll_high": hi, "depth": depth})
            cooldown_until = bars[low_i][0] + COOLDOWN_S
            i = low_i + 1
        else:
            i += 1
    return events


def label_event(bars, ev):
    """Return (label, min_dd_post) label in BOUNCED/DIED/EXCLUDED/COVERAGE_FAIL."""
    low_i = ev["low_i"]
    low_t, low_c = bars[low_i]
    hi = ev["roll_high"]
    post60 = [(t, c) for t, c in bars[low_i + 1:] if t <= low_t + BOUNCE_S]
    post90 = [(t, c) for t, c in bars[low_i + 1:] if t <= low_t + DIE_S]
    min_post = min([c for _, c in post90], default=low_c)
    min_dd_post = min(low_c, min_post) / hi - 1.0
    if any(c >= low_c * (1 + BOUNCE_PCT) for _, c in post60):
        return "BOUNCED", min_dd_post
    # DIED needs coverage out to ~+90m
    last_t = bars[-1][0]
    if last_t < low_t + DIE_S - 300:  # allow 5m slack
        return "COVERAGE_FAIL", min_dd_post
    if not any(c >= low_c * (1 + DIE_PCT) for _, c in post90):
        at90 = [c for t, c in post90 if t >= low_t + DIE_S - 900]  # last 15m of window
        close90 = at90[-1] if at90 else (post90[-1][1] if post90 else low_c)
        if close90 < low_c * (1 + DIE_CLOSE_PCT):
            return "DIED", min_dd_post
    return "EXCLUDED", min_dd_post


def load_tape(pair8):
    seen, trades = set(), []
    for path in (os.path.join(RIP, "tape_%s.jsonl" % pair8),
                 os.path.join(RIP, "live_tapes", "tape_%s.jsonl" % pair8)):
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            key = (r.get("ts"), r.get("maker"), r.get("volume_usd"), r.get("kind"))
            if key in seen:
                continue
            seen.add(key)
            try:
                ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
                epoch = ts.astimezone(timezone.utc).timestamp()
            except Exception:
                continue
            trades.append((epoch, r.get("kind"), float(r.get("volume_usd") or 0.0),
                           r.get("maker") or ""))
    trades.sort()
    return trades


def tape_features(trades, low_t):
    w0, w1 = low_t - TAPE_PRE_S, low_t + TAPE_POST_S
    if not trades or trades[0][0] > w0 or trades[-1][0] < w1:
        return None  # tape does not span the window
    win = [tr for tr in trades if w0 <= tr[0] <= w1]
    if not win:
        # a real -15% flush REQUIRES sell prints near the low; an empty
        # window inside a "covering" span = harvest gap, not a quiet tape.
        return "GAP"
    buys = [tr for tr in win if tr[1] == "buy"]
    sells = [tr for tr in win if tr[1] == "sell"]
    kill_buys = [tr for tr in buys if tr[3].startswith(KILL_PREFIXES)]
    real_buys = [tr for tr in buys if not tr[3].startswith(KILL_PREFIXES)]
    buy_usd_all = sum(tr[2] for tr in buys)
    sell_usd = sum(tr[2] for tr in sells)
    buy_usd = sum(tr[2] for tr in real_buys)
    return {
        "n_buyers": len(set(tr[3] for tr in real_buys)),
        "n_buys": len(real_buys),
        "max_print": max([tr[2] for tr in real_buys], default=0.0),
        "buy_usd": buy_usd,
        "sell_usd": sell_usd,
        "imbalance": (buy_usd / (buy_usd + sell_usd)) if (buy_usd + sell_usd) > 0 else 0.0,
        "kill_share": (sum(tr[2] for tr in kill_buys) / buy_usd_all) if buy_usd_all > 0 else 0.0,
        "buyer_makers": sorted({tr[3] for tr in real_buys if tr[3]}),
    }


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def dist_str(xs):
    return "med %8.2f  p25 %8.2f  p75 %8.2f" % (pct(xs, .5), pct(xs, .25), pct(xs, .75))


def main():
    ohlc_files = sorted(glob.glob(os.path.join(RIP, "ohlc2_*.json")))
    rows = []
    counts = {"pairs_with_bars": 0, "flush_events": 0, "label_coverage_fail": 0,
              "excluded_ambiguous": 0, "no_tape_coverage": 0, "tape_gap_in_window": 0}
    for path in ohlc_files:
        pair8 = os.path.basename(path)[6:-5]
        try:
            pair, bars = load_bars(path)
        except Exception:
            continue
        if len(bars) < 5:
            continue
        counts["pairs_with_bars"] += 1
        evs = find_flushes(bars)
        if not evs:
            continue
        trades = load_tape(pair8)
        for ev in evs:
            counts["flush_events"] += 1
            label, min_dd = label_event(bars, ev)
            if label == "COVERAGE_FAIL":
                counts["label_coverage_fail"] += 1
                continue
            if label == "EXCLUDED":
                counts["excluded_ambiguous"] += 1
                continue
            low_t = bars[ev["low_i"]][0]
            feats = tape_features(trades, low_t)
            if feats is None:
                counts["no_tape_coverage"] += 1
                continue
            if feats == "GAP":
                counts["tape_gap_in_window"] += 1
                continue
            day = datetime.fromtimestamp(low_t, timezone.utc).strftime("%Y-%m-%d")
            row = {"pair8": pair8, "day": day, "label": label,
                   "depth": ev["depth"], "min_dd_post": min_dd, "low_t": low_t}
            row.update(feats)
            rows.append(row)

    print("=== COVERAGE ===")
    print("ohlc2 files: %d   pairs with >=5 bars: %d" % (len(ohlc_files), counts["pairs_with_bars"]))
    print("flush events: %d" % counts["flush_events"])
    print("  label coverage-fail (bars end <+90m): %d" % counts["label_coverage_fail"])
    print("  excluded ambiguous middle:            %d" % counts["excluded_ambiguous"])
    print("  labeled but no tape span [-10m,+5m]:  %d" % counts["no_tape_coverage"])
    print("  tape spans but 0 trades in window (harvest gap, dropped): %d" % counts["tape_gap_in_window"])
    print("  labeled + tape-joined:                %d  (%d distinct pairs)" %
          (len(rows), len(set(r["pair8"] for r in rows))))

    rug = [r for r in rows if r["min_dd_post"] <= RUG_DD]
    inband = [r for r in rows if r["depth"] >= BAND_MIN and r["min_dd_post"] > RUG_DD]
    oob = [r for r in rows if r not in inband and r not in rug]
    print("\nband split: in-band(-15..-60, non-rug)=%d  rug-class(dd<=-90 within 90m)=%d  other-out-of-band=%d"
          % (len(inband), len(rug), len(oob)))

    for name, grp in (("IN-BAND", inband), ("RUG-CLASS (excluded from test)", rug)):
        b = [r for r in grp if r["label"] == "BOUNCED"]
        d = [r for r in grp if r["label"] == "DIED"]
        print("%s: BOUNCED=%d (%d pairs, %d days)  DIED=%d (%d pairs, %d days)" %
              (name, len(b), len(set(r['pair8'] for r in b)), len(set(r['day'] for r in b)),
               len(d), len(set(r['pair8'] for r in d)), len(set(r['day'] for r in d))))

    print("\n=== IN-BAND COMPOSITION (flush window [-10m,+5m], kill-list buyers excluded) ===")
    metrics = ["n_buyers", "n_buys", "max_print", "buy_usd", "sell_usd", "imbalance", "kill_share", "depth"]
    for lab in ("BOUNCED", "DIED"):
        grp = [r for r in inband if r["label"] == lab]
        print("-- %s (n=%d, %d pairs)" % (lab, len(grp), len(set(r["pair8"] for r in grp))))
        for m in metrics:
            print("   %-10s %s" % (m, dist_str([r[m] for r in grp])))

    print("\n=== THRESHOLD SWEEP (in-band; pass = max_print>=X AND n_buyers>=Y) ===")
    print("%-6s %-9s | %-28s | %-28s" % ("X", "Y", "PASS  br  n  pairs  days", "FAIL  br  n  pairs  days"))

    def cell(grp):
        n = len(grp)
        b = sum(1 for r in grp if r["label"] == "BOUNCED")
        return "%5.0f%% %4d %5d %5d" % (100.0 * b / n if n else float("nan"), n,
                                        len(set(r["pair8"] for r in grp)),
                                        len(set(r["day"] for r in grp)))
    for X in (25, 50, 75, 100, 150):
        for Y in (1, 3, 5, 10):
            p = [r for r in inband if r["max_print"] >= X and r["n_buyers"] >= Y]
            f = [r for r in inband if not (r["max_print"] >= X and r["n_buyers"] >= Y)]
            print("%-6d %-9d | %-28s | %-28s" % (X, Y, cell(p), cell(f)))

    print("\n=== DAY SPLIT (in-band, per event-day bounce rates) ===")
    days = sorted(set(r["day"] for r in inband))
    for day in days:
        grp = [r for r in inband if r["day"] == day]
        b = sum(1 for r in grp if r["label"] == "BOUNCED")
        print("  %s  n=%3d  pairs=%3d  bounce=%3.0f%%" %
              (day, len(grp), len(set(r["pair8"] for r in grp)), 100.0 * b / len(grp)))

    print("\n=== DAY-SPLIT SWEEP for representative gates ===")
    for X, Y in ((50, 3), (75, 5), (100, 5), (150, 3)):
        print("gate max_print>=%d & n_buyers>=%d:" % (X, Y))
        for day in days:
            grp = [r for r in inband if r["day"] == day]
            p = [r for r in grp if r["max_print"] >= X and r["n_buyers"] >= Y]
            f = [r for r in grp if not (r["max_print"] >= X and r["n_buyers"] >= Y)]
            def br(g):
                return ("%3.0f%% (n=%d,p=%d)" % (100.0 * sum(1 for r in g if r["label"] == "BOUNCED") / len(g),
                                                 len(g), len(set(r["pair8"] for r in g)))) if g else "  -- (n=0)"
            print("  %s  pass %s   fail %s" % (day, br(p), br(f)))

    # imbalance as alternate axis
    print("\n=== IMBALANCE SWEEP (in-band; pass = imbalance >= Z) ===")
    for Z in (0.3, 0.4, 0.5, 0.6):
        p = [r for r in inband if r["imbalance"] >= Z]
        f = [r for r in inband if r["imbalance"] < Z]
        print("Z=%.1f | PASS %-28s | FAIL %-28s" % (Z, cell(p), cell(f)))

    # dump rows for downstream use
    out = os.path.join(RIP, "_absorption2_rows.json")
    json.dump(rows, open(out, "w"), indent=0)
    print("\nrows dumped -> %s" % out)

    # ABSORBER LEDGER (2026-07-02): union-counted wallets present at flush-low
    # windows of IN-BAND events, split by outcome. Counting-trap rules: union
    # across events per pair, kill-list excluded upstream (real_buys only).
    # Candidate bar: >=3 DISTINCT bounced pairs AND >=85% bounce association.
    led_path = os.path.join(RIP, "absorber_ledger.json")
    try:
        led = json.load(open(led_path))
    except Exception:
        led = {}
    for r in inband:
        for mk in (r.get("buyer_makers") or []):
            w = led.setdefault(mk, {"bounced_pairs": [], "died_pairs": []})
            key = "bounced_pairs" if r["label"] == "BOUNCED" else "died_pairs"
            if r["pair8"] not in w[key]:
                w[key].append(r["pair8"])
    json.dump(led, open(led_path, "w"))
    cands = []
    for mk, w in led.items():
        b, d = len(w["bounced_pairs"]), len(w["died_pairs"])
        if b >= 3 and b / max(b + d, 1) >= 0.85:
            cands.append((b, d, mk))
    cands.sort(reverse=True)
    print("=== ABSORBER LEDGER: %d wallets tracked; candidates (>=3 bounced pairs, >=85%% assoc): %d ==="
          % (len(led), len(cands)))
    for b, d, mk in cands[:10]:
        print("  %s  bounced=%d died=%d" % (mk[:20], b, d))


if __name__ == "__main__":
    main()
