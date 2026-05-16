"""30d post-exit price behavior grouped by peak tier.

Question: Is the trail leak concentrated on small-peak (+3-5%) trades?

For every trail/TP/stop exit in the last 30d, fetch DexScreener
candles for the 60 min after our exit and compute:
  - max_above_exit_pct (highest price after we exited)
  - drift_60m_pct (where we'd be if we'd held 60 min longer)
Group results by peak tier:
  +3-5%, +5-10%, +10-20%, +20%+
And by exit type (TP1 / TP2 / trail / stop / fast_dud).
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from feeds.dexscreener_client import DexScreenerClient

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"
POST_EXIT_WINDOW_S = 60 * 60  # 60 min


def parse_iso(s):
    if not s: return None
    s = s.replace("Z", "+00:00") if "Z" in s else s
    try: return datetime.fromisoformat(s)
    except: return None


def peak_tier(p):
    if p < 3: return "<+3%"
    if p < 5: return "+3-5%"
    if p < 10: return "+5-10%"
    if p < 20: return "+10-20%"
    return "+20%+"


def exit_kind(reason):
    if reason.startswith("Dip trail"): return "trail"
    if reason.startswith("Dip TP1"): return "TP1"
    if reason.startswith("Dip TP2"): return "TP2"
    if reason.startswith("Dip stop"): return "stop"
    if "fast_dud" in reason: return "fast_dud"
    if "pre-TP1 trail" in reason: return "pre-TP1-trail"
    if "post-TP1 trail" in reason: return "post-TP1-trail"
    return "other"


async def main():
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000") as r:
        trades = json.loads(r.read())
    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 24 * 3600
    sells = []
    for t in trades:
        if t.get("type") != "sell": continue
        if t.get("strategy") not in ("dip_buy", "scanner"): continue
        dt = parse_iso(t.get("time"))
        if not dt or dt.timestamp() < cutoff: continue
        if not t.get("pair_address"): continue
        sells.append(t)
    print(f"Total sells 30d: {len(sells)}")

    client = DexScreenerClient()
    rows = []
    no_candle_count = 0
    for i, t in enumerate(sells):
        exit_dt = parse_iso(t["time"])
        if not exit_dt: continue
        exit_ts = exit_dt.timestamp()
        try:
            candles = await client.fetch_1m(t["pair_address"], limit=200)
        except Exception:
            no_candle_count += 1
            continue
        pre = [c for c in candles if c.open_time <= exit_ts]
        post = [c for c in candles if exit_ts < c.open_time <= exit_ts + POST_EXIT_WINDOW_S]
        if not pre:
            no_candle_count += 1
            continue
        anchor = pre[-1].close
        if anchor <= 0:
            no_candle_count += 1
            continue
        if not post:
            # Mark as "died" — no post-exit trading activity
            rows.append({
                "token": t.get("token"),
                "peak": t.get("peak_pnl_pct") or 0,
                "exit_pnl": t.get("pnl_pct") or 0,
                "reason": t.get("reason", ""),
                "exit_kind": exit_kind(t.get("reason", "")),
                "tier": peak_tier(t.get("peak_pnl_pct") or 0),
                "max_above": None,
                "drift_60m": None,
                "died": True,
            })
            continue
        max_above = (max(c.high for c in post) / anchor - 1) * 100
        drift = (post[-1].close / anchor - 1) * 100
        rows.append({
            "token": t.get("token"),
            "peak": t.get("peak_pnl_pct") or 0,
            "exit_pnl": t.get("pnl_pct") or 0,
            "reason": t.get("reason", ""),
            "exit_kind": exit_kind(t.get("reason", "")),
            "tier": peak_tier(t.get("peak_pnl_pct") or 0),
            "max_above": max_above,
            "drift_60m": drift,
            "died": False,
        })
        await asyncio.sleep(0.04)
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(sells)} ...")

    print(f"\nFetched: {len(rows)} (no candles: {no_candle_count})")

    # ── Group by peak tier × exit kind ───────────────────────────────
    print(f"\n=== Peak tier × exit kind breakdown ===")
    print(f"  {'Tier':<10} {'Exit kind':<14} {'n':>4} {'died':>5} "
          f"{'med_max_above':>14} {'med_drift_60m':>14}")
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r["tier"], r["exit_kind"])].append(r)
    for (tier, kind), group in sorted(grouped.items(),
                                       key=lambda x: (x[0][0], x[0][1])):
        if len(group) < 2: continue
        died = sum(1 for r in group if r["died"])
        alive = [r for r in group if not r["died"]]
        if alive:
            ma = sorted(r["max_above"] for r in alive)[len(alive) // 2]
            dr = sorted(r["drift_60m"] for r in alive)[len(alive) // 2]
            print(f"  {tier:<10} {kind:<14} {len(group):>4} {died:>4}/{len(group)} "
                  f"{ma:>+13.1f}% {dr:>+13.1f}%")
        else:
            print(f"  {tier:<10} {kind:<14} {len(group):>4} {died:>4}/{len(group)} "
                  f"{'all-died':>14} {'all-died':>14}")

    # ── Focus on trail exits specifically (peak tier breakdown) ──────
    print(f"\n=== Trail-exits only — peak tier detail ===")
    trail_only = [r for r in rows if "trail" in r["exit_kind"]]
    print(f"All trail exits: {len(trail_only)}")
    for tier in ("<+3%", "+3-5%", "+5-10%", "+10-20%", "+20%+"):
        group = [r for r in trail_only if r["tier"] == tier]
        if not group: continue
        died = sum(1 for r in group if r["died"])
        alive = [r for r in group if not r["died"]]
        continuers = [r for r in alive if r["max_above"] > 2.0 and r["drift_60m"] > 0]
        dumpers = [r for r in alive if r["drift_60m"] < -2.0]
        if not alive:
            print(f"  {tier:<10} n={len(group):>3} died={died:>3}/{len(group)} "
                  f"alive=0 — entire tier died after exit")
            continue
        ma = sorted(r["max_above"] for r in alive)[len(alive) // 2]
        dr = sorted(r["drift_60m"] for r in alive)[len(alive) // 2]
        avg_pnl = sum(r["exit_pnl"] for r in group) / len(group)
        print(f"  {tier:<10} n={len(group):>3}  died={died:>3} "
              f"continuers={len(continuers):>3} dumpers={len(dumpers):>3}  "
              f"med_max_above=+{ma:>4.1f}%  med_drift=+{dr:>+4.1f}%  "
              f"avg_realized={avg_pnl:>+4.1f}%")
        # Per-trade detail for continuers
        if continuers and tier in ("+3-5%", "+5-10%"):
            print(f"    Continuers in tier (sample):")
            for r in sorted(continuers, key=lambda x: -x["max_above"])[:5]:
                print(f"      {r['token']:<12} peak={r['peak']:+.1f}% exit={r['exit_pnl']:+.1f}% "
                      f"max_above={r['max_above']:+.1f}% drift60m={r['drift_60m']:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
