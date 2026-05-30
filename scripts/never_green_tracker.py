#!/usr/bin/env python
"""Never-green rate tracker — forward entry-quality monitor.

A loss is either a +EV bet that lost (variance) or a demand-less entry that never
bounced (a real signal leak). The discriminator is the NEVER-GREEN rate: of a
signal's closed positions, what fraction never reached +1% peak. Validated
dip/bottom signals should land ~20-35% never-green (the complement of their WR,
minus the ones that went green then gave back). A never-green rate that stays
>45-50% as n grows = the signal is firing on knives, not dips (a fixable upstream
leak — see feedback_no_bandaids). Built 2026-05-30 after tonight's losers were
3/3 never-green (ATTENTION/GACHA) while both winners went green.

Attributes never-green BOTH ways:
  * per BOT  — covers the gate-selection bots (whale_buyers = top_buy_makers_n
    gate; post_peak = time_since_h24_peak gate). The bot's never-green rate IS
    its gate's never-green rate.
  * per TRIGGER — a position counts against EVERY trigger it fired, so a trigger
    present on many never-greens is the suspect (same logic as trigger attribution).

Usage:
  python scripts/never_green_tracker.py                 # 3 new positive-selection bots
  python scripts/never_green_tracker.py --bots champion_premium,champion_defender_v4
  python scripts/never_green_tracker.py --all           # every bot
  python scripts/never_green_tracker.py --min-n 15      # flag threshold

No deploy impact — read-only pull of /api/trades?full=1.
"""
from __future__ import annotations
import argparse, json, sys, io, urllib.request
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
API = "https://gracious-inspiration-production.up.railway.app/api/trades?full=1&limit={n}"
NEW_BOTS = ["champion_premium", "champion_whale_buyers", "champion_post_peak"]
NG_PEAK = 1.0           # "green" = peak_pnl_pct >= 1%
WATCH_RATE = 40.0       # never-green % above this (at >=min_n) = watch
LEAK_RATE = 50.0        # never-green % above this (at >=min_n) = leak suspect


def pull(limit):
    req = urllib.request.Request(API.format(n=limit), headers={"User-Agent": "ngt"})
    d = json.load(urllib.request.urlopen(req, timeout=25))
    return d if isinstance(d, list) else d.get("trades", d.get("data", []))


def pair(trs, bots):
    """Pair buys->sells per (bot,token) into closed positions with peak + pnl."""
    sel = [t for t in trs if (bots is None or t.get("bot_id") in bots)]
    sel.sort(key=lambda t: t.get("time", ""))
    ob, closed, open_n = defaultdict(list), [], 0
    for t in sel:
        bot, tok, ty = t.get("bot_id"), t.get("token"), (t.get("type") or "").lower()
        if not bot or not tok:
            continue
        k = (bot, tok)
        if ty == "buy":
            em = t.get("entry_meta") or {}
            trg = t.get("triggers_fired") or em.get("triggers_fired") or []
            ob[k].append({"bot": bot, "trig": list(trg), "pnl": 0.0, "rem": 1.0, "peak": None})
        elif ty == "sell" and ob[k]:
            x = ob[k][0]
            x["pnl"] += float(t.get("pnl") or 0)
            pk = t.get("peak_pnl_pct")
            if pk is not None and (x["peak"] is None or float(pk) > x["peak"]):
                x["peak"] = float(pk)
            fr = t.get("sell_fraction")
            x["rem"] -= float(fr) if fr is not None else x["rem"]
            if t.get("fully_closed") or x["rem"] <= 0.01:
                closed.append(x)
                ob[k].pop(0)
    open_n = sum(len(v) for v in ob.values())
    return closed, open_n


def ng_rate(rows):
    have = [c for c in rows if c["peak"] is not None]
    if not have:
        return None, 0
    ng = sum(1 for c in have if c["peak"] < NG_PEAK)
    return 100.0 * ng / len(have), len(have)


def report(rows, key_fn, label, min_n):
    groups = defaultdict(list)
    for c in rows:
        for k in key_fn(c):
            groups[k].append(c)
    print(f"\n=== never-green rate per {label} ===")
    print(f"{label:30s}{'n':>4}{'NG%':>6}{'WR%':>6}{'$/tr':>8}  flag")
    out = []
    for k, g in groups.items():
        rate, n = ng_rate(g)
        if n == 0:
            continue
        wr = 100.0 * sum(1 for c in g if c["pnl"] > 0) / len(g)
        dpt = sum(c["pnl"] for c in g) / len(g)
        out.append((rate, n, wr, dpt, k))
    for rate, n, wr, dpt, k in sorted(out, key=lambda r: -r[0]):
        flag = ""
        if n >= min_n and rate > LEAK_RATE:
            flag = "** LEAK SUSPECT"
        elif n >= min_n and rate > WATCH_RATE:
            flag = "watch"
        elif n < min_n:
            flag = f"(n<{min_n}, provisional)"
        print(f"{str(k)[:30]:30s}{n:>4}{rate:>6.0f}{wr:>6.0f}{dpt:>+8.2f}  {flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bots", default=",".join(NEW_BOTS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--min-n", type=int, default=15)
    args = ap.parse_args()
    bots = None if args.all else set(b.strip() for b in args.bots.split(",") if b.strip())
    trs = pull(args.limit)
    closed, open_n = pair(trs, bots)
    scope = "ALL bots" if args.all else ",".join(sorted(bots))
    print(f"scope: {scope} | closed positions: {len(closed)} | still open: {open_n}")
    overall, n = ng_rate(closed)
    if overall is not None:
        wr = 100.0 * sum(1 for c in closed if c["pnl"] > 0) / len(closed)
        print(f"OVERALL never-green: {overall:.0f}% (n={n}) | WR {wr:.0f}% | "
              f"healthy band ~20-35%; >{LEAK_RATE:.0f}% at n>={args.min_n} = leak")
    report(closed, lambda c: [c["bot"]], "bot (gate)", args.min_n)
    report(closed, lambda c: c["trig"] or ["(no-trigger)"], "trigger", args.min_n)


if __name__ == "__main__":
    main()
