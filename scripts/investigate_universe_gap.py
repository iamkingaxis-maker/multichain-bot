"""Why doesn't the universe-gold cohort translate to our live trades?

Three hypotheses to test:
  H1. Our scanner gates filter out the gold cohort. (would_pass_scanner)
  H2. The gold cohort exists in universe but is rare in our scan window
      (sampling/timing).
  H3. Of the gold-cohort events that PASS scanner, downstream filters or
      triggers reject them.

Approach:
  1. For each universe-gold cohort, count how many would pass each
     scanner gate (vol_h1, red_h24, no_real_dip, peak1000).
  2. Of the live trades we have, count how many fall in each gold cohort
     (using entry_meta fields). Compare WR.
  3. Estimate what fraction of universe gold makes it through.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"


def load_universe(path="universe_fresh.json"):
    events = json.loads(Path(path).read_text())
    for e in events:
        iso = e.get("detected_at_iso") or ""
        try:
            s = iso.replace("Z", "+00:00") if "Z" in iso else iso
            pdt = dt.datetime.fromisoformat(s)
            e["_hour_ct"] = (pdt - dt.timedelta(hours=5)).hour
        except Exception:
            pass
    return events


# Scanner gates — matches scanner_block_reasons in live_forward_test.py
def scanner_gate_status(e):
    """Returns dict {gate_name: pass/fail}. Returns 'PASS' overall in
    the 'overall' key if all gates pass."""
    out = {}
    out["vol_h1>=10k"] = isinstance(e.get("vol_h1"), (int, float)) and e["vol_h1"] >= 10000
    out["red_h24"] = isinstance(e.get("pc_h24"), (int, float)) and e["pc_h24"] > 0
    pcm5 = e.get("pc_m5"); pch1 = e.get("pc_h1")
    if isinstance(pcm5, (int, float)) and isinstance(pch1, (int, float)):
        out["real_dip"] = pcm5 <= -3 or pch1 <= -3
    else:
        out["real_dip"] = False
    # peak1000 — not in universe data, assume pass
    out["overall"] = out["vol_h1>=10k"] and out["red_h24"] and out["real_dip"]
    return out


# Universe-gold compounds
def in_calm_seller(e):
    s = e.get("sells_h1"); m = e.get("mcap")
    return (isinstance(s, (int, float)) and s <= 411
            and isinstance(m, (int, float)) and m >= 531083)


def in_hot_microcap(e):
    m = e.get("mcap"); h = e.get("_hour_ct")
    return (isinstance(m, (int, float)) and m <= 88901
            and h in {5, 22, 23, 2})


def in_calm_hot(e):
    h = e.get("_hour_ct"); p6 = e.get("pc_h6")
    return (h in {5, 22, 23, 2}
            and isinstance(p6, (int, float)) and p6 <= 50)


COMPOUNDS = [
    ("calm_seller (sells_h1≤411 AND mcap≥$531k)", in_calm_seller),
    ("hot_microcap (mcap≤$88,901 AND CT∈{5,22,23,2})", in_hot_microcap),
    ("calm_hot (CT∈{5,22,23,2} AND pc_h6≤+50%)", in_calm_hot),
]


def is_loose_winner(e):
    p = e.get("peak_pct"); x = e.get("exit_pct")
    return (isinstance(p, (int, float)) and p >= 5.0
            and isinstance(x, (int, float)) and x >= -5.0)


def main():
    events = load_universe()
    print(f"Universe events: {len(events)}\n")

    # ── H1: Scanner gate analysis per compound ──────────────────────
    print(f"=== H1: Scanner gates per universe-gold compound ===")
    print(f"  {'Compound':<48} {'matches':>8} {'pass_scan':>10} {'fail_vol':>9} "
          f"{'fail_red':>9} {'fail_dip':>9}")
    for label, pred in COMPOUNDS:
        matched = [e for e in events if pred(e)]
        gate_status = [scanner_gate_status(e) for e in matched]
        n = len(matched)
        if n == 0:
            print(f"  {label[:46]:<48}    n=0")
            continue
        passed = sum(1 for g in gate_status if g["overall"])
        fail_vol = sum(1 for g in gate_status if not g["vol_h1>=10k"])
        fail_red = sum(1 for g in gate_status if not g["red_h24"])
        fail_dip = sum(1 for g in gate_status if not g["real_dip"])
        print(f"  {label[:46]:<48} {n:>8} {passed:>8} ({passed/n*100:>3.0f}%) "
              f"{fail_vol:>8} {fail_red:>8} {fail_dip:>8}")

    # ── For "PASS scanner" subset of each compound, what's the WR? ──
    print(f"\n=== Survivor rate per compound (passes scanner only) ===")
    print(f"  {'Compound':<48} {'pass_n':>7} {'loose_W%':>10} {'avg_exit':>9}")
    for label, pred in COMPOUNDS:
        matched = [e for e in events if pred(e) and scanner_gate_status(e)["overall"]]
        if not matched:
            print(f"  {label[:46]:<48}  n=0")
            continue
        wins = sum(1 for e in matched if is_loose_winner(e))
        avg = sum(e.get("exit_pct", 0) for e in matched) / len(matched)
        print(f"  {label[:46]:<48} {len(matched):>7} {wins/len(matched)*100:>8.0f}% "
              f"{avg:>+7.1f}%")

    # ── H2/H3: Live trade cohort cross-reference ───────────────────
    print(f"\n=== Live trade cohort match against gold compounds ===")
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000") as r:
            trades = json.loads(r.read())
    except Exception as e:
        print(f"  fetch err: {e}")
        return

    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - 14*24*3600
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
        d = buy.get("time", "").replace("Z", "+00:00") if "Z" in buy.get("time", "") else buy.get("time", "")
        try:
            buy_dt = dt.datetime.fromisoformat(d)
            if buy_dt.timestamp() < cutoff: continue
        except: continue
        ss.sort(key=lambda x: x.get("time", ""))
        last = ss[-1]
        em = buy.get("entry_meta", {}) or {}
        # Live-data fields: entry_meta has e.g. entry_market_cap_usd (= mcap),
        # sells in last hour may not be directly stamped — need to derive
        pairs.append({
            "token": k[0],
            "won": (last.get("pnl_pct") or 0) > 0,
            "pnl": last.get("pnl_pct") or 0,
            "peak": last.get("peak_pnl_pct") or 0,
            "buy_dt": buy_dt,
            "_hr": (buy_dt - dt.timedelta(hours=5)).hour,
            "mcap": em.get("entry_market_cap_usd") or em.get("mcap"),
            "sells_h1": em.get("sells_h1"),
            "pc_h6": em.get("pc_h6"),
            "pc_h24": em.get("pc_h24"),
            "vol_h1": em.get("vol_h1") or em.get("entry_volume_h1_usd"),
        })
    print(f"  Live trades (14d): {len(pairs)}")
    # Per compound (live cohort)
    print(f"\n  {'Compound':<46} {'matches':>8} {'wins':>5} {'WR':>4} {'avg_pnl':>8}")
    def stats(label, sub):
        if not sub:
            print(f"  {label[:44]:<46} n=0")
            return
        w = sum(1 for p in sub if p["won"])
        ap = sum(p["pnl"] for p in sub) / len(sub)
        print(f"  {label[:44]:<46} {len(sub):>8} {w:>5} {w/len(sub)*100:>3.0f}% {ap:>+6.2f}%")
    # calm_seller
    sub = [p for p in pairs
           if isinstance(p["sells_h1"], (int, float)) and p["sells_h1"] <= 411
           and isinstance(p["mcap"], (int, float)) and p["mcap"] >= 531083]
    stats("calm_seller (sells_h1≤411 AND mcap≥531k)", sub)
    # hot_microcap
    sub = [p for p in pairs
           if isinstance(p["mcap"], (int, float)) and p["mcap"] <= 88901
           and p["_hr"] in {5, 22, 23, 2}]
    stats("hot_microcap (mcap≤88,901 AND CT∈{5,22,23,2})", sub)
    # calm_hot
    sub = [p for p in pairs
           if p["_hr"] in {5, 22, 23, 2}
           and isinstance(p["pc_h6"], (int, float)) and p["pc_h6"] <= 50]
    stats("calm_hot (CT∈hot AND pc_h6≤+50%)", sub)

    # Coverage check on sells_h1 specifically
    sells_h1_pop = sum(1 for p in pairs if isinstance(p["sells_h1"], (int, float)))
    print(f"\n  sells_h1 populated in entry_meta: {sells_h1_pop}/{len(pairs)}")
    # Maybe it's under a different key — check txns_h1.sells or just b_h1/s_h1
    for t in trades[:5]:
        em = t.get("entry_meta", {}) or {}
        if not em: continue
        # Look for sell-related keys
        sell_keys = [k for k in em.keys() if "sell" in k.lower() and "h1" in k.lower()]
        if sell_keys:
            print(f"  Trade {t.get('token')} sell_h1 keys: {sell_keys}")
            break

    # ── Compound-vs-baseline live cohort ────────────────────────────
    print(f"\n=== Live cohort baseline ===")
    if pairs:
        w = sum(1 for p in pairs if p["won"])
        ap = sum(p["pnl"] for p in pairs) / len(pairs)
        print(f"  All 14d trades:  n={len(pairs)}  WR={w/len(pairs)*100:.0f}%  "
              f"avg_pnl={ap:+.2f}%")

    # ── Universe events that DO pass scanner AND match gold compound ─
    # — what's their TIME distribution? Do they happen when our bot scans?
    print(f"\n=== Universe gold-AND-pass-scanner events: time distribution ===")
    for label, pred in COMPOUNDS:
        matched = [e for e in events if pred(e) and scanner_gate_status(e)["overall"]]
        if len(matched) < 5: continue
        hours = defaultdict(int)
        for e in matched:
            h = e.get("_hour_ct")
            if h is not None:
                hours[h] += 1
        print(f"\n  {label[:50]}: n={len(matched)}")
        for h in sorted(hours):
            print(f"    CT{h:>2}: {hours[h]}")


if __name__ == "__main__":
    main()
