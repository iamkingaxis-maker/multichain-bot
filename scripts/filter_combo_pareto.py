"""
Pareto-aware exhaustive combo search.

Differs from filter_combo_exhaustive.py:
  - Ranks combos by $/day (estimated daily PnL on production rate),
    not by Wilson lower bound on win rate.
  - CI lower bound is a constraint (>= 50%), not the optimization target.
  - Reports the trade-off frontier at multiple block-rate buckets so
    you can pick a deployment based on throughput preference.
  - Prints the full kept_n / block_pct / WR / CI_lo / total_PnL /
    per_trade_PnL / est_daily_PnL table for every survivor.

Search space (current regime — post-Apr-30 cohort, n=466):
  - All OR-block combos of size 1..5  (≈8M combos)
  - All AND-allow combos of size 1..3
  - Hybrid combos: 1B+1A, 2B+1A, 1B+2A
  - Library: 64 BLOCK rules + 30 ALLOW rules

Survivors: combos with kept_n>=20 AND CI_lo>=0.50.
Reported: top 25 by est_daily_PnL, plus frontier at 95%/85%/70%/50% block.
"""
from __future__ import annotations
import json, math, time, itertools as it, sys
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np
sys.stdout.reconfigure(encoding="utf-8")


def parse_iso(ts):
    if not ts: return None
    if ts.endswith("Z"): ts = ts[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(ts)
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d
    except: return None

def is_dip_close(s):
    if s.get("type") != "sell": return False
    r = (s.get("reason") or "").lower()
    if "cancelled on restart" in r: return False
    return any(k in r for k in ("dip stop","dip tp","dip trail","dip max","dip stall"))


def load_and_join():
    raw = json.load(open("all_trades.json"))
    trades = raw if isinstance(raw, list) else raw.get("trades", [])
    buys = [t for t in trades if t.get("type")=="buy" and t.get("strategy")=="dip_buy"]
    sells = [t for t in trades if is_dip_close(t)]
    by_pair = defaultdict(list)
    for b in buys:
        by_pair[(b.get("pair_address") or b.get("address") or "").lower()].append(b)
    for k in by_pair: by_pair[k].sort(key=lambda b: b.get("time",""))
    rows = []
    for s in sells:
        key = (s.get("pair_address") or s.get("address") or "").lower()
        cands = [b for b in by_pair.get(key,[]) if b.get("time","") < s.get("time","")]
        if not cands: continue
        if (s.get("pnl_pct") or 0) <= -15: continue
        rows.append({"em":cands[-1].get("entry_meta") or {}, "pnl":s.get("pnl") or 0,
                     "pnl_pct": s.get("pnl_pct") or 0, "time":cands[-1].get("time","")})
    rows.sort(key=lambda r: r["time"])
    return rows


def in_band(em,k,lo,hi):
    v=em.get(k)
    if v is None: return False
    try: return lo<=float(v)<hi
    except: return False
def gt(em,k,t):
    v=em.get(k)
    if v is None: return False
    try: return float(v)>t
    except: return False
def lt(em,k,t):
    v=em.get(k)
    if v is None: return False
    try: return float(v)<t
    except: return False
def eq(em,k,v): return em.get(k)==v
def in_set(em, k, S): return em.get(k) in S


# ── Same BLOCK + ALLOW libraries as filter_combo_exhaustive.py ──
BLOCK = {
    "B_struct1h_TUP":      lambda em: eq(em,"chart_structure_1h_verdict","TREND_UP"),
    "B_struct15m_TUP":     lambda em: eq(em,"chart_structure_15m_verdict","TREND_UP"),
    "B_struct15m_RUP":     lambda em: eq(em,"chart_structure_15m_verdict","REVERSAL_UP"),
    "B_struct5m_TUP":      lambda em: eq(em,"chart_structure_5m_verdict","TREND_UP"),
    "B_struct5m_RUP":      lambda em: eq(em,"chart_structure_5m_verdict","REVERSAL_UP"),
    "B_struct5m_RDN":      lambda em: eq(em,"chart_structure_5m_verdict","REVERSAL_DOWN"),
    "B_sweep5m_BEAR":      lambda em: eq(em,"chart_sweep_5m_verdict","BEARISH_SWEEP"),
    "B_sweep5m_NONE":      lambda em: eq(em,"chart_sweep_5m_verdict","NONE"),
    "B_reaccum_DUMP":      lambda em: eq(em,"chart_reaccum_verdict","DUMPING"),
    "B_trendline5_BUP":    lambda em: eq(em,"chart_trendline_5m_verdict","BREAKOUT_UP"),
    "B_trendline5_BLK":    lambda em: eq(em,"chart_trendline_5m_verdict","BLOCK"),
    "B_trendline5_BDN":    lambda em: eq(em,"chart_trendline_5m_verdict","BREAKDOWN"),
    "B_chart_score_hi":    lambda em: gt(em,"chart_score",59.999) if em.get("chart_full_coverage") else False,
    "B_chart_score_lo":    lambda em: lt(em,"chart_score",30) if em.get("chart_full_coverage") else False,
    "B_chart_v_neutral":   lambda em: eq(em,"chart_verdict","neutral"),
    "B_pat5m_bearish":     lambda em: eq(em,"chart_pattern_5m_dir","bearish"),
    "B_pat5m_no_dir":      lambda em: em.get("chart_pattern_5m_dir") in ("none", None) and em.get("chart_full_coverage"),
    "B_mtf_strong_bull":   lambda em: eq(em,"chart_mtf_alignment","strong_bull"),
    "B_peak_300":          lambda em: gt(em,"peak_h24_6h_pct",300),
    "B_peak_500":          lambda em: gt(em,"peak_h24_6h_pct",500),
    "B_peak_1000":         lambda em: gt(em,"peak_h24_6h_pct",1000),
    "B_peak_neg":          lambda em: lt(em,"peak_h24_6h_pct",-17),
    "B_stop_cluster_close":lambda em: in_band(em,"chart_stop_cluster_5m_pct_below",1.26,3.78) if em.get("chart_full_coverage") else False,
    "B_stop_cluster_dense":lambda em: gt(em,"chart_stop_cluster_5m_density",1.999) if em.get("chart_full_coverage") else False,
    "B_velocity_QUIET":    lambda em: eq(em,"velocity_verdict","QUIET"),
    "B_velocity_SBUY":     lambda em: eq(em,"velocity_verdict","SURGE_BUY"),
    "B_buy_pressure_hi":   lambda em: gt(em,"buy_pressure_60s",0.67),
    "B_buy_pressure_lo":   lambda em: lt(em,"buy_pressure_60s",0.40),
    "B_1m_volspike_lo":    lambda em: in_band(em,"1m_volume_spike",0.31,0.80),
    "B_1m_volspike_v_lo":  lambda em: lt(em,"1m_volume_spike",0.30),
    "B_1m_lastclose_neg":  lambda em: in_band(em,"1m_last_close_pct",-0.70,0.00),
    "B_1m_lastclose_pos":  lambda em: gt(em,"1m_last_close_pct",1.75),
    "B_top1_pct_hi":       lambda em: gt(em,"top1_holder_pct",20.69),
    "B_top1_pct_v_hi":     lambda em: gt(em,"top1_holder_pct",30),
    "B_top1_share_band":   lambda em: in_band(em,"top1_share_of_top10",0.41,0.44),
    "B_top1_share_hi":     lambda em: gt(em,"top1_share_of_top10",0.45),
    "B_top10_pct_hi":      lambda em: gt(em,"top10_holder_pct",60.15),
    "B_top10_pct_v_hi":    lambda em: gt(em,"top10_holder_pct",70),
    "B_lp_locked_mid":     lambda em: in_band(em,"lp_locked_pct",60.15,78.90),
    "B_lp_locked_lo":      lambda em: lt(em,"lp_locked_pct",50),
    "B_uniqbuy_lo":        lambda em: lt(em,"unique_buyers_n",20),
    "B_uniqbuy_v_lo":      lambda em: lt(em,"unique_buyers_n",10),
    "B_top5_buyvol_hi":    lambda em: gt(em,"top5_buyer_volume_pct",0.65),
    "B_top5_buyvol_v_hi":  lambda em: gt(em,"top5_buyer_volume_pct",0.80),
    "B_uniqbuy_ratio_hi":  lambda em: in_band(em,"unique_buyer_ratio",0.83,0.95),
    "B_n_recur_lo":        lambda em: lt(em,"n_recurring_buyers_3plus",2),
    "B_median_buy_lo":     lambda em: lt(em,"median_buy_size_usd",6.45),
    "B_median_buy_hi":     lambda em: gt(em,"median_buy_size_usd",100),
    "B_active_runner":     lambda em: eq(em,"lifecycle_stage","active_runner"),
    "B_reviving":          lambda em: eq(em,"lifecycle_stage","reviving"),
    "B_post_pump_corpse":  lambda em: eq(em,"lifecycle_stage","post_pump_corpse"),
    "B_lifecycle_young":   lambda em: lt(em,"lifecycle_age_hours",6),
    "B_lifecycle_v_young": lambda em: lt(em,"lifecycle_age_hours",3),
    "B_just_graduated":    lambda em: eq(em,"graduation_status","just_graduated"),
    "B_just_grad_high_peak": lambda em: eq(em,"graduation_status","just_graduated") and gt(em,"peak_h24_6h_pct",500),
    "B_sol_pc_h1_hi":      lambda em: gt(em,"sol_pc_h1",0.16),
    "B_sol_pc_h1_neg":     lambda em: lt(em,"sol_pc_h1",-0.10),
    "B_filter_a":          lambda em: em.get("filter_a_verdict") == "BLOCK",
    "B_filter_1m":         lambda em: em.get("filter_1m_verdict") == "BLOCK",
    "B_filter_quad":       lambda em: em.get("filter_quad_verdict") == "BLOCK",
    "B_filter_quad_robust":lambda em: em.get("filter_quad_robust_verdict") == "BLOCK",
    "B_filter_quad_hi_wr": lambda em: em.get("filter_quad_hi_wr_verdict") == "BLOCK",
    "B_ema_NEUTRAL":       lambda em: eq(em,"token_ema_verdict","NEUTRAL"),
    "B_ema_BEAR":          lambda em: eq(em,"token_ema_verdict","BEAR"),
}


def precompute_masks(rows, lib):
    fids = list(lib.keys())
    if len(fids) > 64:
        raise ValueError(f"Library size {len(fids)} exceeds 64-bit mask")
    N = len(rows)
    masks = np.zeros(N, dtype=np.uint64)
    for j, fid in enumerate(fids):
        pred = lib[fid]
        bit = np.uint64(1) << np.uint64(j)
        for i, r in enumerate(rows):
            if pred(r["em"]):
                masks[i] |= bit
    return fids, masks


def wilson(wins, n, z=1.96):
    if n == 0: return 0,0,0
    p=wins/n; d=1+z*z/n
    c=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return p, max(0,c-h), min(1,c+h)


def main():
    t0 = time.time()
    rows = load_and_join()
    SINCE = "2026-04-30T00:00:00Z"
    cohort = [r for r in rows if r["time"] >= SINCE]
    N = len(cohort)
    is_win = np.array([r["pnl"] > 0 for r in cohort], dtype=bool)
    pnls = np.array([r["pnl"] for r in cohort], dtype=float)
    # Days in cohort
    if cohort:
        t_first = parse_iso(cohort[0]["time"])
        t_last = parse_iso(cohort[-1]["time"])
        cohort_days = max((t_last - t_first).total_seconds() / 86400.0, 0.5)
    else:
        cohort_days = 1.0

    base_wins = int(is_win.sum())
    base_pnl = float(pnls.sum())
    print(f"Cohort: {SINCE} to now")
    print(f"  n={N}  cohort_days={cohort_days:.1f}")
    print(f"  unfiltered: WR={base_wins/N*100:.1f}%, PnL=${base_pnl:+.2f} (${base_pnl/cohort_days:+.2f}/day)")
    print()

    print(f"BLOCK library: {len(BLOCK)} filters")
    print("Precomputing bitmasks...")
    fids, masks = precompute_masks(cohort, BLOCK)
    NB = len(fids)
    print(f"  done in {time.time()-t0:.1f}s")
    print()

    # Helper: evaluate combo
    def eval_combo(combo_idx):
        cmask = np.uint64(0)
        for j in combo_idx:
            cmask |= np.uint64(1) << np.uint64(j)
        keep = (masks & cmask) == 0
        n = int(keep.sum())
        if n == 0: return None
        wins = int((keep & is_win).sum())
        pnl = float(pnls[keep].sum())
        return n, wins, pnl

    # ── Search all combos size 1..5 ──
    survivors = []  # (combo_fids, kept_n, wins, pnl, ci_lo, ci_hi, est_daily_pnl)
    for size in (1, 2, 3, 4):
        n_combos = math.comb(NB, size)
        t1 = time.time()
        kept_count = 0
        for combo_idx in it.combinations(range(NB), size):
            res = eval_combo(combo_idx)
            if res is None: continue
            n, wins, pnl = res
            if n < 20: continue
            p, lo, hi = wilson(wins, n)
            if lo < 0.50: continue
            est_daily = pnl / cohort_days
            survivors.append((
                tuple(fids[j] for j in combo_idx), n, wins, p, lo, hi, pnl, est_daily, size
            ))
            kept_count += 1
        print(f"  size={size}: searched {n_combos:,} in {time.time()-t1:.1f}s, kept {kept_count}")

    # Size 5 — only on top-30 single filters (else too many)
    single_lo = []
    for j in range(NB):
        res = eval_combo((j,))
        if res:
            n, wins, pnl = res
            _, lo, _ = wilson(wins, n)
            single_lo.append((j, lo, pnl))
    single_lo.sort(key=lambda x: -x[2])  # rank by total PnL (favors high-edge filters)
    top30 = [j for j, _, _ in single_lo[:30]]
    n_combos = math.comb(30, 5)
    t1 = time.time()
    kept_count = 0
    for combo_idx in it.combinations(top30, 5):
        res = eval_combo(combo_idx)
        if res is None: continue
        n, wins, pnl = res
        if n < 20: continue
        p, lo, hi = wilson(wins, n)
        if lo < 0.50: continue
        est_daily = pnl / cohort_days
        survivors.append((
            tuple(fids[j] for j in combo_idx), n, wins, p, lo, hi, pnl, est_daily, 5
        ))
        kept_count += 1
    print(f"  size=5 (top-30): searched {n_combos:,} in {time.time()-t1:.1f}s, kept {kept_count}")
    print()

    print(f"Total survivors with CI_lo>=50%: {len(survivors)}")
    if not survivors:
        print("No survivors. Lower threshold or expand library.")
        return

    # ── Top 30 by $/day ──
    survivors.sort(key=lambda r: -r[7])
    print()
    print("=" * 130)
    print(f"TOP 30 by est $/day (CI_lo>=50%, kept_n>=20)")
    print("=" * 130)
    print(f"{'Rank':>4s} {'Combo':75s} {'sz':>2s} {'kept':>5s} {'block%':>7s} {'WR':>6s} {'CI_lo':>6s} {'PnL':>8s} {'$/trade':>8s} {'$/day':>7s}")
    print("-" * 130)
    for rank, (fids_t, n, w, p, lo, hi, pnl, daily, sz) in enumerate(survivors[:30], 1):
        cs = " | ".join(fids_t)[:75]
        block_pct = (1 - n/N) * 100
        per_t = pnl / n
        print(f"{rank:4d} {cs:75s} {sz:2d}  {n:5d}  {block_pct:6.1f}% {p*100:5.1f}% {lo*100:5.1f}%  ${pnl:+7.2f} ${per_t:+7.3f} ${daily:+6.2f}")

    # ── Pareto frontier: best $/day at each block-rate bucket ──
    print()
    print("=" * 130)
    print("PARETO FRONTIER — best $/day at each block_pct bucket (10pp buckets)")
    print("=" * 130)
    buckets = {}
    for s in survivors:
        fids_t, n, w, p, lo, _, pnl, daily, sz = s
        block_pct = (1 - n/N) * 100
        b = int(block_pct // 10) * 10
        if b not in buckets or buckets[b][7] < daily:
            buckets[b] = s
    print(f"{'block%':>7s} {'kept':>5s} {'WR':>6s} {'CI_lo':>6s} {'PnL':>8s} {'$/trade':>8s} {'$/day':>7s} {'sz':>2s} {'Combo':75s}")
    print("-" * 130)
    for b in sorted(buckets.keys()):
        fids_t, n, w, p, lo, _, pnl, daily, sz = buckets[b]
        cs = " | ".join(fids_t)[:75]
        per_t = pnl / n
        print(f"{b:6d}% {n:5d}  {p*100:5.1f}% {lo*100:5.1f}%  ${pnl:+7.2f} ${per_t:+7.3f} ${daily:+6.2f}  {sz:2d}  {cs}")

    # ── Top 10 by raw PnL (sanity check — these are the high-volume picks) ──
    print()
    print("=" * 130)
    print("TOP 10 by total PnL (high-volume picks)")
    print("=" * 130)
    by_pnl = sorted(survivors, key=lambda r: -r[6])[:10]
    print(f"{'Combo':75s} {'sz':>2s} {'kept':>5s} {'block%':>7s} {'WR':>6s} {'CI_lo':>6s} {'PnL':>8s} {'$/day':>7s}")
    print("-" * 130)
    for fids_t, n, w, p, lo, _, pnl, daily, sz in by_pnl:
        cs = " | ".join(fids_t)[:75]
        block_pct = (1 - n/N) * 100
        print(f"{cs:75s} {sz:2d}  {n:5d}  {block_pct:6.1f}% {p*100:5.1f}% {lo*100:5.1f}%  ${pnl:+7.2f} ${daily:+6.2f}")

    print()
    print(f"=== TOTAL RUNTIME: {time.time()-t0:.1f}s ===")


if __name__ == "__main__":
    main()
