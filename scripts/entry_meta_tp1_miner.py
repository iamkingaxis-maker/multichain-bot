"""Mine entry_meta features that predict TP1 hits vs STOP hits.

This is a cleaner outcome signal than P&L bucketing — TP1 means the
token actually moved +8% within max_hold (fast bouncer). STOP means
-12% (loser). FLAT/TRAIL/VOLUME are middle ground.

Goal: find single + 2-feature combos where P(TP1 | feature) is
much higher than baseline.
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
        bt = b.get("time") or ""
        if bt < CUTOFF or not em:
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
        cat = "OTHER"
        if "tp1" in last_reason and "trail" not in last_reason:
            cat = "TP1"
        elif "stop" in last_reason:
            cat = "STOP"
        elif "trail" in last_reason:
            cat = "TRAIL"
        elif "volume" in last_reason:
            cat = "VOLUME"
        out.append({"em": em, "pnl": pnl, "cat": cat, "token": b.get("token")})
    return out


def main():
    trades = load_trades()
    n = len(trades)
    print(f"Trades: {n}")
    cats = defaultdict(int)
    for t in trades:
        cats[t["cat"]] += 1
    for c, k in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {c}: {k}")
    base_tp1_pct = cats["TP1"] / n * 100
    print(f"\nBaseline TP1-hit rate: {base_tp1_pct:.1f}%")
    print()

    # Numeric features
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
        "wick_body_5m_avg",
        "cycles_seen_before_buy",
        "liquidity_usd",
        "concurrent_positions_at_entry",
    ]

    # Test thresholds and gather TP1-rate
    print(f"=== Single-feature thresholds (matched n>=40, TP1-rate >= 50%, lift >= 1.4x) ===")
    print(f"{'feature':<28} {'op':>3} {'thr':>11} {'n':>4} {'tp1%':>5} {'lift':>5} {'avg$':>6}")
    rows = []
    for feat in numeric_features:
        vals = []
        for t in trades:
            v = t["em"].get(feat)
            if v is None: continue
            try: vals.append((float(v), t))
            except: continue
        if len(vals) < 50: continue
        vals.sort(key=lambda x: x[0])
        for pct in (10, 25, 50, 75, 90):
            idx = int(len(vals) * pct / 100)
            thr = vals[idx][0]
            for op in (">=", "<"):
                if op == ">=":
                    cohort = [t for v, t in vals if v >= thr]
                else:
                    cohort = [t for v, t in vals if v < thr]
                if len(cohort) < 40: continue
                tp1_n = sum(1 for t in cohort if t["cat"] == "TP1")
                tp1_pct = tp1_n / len(cohort) * 100
                avg_p = sum(t["pnl"] for t in cohort) / len(cohort)
                lift = tp1_pct / base_tp1_pct
                rows.append({
                    "feat": feat, "op": op, "thr": thr,
                    "n": len(cohort), "tp1_n": tp1_n,
                    "tp1_pct": tp1_pct, "lift": lift, "avg": avg_p,
                })
    rows.sort(key=lambda r: -r["tp1_pct"])
    for r in rows[:30]:
        if r["tp1_pct"] < 50 or r["lift"] < 1.4:
            continue
        thr_str = f"{r['thr']:.3f}" if abs(r['thr']) < 1000 else f"{r['thr']:,.0f}"
        print(f"{r['feat']:<28} {r['op']:>3} {thr_str:>11} {r['n']:>4} "
              f"{r['tp1_pct']:>4.1f}% {r['lift']:>4.2f}x ${r['avg']:>+5.2f}")

    # 2-feature combos
    print()
    print(f"=== 2-FEATURE AND COMBOS (top 8 single TP1 hits) ===")
    top = [r for r in rows if r["tp1_pct"] >= 45 and r["n"] >= 50][:8]
    if len(top) < 2:
        print("Not enough top singles to combine.")
        return

    print(f"\n{'combo':<70} {'n':>4} {'tp1%':>5} {'avg$':>7}")
    seen = set()
    pair_results = []
    for i in range(len(top)):
        for j in range(i+1, len(top)):
            r1, r2 = top[i], top[j]
            if r1["feat"] == r2["feat"]: continue
            sig = tuple(sorted([r1["feat"], r2["feat"]]))
            if sig in seen: continue
            seen.add(sig)
            cohort = []
            for t in trades:
                v1 = t["em"].get(r1["feat"]); v2 = t["em"].get(r2["feat"])
                if v1 is None or v2 is None: continue
                try: v1 = float(v1); v2 = float(v2)
                except: continue
                ok1 = (v1 >= r1["thr"]) if r1["op"] == ">=" else (v1 < r1["thr"])
                ok2 = (v2 >= r2["thr"]) if r2["op"] == ">=" else (v2 < r2["thr"])
                if ok1 and ok2: cohort.append(t)
            if len(cohort) < 25: continue
            tp1_n = sum(1 for t in cohort if t["cat"] == "TP1")
            tp1_pct = tp1_n / len(cohort) * 100
            avg_p = sum(t["pnl"] for t in cohort) / len(cohort)
            pair_results.append({
                "f1": r1["feat"], "thr1": r1["thr"], "op1": r1["op"],
                "f2": r2["feat"], "thr2": r2["thr"], "op2": r2["op"],
                "n": len(cohort), "tp1_pct": tp1_pct, "avg": avg_p,
            })
    pair_results.sort(key=lambda r: -r["tp1_pct"])
    for r in pair_results[:20]:
        if r["tp1_pct"] < 50: continue
        combo = f"{r['f1']}{r['op1']}{r['thr1']:.2f} & {r['f2']}{r['op2']}{r['thr2']:.2f}"
        if len(combo) > 68: combo = combo[:67] + "…"
        print(f"{combo:<70} {r['n']:>4} {r['tp1_pct']:>4.1f}% ${r['avg']:>+5.2f}")


if __name__ == "__main__":
    main()
