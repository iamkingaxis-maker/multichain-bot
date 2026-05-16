"""mtf_strong_downtrend false-block audit — v2: deeper analysis.

Improvements over v1:
  1. STRATIFICATION by mtf value (strong_bear vs bear vs mixed) and by
     mcap tier. Filter may over-block one but be correct on others.
  2. REALISTIC P&L: apply our actual TP1+trail+stop logic to blocked
     price paths. Compute realized P&L, not just peak. Answers
     "would we have actually made money if we'd entered?"
  3. COHORT BREAKDOWN: token age, mcap, pc_h24 — where are the
     false-blocks concentrated?
  4. UNIVERSE CROSS-REF: for blocked tokens that appear in universe
     data, use universe's pre-computed peak/exit (no DexScreener fetch
     needed — much faster + more sample).

Output: per-stratum false-block rate + realistic-P&L EV per block.
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

# Our exit logic parameters (from utils/config.py)
TP1_PCT = 3.0; TP1_SELL = 0.50
TP2_PCT = 5.0; TP2_SELL = 0.50
STOP_PCT = -4.0
TRAIL_PP = 1.0          # post-TP1 trail (recently changed)
SLIPPAGE_PCT = 1.1


def parse_iso(s):
    s = s.replace("Z", "+00:00") if "Z" in s else s
    return datetime.fromisoformat(s)


def _load_universe_index():
    """Build {symbol: [universe_events]} index from universe_fresh.json
    so we can join signal-event tokens against universe-recorded outcomes."""
    try:
        data = json.loads(Path("universe_fresh.json").read_text())
    except Exception:
        return {}, {}
    by_sym = defaultdict(list)
    by_sym_pair = {}
    for e in data:
        sym = e.get("symbol")
        if not sym: continue
        by_sym[sym].append(e)
        if sym not in by_sym_pair and e.get("pair_address"):
            by_sym_pair[sym] = e["pair_address"]
    return dict(by_sym), by_sym_pair


def _load_trade_pairs():
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000") as r:
            trades = json.loads(r.read())
    except Exception:
        return {}
    out = {}
    for t in trades:
        s = t.get("token"); p = t.get("pair_address")
        if s and p: out.setdefault(s, p)
    return out


def simulate_exit(price_path: list[tuple[float, float]]) -> dict:
    """Walk (timestamp_offset_s, price_pct_from_entry) tuples; apply our
    exit logic; return realized P&L.

    price_path: list of (offset_seconds, price_pct) sorted by time.
                price_pct is % change from entry (e.g., +5.0 = +5%).
    """
    peak_pct = 0.0
    tp1_hit = False
    tp2_hit = False
    realized: list[tuple[float, float]] = []  # (portion, pct_realized)
    portion_remaining = 1.0

    def _add(portion, exit_pct):
        nonlocal portion_remaining
        actual = min(portion, portion_remaining)
        if actual <= 0: return
        realized.append((actual, exit_pct - SLIPPAGE_PCT))
        portion_remaining -= actual

    for _ts, pct in price_path:
        if portion_remaining <= 0.001: break
        if pct > peak_pct: peak_pct = pct
        # Stop (pre-TP1 only)
        if not tp1_hit and pct <= STOP_PCT:
            _add(portion_remaining, STOP_PCT); break
        # TP1
        if not tp1_hit and pct >= TP1_PCT:
            tp1_hit = True
            _add(TP1_SELL, TP1_PCT)
        # TP2
        if tp1_hit and not tp2_hit and pct >= TP2_PCT:
            tp2_hit = True
            _add(TP2_SELL * (1 - TP1_SELL), TP2_PCT)
        # Post-TP1 trail
        if tp1_hit and portion_remaining > 0:
            trail_target = peak_pct - TRAIL_PP
            if pct <= trail_target:
                _add(portion_remaining, trail_target)
                break
    if portion_remaining > 0.001:
        # Position open at end — close at last price
        last = price_path[-1][1] if price_path else 0
        _add(portion_remaining, last)
    total = sum(p * v for p, v in realized)
    return {
        "realized_pct": total,
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "n_legs": len(realized),
    }


async def fetch_price_path(client, pair, anchor_ts, window_s=3600):
    """Return list of (offset_s, price_pct_from_anchor) for the next window_s."""
    try:
        candles = await client.fetch_1m(pair, limit=200)
    except Exception:
        return None
    pre = [c for c in candles if c.open_time <= anchor_ts]
    post = [c for c in candles if anchor_ts < c.open_time <= anchor_ts + window_s]
    if not pre or not post: return None
    anchor = pre[-1].close
    if anchor <= 0: return None
    path = []
    for c in post:
        offset = c.open_time - anchor_ts
        # We use the HIGH of each minute (assumes we'd see it in the 5s mgmt cycle)
        # and the LOW for stop checks
        # For sim simplicity, sample two points per candle: high then low
        path.append((offset + 30, (c.high / anchor - 1) * 100))
        path.append((offset + 60, (c.low / anchor - 1) * 100))
    path.sort(key=lambda x: x[0])
    return path


async def main():
    by_sym_universe, by_sym_pair = _load_universe_index()
    trade_pairs = _load_trade_pairs()
    print(f"Universe symbols: {len(by_sym_universe)}  ({len(by_sym_pair)} with pair_address)")
    print(f"Trade pairs:     {len(trade_pairs)}")

    # ── Pull current signal events ───────────────────────────────────
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/signal-events?limit=2000") as r:
        events = json.loads(r.read())
    events = events if isinstance(events, list) else events.get("events", events.get("rows", []))

    mtf_blocks = [e for e in events if e.get("block_filter") == "mtf_strong_downtrend"]
    print(f"\nSignal events: {len(events)}  mtf_strong_downtrend blocks: {len(mtf_blocks)}")

    # Dedup by (token, hour)
    seen = set()
    unique_blocks = []
    for e in mtf_blocks:
        tk = e.get("token", "?"); ts = e.get("ts", "")
        try:
            hour_key = (tk, parse_iso(ts).strftime("%Y-%m-%dT%H"))
        except: continue
        if hour_key in seen: continue
        seen.add(hour_key)
        unique_blocks.append(e)
    print(f"Unique (token, hour) blocks: {len(unique_blocks)}")

    # ── PATH 1: Universe cross-ref (no DexScreener fetch) ────────────
    # If blocked token appears in universe data within ±1h of block ts,
    # use universe's recorded peak_pct/exit_pct.
    universe_resolved = []
    for e in unique_blocks:
        tk = e.get("token", "?")
        if tk not in by_sym_universe: continue
        try:
            block_ts = parse_iso(e.get("ts", "")).timestamp()
        except: continue
        univ_events = by_sym_universe[tk]
        # Find closest universe event by detected_at_iso
        closest = None
        closest_diff = float("inf")
        for ue in univ_events:
            try:
                ue_ts = parse_iso(ue.get("detected_at_iso", "")).timestamp()
                diff = abs(ue_ts - block_ts)
                if diff < closest_diff:
                    closest_diff = diff; closest = ue
            except: continue
        if not closest or closest_diff > 3600: continue
        universe_resolved.append({
            "token": tk,
            "mtf": e.get("mtf"), "chart_score": e.get("chart_score"),
            "pc_h24": e.get("pc_h24"), "mcap_m": e.get("mcap_m"),
            "univ_peak": closest.get("peak_pct"),
            "univ_exit": closest.get("exit_pct"),
            "univ_won_10pct": closest.get("won_10pct"),
            "time_diff_s": closest_diff,
        })
    print(f"\nUniverse-cross-ref resolved: {len(universe_resolved)}")

    # ── PATH 2: DexScreener forward fetch for blocks not in universe ─
    fetch_targets = []
    seen_tokens_univ = {r["token"] for r in universe_resolved}
    for e in unique_blocks:
        tk = e.get("token", "?")
        if tk in seen_tokens_univ: continue
        pair = by_sym_pair.get(tk) or trade_pairs.get(tk)
        if not pair: continue
        fetch_targets.append((e, pair))
    print(f"DexScreener-fetch targets: {len(fetch_targets)}")

    client = DexScreenerClient()
    fetched = []
    for i, (e, pair) in enumerate(fetch_targets):
        try:
            anchor_ts = parse_iso(e.get("ts", "")).timestamp()
        except: continue
        path = await fetch_price_path(client, pair, anchor_ts)
        if not path: continue
        sim = simulate_exit(path)
        peak = max(p[1] for p in path)
        drift = path[-1][1] if path else 0
        min_low = min(p[1] for p in path)
        fetched.append({
            "token": e.get("token", "?"),
            "mtf": e.get("mtf"), "chart_score": e.get("chart_score"),
            "pc_h24": e.get("pc_h24"), "mcap_m": e.get("mcap_m"),
            "peak": peak, "drift_60m": drift, "min": min_low,
            "sim_realized": sim["realized_pct"],
            "sim_tp1_hit": sim["tp1_hit"],
            "sim_tp2_hit": sim["tp2_hit"],
        })
        await asyncio.sleep(0.04)
        if (i+1) % 30 == 0:
            print(f"  fetched {len(fetched)}/{i+1} ...")

    print(f"\nDexScreener-resolved: {len(fetched)}")
    print(f"Total samples (universe + fetched): {len(universe_resolved) + len(fetched)}")

    # ── STRATIFICATION: by mtf value ────────────────────────────────
    print(f"\n=== Stratification by mtf value (universe+DS combined) ===")
    all_samples = []
    for r in universe_resolved:
        all_samples.append({
            "token": r["token"], "mtf": r["mtf"], "mcap_m": r.get("mcap_m"),
            "peak": r["univ_peak"], "is_winner": (r["univ_won_10pct"] or False) and (r.get("univ_exit", 0) >= 0),
            "exit": r.get("univ_exit", 0),
            "source": "universe",
        })
    for r in fetched:
        all_samples.append({
            "token": r["token"], "mtf": r["mtf"], "mcap_m": r.get("mcap_m"),
            "peak": r["peak"], "is_winner": r["peak"] >= 5 and r["drift_60m"] > 0,
            "exit": r["drift_60m"],
            "sim_realized": r["sim_realized"],
            "source": "dexscreener",
        })

    by_mtf = defaultdict(list)
    for s in all_samples:
        by_mtf[s["mtf"]].append(s)
    print(f"  {'mtf':<14} {'n':>4} {'winners':>8} {'win_rate':>9} {'avg_peak':>9} {'avg_exit':>9}")
    for mtf_v in ("strong_bear", "bear", "mixed", "flat", "bull", "strong_bull", None):
        sub = by_mtf.get(mtf_v, [])
        if not sub: continue
        w = sum(1 for s in sub if s["is_winner"])
        avg_peak = sum(s["peak"] for s in sub) / len(sub)
        avg_exit = sum(s["exit"] for s in sub) / len(sub)
        print(f"  {str(mtf_v):<14} {len(sub):>4} {w:>8} {w/len(sub)*100:>7.0f}% "
              f"{avg_peak:>+7.1f}% {avg_exit:>+7.1f}%")

    # ── REALISTIC P&L on DexScreener-fetched subset ─────────────────
    print(f"\n=== Realistic P&L sim (DexScreener subset, n={len(fetched)}) ===")
    print(f"  Apply our exit logic (TP1 +3%/50%, TP2 +5%/25%, trail 1pp, stop -4%)")
    sim_winners = [r for r in fetched if r["sim_realized"] > 0]
    sim_losers = [r for r in fetched if r["sim_realized"] < 0]
    avg_realized = sum(r["sim_realized"] for r in fetched) / len(fetched) if fetched else 0
    total_realized = sum(r["sim_realized"] for r in fetched)
    print(f"  Sim winners (realized>0): {len(sim_winners)}/{len(fetched)} = {len(sim_winners)/max(len(fetched),1)*100:.0f}%")
    print(f"  Sim losers (realized<0):  {len(sim_losers)}/{len(fetched)}")
    print(f"  Avg realized per block:   {avg_realized:+.2f}%")
    print(f"  Total realized:           {total_realized:+.1f}% over {len(fetched)} would-be entries")
    if fetched:
        # If we'd traded all 34 at $20 each
        avg_ev = avg_realized / 100 * 20
        print(f"  Est. $ EV per blocked-token-trade: ${avg_ev:+.2f} (at $20 sizing)")
        # tp1 / tp2 hit rates
        tp1_rate = sum(1 for r in fetched if r["sim_tp1_hit"]) / len(fetched)
        tp2_rate = sum(1 for r in fetched if r["sim_tp2_hit"]) / len(fetched)
        print(f"  TP1 hit rate in sim: {tp1_rate*100:.0f}%   TP2 hit rate: {tp2_rate*100:.0f}%")

    # ── COHORT: by mcap tier ─────────────────────────────────────────
    print(f"\n=== False-block by mcap tier ===")
    tiers = [
        ("<= 88k", lambda m: m is not None and m <= 0.089),
        ("88k-500k", lambda m: m is not None and 0.089 < m <= 0.5),
        ("500k-2M", lambda m: m is not None and 0.5 < m <= 2.0),
        ("> 2M", lambda m: m is not None and m > 2.0),
    ]
    print(f"  {'mcap tier':<12} {'n':>4} {'winners':>8} {'win%':>5} {'avg_exit':>9}")
    for label, pred in tiers:
        sub = [s for s in all_samples if pred(s.get("mcap_m"))]
        if not sub: continue
        w = sum(1 for s in sub if s["is_winner"])
        avg_exit = sum(s["exit"] for s in sub) / len(sub)
        print(f"  {label:<12} {len(sub):>4} {w:>8} {w/len(sub)*100:>4.0f}% {avg_exit:>+7.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
