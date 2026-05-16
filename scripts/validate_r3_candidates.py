"""Validate round 3 candidate triggers — recent 7d window + complementarity vs already-shipped.

Top candidates from round 3 mining:
  C1: mean_buy>=15 & mtf>=1 & 1s_close_pos>0.6 & bs_m5>=1.5
  C2: mean_buy>=15 & mtf>=1 & reaccum_vol>1 & 1s_close_pos>0.5
  C3: mean_buy>=15 & mtf>=1 & vp_poc<20 & 1s_close_pos>0.6
  C4: mean_buy>=15 & flow60>0 & mtf>=1 & 1s_close_pos>0.5 & bs_m5>=1.5
  C5: mean_buy>=15 & flow60>50 & mtf>=1 & 1s_close_pos>0.5 & bs_m5>=1.5
  C6: mean_buy>=15 & flow60>50 & vp_poc<20 & 1s_close_pos>0.5 & bs_m5>=1.5

Validation tasks:
 1) Cohort outcome on full set
 2) Cohort outcome on last 7d only
 3) Overlap with already-shipped triggers (strong_orderflow, sustained_accumulation,
    chart_quality_bottom, buyer_momentum_burst, flow_reversal, chart_score_reversal,
    micro_pattern_confirmed, volume_profile_aligned, quiet_1s_buyer_dominance)
 4) Marginal value: how many trades does this catch that NO existing trigger caught?
"""
from __future__ import annotations
import requests, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

API = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"


def main():
    trades = requests.get(API, timeout=20).json()
    trades = [t for t in trades if isinstance(t, dict)]
    paired = [t for t in trades if t.get("pnl_pct") is not None]

    # Reference cutoff: last 7d
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)

    def parse_time(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def in_7d(t):
        dt = parse_time(t.get("time"))
        return dt is not None and dt >= cutoff_7d

    # Define candidate predicates
    def candidate_C1(m):
        return ((m.get("mean_buy_size_usd") or 0) >= 15 and
                m.get("chart_mtf_score") is not None and m.get("chart_mtf_score") >= 1 and
                (m.get("1s_close_pos_60s") or 0) > 0.6 and
                (m.get("bs_m5") or 0) >= 1.5)

    def candidate_C2(m):
        return ((m.get("mean_buy_size_usd") or 0) >= 15 and
                m.get("chart_mtf_score") is not None and m.get("chart_mtf_score") >= 1 and
                (m.get("chart_reaccum_vol_return_ratio") or 0) > 1 and
                (m.get("1s_close_pos_60s") or 0) > 0.5)

    def candidate_C3(m):
        return ((m.get("mean_buy_size_usd") or 0) >= 15 and
                m.get("chart_mtf_score") is not None and m.get("chart_mtf_score") >= 1 and
                m.get("chart_vp_poc_distance_pct") is not None and abs(m["chart_vp_poc_distance_pct"]) < 20 and
                (m.get("1s_close_pos_60s") or 0) > 0.6)

    def candidate_C4(m):
        return ((m.get("mean_buy_size_usd") or 0) >= 15 and
                (m.get("net_flow_60s_usd") or 0) > 0 and
                m.get("chart_mtf_score") is not None and m.get("chart_mtf_score") >= 1 and
                (m.get("1s_close_pos_60s") or 0) > 0.5 and
                (m.get("bs_m5") or 0) >= 1.5)

    def candidate_C5(m):
        return ((m.get("mean_buy_size_usd") or 0) >= 15 and
                (m.get("net_flow_60s_usd") or 0) > 50 and
                m.get("chart_mtf_score") is not None and m.get("chart_mtf_score") >= 1 and
                (m.get("1s_close_pos_60s") or 0) > 0.5 and
                (m.get("bs_m5") or 0) >= 1.5)

    def candidate_C6(m):
        return ((m.get("mean_buy_size_usd") or 0) >= 15 and
                (m.get("net_flow_60s_usd") or 0) > 50 and
                m.get("chart_vp_poc_distance_pct") is not None and abs(m["chart_vp_poc_distance_pct"]) < 20 and
                (m.get("1s_close_pos_60s") or 0) > 0.5 and
                (m.get("bs_m5") or 0) >= 1.5)

    # Already-shipped trigger boolean flags (from entry_meta)
    SHIPPED = [
        "trigger_strong_orderflow",
        "trigger_sustained_accumulation",
        "trigger_chart_quality_bottom",
        "trigger_buyer_momentum_burst",
        "trigger_flow_reversal",
        "trigger_chart_score_reversal",
        "trigger_micro_pattern_confirmed",
        "trigger_volume_profile_aligned",
        "trigger_quiet_1s_buyer_dominance",
    ]

    def hit_any_shipped(m):
        for k in SHIPPED:
            if m.get(k):
                return True
        # Also check triggers_fired list (some triggers stamp differently)
        tf = m.get("triggers_fired") or []
        for tname in [
            "strong_orderflow", "sustained_accumulation", "chart_quality_bottom",
            "buyer_momentum_burst", "flow_reversal", "chart_score_reversal",
            "micro_pattern_confirmed", "volume_profile_aligned", "quiet_1s_buyer_dominance"
        ]:
            if tname in (tf or []):
                return True
        return False

    candidates = [
        ("C1: mean_buy>=15 & mtf>=1 & 1s_close_pos>0.6 & bs_m5>=1.5", candidate_C1),
        ("C2: mean_buy>=15 & mtf>=1 & reaccum_vol>1 & 1s_close_pos>0.5", candidate_C2),
        ("C3: mean_buy>=15 & mtf>=1 & vp_poc<20 & 1s_close_pos>0.6", candidate_C3),
        ("C4: mean_buy>=15 & flow60>0 & mtf>=1 & 1s_close_pos>0.5 & bs_m5>=1.5", candidate_C4),
        ("C5: mean_buy>=15 & flow60>50 & mtf>=1 & 1s_close_pos>0.5 & bs_m5>=1.5", candidate_C5),
        ("C6: mean_buy>=15 & flow60>50 & vp_poc<20 & 1s_close_pos>0.5 & bs_m5>=1.5", candidate_C6),
    ]

    print(f"Total paired trades: {len(paired)}")
    n_7d = sum(1 for t in paired if in_7d(t))
    print(f"Trades in last 7d: {n_7d}")
    print(f"Trades with any shipped trigger flag in meta: {sum(1 for t in paired if hit_any_shipped(t.get('entry_meta') or {}))}")
    print()

    for name, pred in candidates:
        print(f"=== {name} ===")
        # Full population
        hits = [t for t in paired if pred(t.get("entry_meta") or {})]
        wins = sum(1 for t in hits if t["pnl_pct"] > 0)
        n = len(hits)
        if n == 0:
            print("  (no trades match)\n")
            continue
        avg = sum(t["pnl_pct"] for t in hits) / n
        print(f"  Full lifetime: n={n}, wins={wins}, WR={wins/n:.0%}, avg_pnl={avg:+.2f}%")

        # Last 7d
        hits_7d = [t for t in hits if in_7d(t)]
        if hits_7d:
            wins7 = sum(1 for t in hits_7d if t["pnl_pct"] > 0)
            avg7 = sum(t["pnl_pct"] for t in hits_7d) / len(hits_7d)
            print(f"  Last 7d:       n={len(hits_7d)}, wins={wins7}, WR={wins7/len(hits_7d):.0%}, avg_pnl={avg7:+.2f}%")
        else:
            print(f"  Last 7d: 0 hits")

        # Marginal (not caught by any shipped trigger)
        novel = [t for t in hits if not hit_any_shipped(t.get("entry_meta") or {})]
        if novel:
            wins_n = sum(1 for t in novel if t["pnl_pct"] > 0)
            avg_n = sum(t["pnl_pct"] for t in novel) / len(novel)
            print(f"  Novel (no existing trigger fired): n={len(novel)}, wins={wins_n}, WR={wins_n/len(novel):.0%}, avg={avg_n:+.2f}%")
        else:
            print(f"  Novel: 0 (every hit was already caught by an existing trigger)")

        # Overlap with shipped triggers
        overlap_counts = defaultdict(int)
        for t in hits:
            m = t.get("entry_meta") or {}
            tf = m.get("triggers_fired") or []
            for k in SHIPPED:
                if m.get(k):
                    overlap_counts[k.replace("trigger_", "")] += 1
            for tname in [
                "strong_orderflow", "sustained_accumulation", "chart_quality_bottom",
                "buyer_momentum_burst", "flow_reversal", "chart_score_reversal",
                "micro_pattern_confirmed", "volume_profile_aligned", "quiet_1s_buyer_dominance"
            ]:
                if tname in (tf or []):
                    overlap_counts[tname] += 1
        if overlap_counts:
            ov = ", ".join(f"{k}={v}" for k, v in sorted(overlap_counts.items(), key=lambda x: -x[1])[:5])
            print(f"  Overlap (shipped triggers also fire): {ov}")
        print()


if __name__ == "__main__":
    main()
