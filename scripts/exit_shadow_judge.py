#!/usr/bin/env python3
"""
exit_shadow_judge.py — one-command enforce verdicts for the fast-path exit
shadow instruments (2026-07-05).

Joins the shadow stamps that now flow onto SELL records (dfdc3d6) to their
legs' realized outcomes:
  tp1_ff_shadow_pnl        TP1_FASTFILL would-fire (pre-TP1 fresh touch)
  gb2_shadow_pnl_at_fire   GIVEBACK_TRAIL would-fire (pre-TP1 peak-2pp)
  trail_reprice_shadow_pnl POST_TP1_TRAIL fresh would-fire
  bel_shadow_pnl_at_fire   BREAKEVEN_LOCK would-fire (existing)

Per instrument: n fires, mean/median saved_pp (would-fire pnl minus realized),
how many fires sat on legs that ultimately WON anyway (the winner-hurt side),
and the verdict vs the standing bar (n>=30 fires AND mean saved > 0 with
winner-hurt < 50%% of saves).

Usage: PYTHONPATH=. python scripts/exit_shadow_judge.py [days=3]
Pulls per-bot full records (the stamps are top-level sell fields; the slim
global feed strips them).
"""
import json, sys, urllib.request, gzip, io
import statistics as st
from collections import defaultdict

DASH = "https://gracious-inspiration-production.up.railway.app"
BOTS = ("badday_flush", "badday_allday", "badday_flush_nf15", "badday_flush_rsi_ab",
        "badday_flush_wickride_ab", "badday_flush_wideexit_ab",
        "badday_young_absorb", "badday_adolescent_absorb", "badday_swing_latch")
INSTRUMENTS = {
    "tp1_fastfill": ("tp1_ff_shadow_pnl", 0.75),      # would sell tp1_fraction
    "giveback_trail": ("gb2_shadow_pnl_at_fire", 1.0),
    "trail_reprice": ("trail_reprice_shadow_pnl", 1.0),
    "breakeven_lock": ("bel_shadow_pnl_at_fire", 1.0),
}


def g(p):
    req = urllib.request.Request(DASH + p, headers={
        "User-Agent": "esj/1", "Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=60)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def main():
    days = float(sys.argv[1]) if len(sys.argv) > 1 else 3
    import datetime as dt
    cut = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
    rows = defaultdict(list)   # instrument -> [(saved_pp, realized, bot, token)]
    for bot in BOTS:
        try:
            arr = g(f"/api/bots/{bot}/trades?limit=600&meta_keys=_none_")
        except Exception as e:
            print(f"  ({bot}: pull failed {str(e)[:40]})")
            continue
        for t in arr:
            if t.get("type") != "sell" or t.get("pnl_pct") is None:
                continue
            if str(t.get("time", "")) < cut:
                continue
            realized = float(t["pnl_pct"])
            for name, (field, frac) in INSTRUMENTS.items():
                v = t.get(field)
                if isinstance(v, (int, float)):
                    # saved = what the instrument would have banked (on its
                    # fraction) minus what the leg realized
                    saved = (float(v) - realized) * frac
                    rows[name].append((saved, realized, bot, t.get("token")))
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(f"EXIT-SHADOW JUDGE (last {days:g}d, per-leg joins)")
    print(f"{'instrument':16} {'n':>4} {'meanSave':>9} {'medSave':>8} {'hurtWinners':>12}  verdict")
    for name in INSTRUMENTS:
        v = rows.get(name) or []
        if not v:
            print(f"{name:16} {'0':>4}  (no stamped legs yet)")
            continue
        saves = [x[0] for x in v]
        hurt = sum(1 for s, r, _, _ in v if r > 0 and s < 0)
        helps = sum(1 for s in saves if s > 0)
        bar = len(v) >= 30 and st.mean(saves) > 0 and (hurt == 0 or hurt < 0.5 * max(helps, 1))
        print(f"{name:16} {len(v):>4} {st.mean(saves):>+9.2f} {st.median(saves):>+8.2f} "
              f"{hurt:>5}/{len(v):<6} {'ENFORCE-READY' if bar else 'accrue'}")
        worst = sorted(v)[:3]
        best = sorted(v, reverse=True)[:3]
        print(f"  best saves : {[(x[3], round(x[0],1)) for x in best]}")
        print(f"  worst hurts: {[(x[3], round(x[0],1)) for x in worst]}")
    print("\nbar: n>=30 AND mean save>0 AND hurt-winners < 50% of helps")


if __name__ == "__main__":
    main()
