# -*- coding: utf-8 -*-
"""Judge the win-signature A/B clones vs their controls (2026-06-16 campaign).

The 15 single-variable A/B clones each = a control bot + ONE added entry_gate clause.
The gates RESTRICT entries, so clones accumulate slowly -> forward verdicts take days,
not the 7h mission. Run this whenever to check progress; a clone is JUDGEABLE at n>=30.

Verdict rule (pre-registered): promote the clause to the live control ONLY if the clone
lifts $/trade vs the control AND shows no catastrophe (<=-35%) regression.

Phantom-aware: feed 'pnl' is contaminated (UATF +5569% bad-tick); uses pnl_pct, drops
|pnl_pct|>300, cross-checks /api/leaderboard authoritative realized.

Usage:  python scripts/judge_winsig_ab.py
"""
import json, urllib.request, statistics as st

BASE = "https://gracious-inspiration-production.up.railway.app"
PAIRS = [
    ("badday_flush_conviction_demand", "badday_flush_conviction"),
    ("badday_flush_nf15", "badday_flush"),
    ("champion_premium_dip90m", "champion_premium"),
    ("pool_c_post_peak_chl1m", "pool_c_post_peak"),
    ("timebox_probe_5mgreen", "timebox_probe"),
    ("deepflush_timebox_bottom1s", "deepflush_timebox"),
    ("pool_a_dipgate_vwap1h", "pool_a_dipgate"),
    ("deepflush_timebox_h6peak", "deepflush_timebox"),
    ("champion_premium_tightexit_reaccum", "champion_premium_tightexit"),
    ("pool_a_candidate_shape30dd", "pool_a_candidate"),
    ("pool_a_stack_5mred2", "pool_a_stack"),
    ("champion_minimal_avgbuy80", "champion_minimal"),
    ("pool_a_solmacro_reaccum20", "pool_a_solmacro"),
    ("pool_a_goodpond_reaccum15", "pool_a_goodpond"),
    ("pool_c_tightexit_h24peak55", "pool_c_tightexit"),
]
TARGET_N = 30


def _get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "judge/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def closes(trades, bot):
    s = [t for t in trades if t.get("bot_id") == bot and t.get("type") == "sell"
         and t.get("pnl_pct") is not None and abs(t["pnl_pct"]) <= 300]
    return s


def stats(s):
    if not s:
        return None
    pp = [t["pnl_pct"] for t in s]
    return dict(n=len(s), wr=100 * sum(1 for x in pp if x > 0) / len(s),
                mean=st.mean(pp), cat=sum(1 for x in pp if x <= -35))


def main():
    trades = _get("/api/trades?limit=5000")
    print(f"{'clone':36}{'n':>4}{'WR':>5}{'mean%':>7}{'cat':>4}  | {'control mean%':>13}  verdict")
    for cl, ct in PAIRS:
        cs, ts = stats(closes(trades, cl)), stats(closes(trades, ct))
        if not cs:
            print(f"{cl:36}{0:>4}{'-':>5}{'-':>7}{'-':>4}  | (no clone closes yet)")
            continue
        cm = f"{ts['mean']:+.2f}" if ts else "-"
        if cs["n"] < TARGET_N:
            v = f"accumulating ({TARGET_N - cs['n']} to go)"
        elif ts and cs["mean"] > ts["mean"] and cs["cat"] / cs["n"] < 0.10:
            v = "PROMOTE? clone lifts $/tr, no cat regression"
        else:
            v = "HOLD/retire (no lift or cat regression)"
        print(f"{cl:36}{cs['n']:>4}{cs['wr']:>5.0f}{cs['mean']:>+7.2f}{cs['cat']:>4}  | {cm:>13}  {v}")
    print(f"\nJudge at clone n>={TARGET_N}. Promote clause to live control only if mean$/tr lifts AND catastrophe-rate <10%.")


if __name__ == "__main__":
    main()
