# -*- coding: utf-8 -*-
"""Measure the CLEAN post-fix meta_chameleon green-base default at n>=30 fresh closes.

Why: most of meta_chameleon's lifetime -$ predates today's fixes (wear-gate off 680de00,
green base, TP1-bank-100%, green-pond mcap_min null 14625fd). To judge whether the bot is
NOW profitable we must look ONLY at closes AFTER the last fix cut over, and compare to its
now-aligned static twin timebox_probe.

Cutoff is pinned in _chameleon_measure_since.txt (default = the 14625fd deploy, 2026-06-15T22:13Z).
Phantom-aware: the /api/trades feed 'pnl' (dollars) is contaminated (UATF +5569% bad-tick),
so we use pnl_pct and DROP phantom-suspect outliers (|pnl_pct|>300), reporting them separately.

Usage:  python scripts/measure_chameleon_green.py [--since 2026-06-15T22:13:50Z]
"""
import sys, json, urllib.request, statistics as st
from pathlib import Path

BASE = "https://gracious-inspiration-production.up.railway.app"
MARKER = Path(__file__).resolve().parent.parent / "_chameleon_measure_since.txt"
DEFAULT_SINCE = "2026-06-15T22:13:50Z"
BOTS = ["meta_chameleon", "timebox_probe"]
PHANTOM_ABS_PCT = 300.0  # |pnl_pct| beyond this = bad-tick/phantom, excluded + flagged
TARGET_N = 30


def _get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "measure/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    since = DEFAULT_SINCE
    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]
    elif MARKER.exists():
        since = MARKER.read_text().strip() or DEFAULT_SINCE
    if not MARKER.exists():
        MARKER.write_text(since)
    print(f"measuring fresh closes since {since}  (target n>={TARGET_N})\n")

    trades = _get("/api/trades?limit=5000")
    lb = {r["bot_id"]: r for r in _get("/api/leaderboard")}

    for bot in BOTS:
        sells = [t for t in trades if t.get("bot_id") == bot and t.get("type") == "sell"
                 and t.get("pnl_pct") is not None and t.get("time", "") >= since]
        clean = [t for t in sells if abs(t["pnl_pct"]) <= PHANTOM_ABS_PCT]
        phantom = [t for t in sells if abs(t["pnl_pct"]) > PHANTOM_ABS_PCT]
        n = len(clean)
        lbr = lb.get(bot, {})
        print(f"=== {bot} ===")
        print(f"  fresh clean closes: {n}" + (f"   (phantom-excluded: {len(phantom)} -> "
              f"{[round(t['pnl_pct']) for t in phantom]})" if phantom else ""))
        if n:
            pp = [t["pnl_pct"] for t in clean]
            wr = 100.0 * sum(1 for x in pp if x > 0) / n
            deep = sum(1 for x in pp if x <= -25)
            print(f"  WR={wr:.1f}%  mean_pnl%={st.mean(pp):+.2f}  median={st.median(pp):+.2f}"
                  f"  deep-loss(<=-25%)={deep} ({100*deep/n:.0f}%)")
        print(f"  leaderboard (lifetime, authoritative): realized=${lbr.get('realized_pnl_total_usd')}"
              f"  trades={lbr.get('total_trades')}  open={lbr.get('open_position_count')}")
        print(f"  -> {'VERDICT READY (n>=%d)' % TARGET_N if n >= TARGET_N else 'accumulating (need %d more)' % (TARGET_N - n)}\n")

    print("Re-run periodically. When meta_chameleon reaches n>=30 fresh: judge mean_pnl%/WR/deep-loss "
          "vs timebox_probe. Profitable + deep-loss<10% -> the green-base default works; else the "
          "gap-through detector (next build) is the lever.")


if __name__ == "__main__":
    main()
