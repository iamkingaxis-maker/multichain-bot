"""BAD-DAY SCORECARD (2026-06-10) — the daily accountability report.

Grades every bad-day mechanism against its pre-registered forecast, with no
narrative. Run after a CT day closes (or anytime for day-to-date):

  python scripts/sync_trades_cache.py && python scripts/badday_scorecard.py

Sections:
  1. Day verdict        — fleet + candidate-set + walk-forward live-set P&L/WR
  2. Dial forecast log  — P7 multiplier per day (reconstructed from the
                          regime_dial stamps on buys) vs the realized day sign;
                          running forecast accuracy. KILL: <50% at n>=10.
  3. Badday family      — realized $/tr + catastrophe rate vs pre-registration
                          (>= +$2/tr at n>=30 dial-bad closes; cat rate <10%).
  4. Stop-grace A/B     — treatment vs control arm fills (address parity).
  5. Trigger-state shadow — pass/block forward outcomes per gate (enforce at
                          n>=50 with WR lift).
"""
from __future__ import annotations
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, ".")
from scripts.goal_tracker import (  # noqa: E402
    CANDIDATE_BOTS, build_daily, live_set_for_day)

CACHE = "_trades_cache.json"
BADDAY_BOTS = {"badday_flush", "badday_momo"}


def ct_day(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
    except Exception:
        return None


def main():
    trades = json.load(open(CACHE))
    sells = [t for t in trades if t.get("type") == "sell"
             and "cancelled on restart" not in (t.get("reason") or "").lower()]
    buys = [t for t in trades if t.get("type") == "buy"]
    for t in sells + buys:
        t["_day"] = ct_day(t.get("time"))

    # ── 1. day verdict ────────────────────────────────────────────────────
    daily = build_daily(trades)
    days = sorted({t["_day"] for t in sells if t["_day"]})
    print("══ 1. DAY VERDICT (last 7 CT days) ══")
    print(f"{'day':12s}{'fleet $':>9s}{'fleetWR':>8s}{'cand $':>8s}{'live-set $':>11s}")
    fleet_day = defaultdict(list)
    for t in sells:
        if t["_day"]:
            fleet_day[t["_day"]].append(float(t.get("pnl") or 0))
    for d in days[-7:]:
        ps = fleet_day[d]
        wr = sum(1 for p in ps if p > 0) / max(len(ps), 1)
        cand = sum(r["pnl"] for r in (daily.get(d) or {}).values())
        ls = live_set_for_day(daily, d)
        lt = sum(r["pnl"] for b, r in (daily.get(d) or {}).items() if b in ls)
        print(f"{d:12s}{sum(ps):+9.0f}{wr:8.0%}{cand:+8.0f}{lt:+11.0f}")

    # ── 2. dial forecast scoring ──────────────────────────────────────────
    print("\n══ 2. P7 DIAL FORECAST RECORD (from buy stamps) ══")
    dial_day = {}
    for t in buys:
        em = t.get("entry_meta") or {}
        m = em.get("regime_dial_full")
        if isinstance(m, (int, float)) and t["_day"]:
            dial_day.setdefault(t["_day"], []).append(m)
    hits = n_scored = 0
    for d in sorted(dial_day):
        mults = dial_day[d]
        med = statistics.median(mults)
        realized = sum(fleet_day.get(d) or [0])
        call = "bad" if med < 1.0 else ("good" if med > 1.0 else "neutral")
        if call != "neutral":
            correct = (realized < 0) if call == "bad" else (realized > 0)
            hits += correct
            n_scored += 1
            verdict = "HIT" if correct else "MISS"
        else:
            verdict = "-"
        print(f"  {d}  dial(med)={med:g}  realized=${realized:+.0f}  {verdict}")
    if n_scored:
        acc = hits / n_scored
        print(f"  forecast accuracy: {hits}/{n_scored} ({acc:.0%})"
              + ("  ⚠ KILL CRITERION (<50% at n>=10) — demote dial to shadow"
                 if n_scored >= 10 and acc < 0.5 else ""))
    else:
        print("  no scored forecasts yet (stamps accumulate from 2026-06-10 deploy)")

    # ── 3. badday family vs pre-registration ─────────────────────────────
    print("\n══ 3. BADDAY MICROCAP FAMILY (pre-reg: >=+$2/tr @ n>=30 dial-bad closes; cat<10%) ══")
    fam = [t for t in sells if (t.get("bot_id") or "") in BADDAY_BOTS]
    if not fam:
        print("  no closes yet")
    else:
        pnls = [float(t.get("pnl") or 0) for t in fam]
        fills = [float(t.get("pnl_pct") or 0) for t in fam]
        cat = sum(1 for f in fills if f <= -35)
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        print(f"  n={len(pnls)} WR={wr:.0%} ${statistics.mean(pnls):+.2f}/tr "
              f"net=${sum(pnls):+.2f} | catastrophe fills (<=-35%): {cat} "
              f"({100*cat/len(pnls):.0f}%)"
              + ("  ⚠ KILL: catastrophe rate >=10%" if cat / len(pnls) >= 0.10 and len(pnls) >= 15 else ""))
        per = defaultdict(list)
        for t in fam:
            per[t["bot_id"]].append(float(t.get("pnl") or 0))
        for b, ps in sorted(per.items()):
            print(f"    {b:14s} n={len(ps):3d} ${statistics.mean(ps):+.2f}/tr net=${sum(ps):+.2f}")

    # ── 4. stop-grace A/B ────────────────────────────────────────────────
    print("\n══ 4. STOP-GRACE A/B (smart_follow stops; treatment=addr-parity even) ══")
    buy_strat = {}
    for t in buys:
        if t.get("strategy"):
            buy_strat[(t.get("pair_address") or t.get("address") or "").lower()] = t["strategy"]
    sf_stops = [t for t in sells
                if buy_strat.get((t.get("pair_address") or t.get("address") or "").lower()) == "smart_follow"
                and "stop" in (t.get("reason") or "").lower()
                and "pre-stop" not in (t.get("reason") or "").lower()
                and (t.get("time") or "") >= "2026-06-10T14:00"]   # grace deploy
    for lbl, arm in (("TREATMENT", True), ("CONTROL  ", False)):
        g = [t for t in sf_stops
             if (sum(ord(c) for c in (t.get("address") or "")) % 2 == 0) == arm]
        if g:
            fills = [float(t.get("pnl_pct") or 0) for t in g]
            print(f"  {lbl} n={len(g):3d} avg fill {statistics.mean(fills):+.1f}% "
                  f"net=${sum(float(t.get('pnl') or 0) for t in g):+.2f}")
        else:
            print(f"  {lbl} n=0")

    # ── 5. trigger-state shadow forward record ───────────────────────────
    print("\n══ 5. TRIGGER-STATE SHADOW (enforce candidates at n>=50 + WR lift) ══")
    bb = defaultdict(list)
    for t in buys:
        k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
        bb[k].append(t)
    for k in bb:
        bb[k].sort(key=lambda b: b.get("time", ""))
    rec = defaultdict(lambda: {"pass": [0, 0], "block": [0, 0]})
    for t in sells:
        k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
        c = [b for b in bb.get(k, []) if (b.get("time") or "") < (t.get("time") or "")]
        if not c:
            continue
        shadow = (c[-1].get("entry_meta") or {}).get("trigger_state_shadow") or {}
        won = float(t.get("pnl") or 0) > 0
        for trig, v in shadow.items():
            if v in ("pass", "block"):
                rec[trig][v][0] += 1
                rec[trig][v][1] += won
    if not rec:
        print("  no stamped closes yet (stamps accumulate from 2026-06-10 deploy)")
    for trig in sorted(rec):
        p, b = rec[trig]["pass"], rec[trig]["block"]
        pw = f"{p[1]/p[0]:.0%}" if p[0] else "--"
        bw = f"{b[1]/b[0]:.0%}" if b[0] else "--"
        ready = " <- ENFORCEABLE" if p[0] + b[0] >= 50 and p[0] and b[0] and p[1]/p[0] > b[1]/b[0] + 0.08 else ""
        print(f"  {trig:30s} pass n={p[0]:3d} WR={pw:>4s} | block n={b[0]:3d} WR={bw:>4s}{ready}")


if __name__ == "__main__":
    main()
