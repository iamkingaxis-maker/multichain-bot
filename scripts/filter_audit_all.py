"""Master filter audit (2026-06-27): EV trajectory + enforce status for EVERY filter.

For all ~96 filter_*_verdict fields, join each filter's per-entry verdict to the
position's REALIZED blended pnl_pct (address+entry_price, fraction-weighted) and
report blocked-vs-kept mean/WR/n on TWO windows — MAY (.session_full_trades.json,
the +EV era) and JUNE 12-16 (_df_full.json.gz) — to expose EV decay/inversion over
time (the steep_fall problem, generalized). Merges code-derived ENFORCE status so
inverted-AND-enforced gates (silent winner-kills) surface first.

A gate is HEALTHY-enforced when BLOCK mean << KEPT mean (it removes -EV flow).
INVERTED when BLOCK mean >= KEPT (blocking no longer separates, or helps the wrong
side). Realized trade-join (every shadow trade happened); judge mean+tail (fat-tail).

Usage: python scripts/filter_audit_all.py [--min-n 20]
"""
from __future__ import annotations
import json
import gzip
import statistics as st
import sys
from collections import defaultdict

WINDOWS = [
    ("MAY", ".session_full_trades.json"),
    ("JUN12-16", "_df_full.json.gz"),
]

# Filters that HARD-BLOCK on the candidate path (grep _filters_block.append).
ENFORCED_HARD = {
    "filter_1h_v_bottom_fake_recovery", "filter_1m_steep_fall", "filter_above_vwap_chase",
    "filter_aged_corpse", "filter_blowoff_top", "filter_bs_m5_weak", "filter_btc_overheat",
    "filter_chasing_bounce", "filter_clean_break_p90", "filter_cluster_19_rug",
    "filter_consec_red", "filter_dead_5m_eve_wknd", "filter_dead_low_demand",
    "filter_dead_meme_lagging_pressure", "filter_dead_vol_with_cnn_carveout",
    "filter_dead_volume", "filter_dev_rugged", "filter_fake_bounce", "filter_falling_pump",
    "filter_fusion_floor", "filter_high_regime_buyvol", "filter_huge_wick",
    "filter_knife_catch_peak", "filter_lazy_fade_buy", "filter_low_volatility",
    "filter_lower_low", "filter_lp_drain", "filter_meteora_dex", "filter_microcap_trap",
    "filter_morning_dead_zone", "filter_mtf_strong_downtrend", "filter_negative_net_flow_5m",
    "filter_no_signatures", "filter_orca_dex", "filter_post_pump_corpse",
    "filter_premium_required", "filter_premium_shallow_dip", "filter_quote_asymmetry",
    "filter_reviving_lifecycle", "filter_rolling_ng", "filter_round_trip",
    "filter_sat_eve_midliq", "filter_seller_imbalance", "filter_sol_flicker",
    "filter_sol_macro_down", "filter_solo_decay", "filter_solo_dropouts",
    "filter_stale_h1_peak", "filter_terminal_collapse", "filter_topping", "filter_turn",
    "filter_vp_poc", "filter_wynn_killer", "filter_zero_winner_compound",
    "filter_clean_break", "filter_double_bear", "filter_seller_dominant",
    "filter_confirmation_candle",
}


def _load(path):
    if path.endswith(".gz"):
        try:
            return json.load(gzip.open(path))
        except (OSError, EOFError):
            return json.load(open(path))  # mislabeled plain json
    return json.load(open(path))


def _blended(legs):
    fr = [(p, float(f)) for p, f in legs if isinstance(f, (int, float))]
    tot = sum(f for _p, f in fr)
    if fr and 0.8 <= tot <= 1.2:
        return sum(p * f for p, f in fr)
    cl = [p for p, _f in legs]
    return sum(cl) / len(cl) if cl else None


def positions(recs):
    pos = defaultdict(lambda: {"verdicts": {}, "legs": [], "peak": None})
    for r in recs:
        key = (r.get("address"), r.get("entry_price"))
        em = r.get("entry_meta") or {}
        for k, v in em.items():
            if k.endswith("_verdict") and v in ("BLOCK", "PASS"):
                pos[key]["verdicts"][k] = v
        pn = r.get("pnl_pct")
        if isinstance(pn, (int, float)):
            pos[key]["legs"].append((float(pn), r.get("sell_fraction")))
        pk = r.get("peak_pnl_pct")
        if isinstance(pk, (int, float)):
            pos[key]["peak"] = pk if pos[key]["peak"] is None else max(pos[key]["peak"], pk)
    out = []
    for d in pos.values():
        b = _blended(d["legs"]) if d["legs"] else None
        if b is not None:
            out.append({"verdicts": d["verdicts"], "pnl": b, "peak": d["peak"]})
    return out


def _agg(rows):
    if not rows:
        return None
    pn = [r["pnl"] for r in rows]
    ng = sum(1 for r in rows if isinstance(r["peak"], (int, float)) and r["peak"] <= 0)
    return {"n": len(pn), "mean": st.mean(pn), "median": st.median(pn),
            "wr": sum(1 for p in pn if p > 0) / len(pn),
            "ng": ng / len(pn) if pn else 0.0}


def main():
    min_n = 20
    if "--min-n" in sys.argv:
        min_n = int(sys.argv[sys.argv.index("--min-n") + 1])

    win_pos = {}
    for name, path in WINDOWS:
        try:
            win_pos[name] = positions(_load(path))
            print(f"loaded {name} ({path}): {len(win_pos[name])} positions")
        except FileNotFoundError:
            print(f"SKIP {name}: {path} not found")

    fields = set()
    for plist in win_pos.values():
        for p in plist:
            fields.update(p["verdicts"])

    rows = []
    for field in fields:
        rec = {"field": field, "enforced": field.replace("_verdict", "") in ENFORCED_HARD}
        for name in win_pos:
            blk = [p for p in win_pos[name] if p["verdicts"].get(field) == "BLOCK"]
            pas = [p for p in win_pos[name] if p["verdicts"].get(field) == "PASS"]
            rec[name] = {"blk": _agg(blk), "pas": _agg(pas)}
        rows.append(rec)

    def delta(rec, w):
        d = rec.get(w) or {}
        b, p = d.get("blk"), d.get("pas")
        if not b or not p or b["n"] < min_n:
            return None
        return p["mean"] - b["mean"]  # +ve = BLOCK worse than PASS = gate helps

    def signal(rec, w):
        """ROBUST per-window verdict. Fat-tail means at small-n are noise, so a gate
        only counts as HELPING when the blocked cohort is worse on the STABLE axes
        (win-rate AND never-green AND median), not just mean. Requires blocked-n>=50."""
        d = rec.get(w) or {}
        b, p = d.get("blk"), d.get("pas")
        if not b or not p or b["n"] < 50 or p["n"] < 50:
            return None  # insufficient n for a fat-tail-robust call
        # gate HELPS if blocked cohort loses more often + is more doomed + lower median
        helps = (b["wr"] <= p["wr"] - 0.03 and b["ng"] >= p["ng"] and b["median"] <= p["median"])
        hurts = (b["wr"] >= p["wr"] + 0.03 and b["median"] >= p["median"])  # winner-kill
        return "helps" if helps else ("hurts" if hurts else "neutral")

    def classify(rec):
        smay, sjun = signal(rec, "MAY"), signal(rec, "JUN12-16")
        sigs = [s for s in (smay, sjun) if s is not None]
        latest = delta(rec, "JUN12-16")
        if latest is None:
            latest = delta(rec, "MAY")
        if not sigs:
            return "THIN-N", latest
        # robust = require the available windows AGREE (no single-window/outlier calls)
        agree = len(set(sigs)) == 1
        verdict = sigs[-1]  # most recent window with adequate n
        if rec["enforced"]:
            if verdict == "hurts" and agree:
                return "!! INVERTED-ENFORCED (loosen)", latest
            if verdict == "hurts":
                return "? inverted-1window (verify)", latest
            if verdict == "neutral":
                return "~ no-op-enforced (low value)", latest
            return "ok enforced (helps)", latest
        else:
            if verdict == "helps" and agree:
                return ">> STRONG-SHADOW (enforce?)", latest
            if verdict == "helps":
                return "? strong-1window (verify)", latest
            if verdict == "hurts":
                return "inverted-shadow (keep off)", latest
            return "weak-shadow", latest

    for r in rows:
        r["_class"], r["_latest_delta"] = classify(r)

    order = {"!! INVERTED-ENFORCED (loosen)": 0, "? inverted-1window (verify)": 1,
             "~ no-op-enforced (low value)": 2, ">> STRONG-SHADOW (enforce?)": 3,
             "? strong-1window (verify)": 4, "ok enforced (helps)": 5,
             "inverted-shadow (keep off)": 6, "weak-shadow": 7, "THIN-N": 8}
    rows.sort(key=lambda r: (order.get(r["_class"], 9), -(r["_latest_delta"] or -99)))

    def fmt(a):
        return (f"n={a['n']:<4} mean={a['mean']:+6.2f}% med={a['median']:+6.2f}% "
                f"WR={a['wr']:.0%} ng={a['ng']:.0%}") if a else "n=0"

    print(f"\n{'='*100}\nMASTER FILTER AUDIT — Δ=PASS-BLOCK mean (+ve = gate removes -EV flow; <=0 = inverted)\n{'='*100}")
    for r in rows:
        if r["_class"] == "THIN-N":
            continue
        tag = "ENF" if r["enforced"] else "shd"
        print(f"\n[{r['_class']}] {r['field'].replace('_verdict','')}  ({tag})")
        for name in win_pos:
            d = r.get(name) or {}
            dd = delta(r, name)
            print(f"   {name:9} BLOCK {fmt(d.get('blk'))} | PASS {fmt(d.get('pas'))}"
                  + (f"  Δ={dd:+.2f}pp" if dd is not None else "  Δ=thin"))

    print(f"\n{'='*100}\nACTION SUMMARY\n{'='*100}")
    for cls in ("!! INVERTED-ENFORCED (loosen)", "? inverted-1window (verify)",
                "~ no-op-enforced (low value)", ">> STRONG-SHADOW (enforce?)",
                "? strong-1window (verify)"):
        hits = [r["field"].replace("_verdict", "") for r in rows if r["_class"] == cls]
        print(f"{cls}: {', '.join(hits) if hits else '(none)'}")


if __name__ == "__main__":
    main()
