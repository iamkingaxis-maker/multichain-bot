"""Mine entry_meta features for high-WR patterns on ACTUAL bot trades.

Approach: instead of OHLCV simulation, use bot's actual closed trades
with their entry_meta + realized P&L. This captures features only
available at trade time (lp_locked, dev_pct, bs_ratios, structure
verdicts, etc.) that the master bar dataset doesn't have.

Goal: find feature combinations producing WR >= 60% on actual P&L
outcomes, with sample size >= 30 (meaningful) and orthogonal to
existing trigger conditions.
"""
import json
from collections import defaultdict


CUTOFF = "2026-05-04T00:00:00"


def load_trades():
    data = json.load(open("trades_full.json", encoding="utf-8"))
    buys = [t for t in data if t.get("type") == "buy"
            and t.get("strategy") == "dip_buy"]
    sells = [t for t in data if t.get("type") == "sell"]
    sell_idx = defaultdict(list)
    for s in sells:
        sell_idx[(s.get("address"), s.get("pair_address"))].append(s)
    buys_by_key = defaultdict(list)
    for b in buys:
        buys_by_key[(b.get("address"), b.get("pair_address"))].append(b.get("time") or "")
    for k in buys_by_key:
        buys_by_key[k].sort()

    out = []
    for b in buys:
        em = b.get("entry_meta") or {}
        if not em:
            continue
        bt = b.get("time") or ""
        if bt < CUTOFF:
            continue
        addr = b.get("address")
        pair = b.get("pair_address")
        next_bt = "9999"
        for c in buys_by_key.get((addr, pair), []):
            if c > bt:
                next_bt = c
                break
        rel = [s for s in sell_idx.get((addr, pair), [])
               if bt < (s.get("time") or "") < next_bt]
        if not rel:
            continue
        rel.sort(key=lambda s: s.get("time") or "")
        last_reason = (rel[-1].get("reason") or "").lower()
        if "restart" in last_reason and len(rel) == 1:
            continue
        pnl = sum(float(s.get("pnl") or 0) for s in rel)
        out.append({"em": em, "pnl": pnl, "token": b.get("token"), "time": bt})
    return out


def main():
    trades = load_trades()
    n = len(trades)
    print(f"Trades since {CUTOFF}: {n}")
    if not n:
        return

    # Compute baseline
    avg = sum(t["pnl"] for t in trades) / n
    wr = sum(1 for t in trades if t["pnl"] > 0) / n * 100
    print(f"Baseline: avg=${avg:+.3f}/trade WR={wr:.1f}%")
    print()

    # Define "FAST WIN" = pnl > $1.0 (clear winner, not just barely positive)
    # "LOSER" = pnl < 0 (any loss)
    fast_w = [t for t in trades if t["pnl"] > 1.0]
    losers = [t for t in trades if t["pnl"] < 0]
    print(f"FAST_WIN (>$1):  {len(fast_w)}")
    print(f"LOSER (<$0):     {len(losers)}")
    print(f"FLAT (0-$1):     {n - len(fast_w) - len(losers)}")
    print()

    # Numeric features to scan
    numeric_features = [
        "lp_locked_pct", "rugcheck_score",
        "top10_holder_pct", "top1_holder_pct",
        "lp_dominant_depth_usd",
        "bs_m5", "bs_h1", "bs_h6",
        "1m_volume_spike", "1m_cum_3min_pct",
        "1m_consec_red", "1m_red_count_5",
        "1m_higher_highs",
        "5m_consec_green", "5m_consec_red", "5m_red_count",
        "5m_vol_decay",
        "net_flow_5m_imbalance", "net_flow_5m_usd",
        "regime_dip_breadth_pct", "regime_h1_neg_pct",
        "pct_off_peak", "minutes_since_peak",
        "peak_h24_6h_pct", "lifecycle_peak_h24_pct",
        "h1_peak_in_window", "h6_peak_in_window",
        "regime_dip_breadth_pct",
        "wick_body_5m_avg", "upper_wick_dom_5m_avg",
        "cycles_seen_before_buy",
        "liquidity_usd",
        "dev_pct_remaining",  # if logged separately
        "concurrent_positions_at_entry",
        "trades_today_at_entry",
        "available_capital_at_entry",
        "chart_pattern_5m_conf", "chart_pattern_15m_conf",
    ]

    # Categorical features (state/verdict)
    categorical_features = [
        "chart_structure_5m_state", "chart_structure_15m_state",
        "chart_structure_1h_state",
        "chart_pattern_5m_dir", "chart_pattern_15m_dir",
        "chart_sweep_5m_verdict", "chart_sweep_15m_verdict",
        "chart_trendline_5m_verdict", "chart_trendline_15m_verdict",
        "chart_verdict",
        "chart_structure_5m_recent_choch_dir",
        "chart_structure_15m_recent_choch_dir",
        "protocol",
    ]

    # ── Single-feature thresholds ───────────────────────────────
    print("=" * 90)
    print("SINGLE-FEATURE THRESHOLD SWEEP (recent trades only)")
    print("=" * 90)
    print(f"{'feature':<30} {'thr':>10} {'op':>3} {'matched':>7} {'fw_in':>5} {'l_in':>5} {'WR%':>5} {'avg$':>6}")

    rows = []
    for feat in numeric_features:
        # Collect values from trades that have this feature
        vals = []
        for t in trades:
            v = t["em"].get(feat)
            if v is None:
                continue
            try:
                vals.append((float(v), t["pnl"]))
            except (TypeError, ValueError):
                continue
        if len(vals) < 50:
            continue
        vals.sort(key=lambda x: x[0])

        # Try percentile thresholds for >= and <
        for pct in (25, 50, 75, 80, 90, 95):
            idx = int(len(vals) * pct / 100)
            thr = vals[idx][0]
            for op in (">=", "<"):
                if op == ">=":
                    cohort = [v for v, p in vals if v >= thr]
                    pnls = [p for v, p in vals if v >= thr]
                else:
                    cohort = [v for v, p in vals if v < thr]
                    pnls = [p for v, p in vals if v < thr]
                if len(pnls) < 30:
                    continue
                matched = len(pnls)
                fw = sum(1 for p in pnls if p > 1.0)
                l = sum(1 for p in pnls if p < 0)
                wr_pct = sum(1 for p in pnls if p > 0) / matched * 100
                avg_p = sum(pnls) / matched
                rows.append({
                    "feat": feat, "op": op, "thr": thr,
                    "matched": matched, "fw": fw, "l": l,
                    "wr": wr_pct, "avg": avg_p,
                })

    # Filter rows with WR >= 60% and matched >= 30
    rows.sort(key=lambda r: (-r["wr"], -r["matched"]))
    print()
    print("Top single-feature splits with WR >= 60% (matched >= 30):")
    for r in rows[:30]:
        if r["wr"] < 60 or r["matched"] < 30:
            continue
        thr_str = f"{r['thr']:.3f}" if abs(r['thr']) < 1000 else f"{r['thr']:,.0f}"
        print(f"{r['feat']:<30} {thr_str:>10} {r['op']:>3} {r['matched']:>7} "
              f"{r['fw']:>5} {r['l']:>5} {r['wr']:>4.1f}% ${r['avg']:>+5.2f}")

    # ── Categorical features ───────────────────────────────────
    print()
    print("=" * 90)
    print("CATEGORICAL FEATURE BREAKDOWN")
    print("=" * 90)
    for feat in categorical_features:
        groups = defaultdict(list)
        for t in trades:
            v = t["em"].get(feat)
            if v is None:
                continue
            groups[str(v)].append(t["pnl"])
        if not groups:
            continue
        print(f"\n{feat}:")
        for val, pnls in sorted(groups.items(), key=lambda x: -len(x[1])):
            if len(pnls) < 20:
                continue
            mat = len(pnls)
            wr_pct = sum(1 for p in pnls if p > 0) / mat * 100
            avg_p = sum(pnls) / mat
            marker = "  PASS  " if wr_pct >= 60 and mat >= 30 else ""
            print(f"  {val:<30} n={mat:>3} WR={wr_pct:>4.1f}% avg=${avg_p:>+5.2f}{marker}")

    # ── 2-feature combinations on top single discriminators ────
    print()
    print("=" * 90)
    print("2-FEATURE AND COMBINATIONS (top single hits)")
    print("=" * 90)

    # Take top 8 single-feature hits with WR >= 60% AND meaningful matched
    top_singles = [r for r in rows if r["wr"] >= 60 and r["matched"] >= 50]
    top_singles.sort(key=lambda r: (-r["wr"], -r["matched"]))
    top8 = top_singles[:8]
    if len(top8) < 2:
        print("Not enough top-singles to combine.")
        return

    seen = set()
    pair_results = []
    for i in range(len(top8)):
        for j in range(i+1, len(top8)):
            r1 = top8[i]
            r2 = top8[j]
            if r1["feat"] == r2["feat"]:
                continue
            sig = tuple(sorted([r1["feat"], r2["feat"]]))
            if sig in seen:
                continue
            seen.add(sig)
            # Apply both
            cohort = []
            for t in trades:
                v1 = t["em"].get(r1["feat"])
                v2 = t["em"].get(r2["feat"])
                if v1 is None or v2 is None:
                    continue
                try:
                    v1 = float(v1)
                    v2 = float(v2)
                except (TypeError, ValueError):
                    continue
                ok1 = (v1 >= r1["thr"]) if r1["op"] == ">=" else (v1 < r1["thr"])
                ok2 = (v2 >= r2["thr"]) if r2["op"] == ">=" else (v2 < r2["thr"])
                if ok1 and ok2:
                    cohort.append(t["pnl"])
            if len(cohort) < 20:
                continue
            wr_pct = sum(1 for p in cohort if p > 0) / len(cohort) * 100
            avg_p = sum(cohort) / len(cohort)
            pair_results.append({
                "f1": r1["feat"], "thr1": r1["thr"], "op1": r1["op"],
                "f2": r2["feat"], "thr2": r2["thr"], "op2": r2["op"],
                "n": len(cohort), "wr": wr_pct, "avg": avg_p,
            })
    pair_results.sort(key=lambda r: (-r["wr"], -r["n"]))
    print(f"\n{'combo':<70} {'n':>4} {'WR%':>5} {'avg$':>7}")
    for r in pair_results[:20]:
        if r["wr"] < 60 or r["n"] < 20:
            continue
        combo = f"{r['f1']}{r['op1']}{r['thr1']:.2f} & {r['f2']}{r['op2']}{r['thr2']:.2f}"
        if len(combo) > 68:
            combo = combo[:67] + "…"
        print(f"{combo:<70} {r['n']:>4} {r['wr']:>4.1f}% ${r['avg']:>+5.2f}")


if __name__ == "__main__":
    main()
