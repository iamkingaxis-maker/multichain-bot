#!/usr/bin/env python
"""Validate: does the never-green scorer's probability predict realized EV well
enough to drive SIZING (not just a binary block)?

Mission-2 entry/WR lever. The scorer currently only BLOCKS (binary, ~9.5% live).
feedback_wr_and_asymmetry_both says its probability should also feed SIZING:
down-size high-dud-prob entries that still pass the gate -> lifts WR-weighted EV
AND asymmetry at once. Earlier (pre-compaction) finding was non-monotonic:
Q4 (highest dud-prob) worst EV, Q2 (mid) best (+$0.60). This script tests whether
that prob->EV curve holds on RECENT, HELD-OUT data before we build a sizing curve.

Join (3 record types, all from prod API):
  decision-log {bot, addr, t, p, blocked}  --(bot,addr,nearest t)-->
  BUY {bot_id, address, time, amount_usd}   --(bot,addr,next sell)-->
  SELL {bot_id, address, pnl_pct, peak_pnl_pct}

Only PASSED decisions (blocked=False) have an outcome (blocks leave no trade).
Reports: EV/WR/never-green-rate by proba quartile, overall and time-split
(older half = "train-era", recent half = "held-out"), plus a token-deduped view
(FCM artifact guard). Read-only; ships nothing.
"""
from __future__ import annotations
import sys, json, urllib.request, time
from datetime import datetime

BASE = "https://gracious-inspiration-production.up.railway.app"
JOIN_WINDOW_S = 300   # decision <-> buy match window
NG_PEAK = 2.0         # never-green if forward peak < 2%


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def _epoch(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def main():
    decs = _get("/api/ng-scorer-decisions?limit=5000")
    trades = _get("/api/trades?limit=5000&full=1")
    decs = decs if isinstance(decs, list) else decs.get("decisions", decs.get("records", []))
    buys = [x for x in trades if x.get("type") == "buy"]
    sells = [x for x in trades if x.get("type") == "sell"]

    # index buys/sells by (bot, addr)
    buy_idx, sell_idx = {}, {}
    for b in buys:
        buy_idx.setdefault((b.get("bot_id"), b.get("address")), []).append(
            (_epoch(b.get("time")), b))
    for s in sells:
        sell_idx.setdefault((s.get("bot_id"), s.get("address")), []).append(
            (_epoch(s.get("time")), s))
    for d in (buy_idx, sell_idx):
        for k in d:
            d[k].sort(key=lambda x: (x[0] is None, x[0]))

    rows = []  # (t_epoch, bot, token, p, pnl_pct, peak_pct)
    matched = 0
    for d in decs:
        if d.get("blocked"):
            continue
        bot, addr, p = d.get("bot"), d.get("addr"), d.get("p")
        te = _epoch(d.get("t"))
        if p is None or te is None:
            continue
        bcand = buy_idx.get((bot, addr))
        if not bcand:
            continue
        # nearest buy within window
        bbest = min(bcand, key=lambda x: abs((x[0] or 0) - te))
        if bbest[0] is None or abs(bbest[0] - te) > JOIN_WINDOW_S:
            continue
        # first sell after that buy (same bot/addr)
        scand = sell_idx.get((bot, addr))
        if not scand:
            continue
        after = [s for s in scand if s[0] and s[0] >= bbest[0] - 5]
        if not after:
            continue
        srec = after[0][1]
        pnl_pct = srec.get("pnl_pct")
        peak = srec.get("peak_pnl_pct")
        if pnl_pct is None:
            continue
        matched += 1
        rows.append((te, bot, d.get("token"), float(p), float(pnl_pct),
                     float(peak) if peak is not None else None))

    print(f"decisions {len(decs)} | passed+closed matched {matched}")
    if matched < 12:
        print("too few matched outcomes yet — re-run as live decisions accumulate.")
        return

    def quartile_report(data, label):
        ps = sorted(r[3] for r in data)
        n = len(ps)
        q = [ps[int(n*0.25)], ps[int(n*0.5)], ps[int(n*0.75)]]
        buckets = {0: [], 1: [], 2: [], 3: []}
        for r in data:
            p = r[3]
            qi = 0 if p <= q[0] else 1 if p <= q[1] else 2 if p <= q[2] else 3
            buckets[qi].append(r)
        print(f"\n=== {label} (n={len(data)}) | proba cuts {[round(x,3) for x in q]} ===")
        print(f"{'bucket':8} {'n':>4} {'p_mean':>7} {'EV%':>7} {'WR%':>6} {'NG%':>6} {'avg_win':>7} {'avg_loss':>8}")
        for qi in range(4):
            bk = buckets[qi]
            if not bk:
                continue
            pm = sum(r[3] for r in bk) / len(bk)
            ev = sum(r[4] for r in bk) / len(bk)
            wr = 100 * sum(1 for r in bk if r[4] > 0) / len(bk)
            ngs = [r for r in bk if r[5] is not None]
            ng = 100 * sum(1 for r in ngs if r[5] < NG_PEAK) / len(ngs) if ngs else float("nan")
            wins = [r[4] for r in bk if r[4] > 0]
            loss = [r[4] for r in bk if r[4] <= 0]
            aw = sum(wins)/len(wins) if wins else 0
            al = sum(loss)/len(loss) if loss else 0
            print(f"Q{qi+1:<7} {len(bk):>4} {pm:>7.3f} {ev:>+7.2f} {wr:>6.0f} {ng:>6.0f} {aw:>+7.2f} {al:>+8.2f}")

    quartile_report(rows, "ALL passed entries")

    # held-out time split
    rows.sort(key=lambda r: r[0])
    mid = len(rows) // 2
    quartile_report(rows[:mid], "TRAIN-ERA (older half)")
    quartile_report(rows[mid:], "HELD-OUT (recent half)")

    # token-dedup (FCM guard): one row per token = median pnl across bots
    bytok = {}
    for r in rows:
        bytok.setdefault(r[2], []).append(r)
    ded = []
    for tok, rs in bytok.items():
        rs.sort(key=lambda r: r[4])
        med = rs[len(rs)//2]
        ded.append(med)
    quartile_report(ded, f"TOKEN-DEDUP ({len(ded)} unique tokens)")


if __name__ == "__main__":
    main()
