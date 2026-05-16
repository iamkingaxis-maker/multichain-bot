"""Why does our scanner never fire in regime=up?

Checks three explanations:
  (1) Frequency: how often does universe data show regime=up?
  (2) Token-side: of universe events in regime=up, what are their
      properties (pc_h24, vol, mcap)?  Do they violate scanner gates?
  (3) Outcome: when universe events occur in regime=up, what's the
      win rate? If up-regime tokens have BETTER outcomes, the gap is
      a missed opportunity.

Universe data is broader than our trade cohort — it captures dip
candidates regardless of whether we'd buy them.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path


def parse_iso(s):
    if not s: return None
    s = s.replace("Z", "+00:00") if "Z" in s else s
    try: return dt.datetime.fromisoformat(s)
    except: return None


def main():
    events = json.loads(Path("universe_fresh.json").read_text())
    print(f"Universe events: {len(events)}")

    # The regime tag isn't in universe data — it's computed at scanner time
    # from sol_pc_h1/h4, btc_pc_h1, meme_sector_pct_h24. The universe doesn't
    # capture macro context. So we need to RECREATE the regime tag from
    # universe-side proxies... but it doesn't have SOL/BTC data either.
    #
    # Alternative: use detected_at_iso to bucket by hour-of-day + day, then
    # cross-check against historical regime by date. Too indirect to be useful.
    #
    # SIMPLER ANGLE: just look at how scanner gates filter universe events.
    # Each universe event is a dip candidate. If scanner gates were applied,
    # how many pass? And what's the outcome distribution?

    # Scanner gates (from scanner_block_reasons in live_forward_test):
    #   vol_h1 < 10000          → skip
    #   pc_h24 <= 0             → skip (red_h24 rule)
    #   pc_m5 > -3 AND pc_h1 > -3 → skip (no_real_dip)
    #   peak_h24_6h_pct > 1000  → skip
    # (peak_h24_6h_pct not in universe data)

    def passes_scanner(e):
        if not isinstance(e.get("vol_h1"), (int, float)) or e["vol_h1"] < 10000:
            return False, "vol_h1<10k"
        if not isinstance(e.get("pc_h24"), (int, float)) or e["pc_h24"] <= 0:
            return False, "red_h24 (pc_h24<=0)"
        pc_m5 = e.get("pc_m5")
        pc_h1 = e.get("pc_h1")
        if isinstance(pc_m5, (int, float)) and isinstance(pc_h1, (int, float)):
            if pc_m5 > -3 and pc_h1 > -3:
                return False, "no_real_dip"
        return True, "PASS"

    # Block-reason distribution
    block_reasons = Counter()
    passes = []
    for e in events:
        ok, reason = passes_scanner(e)
        block_reasons[reason] += 1
        if ok:
            passes.append(e)

    print(f"\n=== Scanner gate distribution on universe ===")
    for reason, n in block_reasons.most_common():
        print(f"  {reason:<25}  n={n:>4}  ({n/len(events)*100:>4.0f}%)")
    print(f"\n  Would-pass-scanner: {len(passes)}/{len(events)} = {len(passes)/len(events)*100:.0f}%")

    # ── Why specifically does red_h24 dominate? ──────────────────────
    # The red_h24 rule (pc_h24 <= 0 skip) is the historical biggest blocker.
    # In an UP regime (memes rallying), tokens are GREEN h24, so red_h24
    # blocks them. That IS the gap.
    print(f"\n=== pc_h24 distribution ===")
    pc_h24_dist = defaultdict(int)
    for e in events:
        v = e.get("pc_h24")
        if not isinstance(v, (int, float)): continue
        if v <= 0: pc_h24_dist["red (<=0)"] += 1
        elif v < 20: pc_h24_dist["green +0-20%"] += 1
        elif v < 100: pc_h24_dist["green +20-100%"] += 1
        elif v < 500: pc_h24_dist["green +100-500%"] += 1
        else: pc_h24_dist["green +500%+"] += 1
    for k, n in sorted(pc_h24_dist.items()):
        print(f"  {k:<22} n={n:>4} ({n/len(events)*100:>4.0f}%)")

    # ── If we LOOSENED red_h24 to allow green tokens, what's the WR? ─
    print(f"\n=== WR by pc_h24 bucket (universe outcomes) ===")
    print(f"  {'Bucket':<25} {'n':>5} {'won_10pct':>10} {'avg_exit':>10}")
    buckets = [
        ("red (<=0)", lambda v: v <= 0),
        ("green +0-20%", lambda v: 0 < v < 20),
        ("green +20-100%", lambda v: 20 <= v < 100),
        ("green +100-500%", lambda v: 100 <= v < 500),
        ("green +500%+", lambda v: 500 <= v),
    ]
    for label, pred in buckets:
        sub = [e for e in events
               if isinstance(e.get("pc_h24"), (int, float)) and pred(e["pc_h24"])]
        if not sub: continue
        w10 = sum(1 for e in sub if e.get("won_10pct")) / len(sub) * 100
        avg = sum(e.get("exit_pct", 0) for e in sub) / len(sub)
        print(f"  {label:<25} {len(sub):>5} {w10:>8.0f}%  {avg:>+8.1f}%")

    # ── Specifically: GREEN dips (pc_h24 > 0 AND pc_m5 < -3) ─────────
    print(f"\n=== GREEN tokens with REAL DIP (pc_h24>0 AND pc_m5<-3) ===")
    print(f"  These are blocked by red_h24 today.")
    green_dips = [e for e in events
                  if isinstance(e.get("pc_h24"), (int, float)) and e["pc_h24"] > 0
                  and isinstance(e.get("pc_m5"), (int, float)) and e["pc_m5"] < -3]
    if green_dips:
        w10 = sum(1 for e in green_dips if e.get("won_10pct"))
        avg = sum(e.get("exit_pct", 0) for e in green_dips) / len(green_dips)
        print(f"  n={len(green_dips)}  won_10pct={w10}/{len(green_dips)} ({w10/len(green_dips)*100:.0f}%)  "
              f"avg_exit={avg:+.1f}%")
        # Bucket by pc_h24
        print(f"\n  Per pc_h24 sub-bucket:")
        for label, pred in [("0-20%", lambda v: 0 < v < 20),
                             ("20-100%", lambda v: 20 <= v < 100),
                             ("100-500%", lambda v: 100 <= v < 500),
                             ("500%+", lambda v: v >= 500)]:
            sub = [e for e in green_dips if pred(e["pc_h24"])]
            if len(sub) < 5: continue
            w10 = sum(1 for e in sub if e.get("won_10pct")) / len(sub) * 100
            avg = sum(e.get("exit_pct", 0) for e in sub) / len(sub)
            print(f"    pc_h24 {label:<10} n={len(sub):>4}  won_10pct={w10:>3.0f}%  avg_exit={avg:>+5.1f}%")


if __name__ == "__main__":
    main()
