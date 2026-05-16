"""Round 5: mine the LOSERS — find features that separate losing trades
from winning ones WITHIN the population that already passes wash-floor +
some bullish signal. The goal is new FILTERS that block these losers
without harming winners.

Approach:
 1. Start with all paired trades
 2. For each candidate filter dimension, find threshold that maximizes
    (losses blocked) - (winners blocked) * dollar_weight.
 3. Surface top-10 dimensions that are NOT yet in any enforced filter.
"""
from __future__ import annotations
import requests, statistics
from collections import defaultdict

API = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"


def fetch():
    trades = requests.get(API, timeout=20).json()
    trades = [t for t in trades if isinstance(t, dict)]
    return [t for t in trades if t.get("pnl_pct") is not None]


SELL_TIME = {
    "top10_holder_delta", "top10_holder_pct_at_sell",
    "lp_locked_pct_at_sell", "rugcheck_score_at_sell",
    "holder_snapshots", "hold_pnl_snapshots", "lp_snapshots",
    "rugcheck_score_snapshots", "orderflow_snapshots",
    "minutes_since_peak", "peak_pnl_pct", "peak_pnl_at_secs",
    "hold_secs", "pct_off_peak",
}

# Features that are essentially the same signal as wash-floor
DUPLICATE_OF_WASH = {"avg_trade_size_h1_usd", "p90_buy_size_usd"}


def main():
    paired = fetch()
    print(f"Closed paired trades: {len(paired)}")

    # Collect numeric features
    features = defaultdict(list)
    for t in paired:
        m = t.get("entry_meta") or {}
        for k, v in m.items():
            if k in SELL_TIME or k in DUPLICATE_OF_WASH:
                continue
            if k.endswith("_block_reasons") or k.endswith("_reasons"):
                continue
            if k.endswith("_verdict"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                features[k].append((float(v), t["pnl_pct"] > 0, t["pnl_pct"]))

    # For each feature, find best filter threshold:
    # - Try "blocks where val >= T" and "blocks where val <= T"
    # - Compute: net_gain = $ saved by blocking losers - $ lost by blocking winners
    print(f"\n=== Loser-pattern filter mining ===")
    print(f"For each feature, scan thresholds; show best filter direction by net $.\n")

    results = []
    for feat, vals in features.items():
        if len(vals) < 30:
            continue
        wins = [(v, p) for v, w, p in vals if w]
        losses = [(v, p) for v, w, p in vals if not w]
        if len(wins) < 10 or len(losses) < 10:
            continue

        # Try each percentile as threshold (10/20/30/40/50/60/70/80/90)
        all_vals = sorted([v for v, _, _ in vals])
        for direction in ("ge", "le"):
            best_net = None
            best_thr = None
            best_n_blocked = 0
            best_winners_blocked = 0
            for pct in (10, 20, 30, 40, 50, 60, 70, 80, 90):
                idx = int(len(all_vals) * pct / 100)
                if idx >= len(all_vals):
                    continue
                thr = all_vals[idx]
                # Trades blocked = (val >= thr) if direction='ge' else (val <= thr)
                blocked = [(v, w, p) for v, w, p in vals if (v >= thr if direction == "ge" else v <= thr)]
                if not blocked:
                    continue
                # Net $ saved: -sum(pnl_pct of blocked) (negative pnl is good to block)
                losers_blocked_pnl = sum(p for v, w, p in blocked if not w)
                winners_blocked_pnl = sum(p for v, w, p in blocked if w)
                net_savings = -losers_blocked_pnl - winners_blocked_pnl  # if blocked losers had -10% and winners had +5%, net=10-5=+5
                n_blk = len(blocked)
                n_wblk = sum(1 for v, w, p in blocked if w)
                # Sanity: don't accept filters that block more winners than losers
                if n_wblk >= (n_blk - n_wblk):
                    continue
                if best_net is None or net_savings > best_net:
                    best_net = net_savings
                    best_thr = thr
                    best_n_blocked = n_blk
                    best_winners_blocked = n_wblk
            if best_net is not None and best_net > 0:
                results.append({
                    "feat": feat,
                    "dir": direction,
                    "thr": best_thr,
                    "n_blocked": best_n_blocked,
                    "winners_blocked": best_winners_blocked,
                    "losers_blocked": best_n_blocked - best_winners_blocked,
                    "net_pct_savings": best_net,
                    "win_block_pct": best_winners_blocked / best_n_blocked,
                })

    # Sort by net % savings
    results.sort(key=lambda x: -x["net_pct_savings"])
    print(f"{'Feature':<40} {'Op':>3} {'Thr':>10} {'n_blk':>6} {'L blk':>6} {'W blk':>6} {'NetΔ%':>8}")
    print("-" * 90)
    for r in results[:25]:
        op = ">=" if r["dir"] == "ge" else "<="
        print(f"{r['feat']:<40} {op:>3} {r['thr']:>10.3f} {r['n_blocked']:>6} "
              f"{r['losers_blocked']:>6} {r['winners_blocked']:>6} {r['net_pct_savings']:>+7.1f}%")

    # Compound check: top 3 unblocked dims + already-shipped pop, simulate combined filter
    print(f"\n=== Combined filter candidates (2-way new filter compounds) ===")
    # For top 5 single filters by net $, try AND of pairs
    top_solo = results[:8]
    for i, r1 in enumerate(top_solo):
        for r2 in top_solo[i+1:]:
            if r1["feat"] == r2["feat"]:
                continue
            # Check the cohort that satisfies both filter conditions
            def matches(t, r):
                v = (t.get("entry_meta") or {}).get(r["feat"])
                if v is None:
                    return False
                if r["dir"] == "ge":
                    return v >= r["thr"]
                return v <= r["thr"]
            blocked = [t for t in paired if matches(t, r1) and matches(t, r2)]
            if not blocked or len(blocked) < 4:
                continue
            l_blk = sum(1 for t in blocked if t["pnl_pct"] <= 0)
            w_blk = sum(1 for t in blocked if t["pnl_pct"] > 0)
            if w_blk >= l_blk:
                continue
            net = -sum(t["pnl_pct"] for t in blocked)
            print(f"  {r1['feat']}{'>=' if r1['dir']=='ge' else '<='}{r1['thr']:.2f} "
                  f"AND {r2['feat']}{'>=' if r2['dir']=='ge' else '<='}{r2['thr']:.2f}: "
                  f"n={len(blocked)} L={l_blk} W={w_blk} netΔ={net:+.1f}%")


if __name__ == "__main__":
    main()
