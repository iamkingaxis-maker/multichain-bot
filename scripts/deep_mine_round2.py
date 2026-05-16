"""Round 2 deep mining — looser definitions, focus on actionable cohorts.

Stages:
  A. Loose-survivor mining (peak>=+5 AND exit>=-5 — captures more
     "decent outcome" trades, not just strict survivors).
  B. exit_pct regression mining — find features that correlate with
     realized P&L, not just binary outcome.
  C. CT-hour × structural feature compounds — does hot hour + signature
     yield 60%+ WR cohorts?
  D. micro-cap (mcap <= 88,901) sub-mining — is there a sub-compound
     within micro-cap that gets to 60%+?
  E. Cross-validation on our actual trades — do universe findings
     hold up on our 88-trade live cohort?
"""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.request
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"


def load_universe(path="universe_fresh.json"):
    events = json.loads(Path(path).read_text())
    for e in events:
        iso = e.get("detected_at_iso") or ""
        try:
            s = iso.replace("Z", "+00:00") if "Z" in iso else iso
            pdt = dt.datetime.fromisoformat(s)
            ct = pdt - dt.timedelta(hours=5)
            e["_hour_ct"] = ct.hour
        except Exception:
            pass
    return events


def is_loose_winner(e):
    """Looser definition: hit at least +5 peak AND didn't dump past -5"""
    p = e.get("peak_pct")
    x = e.get("exit_pct")
    return (isinstance(p, (int, float)) and p >= 5.0
            and isinstance(x, (int, float)) and x >= -5.0)


def is_clear_loser(e):
    x = e.get("exit_pct")
    return isinstance(x, (int, float)) and x <= -15.0


def get_val(e, k):
    v = e.get(k)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    return None


def stage_a_loose_compounds(events):
    print(f"=== Stage A: Loose-winner compound search ===")
    win = sum(1 for e in events if is_loose_winner(e))
    lose = sum(1 for e in events if is_clear_loser(e))
    total = win + lose
    base = win / total if total else 0
    print(f"  Loose winners (peak>=+5 AND exit>=-5):  {win}")
    print(f"  Clear losers (exit<=-15):                {lose}")
    print(f"  Baseline (of those classified):          {base*100:.0f}%")

    # Top 15 features by cohen's d
    feats = [k for k in events[0].keys()
             if isinstance(events[0].get(k), (int, float))
             and k not in {"peak_pct", "exit_pct", "_hour_ct", "vol_at_event",
                            "high_at_event", "low_at_event", "open_at_event",
                            "close_at_event", "entry_price",
                            "event_ts", "outcome_at_ts", "n_post_candles"}]
    discs = []
    for f in feats:
        a = [get_val(e, f) for e in events if is_loose_winner(e) and get_val(e, f) is not None]
        b = [get_val(e, f) for e in events if is_clear_loser(e) and get_val(e, f) is not None]
        if len(a) < 10 or len(b) < 10: continue
        ma, mb = sum(a)/len(a), sum(b)/len(b)
        va = sum((x-ma)**2 for x in a)/(len(a)-1)
        vb = sum((x-mb)**2 for x in b)/(len(b)-1)
        pooled = math.sqrt((va+vb)/2)
        if pooled == 0: continue
        d = (ma - mb) / pooled
        if abs(d) < 0.2: continue
        # Threshold
        vals = sorted(a + b)
        cut = vals[int(len(vals) * (0.5 if d > 0 else 0.5))]  # median split
        discs.append({"feat": f, "d": d, "cut": cut,
                      "direction": ">=" if d > 0 else "<="})
    discs.sort(key=lambda x: -abs(x["d"]))
    pool = discs[:12]
    print(f"  Top feature directions for compound search:")
    for d in pool:
        print(f"    {d['feat']:<22} {d['direction']} d={d['d']:+.2f}")

    # Smart threshold optimization: for each feature, find the THRESHOLD
    # that maximizes precision × log(n) on the loose-winner cohort.
    smart_cuts = []
    for d in pool:
        f = d["feat"]
        direction = d["direction"]
        vals = sorted([get_val(e, f) for e in events
                       if (is_loose_winner(e) or is_clear_loser(e))
                       and get_val(e, f) is not None])
        if len(vals) < 50: continue
        best = None
        for pct in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
            cut = vals[int(len(vals) * pct)]
            if direction == ">=":
                w = sum(1 for e in events if is_loose_winner(e) and get_val(e, f) is not None and get_val(e, f) >= cut)
                l = sum(1 for e in events if is_clear_loser(e) and get_val(e, f) is not None and get_val(e, f) >= cut)
            else:
                w = sum(1 for e in events if is_loose_winner(e) and get_val(e, f) is not None and get_val(e, f) <= cut)
                l = sum(1 for e in events if is_clear_loser(e) and get_val(e, f) is not None and get_val(e, f) <= cut)
            n = w + l
            if n < 30: continue
            wr = w / n
            score = wr * math.log(n)
            if best is None or score > best["score"]:
                best = {"feat": f, "direction": direction, "cut": cut,
                        "n": n, "wr": wr, "w": w, "l": l, "score": score}
        if best:
            smart_cuts.append(best)
    smart_cuts.sort(key=lambda x: -x["score"])

    # Compound search using smart cuts
    print(f"\n  Smart single-feature cuts:")
    print(f"  {'Feature':<24} {'dir':>3} {'cut':>10} {'n':>4} {'wr':>5} ")
    for c in smart_cuts[:15]:
        print(f"  {c['feat']:<24} {c['direction']:>3} {c['cut']:>+9.2f} {c['n']:>4} {c['wr']*100:>4.0f}%")

    # 2-3 way compound
    print(f"\n  Pareto compound search (k=2,3):")
    candidates = []
    for k in (2, 3):
        for combo in combinations(smart_cuts[:10], k):
            def match(e, combo=combo):
                for c in combo:
                    v = get_val(e, c["feat"])
                    if v is None: return False
                    if c["direction"] == ">=":
                        if v < c["cut"]: return False
                    else:
                        if v > c["cut"]: return False
                return True
            w = sum(1 for e in events if is_loose_winner(e) and match(e))
            l = sum(1 for e in events if is_clear_loser(e) and match(e))
            n = w + l
            if n < 30: continue
            wr = w / n
            # Also compute avg_exit on the matched cohort (incl. all events, not just classified)
            full = [e for e in events if match(e)]
            full_exit = [e.get("exit_pct", 0) for e in full if isinstance(e.get("exit_pct"), (int, float))]
            avg_exit = sum(full_exit) / len(full_exit) if full_exit else 0
            label = " AND ".join(f"{c['feat']}{c['direction']}{c['cut']:.3g}" for c in combo)
            candidates.append({
                "k": k, "label": label, "n": n, "w": w, "l": l, "wr": wr,
                "avg_exit": avg_exit, "match_n": len(full),
            })
    candidates.sort(key=lambda c: -c["wr"])
    print(f"  {'k':>1} {'n_clf':>5} {'wr':>5} {'match_n':>7} {'avg_exit':>9} {'compound':<74}")
    shown = 0
    seen = set()
    for c in candidates:
        if c["wr"] < 0.55: continue
        feats_in = frozenset(p.split(">")[0].split("<")[0] for p in c["label"].split(" AND "))
        if feats_in in seen: continue
        seen.add(feats_in)
        print(f"  {c['k']:>1} {c['n']:>5} {c['wr']*100:>4.0f}% {c['match_n']:>7} {c['avg_exit']:>+7.1f}% {c['label'][:74]}")
        shown += 1
        if shown >= 25: break
    return candidates


def stage_b_exit_regression(events):
    print(f"\n=== Stage B: exit_pct correlation mining ===")
    # For each feature, compute Pearson-like correlation with exit_pct
    feats = [k for k in events[0].keys()
             if isinstance(events[0].get(k), (int, float))
             and k not in {"peak_pct", "exit_pct", "_hour_ct", "vol_at_event",
                            "high_at_event", "low_at_event", "open_at_event",
                            "close_at_event", "entry_price",
                            "event_ts", "outcome_at_ts", "n_post_candles"}]
    results = []
    for f in feats:
        paired = [(get_val(e, f), e.get("exit_pct", 0))
                  for e in events
                  if get_val(e, f) is not None
                  and isinstance(e.get("exit_pct"), (int, float))]
        if len(paired) < 100: continue
        xs = [p[0] for p in paired]; ys = [p[1] for p in paired]
        n = len(xs)
        mx = sum(xs)/n; my = sum(ys)/n
        num = sum((x-mx)*(y-my) for x, y in paired)
        denx = math.sqrt(sum((x-mx)**2 for x in xs))
        deny = math.sqrt(sum((y-my)**2 for y in ys))
        if denx == 0 or deny == 0: continue
        r = num / (denx * deny)
        if abs(r) < 0.05: continue
        results.append({"feat": f, "r": r, "n": n})
    results.sort(key=lambda x: -abs(x["r"]))
    print(f"  {'Feature':<24} {'r':>6} {'n':>5}")
    for r in results[:15]:
        print(f"  {r['feat']:<24} {r['r']:>+5.3f} {r['n']:>5}")


def stage_c_hour_compounds(events):
    print(f"\n=== Stage C: CT-hour × feature compounds ===")
    HOT_HRS = [5, 23, 22, 2]
    DEAD_HRS = [7, 8, 9, 0]
    base = sum(1 for e in events if is_loose_winner(e)) / sum(1 for e in events if is_loose_winner(e) or is_clear_loser(e))
    print(f"  Baseline (loose-winner share of classified): {base*100:.0f}%")
    # Hot hours × top discriminators
    for label, hrs in [("HOT (CT 22-23+02+05)", HOT_HRS), ("DEAD (CT 7-9+0)", DEAD_HRS)]:
        sub = [e for e in events if e.get("_hour_ct") in hrs]
        print(f"\n  {label}: n={len(sub)} events")
        for feat, direction, cut, desc in [
            ("mcap", "<=", 88901, "micro-cap"),
            ("liq_usd", "<=", 31350, "low-liq"),
            ("vol_h1", "<=", 50000, "low-vol-h1"),
            ("pc_h6", "<=", 50, "calm-h6"),
            ("age_hours", "<=", 25.72, "young"),
        ]:
            def m(e):
                v = get_val(e, feat);
                if v is None: return False
                return (v >= cut) if direction == ">=" else (v <= cut)
            matched = [e for e in sub if m(e)]
            if len(matched) < 15: continue
            w = sum(1 for e in matched if is_loose_winner(e))
            l = sum(1 for e in matched if is_clear_loser(e))
            tot = w + l
            wr = w / tot if tot else 0
            avg_exit = sum(e.get("exit_pct", 0) for e in matched) / len(matched)
            print(f"    {desc:<14}  match_n={len(matched):>3}  clf_n={tot:>3}  wr={wr*100:>3.0f}%  avg_exit={avg_exit:>+5.1f}%")


def stage_d_microcap_submine(events):
    print(f"\n=== Stage D: Micro-cap (mcap<=88,901) sub-mining ===")
    bucket = [e for e in events if isinstance(e.get("mcap"), (int, float)) and e["mcap"] <= 88901]
    print(f"  Micro-cap bucket: n={len(bucket)}")
    win = [e for e in bucket if is_loose_winner(e)]
    lose = [e for e in bucket if is_clear_loser(e)]
    print(f"  Loose winners: {len(win)}  Clear losers: {len(lose)}")
    base = len(win) / (len(win) + len(lose)) if (len(win) + len(lose)) else 0
    print(f"  In-bucket baseline: {base*100:.0f}%")
    # Top discriminators within micro-cap
    feats = [k for k in bucket[0].keys()
             if isinstance(bucket[0].get(k), (int, float))
             and k not in {"peak_pct", "exit_pct", "_hour_ct", "mcap", "fdv",
                            "vol_at_event", "high_at_event", "low_at_event",
                            "open_at_event", "close_at_event", "entry_price",
                            "event_ts", "outcome_at_ts", "n_post_candles"}]
    results = []
    for f in feats:
        a = [get_val(e, f) for e in win if get_val(e, f) is not None]
        b = [get_val(e, f) for e in lose if get_val(e, f) is not None]
        if len(a) < 10 or len(b) < 10: continue
        ma, mb = sum(a)/len(a), sum(b)/len(b)
        va = sum((x-ma)**2 for x in a)/(len(a)-1) if len(a) > 1 else 0
        vb = sum((x-mb)**2 for x in b)/(len(b)-1) if len(b) > 1 else 0
        pooled = math.sqrt((va+vb)/2)
        if pooled == 0: continue
        d = (ma - mb) / pooled
        if abs(d) < 0.3: continue
        results.append({"feat": f, "d": d,
                        "win_med": sorted(a)[len(a)//2],
                        "lose_med": sorted(b)[len(b)//2]})
    results.sort(key=lambda x: -abs(x["d"]))
    print(f"\n  Top within-bucket discriminators:")
    for r in results[:10]:
        print(f"    {r['feat']:<22} d={r['d']:+.2f}  win_med={r['win_med']:>+9.2g}  lose_med={r['lose_med']:>+9.2g}")


def stage_e_validate_on_trades(top_universe_compounds):
    print(f"\n=== Stage E: Cross-validate top compounds on our actual trades ===")
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000") as r:
            trades = json.loads(r.read())
    except Exception as e:
        print(f"  Could not fetch trades: {e}")
        return
    by_key = defaultdict(list)
    for t in trades:
        if t.get("strategy") not in ("dip_buy", "scanner"): continue
        key = (t.get("token"), round(t.get("entry_price", 0), 10))
        by_key[key].append(t)
    pairs = []
    for k, ev in by_key.items():
        bs = [e for e in ev if e.get("type") == "buy"]
        ss = [e for e in ev if e.get("type") == "sell"]
        if not bs or not ss: continue
        buy = bs[0]
        ss.sort(key=lambda x: x.get("time", ""))
        last = ss[-1]
        em = buy.get("entry_meta", {}) or {}
        pairs.append({
            "token": k[0],
            "won": (last.get("pnl_pct") or 0) > 0,
            "pnl": last.get("pnl_pct") or 0,
            "peak": last.get("peak_pnl_pct") or 0,
            "mcap": em.get("entry_market_cap_usd") or em.get("mcap"),
            "liq_usd": em.get("liq_usd"),
            "vol_h1": em.get("vol_h1") or em.get("entry_volume_h1_usd"),
            "pc_h6": em.get("pc_h6"),
            "pc_h24": em.get("pc_h24"),
            "age_hours": em.get("entry_age_hours"),
            "buys_h1": em.get("buys_h1"),
        })
    print(f"  Live trade cohort: {len(pairs)}")
    # Test: mcap <= 88,901
    sub = [p for p in pairs if isinstance(p["mcap"], (int, float)) and p["mcap"] <= 88901]
    if sub:
        w = sum(1 for p in sub if p["won"])
        print(f"\n  mcap <= 88,901:        n={len(sub):>3}  WR={w/len(sub)*100:>3.0f}%  avg_pnl={sum(p['pnl'] for p in sub)/len(sub):+.2f}%")
    sub = [p for p in pairs if isinstance(p["liq_usd"], (int, float)) and p["liq_usd"] <= 31350]
    if sub:
        w = sum(1 for p in sub if p["won"])
        print(f"  liq_usd <= 31,350:     n={len(sub):>3}  WR={w/len(sub)*100:>3.0f}%  avg_pnl={sum(p['pnl'] for p in sub)/len(sub):+.2f}%")
    sub = [p for p in pairs if isinstance(p["age_hours"], (int, float)) and p["age_hours"] <= 25.72]
    if sub:
        w = sum(1 for p in sub if p["won"])
        print(f"  age_hours <= 25.7:     n={len(sub):>3}  WR={w/len(sub)*100:>3.0f}%  avg_pnl={sum(p['pnl'] for p in sub)/len(sub):+.2f}%")
    # Hot-hour
    def parse_iso(s):
        s = s.replace("Z","+00:00") if "Z" in s else s
        return dt.datetime.fromisoformat(s)
    # The hour is on the buy time. Need to fetch buy times.
    for t in trades:
        if t.get("type") == "buy":
            key = (t.get("token"), round(t.get("entry_price", 0), 10))
            for p in pairs:
                if p["token"] == key[0]:
                    try:
                        d = parse_iso(t["time"])
                        p["_hr"] = (d - dt.timedelta(hours=5)).hour
                    except: pass
                    break
    HOT = {5, 22, 23, 2}
    sub = [p for p in pairs if p.get("_hr") in HOT]
    if sub:
        w = sum(1 for p in sub if p["won"])
        print(f"  CT hour in HOT (5/22/23/2):  n={len(sub):>3}  WR={w/len(sub)*100:>3.0f}%  avg_pnl={sum(p['pnl'] for p in sub)/len(sub):+.2f}%")
    DEAD = {7, 8, 9, 0}
    sub = [p for p in pairs if p.get("_hr") in DEAD]
    if sub:
        w = sum(1 for p in sub if p["won"])
        print(f"  CT hour in DEAD (0/7/8/9):    n={len(sub):>3}  WR={w/len(sub)*100:>3.0f}%  avg_pnl={sum(p['pnl'] for p in sub)/len(sub):+.2f}%")


if __name__ == "__main__":
    events = load_universe()
    print(f"Loaded {len(events)} events\n")
    compounds = stage_a_loose_compounds(events)
    stage_b_exit_regression(events)
    stage_c_hour_compounds(events)
    stage_d_microcap_submine(events)
    stage_e_validate_on_trades(compounds)
