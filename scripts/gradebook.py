#!/usr/bin/env python3
"""gradebook.py — automated FIDELITY-HONEST bar status for every standing
experiment (2026-07-19, last-Fable-night build; manual section 3).

WHY THIS EXISTS: months of iteration re-selected illusion because grading was
manual, ad-hoc, and often in paper dollars. This tool makes the grading loop
mechanical so any session (any model) runs the same honest ruler:
  * phantom scrub: drop wins >+100% inside 5min, and ANY win held <10s
  * dead-token re-book (RH): every stake into a token in _dead_tokens.json is
    a full loss, N re-buys = N stakes (uses the freshest local dead set; if
    the set is older than DEAD_MAX_AGE_H it is SKIPPED with a loud warning —
    a stale corpse list is its own illusion)
  * bar: n>=30 closes, >=5 distinct UTC days, >=20 unique tokens, and
    drop-top-2 closes still positive — ALL required before any grade talk
Run: python scripts/gradebook.py            (RH experiments, ~1 pull/bot)
     python scripts/gradebook.py --sol      (also pull SOL trades, heavier)
Output: table per experiment + scratchpad/_gradebook.json for the loop.
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

BASE = os.environ.get("RH_DASH_BASE",
                      "https://gracious-inspiration-production.up.railway.app")
DEAD_PATH = os.path.join("scratchpad", "_dead_tokens.json")
DEAD_MAX_AGE_H = 36.0
OUT = os.path.join("scratchpad", "_gradebook.json")

# ── the standing experiments (manual section 3; edit HERE when racing changes)
EXPERIMENTS = [
    {"name": "RH dipall quartet (entry-source #2+#3)", "chain": "rh",
     "arms": ["rh_dipall_ctrl", "rh_dipall_knife",
              "rh_dipall_young1h", "rh_dipall_both"],
     "note": "knife: kept-beats-skipped >=70% of days; kept lane MAY stay "
             "mildly red (pre-registered). Measure marginal value via arms."},
    {"name": "RH LET-WINNERS-RUN vs scalp (the power-law test)", "chain": "rh",
     "arms": ["rh_letrun", "rh_letrun_runner", "rh_dipall_ctrl"],
     "note": "SAME entry, let-run exit (wide trail, no SL1) vs scalp. Thesis: "
             "memecoins are a lottery, scalping decapitates winners. mfe test "
             "scalp -1.92% vs let-run +5-10%. Report WITH-tail AND ex-top-2; "
             "kill only if with-tail loses to scalp at n>=30. Open Q: is the "
             "tail REALIZABLE or spike-illusion? Fidelity-honest $ decides."},
    {"name": "RH professional seat (panel synthesis)", "chain": "rh",
     "arms": ["rh_pro_agedflush"],
     "note": "kills: fid<=-$40 wk1 / 4 red days / wr<40%@n>=25 / entries-day "
             ">20 or <2 x3d = population mismatch -> fix-or-kill, NOT grade."},
    {"name": "RH slcut SL1 trio (paired vs parents)", "chain": "rh",
     "arms": ["rh_slcut_agedhold", "rh_slcut_ageddeep", "rh_slcut_demand"],
     "note": "paired RELATIVE verdict stands; ABSOLUTE promotion needs clean "
             "fidelity re-grade (first measurement was red)."},
    {"name": "RH phoenix2 (RECLAIM entry, replaces v1 07-20)", "chain": "rh",
     "arms": ["rh_phoenix2"],
     "note": "reclaim>=2% above stop print + one bite ever. Same kills as v1 "
             "(fid<-$20 at n>=30, deaths eat bounces), fresh clock. v1 died "
             "-$122/n=57 of exposure inversion (postmortem in memory)."},
    {"name": "RH exit-memo A/Bs (#1 ladder, #2 bail-frac)", "chain": "rh",
     "arms": ["rh_bailfrac_ab", "rh_aged_hold",
              "rh_young_agedladder_ab", "rh_young_v1"],
     "note": "bailfrac vs aged_hold (paired); agedladder vs young_v1. Kills: "
             "tail-cohort < full-close; ex-top-2 negative at n>=30."},
    {"name": "SOL hype-block A/B (entry-source #1)", "chain": "sol",
     "arms": ["badday_young_hypeblock_ab", "badday_young_absorb"],
     "note": "clone vs parent; winner-kill <=5%; log daily block-rate."},
    {"name": "SOL ng_faststop enforce (exit memo #4)", "chain": "sol",
     "arms": ["badday_ngfast_ab", "badday_young_absorb"],
     "note": "grade enforced-FILL vs stamped-fire (the gap IS the economics; "
             "kill if gap>=2.5pp or winner-kill>20%). 90s min-hold."},
    {"name": "SOL admission arms (volume unlock)", "chain": "sol",
     "arms": ["admission_x_liq", "admission_x_liqdemand", "admission_x_liq_sl1"],
     "note": "grade on NG/win/$/entry/buys-day vs the tape, not vs zero."},
]

BAR = {"n": 30, "days": 5, "tokens": 20}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "gradebook"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())


def _dead_set():
    try:
        st = os.stat(DEAD_PATH)
        age_h = (time.time() - st.st_mtime) / 3600
        dead = set(json.load(open(DEAD_PATH)).get("dead") or [])
        if age_h > DEAD_MAX_AGE_H:
            print(f"!! dead-token set is {age_h:.0f}h old (> {DEAD_MAX_AGE_H}h)"
                  f" — dead re-book SKIPPED. Refresh via rh_fleet_fidelity.")
            return None
        return dead
    except Exception:
        print("!! no dead-token set — dead re-book skipped (RH numbers are "
              "phantom-scrubbed only, NOT fully fidelity-corrected)")
        return None


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def rh_bot_closes(bot, dead):
    """-> list of {usd, day, token} fidelity-honest closed results."""
    rows = (_get(f"{BASE}/api/rh-paper?bot={bot}&raw=1").get("rows")) or []
    pos = defaultdict(lambda: {"usd": 0.0, "tok": None, "buy_ts": None,
                               "sells": []})
    for r in rows:
        k = r.get("pool")
        if r.get("ev") == "buy":
            pos[k]["usd"] += abs(r.get("usd") or 25.0)
            pos[k]["tok"] = r.get("token")
            pos[k]["buy_ts"] = _ts(r.get("ts"))
        elif r.get("ev") == "sell" and isinstance(r.get("pnl_usd"),
                                                  (int, float)):
            st_, bt = _ts(r.get("ts")), pos[k]["buy_ts"]
            hold = (st_ - bt) if (st_ and bt) else None
            pct = r.get("pnl_pct")
            # phantom scrub (win legs only; fast losses are real)
            if r["pnl_usd"] > 0 and hold is not None and (
                    hold < 10.0 or (hold < 300.0
                                    and isinstance(pct, (int, float))
                                    and pct > 100.0)):
                continue
            pos[k]["sells"].append((r["pnl_usd"], str(r.get("ts"))[:10]))
    out = []
    for k, v in pos.items():
        if not v["sells"]:
            continue
        day = v["sells"][-1][1]
        if dead is not None and v["tok"] in dead:
            out.append({"usd": -v["usd"], "day": day, "token": v["tok"]})
        else:
            out.append({"usd": sum(s for s, _ in v["sells"]), "day": day,
                        "token": v["tok"]})
    return out


def sol_bot_closes(trades, bot):
    """SOL closes from /api/trades?full=1 rows (phantom-scrubbed)."""
    out = []
    for t in trades:
        if t.get("bot_id") != bot:
            continue
        if t.get("type") == "sell" and isinstance(t.get("pnl"),
                                                  (int, float)):
            hold = t.get("hold_secs")
            pct = t.get("pnl_pct")
            if t["pnl"] > 0 and isinstance(hold, (int, float)) and (
                    hold < 10.0 or (hold < 300.0
                                    and isinstance(pct, (int, float))
                                    and pct > 100.0)):
                continue
            out.append({"usd": t["pnl"],
                        "day": str(t.get("time"))[:10],
                        "token": t.get("address")})
    return out


def grade(closes):
    n = len(closes)
    days = len({c["day"] for c in closes})
    toks = len({c["token"] for c in closes})
    tot = sum(c["usd"] for c in closes)
    wins = sum(1 for c in closes if c["usd"] > 0)
    dt2 = tot - sum(sorted((c["usd"] for c in closes), reverse=True)[:2])
    met = {"n": n >= BAR["n"], "days": days >= BAR["days"],
           "tokens": toks >= BAR["tokens"], "dt2_pos": dt2 > 0}
    at_bar = met["n"] and met["days"] and met["tokens"]
    per_day = {}
    for c in closes:
        per_day[c["day"]] = round(per_day.get(c["day"], 0.0) + c["usd"], 2)
    return {"n": n, "days": days, "tokens": toks,
            "per_day": per_day,
            "fid_usd": round(tot, 2),
            "per_close": round(tot / n, 3) if n else None,
            "win_rate": round(wins / n, 3) if n else None,
            "drop_top2": round(dt2, 2), "bar_met": met,
            "status": ("GRADE NOW" + ("" if met["dt2_pos"]
                                      else " (dt2 NEGATIVE)")
                       if at_bar else
                       f"below bar (n {n}/{BAR['n']}, d {days}/{BAR['days']},"
                       f" t {toks}/{BAR['tokens']})")}


def main():
    want_sol = "--sol" in sys.argv
    dead = _dead_set()
    sol_trades = None
    if want_sol:
        raw = _get(f"{BASE}/api/trades?full=1&limit=5000")
        sol_trades = (raw if isinstance(raw, list)
                      else (raw or {}).get("trades")) or []
    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "dead_rebooked": dead is not None, "experiments": []}
    for exp in EXPERIMENTS:
        if exp["chain"] == "sol" and not want_sol:
            continue
        print(f"\n== {exp['name']} ==   ({exp['note'][:70]}…)")
        rows = []
        for bot in exp["arms"]:
            closes = (rh_bot_closes(bot, dead) if exp["chain"] == "rh"
                      else sol_bot_closes(sol_trades, bot))
            g = grade(closes)
            rows.append({"bot": bot, **g})
            print(f"  {bot:28} n={g['n']:>4} d={g['days']:>2} t={g['tokens']:>3}"
                  f" fid=${g['fid_usd']:>+9.2f} wr={g['win_rate']}"
                  f" dt2=${g['drop_top2']:>+8.2f}  {g['status']}")
        report["experiments"].append({"name": exp["name"], "arms": rows,
                                      "note": exp["note"]})
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    # DURABLE EVIDENCE (2026-07-21, the evidence-evaporation fix): the
    # dashboard ledger caps ~5000 rows/bot, so raw day-level data ages out
    # in ~2-3 days at fleet volume — no 5-day bar can ever be re-verified
    # from the rolling window alone (the young-S route certification became
    # unreproducible within hours). Append every run's per-arm PER-DAY
    # aggregates to a permanent history file; bars count days from HERE.
    # Keyed (bot, day): newest run's value for a day wins (days still
    # inside the rolling window keep improving; aged-out days FREEZE).
    hist_path = os.path.join("scratchpad", "_gradebook_history.jsonl")
    try:
        frozen = {}
        if os.path.exists(hist_path):
            for line in open(hist_path, encoding="utf-8"):
                try:
                    j = json.loads(line)
                    frozen[(j["bot"], j["day"])] = j
                except Exception:
                    continue
        for e in report["experiments"]:
            for a in e["arms"]:
                for d, usd in (a.get("per_day") or {}).items():
                    frozen[(a["bot"], d)] = {
                        "bot": a["bot"], "day": d, "fid_usd": usd,
                        "run_ts": report["ts"],
                        "dead_rebooked": report["dead_rebooked"]}
        with open(hist_path, "w", encoding="utf-8") as f:
            for k in sorted(frozen):
                f.write(json.dumps(frozen[k]) + "\n")
        print(f"-> {hist_path} ({len(frozen)} bot-days archived)")
    except Exception as e:
        print(f"!! history archive failed: {e}")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
