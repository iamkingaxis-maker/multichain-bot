"""Mine the full signal population.

Reads {DATA_DIR}/signal_events.jsonl produced by core/signal_event_recorder.py
and joins it with /api/trades to label BUY events as W/L. Then surfaces:

  1. Population breakdown — how many tokens got Signaled, blocked, bought, won, lost
  2. Per-filter precision — for each block filter: what % of blocked entries are
     net losers IF we knew the outcome (we don't for blocked, so reported as
     'block volume') + for each SHADOW filter: how often it fires on BUYs that
     ended up winners vs losers (the SHADOW false-positive rate)
  3. Cohort comparison — winners vs losers vs blocked, on every numeric feature

This is the FOUNDATION dataset. Mining new filter ideas should always pull from
here, not from trades.db alone.

Usage: python scripts/mine_full_pop.py [--days N]
"""
import argparse
import json
import os
import urllib.request
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from statistics import mean, stdev


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7,
                   help="Recent window in days")
    p.add_argument("--input", type=str, default=None,
                   help="Path to signal_events.jsonl (default: {DATA_DIR}/signal_events.jsonl)")
    p.add_argument("--api-url", type=str,
                   default="https://gracious-inspiration-production.up.railway.app/api/trades?limit=2000")
    return p.parse_args()


def load_events(path):
    if not os.path.exists(path):
        print(f"No event file at {path} — has the recorder run yet?")
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def load_trades_outcomes(api_url):
    """Pull /api/trades and return {token_symbol_or_addr: pnl} for closed trades."""
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            trades = json.loads(r.read())
    except Exception as e:
        print(f"trades pull failed: {e}")
        return {}
    sells_by_addr = defaultdict(list)
    sym_by_addr = {}
    for t in trades:
        em = t.get("entry_meta") or {}
        sym = em.get("token_symbol") or em.get("symbol")
        if t.get("address") and sym:
            sym_by_addr[t["address"]] = sym
        if t.get("type") == "sell":
            sells_by_addr[t.get("address")].append(t)
    # Symbol -> latest pnl  (might collide on duplicate symbols, but it's an MVP)
    out = {}
    for addr, sells in sells_by_addr.items():
        sym = sym_by_addr.get(addr)
        if not sym:
            continue
        # Take latest sell
        latest = sorted(sells, key=lambda s: s.get("time", ""))[-1]
        out[sym] = latest.get("pnl") or 0
    return out


def main():
    args = parse_args()
    data_dir = os.environ.get("DATA_DIR", ".")
    path = args.input or os.path.join(data_dir, "signal_events.jsonl")

    events = load_events(path)
    print(f"Loaded {len(events)} events from {path}")
    if not events:
        return

    # Filter to recent window
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    events = [e for e in events if e.get("ts", "") >= cutoff]
    print(f"After {args.days}d window: {len(events)} events")

    outcomes = load_trades_outcomes(args.api_url)
    print(f"Loaded {len(outcomes)} closed-trade outcomes")

    # ============================================================
    # PART 1: Population breakdown
    # ============================================================
    buy_w = []
    buy_l = []
    buy_open = []
    blocks = defaultdict(list)
    continued = []

    for e in events:
        oc = e.get("outcome")
        if oc == "BUY":
            tok = e.get("token")
            pnl = outcomes.get(tok)
            if pnl is None:
                buy_open.append(e)
            elif pnl > 0:
                e["pnl"] = pnl
                buy_w.append(e)
            else:
                e["pnl"] = pnl
                buy_l.append(e)
        elif oc == "BLOCK":
            blocks[e.get("block_filter", "?")].append(e)
        elif oc == "CONTINUED":
            continued.append(e)

    total_buys = len(buy_w) + len(buy_l) + len(buy_open)
    total_signals = len(events)
    total_blocks = sum(len(v) for v in blocks.values())
    print(f"\n{'='*80}")
    print(f"POPULATION BREAKDOWN ({args.days}d)")
    print(f"{'='*80}")
    print(f"  Total Signal: emissions:        {total_signals}")
    print(f"  Blocked (enforced filter):      {total_blocks} ({total_blocks/total_signals:.0%})")
    print(f"  Bought:                         {total_buys} ({total_buys/total_signals:.0%})")
    print(f"    - Winners (closed +pnl):      {len(buy_w)}")
    print(f"    - Losers (closed <=0):        {len(buy_l)}")
    print(f"    - Open (no close yet):        {len(buy_open)}")
    print(f"  Continued (no terminal log):    {len(continued)}")
    if (len(buy_w) + len(buy_l)) > 0:
        wr = len(buy_w) / (len(buy_w) + len(buy_l))
        print(f"  WR of closed buys:              {wr:.0%}")

    # ============================================================
    # PART 2: Block filter volume
    # ============================================================
    print(f"\n{'='*80}")
    print(f"ENFORCED FILTER BLOCK VOLUMES ({args.days}d)")
    print(f"{'='*80}")
    print(f'  {"filter":<40} {"n_blocked":>10} {"% of signals":>14}')
    for fname, evs in sorted(blocks.items(), key=lambda x: -len(x[1])):
        print(f"  {fname:<40} {len(evs):>10} {len(evs)/total_signals:>13.1%}")

    # ============================================================
    # PART 3: SHADOW filter false-positive analysis
    # ============================================================
    # A shadow filter that fires on many WINNERS is a bad promotion candidate.
    print(f"\n{'='*80}")
    print(f"SHADOW FILTER FP ANALYSIS — fires on winners vs losers")
    print(f"{'='*80}")
    print(f'  {"shadow":<30} {"fire_W":>7} {"fire_L":>7} {"FP%":>7} '
          f'{"WR_when_fired":>14} {"WR_when_not":>13}')
    shadow_w_count = defaultdict(int)
    shadow_l_count = defaultdict(int)
    for e in buy_w:
        for s in e.get("shadows", []):
            shadow_w_count[s] += 1
    for e in buy_l:
        for s in e.get("shadows", []):
            shadow_l_count[s] += 1
    all_shadows = set(shadow_w_count) | set(shadow_l_count)
    for s in sorted(all_shadows,
                    key=lambda x: -(shadow_w_count[x] + shadow_l_count[x])):
        fw = shadow_w_count[s]
        fl = shadow_l_count[s]
        n_fired = fw + fl
        wr_fired = fw / n_fired if n_fired else 0
        # WR when NOT fired = winners without shadow / (winners + losers without)
        not_w = len([e for e in buy_w if s not in e.get("shadows", [])])
        not_l = len([e for e in buy_l if s not in e.get("shadows", [])])
        wr_not = not_w / (not_w + not_l) if (not_w + not_l) else 0
        fp_pct = fw / n_fired if n_fired else 0
        if n_fired < 3:
            continue
        print(f"  {s:<30} {fw:>7} {fl:>7} {fp_pct:>6.0%} "
              f"{wr_fired:>13.0%} {wr_not:>13.0%}")

    # ============================================================
    # PART 4: Cohort feature comparison
    # ============================================================
    print(f"\n{'='*80}")
    print(f"COHORT NUMERIC FEATURE MEANS — Buy-W vs Buy-L vs Blocked")
    print(f"{'='*80}")
    numeric_keys = [
        "pc_h24", "pc_h1", "pc_m5", "vol24h_k", "bs_h6", "bs_h1", "bs_m5",
        "chart_score", "cycles_seen", "1m_cum3", "1m_vol_spike", "mcap_m",
    ]
    print(f'  {"feature":<22} {"BUY_W":>14} {"BUY_L":>14} {"BLOCK":>14}')

    def mean_safe(rs, k):
        vs = [r.get(k) for r in rs if isinstance(r.get(k), (int, float))]
        return sum(vs) / len(vs) if vs else None

    all_blocks_flat = [e for evs in blocks.values() for e in evs]
    for k in numeric_keys:
        w = mean_safe(buy_w, k)
        l = mean_safe(buy_l, k)
        b = mean_safe(all_blocks_flat, k)
        def fmt(v):
            return f"{v:>14.2f}" if v is not None else f"{'-':>14}"
        print(f"  {k:<22} {fmt(w)} {fmt(l)} {fmt(b)}")

    # ============================================================
    # PART 5: Categorical comparison (chart_verdict, mtf, pattern_5m)
    # ============================================================
    print(f"\n{'='*80}")
    print(f"CATEGORICAL — chart_verdict / mtf / pattern_5m distribution")
    print(f"{'='*80}")
    for cat in ["chart_verdict", "mtf", "pattern_5m"]:
        print(f"\n  {cat}:")
        cw = Counter(e.get(cat) for e in buy_w)
        cl = Counter(e.get(cat) for e in buy_l)
        cb = Counter(e.get(cat) for e in all_blocks_flat)
        all_vals = set(cw) | set(cl) | set(cb)
        for v in sorted(all_vals, key=lambda x: -(cw.get(x, 0) + cl.get(x, 0))):
            print(f"    {str(v):<24}  W={cw.get(v, 0):>3}  L={cl.get(v, 0):>3}  Blk={cb.get(v, 0):>4}")


if __name__ == "__main__":
    main()
