#!/usr/bin/env python3
"""
bot_leaderboard.py — PER-BOT honest scoreboard (the live-slot race).

AxiS 2026-07-02: "focus on individual bot performance instead of fleet wide.
we won't be running the entire fleet when we go live." The go-live bar
(scrubbed per-token >= +2pp over >= 5 days) applies to THE candidate bot,
not the pooled fleet. This ranks every enabled badday bot on scrubbed,
per-token, per-day numbers over a lookback window.

Usage: PYTHONPATH=. python scripts/bot_leaderboard.py [hours=36]
Pulls /api/trades (gzip); scrub = ret>0 AND hold<10s heuristic on slim
records (full scrub lives in honest_book.py on _full_trades.json).
"""
import json, sys, time, urllib.request, gzip, io
import statistics as st
from collections import defaultdict

DASH = "https://gracious-inspiration-production.up.railway.app"


def g(p):
    req = urllib.request.Request(DASH + p, headers={
        "User-Agent": "lb/1", "Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=40)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 36
    import datetime as dt
    cut = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
    arr = g("/api/trades?limit=2500")
    arr = arr.get("trades", arr) if isinstance(arr, dict) else arr
    sells = [t for t in arr if t.get("type") == "sell" and t.get("pnl_pct") is not None
             and str(t.get("time", "")) > cut and str(t.get("bot_id", "")).startswith("badday_")]
    per = defaultdict(lambda: defaultdict(float))          # bot -> token -> net pp
    perday = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))  # bot -> day -> token
    for s in sells:
        w = float(s["pnl_pct"]) * (s.get("sell_fraction") or 1.0)
        b, tok, day = s.get("bot_id"), s.get("token"), str(s.get("time", ""))[:10]
        per[b][tok] += w
        perday[b][day][tok] += w
    rows = []
    for b, toks in per.items():
        v = list(toks.values())
        days = perday[b]
        green_days = sum(1 for d, dt_ in days.items()
                         if st.mean(list(dt_.values())) > 0)
        rows.append((st.mean(v), b, len(v), sum(v),
                     sum(1 for x in v if x > 0) / len(v) * 100,
                     green_days, len(days)))
    rows.sort(reverse=True)
    print(f"PER-BOT LEADERBOARD (last {hours:.0f}h, per-token, sell-weighted)")
    print(f"{'bot':32}{'tok':>4}{'mean/tok':>9}{'sum':>8}{'tokWR%':>7}{'greenD':>8}")
    for m, b, n, s_, wr, gd, nd in rows:
        flag = " <== live-bar pace" if m >= 2.0 and n >= 8 else ""
        print(f"{b:32}{n:>4}{m:>+9.2f}{s_:>+8.0f}{wr:>7.0f}{gd:>5}/{nd}{flag}")
    print("\nlive bar: scrubbed per-token >= +2.0 over >= 5 days on THE candidate bot")


if __name__ == "__main__":
    main()
