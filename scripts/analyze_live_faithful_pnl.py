#!/usr/bin/env python3
"""
analyze_live_faithful_pnl.py  — MEASUREMENT ONLY (read-only, no deploy, no behavior change)

Quantifies the live-vs-paper FIDELITY GAP created by two real-money capital guards that
ENFORCE only on live_probe bots but merely SHADOW-log on paper twins:

  1. per-bot daily-loss halt        (entry_meta key: daily_halt_would_block)
  2. per-day per-token re-entry cap  (entry_meta key: reentry_cap_would_block)

See feeds/dip_scanner.py ~2136-2153:
    _do_block = _live_probe_bot and _dl_cfg is not None   # daily halt: live only
    _do_block = (_rf_enforce or _live_probe_bot) and ...   # reentry cap: live only (unless RISK_FLOOR_MODE=enforce)

Because paper twins reach the same code but DON'T return, paper books trades a funded
live bot would NEVER take. Those buys are stamped daily_halt_would_block / reentry_cap_would_block
= True. This script removes them to reconstruct the P&L a live-faithful bot would have realized.

Convention notes:
  * Realized $ P&L uses the per-sell-leg `pnl` field (dollars), summed across legs per buy.
  * Per-trade % uses `pnl_pct` per leg, fraction-weighted by `sell_fraction` (the existing
    fraction-weighted blend convention). Win-rate is on the fraction-weighted realized pnl_pct.
  * Positions are keyed by (bot_id, ADDRESS) — NEVER symbol (cross-ticker poisoning, memory).
  * Buys --> sells paired FIFO chronologically within each (bot_id, address) group; each sell
    leg is attributed WHOLLY to the oldest open lot and decrements its remaining fraction.

Data limitations are reported (orphan sells = position opened before the window started;
open buys = position not yet closed at window end). The would-block flags are stamped at
DECISION time from each bot's own running daily_pnl/token-buy-count state, so they remain
authoritative across the multi-day window (the file currently spans ~2026-06-23..06-28).
"""
import json
import sys
import statistics
from collections import defaultdict, deque, Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else "_full_trades.json"

DAILY_KEY = "daily_halt_would_block"
REENTRY_KEY = "reentry_cap_would_block"
EPS = 0.01


def med(xs):
    return statistics.median(xs) if xs else float("nan")


def mean(xs):
    return (sum(xs) / len(xs)) if xs else float("nan")


def main():
    with open(PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    buys = [r for r in data if r.get("type") == "buy"]
    sells = [r for r in data if r.get("type") == "sell"]

    # ---- 1. confirm the would-block field keys + their value distribution ----
    print("=" * 78)
    print("STEP 1 — would-block flag keys found in buy.entry_meta")
    print("=" * 78)
    for k in (DAILY_KEY, REENTRY_KEY):
        c = Counter(b.get("entry_meta", {}).get(k) for b in buys)
        print(f"  {k:28s}: {dict(c)}")
    print(f"  total buys={len(buys)}  total sells={len(sells)}")
    times = [r.get("time", "") for r in data]
    print(f"  window: {min(times)}  -->  {max(times)}")

    # ---- 2. FIFO pair buys --> sell legs within (bot_id, ADDRESS) ----
    groups = defaultdict(list)
    for r in data:
        groups[(r.get("bot_id"), r.get("address"))].append(r)

    # each lot: {"buy":rec, "remaining":1.0, "legs":[sell,...]}
    lots_for_buy = {}          # id(buyrec) --> lot
    orphan_sells = 0
    orphan_sell_pnl = 0.0

    for key, recs in groups.items():
        recs.sort(key=lambda r: r.get("time", ""))
        open_lots = deque()
        for r in recs:
            if r.get("type") == "buy":
                lot = {"buy": r, "remaining": 1.0, "legs": []}
                lots_for_buy[id(r)] = lot
                open_lots.append(lot)
            else:  # sell
                if not open_lots:
                    orphan_sells += 1
                    orphan_sell_pnl += float(r.get("pnl") or 0.0)
                    continue
                lot = open_lots[0]
                lot["legs"].append(r)
                frac = r.get("sell_fraction")
                frac = float(frac) if frac is not None else lot["remaining"]
                lot["remaining"] -= frac
                if lot["remaining"] <= EPS:
                    open_lots.popleft()

    # ---- 3. roll up per buy ----
    # buy-level record: bot_id, blocked(bool), realized_usd, realized_pct, closed(bool)
    per_buy = []
    open_unsold = 0
    none_flag_buys = 0
    for b in buys:
        lot = lots_for_buy.get(id(b))
        legs = lot["legs"] if lot else []
        em = b.get("entry_meta", {})
        dhalt = em.get(DAILY_KEY)
        rcap = em.get(REENTRY_KEY)
        if dhalt is None and rcap is None:
            none_flag_buys += 1
        blocked = (dhalt is True) or (rcap is True)
        if not legs:
            open_unsold += 1
            continue
        usd = sum(float(s.get("pnl") or 0.0) for s in legs)
        wsum = 0.0
        psum = 0.0
        for s in legs:
            fr = s.get("sell_fraction")
            fr = float(fr) if fr is not None else 1.0
            pp = s.get("pnl_pct")
            if pp is None:
                continue
            wsum += fr
            psum += float(pp) * fr
        pct = (psum / wsum) if wsum > 0 else None
        per_buy.append({
            "bot_id": b.get("bot_id"),
            "blocked": blocked,
            "daily_blocked": dhalt is True,
            "reentry_blocked": rcap is True,
            "usd": usd,
            "pct": pct,
        })

    # ---- 4. aggregate fleet + per-bot ----
    def agg(rows):
        usd = sum(r["usd"] for r in rows)
        pcts = [r["pct"] for r in rows if r["pct"] is not None]
        wins = sum(1 for p in pcts if p > 0)
        wr = (wins / len(pcts)) if pcts else float("nan")
        return {
            "n": len(rows),
            "usd": usd,
            "mean_pct": mean(pcts),
            "med_pct": med(pcts),
            "wr": wr,
        }

    closed = per_buy
    paper = agg(closed)
    livef_rows = [r for r in closed if not r["blocked"]]
    livef = agg(livef_rows)
    blocked_rows = [r for r in closed if r["blocked"]]
    blk = agg(blocked_rows)

    print()
    print("=" * 78)
    print("STEP 2/3 — pairing results")
    print("=" * 78)
    print(f"  closed buys (>=1 realized sell leg): {len(closed)}")
    print(f"  open buys (no sell leg in window, excluded): {open_unsold}")
    print(f"  orphan sells (buy before window start, excluded): {orphan_sells}"
          f"  (their realized ${orphan_sell_pnl:,.2f})")
    print(f"  buys with BOTH flags None (kept, can't determine): {none_flag_buys}")

    print()
    print("=" * 78)
    print("STEP 4 — FLEET-WIDE  (realized $, fraction-weighted pnl_pct)")
    print("=" * 78)
    n_blk = len(blocked_rows)
    pct_blk = 100.0 * n_blk / len(closed) if closed else 0.0
    print(f"  PAPER_TOTAL          : n={paper['n']:5d}  ${paper['usd']:10,.2f}  "
          f"mean%={paper['mean_pct']:7.2f}  med%={paper['med_pct']:7.2f}  WR={paper['wr']*100:5.1f}%")
    print(f"  LIVE_FAITHFUL_TOTAL  : n={livef['n']:5d}  ${livef['usd']:10,.2f}  "
          f"mean%={livef['mean_pct']:7.2f}  med%={livef['med_pct']:7.2f}  WR={livef['wr']*100:5.1f}%")
    delta = paper["usd"] - livef["usd"]
    print(f"  Delta (PAPER - LIVEF)    : ${delta:,.2f}   <-- fidelity gap from the caps (realized $)")
    print(f"  would-blocked trades : n={n_blk} ({pct_blk:.1f}% of closed buys)  "
          f"${blk['usd']:,.2f}  mean%={blk['mean_pct']:.2f}  med%={blk['med_pct']:.2f}  "
          f"WR={blk['wr']*100:.1f}%")
    # flag breakdown across ALL buys (closed+open) for prevalence
    all_daily = sum(1 for b in buys if b.get("entry_meta", {}).get(DAILY_KEY) is True)
    all_reentry = sum(1 for b in buys if b.get("entry_meta", {}).get(REENTRY_KEY) is True)
    print(f"  flag prevalence (all {len(buys)} buys): "
          f"{DAILY_KEY}=True {all_daily} ({100.0*all_daily/len(buys):.1f}%)  "
          f"{REENTRY_KEY}=True {all_reentry} ({100.0*all_reentry/len(buys):.1f}%)")

    direction = ("paper UNDERSTATES; a live bot skipping them looks BETTER than paper"
                 if blk["usd"] < 0 else
                 "paper OVERSTATES; a live bot skipping them looks WORSE than paper")
    if n_blk == 0:
        direction = "no would-blocked closed trades in window"
    print(f"  DIRECTION            : would-blocked trades are net "
          f"{'LOSERS' if blk['usd']<0 else 'WINNERS'} --> {direction}")

    # ---- per-bot table ----
    print()
    print("=" * 78)
    print("STEP 4 — PER-BOT")
    print("=" * 78)
    bybot = defaultdict(list)
    for r in closed:
        bybot[r["bot_id"]].append(r)
    hdr = (f"{'bot_id':30s} {'n':>4s} {'PAPER$':>10s} {'LIVEF$':>10s} "
           f"{'Delta$':>9s} {'blk_n':>5s} {'blk%':>5s} {'blkWR%':>6s}")
    print(hdr)
    print("-" * len(hdr))
    rows_sorted = sorted(bybot.items(),
                         key=lambda kv: (sum(r["usd"] for r in kv[1])
                                         - sum(r["usd"] for r in kv[1] if not r["blocked"])),
                         reverse=True)
    for bot, rows in rows_sorted:
        pa = agg(rows)
        lf = agg([r for r in rows if not r["blocked"]])
        bl = [r for r in rows if r["blocked"]]
        bla = agg(bl)
        d = pa["usd"] - lf["usd"]
        bpct = 100.0 * len(bl) / len(rows) if rows else 0.0
        wr = f"{bla['wr']*100:5.1f}" if bl else "   - "
        print(f"{str(bot)[:30]:30s} {pa['n']:>4d} {pa['usd']:>10,.2f} {lf['usd']:>10,.2f} "
              f"{d:>9,.2f} {len(bl):>5d} {bpct:>5.1f} {wr:>6s}")

    print()
    print("=" * 78)
    print("VERDICT INPUTS")
    print("=" * 78)
    print(f"  fleet Delta = ${delta:,.2f} on n={n_blk} blocked trades ({pct_blk:.1f}% of book)")
    print(f"  blocked trades realized ${blk['usd']:,.2f} (net "
          f"{'LOSERS' if blk['usd']<0 else 'WINNERS'})")


if __name__ == "__main__":
    main()
