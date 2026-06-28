#!/usr/bin/env python3
"""
analyze_live_faithful_pnl.py  — MEASUREMENT ONLY (read-only, no deploy, no behavior change)

CLI front-end over core.live_faithful_pnl.compute_live_faithful. Quantifies the
live-vs-paper FIDELITY GAP created by two real-money capital guards that ENFORCE only on
live_probe bots but merely SHADOW-log on paper twins:

  1. per-bot daily-loss halt        (entry_meta key: daily_halt_would_block)
  2. per-day per-token re-entry cap  (entry_meta key: reentry_cap_would_block)

See feeds/dip_scanner.py ~2136-2153:
    _do_block = _live_probe_bot and _dl_cfg is not None   # daily halt: live only
    _do_block = (_rf_enforce or _live_probe_bot) and ...   # reentry cap: live only (unless RISK_FLOOR_MODE=enforce)

Because paper twins reach the same code but DON'T return, paper books trades a funded
live bot would NEVER take. Those buys are stamped daily_halt_would_block / reentry_cap_would_block
= True. Removing them reconstructs the P&L a live-faithful bot would have realized.

The pairing/flag logic now lives in core/live_faithful_pnl.py (importable, side-effect-free,
also served live at the dashboard /api/live-faithful-pnl). This script just loads the ledger
file and pretty-prints the result.
"""
import json
import os
import sys

# Allow running directly from scripts/ without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.live_faithful_pnl import (  # noqa: E402
    compute_live_faithful, DAILY_KEY, REENTRY_KEY,
)

PATH = sys.argv[1] if len(sys.argv) > 1 else "_full_trades.json"


def _fmt(x, nd=2):
    return "   nan" if x is None else f"{x:.{nd}f}"


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    res = compute_live_faithful(data)
    fleet = res["fleet"]
    meta = res["meta"]
    per_bot = res["per_bot"]

    buys = [r for r in data if r.get("type") == "buy"]

    print("=" * 78)
    print("STEP 1 — would-block flag prevalence (all buys)")
    print("=" * 78)
    for k in (DAILY_KEY, REENTRY_KEY):
        from collections import Counter
        c = Counter(b.get("entry_meta", {}).get(k) for b in buys)
        print(f"  {k:28s}: {dict(c)}")
    print(f"  total buys={meta['n_buys']}  total sells={meta['n_sells']}")
    print(f"  window: {meta['window_start']}  -->  {meta['window_end']}")

    print()
    print("=" * 78)
    print("STEP 2/3 — pairing results")
    print("=" * 78)
    print(f"  closed buys (>=1 realized sell leg): {meta['n_closed']}")
    print(f"  open buys (no sell leg in window, excluded): {meta['open_unsold']}")
    print(f"  orphan sells (buy before window start, excluded): {meta['orphan_sells']}"
          f"  (their realized ${meta['orphan_sell_pnl_usd']:,.2f})")
    print(f"  buys with BOTH flags None (kept, can't determine): {meta['none_flag_buys']}")

    print()
    print("=" * 78)
    print("STEP 4 — FLEET-WIDE  (realized $, fraction-weighted pnl_pct)")
    print("=" * 78)
    print(f"  PAPER_TOTAL          : n={fleet['paper_n']:5d}  ${fleet['paper_total_usd']:10,.2f}  "
          f"mean%={_fmt(fleet['paper_mean_pct']):>7s}  "
          f"WR={_fmt((fleet['paper_wr'] or 0)*100, 1):>5s}%")
    print(f"  LIVE_FAITHFUL_TOTAL  : n={fleet['live_faithful_n']:5d}  ${fleet['live_faithful_total_usd']:10,.2f}  "
          f"mean%={_fmt(fleet['live_faithful_mean_pct']):>7s}  "
          f"WR={_fmt((fleet['live_faithful_wr'] or 0)*100, 1):>5s}%")
    print(f"  Delta (PAPER - LIVEF): ${fleet['delta_usd']:,.2f}   <-- fidelity gap from the caps")
    print(f"  would-blocked trades : n={fleet['would_block_n']} ({fleet['would_block_pct']:.1f}% of closed)  "
          f"${fleet['would_block_usd']:,.2f}  "
          f"WR={_fmt((fleet['would_block_wr'] or 0)*100, 1)}%")
    print(f"  DIRECTION            : {fleet['direction']}")

    print()
    print("=" * 78)
    print("STEP 4 — PER-BOT")
    print("=" * 78)
    hdr = (f"{'bot_id':30s} {'n':>4s} {'PAPER$':>10s} {'LIVEF$':>10s} "
           f"{'Delta$':>9s} {'blk_n':>5s} {'blk%':>5s} {'blkWR%':>6s}")
    print(hdr)
    print("-" * len(hdr))
    rows_sorted = sorted(per_bot.items(), key=lambda kv: kv[1]["delta_usd"], reverse=True)
    for bot, v in rows_sorted:
        wr = f"{(v['would_block_wr'] or 0)*100:5.1f}" if v["would_block_n"] else "   - "
        print(f"{str(bot)[:30]:30s} {v['paper_n']:>4d} {v['paper_total_usd']:>10,.2f} "
              f"{v['live_faithful_total_usd']:>10,.2f} {v['delta_usd']:>9,.2f} "
              f"{v['would_block_n']:>5d} {v['would_block_pct']:>5.1f} {wr:>6s}")

    print()
    print("=" * 78)
    print("VERDICT INPUTS")
    print("=" * 78)
    print(f"  fleet Delta = ${fleet['delta_usd']:,.2f} on n={fleet['would_block_n']} "
          f"blocked trades ({fleet['would_block_pct']:.1f}% of book)")
    print(f"  blocked trades realized ${fleet['would_block_usd']:,.2f}")


if __name__ == "__main__":
    main()
