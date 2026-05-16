"""Mine macro context features (SOL/BTC/meme-sector/regime) against
actual trade outcomes. These features were stamped to entry_meta over
the last several weeks but never explicitly mined.

Cohorts (paired trades, last 30d):
  BIG_WINNER: peak_pnl_pct >= +5 AND pnl_pct > 0
  LOSER:      pnl_pct < 0

For categorical 'regime' field, report cohort breakdown directly.
"""
from __future__ import annotations

import json
import math
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone


DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"


def parse_iso(s):
    s = s.replace("Z", "+00:00") if "Z" in s else s
    return datetime.fromisoformat(s)


def cohen_d(a, b):
    if len(a) < 5 or len(b) < 5: return None
    ma, mb = sum(a)/len(a), sum(b)/len(b)
    va = sum((x-ma)**2 for x in a)/(len(a)-1)
    vb = sum((x-mb)**2 for x in b)/(len(b)-1)
    p = math.sqrt((va+vb)/2)
    return (ma-mb)/p if p > 0 else None


def main():
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000") as r:
        trades = json.loads(r.read())
    cutoff = datetime.now(timezone.utc).timestamp() - 30*24*3600

    # Pair buys with sells; carry entry_meta from buy
    by_key = defaultdict(list)
    for t in trades:
        if t.get("strategy") not in ("dip_buy", "scanner"): continue
        key = (t.get("token"), round(t.get("entry_price", 0), 10))
        by_key[key].append(t)
    pairs = []
    for key, events in by_key.items():
        buys = [e for e in events if e.get("type") == "buy"]
        sells = [e for e in events if e.get("type") == "sell"]
        if not buys or not sells: continue
        buy = buys[0]
        dt = parse_iso(buy.get("time", ""))
        if dt.timestamp() < cutoff: continue
        sells.sort(key=lambda x: x.get("time", ""))
        last = sells[-1]
        pairs.append({
            "token": key[0],
            "pnl_pct": last.get("pnl_pct") or 0,
            "peak_pnl_pct": last.get("peak_pnl_pct") or 0,
            "em": buy.get("entry_meta") or {},
        })

    big_winners = [p for p in pairs if p["peak_pnl_pct"] >= 5.0 and p["pnl_pct"] > 0]
    losers = [p for p in pairs if p["pnl_pct"] < 0]
    print(f"Paired 30d: {len(pairs)}")
    print(f"  Big winners (peak>=+5 AND won):  {len(big_winners)}")
    print(f"  Losers (pnl<0):                  {len(losers)}")

    # ── Numeric features ─────────────────────────────────────────────
    numeric_feats = [
        "sol_pc_m5", "sol_pc_m1", "sol_pc_3m", "sol_pc_h1", "sol_pc_h4",
        "btc_pc_h1", "btc_pc_h4",
        "meme_sector_pct_h24",
    ]
    print(f"\n=== Cohen's d on macro features (winners vs losers) ===")
    print(f"  d > 0 → higher value favors WINNER")
    print(f"  {'Feature':<22} {'d':>6} {'win_mean':>10} {'lose_mean':>10}  {'win_n':>5} {'lose_n':>5}")
    results = []
    for f in numeric_feats:
        a = [p["em"][f] for p in big_winners if isinstance(p["em"].get(f), (int, float))]
        b = [p["em"][f] for p in losers if isinstance(p["em"].get(f), (int, float))]
        d = cohen_d(a, b)
        if d is None: continue
        results.append((f, d, a, b))
    results.sort(key=lambda x: -abs(x[1]))
    for f, d, a, b in results:
        print(f"  {f:<22} {d:>+5.2f}  {sum(a)/len(a):>+9.2f}  {sum(b)/len(b):>+9.2f}  "
              f"{len(a):>5}  {len(b):>5}")

    # ── Categorical: regime ─────────────────────────────────────────
    print(f"\n=== Regime tag breakdown ===")
    for cohort_name, cohort in [("BIG WINNERS", big_winners),
                                  ("LOSERS", losers),
                                  ("ALL paired", pairs)]:
        cnt = Counter(p["em"].get("regime") for p in cohort)
        total = sum(cnt.values())
        if total == 0: continue
        print(f"  {cohort_name:<14}  ({total} total)")
        for regime, n in cnt.most_common():
            print(f"    {regime or 'None':<8}  n={n:>3}  ({n/total*100:>4.0f}%)")

    # ── Threshold sweep on top numeric ──────────────────────────────
    if results:
        print(f"\n=== Threshold sweep on top features ===")
        for f, d, a, b in results[:4]:
            direction = "gte" if d > 0 else "lte"
            combined = sorted(a + b)
            if len(combined) < 20: continue
            cuts = [combined[int(len(combined) * p)]
                    for p in (0.3, 0.5, 0.65, 0.8)]
            print(f"\n  {f}  ({'higher=winner' if d>0 else 'lower=winner'})")
            for cut in cuts:
                if direction == "gte":
                    w = sum(1 for x in a if x >= cut)
                    l = sum(1 for x in b if x >= cut)
                else:
                    w = sum(1 for x in a if x <= cut)
                    l = sum(1 for x in b if x <= cut)
                tot = w + l
                if tot < 10: continue
                prec = w / tot
                sym = "≥" if direction == "gte" else "≤"
                print(f"    {f}{sym}{cut:>+6.2f}  n={tot:>3}  "
                      f"win={w:>3}  lose={l:>3}  win_rate={prec*100:>3.0f}%")

    # ── Compounds: regime × top numeric ──────────────────────────────
    if results:
        print(f"\n=== Regime × top-feature compound ===")
        top = results[0]
        f, d, _, _ = top
        print(f"  Compound = regime={'X'} AND {f} {'≥' if d>0 else '≤'} threshold")
        for regime in ("up", "flat", "down"):
            sub_win = [p for p in big_winners if p["em"].get("regime") == regime
                       and isinstance(p["em"].get(f), (int, float))]
            sub_lose = [p for p in losers if p["em"].get("regime") == regime
                        and isinstance(p["em"].get(f), (int, float))]
            if len(sub_win) + len(sub_lose) < 10: continue
            print(f"    regime={regime}:  win={len(sub_win)}  lose={len(sub_lose)}  "
                  f"WR={len(sub_win)/(len(sub_win)+len(sub_lose))*100:.0f}%")


if __name__ == "__main__":
    main()
