"""Audit exit timing on on-chain trigger trades.

User flagged: PAC exited at +1.82% with peak +5.2% — captured 35% of peak.
Question: is exit logic too tight for the new on-chain trigger archetype?

For each closed trade with an on-chain trigger in triggers_fired:
 - peak_pnl_pct (max during hold)
 - realized_pnl_pct (exit)
 - capture_ratio = realized / peak
 - hold_seconds
 - exit_reason

Compare against trades that fired non-on-chain triggers ("baseline") to see
if the on-chain trigger trades have systematically different peak vs exit
behavior.
"""
from __future__ import annotations
import requests, json
from datetime import datetime, timezone
from collections import defaultdict

API_CLOSED = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"

ONCHAIN_TRIGGERS = {
    "strong_orderflow", "sustained_accumulation", "chart_quality_bottom",
    "buyer_momentum_burst", "flow_reversal", "chart_score_reversal",
    "micro_pattern_confirmed", "volume_profile_aligned",
    "quiet_1s_buyer_dominance",
    # R3 just shipped
    "vp_poc_orderflow_bounce", "reaccum_vol_bounce", "tight_buyer_mtf",
}


def main():
    trades = requests.get(API_CLOSED, timeout=20).json()
    trades = [t for t in trades if isinstance(t, dict)]
    paired = [t for t in trades if t.get("pnl_pct") is not None and t.get("peak_pnl_pct") is not None]
    print(f"Closed trades with peak data: {len(paired)}")

    onchain = []
    baseline = []
    for t in paired:
        m = t.get("entry_meta") or {}
        tf = m.get("triggers_fired") or []
        ts = (m.get("trigger_source") or "")
        all_triggers = set(tf) | set(ts.split("_") if isinstance(ts, str) else [])
        if any(x in ONCHAIN_TRIGGERS for x in all_triggers):
            onchain.append(t)
        else:
            baseline.append(t)

    print(f"  On-chain trigger trades: {len(onchain)}")
    print(f"  Baseline (other trigger) trades: {len(baseline)}")

    def summarize(group, label):
        n = len(group)
        if n == 0:
            print(f"\n=== {label} (n=0) ===")
            return
        peaks = [t.get("peak_pnl_pct", 0) for t in group]
        exits = [t.get("pnl_pct", 0) for t in group]
        capture_ratios = []
        for t in group:
            p, e = t.get("peak_pnl_pct"), t.get("pnl_pct")
            if p is not None and p > 0:
                capture_ratios.append(e / p)
        wins = sum(1 for x in exits if x > 0)
        # Profitability bands
        peak_above_5 = sum(1 for x in peaks if x >= 5)
        peak_above_10 = sum(1 for x in peaks if x >= 10)
        peak_above_25 = sum(1 for x in peaks if x >= 25)
        exit_above_5 = sum(1 for x in exits if x >= 5)
        exit_above_10 = sum(1 for x in exits if x >= 10)
        exit_above_25 = sum(1 for x in exits if x >= 25)

        print(f"\n=== {label} (n={n}) ===")
        print(f"  WR: {wins}/{n} = {wins/n:.0%}")
        print(f"  Avg peak: {sum(peaks)/n:+.2f}%  |  Avg exit: {sum(exits)/n:+.2f}%")
        print(f"  Median peak: {sorted(peaks)[n//2]:+.2f}%  |  Median exit: {sorted(exits)[n//2]:+.2f}%")
        if capture_ratios:
            cr_sorted = sorted(capture_ratios)
            print(f"  Capture ratio (exit/peak when peak>0): "
                  f"avg={sum(capture_ratios)/len(capture_ratios):.0%}, "
                  f"median={cr_sorted[len(cr_sorted)//2]:.0%}")
        print(f"  Peak >=5%: {peak_above_5}/{n} ({peak_above_5/n:.0%}) | Exit >=5%: {exit_above_5}/{n} ({exit_above_5/n:.0%})")
        print(f"  Peak >=10%: {peak_above_10}/{n} ({peak_above_10/n:.0%}) | Exit >=10%: {exit_above_10}/{n} ({exit_above_10/n:.0%})")
        print(f"  Peak >=25%: {peak_above_25}/{n} ({peak_above_25/n:.0%}) | Exit >=25%: {exit_above_25}/{n} ({exit_above_25/n:.0%})")

        # Per-trade table for on-chain (small sample)
        if label.startswith("On-chain") and n <= 20:
            print(f"  Per-trade detail:")
            for t in sorted(group, key=lambda x: -(x.get("peak_pnl_pct") or 0)):
                tf = (t.get("entry_meta") or {}).get("triggers_fired") or []
                tf_onchain = [x for x in tf if x in ONCHAIN_TRIGGERS]
                p, e = t.get("peak_pnl_pct"), t.get("pnl_pct")
                cap = (e/p*100) if (p and p>0) else float("nan")
                print(f"    {t.get('token'):10s} peak={p:+6.2f}% exit={e:+6.2f}% cap={cap:5.0f}% "
                      f"triggers={','.join(tf_onchain) or '-'}")

    summarize(onchain, "On-chain trigger trades")
    summarize(baseline, "Baseline (non-on-chain) trades")

    # Reason breakdown — what's the actual exit reason on on-chain trades that peaked >+5% but exited <+3%?
    print(f"\n=== ON-CHAIN trades that PEAKED >=+5% but EXITED <+3% (under-captured) ===")
    under = [t for t in onchain if (t.get("peak_pnl_pct") or 0) >= 5 and (t.get("pnl_pct") or 0) < 3]
    print(f"  n={len(under)}")
    for t in under:
        p, e = t.get("peak_pnl_pct"), t.get("pnl_pct")
        m = t.get("entry_meta") or {}
        tf = m.get("triggers_fired") or []
        tf_onchain = [x for x in tf if x in ONCHAIN_TRIGGERS]
        print(f"    {t.get('token'):10s} peak={p:+5.2f}% exit={e:+5.2f}% trig={','.join(tf_onchain)}")


if __name__ == "__main__":
    main()
