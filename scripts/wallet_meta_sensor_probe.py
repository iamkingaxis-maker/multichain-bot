"""FEASIBILITY PROBE: wallets as the day-meta sensor (AxiS 2026-06-12).

The idea: read the day's meta from OTHER successful wallets' realized results
BEFORE we trade — they pay the tuition, not us. This probe asks the minimum
viable questions on real chain data:

  Q1  Do panel wallets' daily WRs move TOGETHER? (If yes, there's a common
      "wallet-tape day quality" factor — a sensor exists.)
  Q2  Does the panel's FIRST-6h-CT WR predict OUR fleet's rest-of-day P&L
      as well as / better than our own first-6h WR (the registered dial)?
  Q3  Coverage: how many days back does each wallet's sig history reach
      (poller cadence sizing)?

Panel = config/follow_watchlist.json roster + config/follow_cuts.json keys
(cut for copyability, still sensor-grade). Reads up to SIGS sigs per wallet.

Usage:  python scripts/wallet_meta_sensor_probe.py [sigs=400]
"""
from __future__ import annotations
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from wallet_decode import trade_map   # timestamped per-token buys/sells via RPC


def roundtrips(addr: str, sigs: int):
    """[(sell_ts, ret_pct)] for closed positions (recv vs spent per token)."""
    tok = trade_map(addr, sigs)
    out = []
    for m, r in tok.items():
        if not r["buys"] or not r["sells"] or not r["spent"]:
            continue
        s1 = max(s[0] for s in r["sells"])
        out.append((s1, (r["recv"] / r["spent"] - 1) * 100))
    return out


def main():
    sigs = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    panel = list(json.load(open("config/follow_watchlist.json")))
    try:
        panel += [a for a in json.load(open("config/follow_cuts.json")) if a not in panel]
    except Exception:
        pass
    print(f"panel: {len(panel)} wallets, {sigs} sigs each\n")

    day_w = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # day -> wallet -> [wins,n]
    early_w = defaultdict(lambda: [0, 0])                      # day -> [wins,n] first 6h CT pooled
    cover = {}
    for a in panel:
        try:
            rts = roundtrips(a, sigs)
        except Exception as e:
            print(f"  {a[:8]}… RPC fail: {e}")
            continue
        if not rts:
            print(f"  {a[:8]}… 0 roundtrips (unfollowable custody or quiet)")
            continue
        ts = [t for t, _ in rts]
        cover[a] = (min(ts), max(ts), len(rts))
        for t, ret in rts:
            ct = datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=5)
            d = ct.strftime("%Y-%m-%d")
            day_w[d][a][0] += ret > 0
            day_w[d][a][1] += 1
            if ct.hour < 6:
                early_w[d][0] += ret > 0
                early_w[d][1] += 1
        lo, hi, n = cover[a]
        span_d = (hi - lo) / 86400
        print(f"  {a[:8]}… {n} roundtrips spanning {span_d:.1f}d "
              f"({datetime.fromtimestamp(lo, timezone.utc):%m-%d} -> "
              f"{datetime.fromtimestamp(hi, timezone.utc):%m-%d})")

    # Q1: co-movement — average pairwise daily-WR correlation
    print("\nQ1 CO-MOVEMENT (do wallet days move together?)")
    series = defaultdict(dict)
    for d, ws in day_w.items():
        for a, (w, n) in ws.items():
            if n >= 3:
                series[a][d] = w / n
    wallets = [a for a in series if len(series[a]) >= 4]
    cors = []
    for i in range(len(wallets)):
        for j in range(i + 1, len(wallets)):
            common = sorted(set(series[wallets[i]]) & set(series[wallets[j]]))
            if len(common) < 4:
                continue
            x = [series[wallets[i]][d] for d in common]
            y = [series[wallets[j]][d] for d in common]
            mx, my = statistics.mean(x), statistics.mean(y)
            num = sum((a_ - mx) * (b - my) for a_, b in zip(x, y))
            den = (sum((a_ - mx) ** 2 for a_ in x) * sum((b - my) ** 2 for b in y)) ** 0.5
            if den:
                cors.append(num / den)
    if cors:
        print(f"  pairwise daily-WR corr: mean {statistics.mean(cors):+.2f} "
              f"(n={len(cors)} pairs) | positive {sum(1 for c in cors if c > 0)}/{len(cors)}")
    else:
        print("  insufficient overlapping coverage for co-movement")

    # Q2: panel first-6h WR -> OUR fleet rest-of-day P&L (vs our own dial signal)
    print("\nQ2 PANEL-EARLY -> OUR REST-OF-DAY (the zero-tuition dial)")
    try:
        rows = json.load(open("_trades_cache.json"))
        rows = rows if isinstance(rows, list) else rows.get("trades", [])
        ours_rest = defaultdict(float)
        ours_early = defaultdict(lambda: [0, 0])
        for t in rows:
            if t.get("type") != "sell":
                continue
            if "cancelled on restart" in (t.get("reason") or "").lower():
                continue
            pp = t.get("pnl_pct")
            if isinstance(pp, (int, float)) and abs(pp) > 150:
                continue
            try:
                dt = datetime.fromisoformat(str(t.get("time")).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ct = dt - timedelta(hours=5)
            except Exception:
                continue
            d = ct.strftime("%Y-%m-%d")
            pnl = float(t.get("pnl") or 0)
            if ct.hour < 6:
                ours_early[d][0] += pnl > 0
                ours_early[d][1] += 1
            else:
                ours_rest[d] += pnl
        pts = []
        for d, (w, n) in early_w.items():
            if n >= 5 and d in ours_rest:
                pts.append((d, w / n, ours_rest[d],
                            (ours_early[d][0] / ours_early[d][1]) if ours_early[d][1] >= 10 else None))
        pts.sort()
        for d, pwr, rest, own in pts:
            print(f"  {d}: panel-early WR {pwr:.0%} | our-early WR "
                  f"{'--' if own is None else f'{own:.0%}'} | our rest-of-day ${rest:+.0f}")
        if len(pts) >= 5:
            x = [p[1] for p in pts]
            y = [p[2] for p in pts]
            mx, my = statistics.mean(x), statistics.mean(y)
            num = sum((a_ - mx) * (b - my) for a_, b in zip(x, y))
            den = (sum((a_ - mx) ** 2 for a_ in x) * sum((b - my) ** 2 for b in y)) ** 0.5
            if den:
                print(f"  Pearson r (panel-early WR vs our rest-of-day) = {num/den:+.2f} "
                      f"(n={len(pts)} days)")
        else:
            print(f"  only {len(pts)} joinable days — coverage-limited (see Q3)")
    except Exception as e:
        print(f"  ledger join failed: {e}")


if __name__ == "__main__":
    main()
