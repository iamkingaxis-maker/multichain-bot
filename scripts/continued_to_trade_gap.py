"""Why do filter-passing tokens (CONTINUED) not become trades?

In 4.5h afternoon window: 19 unique CONTINUED tokens, only 3 became
trades. Investigates the gap.

Approach:
  1. Pull all CONTINUED signal events.
  2. Group by token; per-token: trigger fire count, time span.
  3. Cross-ref with /api/trades — which tokens became buys?
  4. For untraded CONTINUED tokens: enumerate blocking causes from
     the signal-event data:
        - empty triggers_fired (no trigger matched even though filters passed)
        - shadows fired (would-block from SHADOW filters)
        - cycles_seen too low / too high (timing)
        - other
  5. Quantify each cause.
"""
from __future__ import annotations

import json
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"


def parse_iso(s):
    s = s.replace("Z", "+00:00") if "Z" in s else s
    return datetime.fromisoformat(s)


def main():
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/signal-events?limit=2000") as r:
        events = json.loads(r.read())
    events = events if isinstance(events, list) else events.get("events", events.get("rows", []))
    cont = [e for e in events if e.get("outcome") == "CONTINUED"]
    print(f"Total CONTINUED signals: {len(cont)}")

    # Group by token
    by_token = defaultdict(list)
    for e in cont:
        tk = e.get("token", "?")
        by_token[tk].append(e)
    print(f"Unique tokens: {len(by_token)}")

    # Cross-ref with trades
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=500") as r:
        trades = json.loads(r.read())
    trades = trades if isinstance(trades, list) else trades.get("trades", [])
    # Buy times by token (recent)
    buys_by_token = defaultdict(list)
    for t in trades:
        if t.get("type") == "buy":
            buys_by_token[t.get("token", "?")].append(t.get("time", ""))

    # First/last continued ts in window
    all_ts = [parse_iso(e.get("ts", "")) for e in events if e.get("ts")]
    if all_ts:
        win_start = min(all_ts); win_end = max(all_ts)
        print(f"Signal window: {win_start.isoformat()} → {win_end.isoformat()}")

    # Per-token analysis
    print(f"\n=== Per-token CONTINUED summary ===")
    print(f"{'Token':<14} {'#cont':>5} {'#triggers':>9} {'traded':>7} {'shadow_count':>13}")
    untraded = []
    traded = []
    for tk, ev_list in sorted(by_token.items(), key=lambda kv: -len(kv[1])):
        # Did this token become a trade in the same window?
        token_buys = buys_by_token.get(tk, [])
        in_window_buys = []
        for bts in token_buys:
            try:
                bdt = parse_iso(bts)
                if win_start <= bdt <= win_end:
                    in_window_buys.append(bts)
            except: pass
        # Trigger counts: at least one event had triggers_fired non-empty?
        trig_fire_count = sum(1 for e in ev_list if e.get("triggers_fired"))
        # Shadow counts
        shadow_count = sum(1 for e in ev_list if e.get("shadows"))
        # Multi-trigger?
        multi_trig = sum(1 for e in ev_list if len(e.get("triggers_fired") or []) >= 2)
        traded_mark = "YES" if in_window_buys else "no"
        print(f"  {tk:<14} {len(ev_list):>5} {trig_fire_count:>9} {traded_mark:>7} {shadow_count:>13}")
        if in_window_buys:
            traded.append({"token": tk, "n_cont": len(ev_list),
                            "n_trig": trig_fire_count, "n_multi": multi_trig})
        else:
            untraded.append({"token": tk, "n_cont": len(ev_list),
                              "n_trig": trig_fire_count, "n_multi": multi_trig,
                              "events": ev_list})

    print(f"\n=== Traded ({len(traded)}) vs Untraded ({len(untraded)}) tokens ===")
    print(f"  Traded:   {[t['token'] for t in traded]}")
    print(f"  Untraded: {[t['token'] for t in untraded]}")

    # For untraded, what's the blocker?
    print(f"\n=== Untraded CONTINUED tokens — analysis ===")
    print(f"  Token cohort: tokens that passed all filters but never bought.\n")
    print(f"  Hypothesis bucketing:")
    h_no_trigger = []
    h_solo_trigger_marginal = []
    h_unknown = []
    MARGINAL = {"patient_bottom", "informed_cluster", "1s_capit_reversal",
                "whale_conviction", "grad_window_dip", "alpha_buyperscold",
                "net_flow_5m_demand", "fresh_pump_retrace"}
    for u in untraded:
        if u["n_trig"] == 0:
            h_no_trigger.append(u)
            continue
        # Did any event have all-marginal triggers?
        only_marginal = True
        for e in u["events"]:
            trigs = e.get("triggers_fired") or []
            if not trigs: continue
            if any(t not in MARGINAL for t in trigs):
                only_marginal = False
                break
        if only_marginal and u["n_trig"] > 0:
            h_solo_trigger_marginal.append(u)
        else:
            h_unknown.append(u)
    print(f"  No trigger fired: {len(h_no_trigger)}/{len(untraded)}")
    print(f"    Tokens: {[u['token'] for u in h_no_trigger][:10]}")
    print(f"  Only MARGINAL triggers fired (likely blocked by filter_premium_required): "
          f"{len(h_solo_trigger_marginal)}/{len(untraded)}")
    print(f"    Tokens: {[u['token'] for u in h_solo_trigger_marginal][:10]}")
    print(f"  Other (triggers fired but no trade — concurrency? cooldown?): "
          f"{len(h_unknown)}/{len(untraded)}")
    print(f"    Tokens: {[u['token'] for u in h_unknown][:10]}")

    # Per-token detail for the "Other" bucket
    if h_unknown:
        print(f"\n=== 'Other' untraded — detail (first 5) ===")
        for u in h_unknown[:5]:
            # Pick the event with most triggers
            ev_list = u["events"]
            ev_list.sort(key=lambda e: -len(e.get("triggers_fired") or []))
            best = ev_list[0]
            print(f"\n  {u['token']}: n_continued={u['n_cont']} n_triggered={u['n_trig']}")
            print(f"    Best event:  triggers={best.get('triggers_fired')}")
            print(f"    chart_score={best.get('chart_score')}  pc_h24={best.get('pc_h24')}  "
                  f"pc_m5={best.get('pc_m5')}  bs_h6={best.get('bs_h6')}  mcap_m={best.get('mcap_m')}")
            print(f"    shadows={best.get('shadows')[:5] if best.get('shadows') else []}")

    # Traded tokens — characteristic
    if traded:
        print(f"\n=== Traded tokens — for comparison ===")
        for t in traded:
            print(f"  {t['token']}: continued={t['n_cont']}  triggered={t['n_trig']}  multi_trig={t['n_multi']}")


if __name__ == "__main__":
    main()
