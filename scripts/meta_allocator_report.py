"""META-ALLOCATOR scoring — backtest the V1 state→family table / score the shadow.

Modes:
  --backtest          reconstruct day-states from the local trades cache
                      (median sol_pc_h24 from buys' entry_meta; breadth from
                      pc_h1<0 share) and score the V1 table: family P&L under
                      shadow multipliers vs flat 1.0. IN-SAMPLE — the table was
                      suggested by part of this window; label accordingly.
  --shadow FILE       score a meta_allocator_shadow.jsonl (forward data) by
                      joining its daily proposals to realized family $/close.

Pre-registered enforcement bar (2026-06-12): >=14 FORWARD shadow days AND
shadow-weighted total > flat-weighted total over that window.
"""
from __future__ import annotations
import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")
from core.meta_allocator import family_of, propose

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _day_ct(t):
    dt = datetime.fromisoformat(str(t.get("time")).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - timedelta(hours=5)).strftime("%Y-%m-%d")


def load_days(cache):
    rows = json.load(open(cache))
    rows = rows if isinstance(rows, list) else rows.get("trades", [])
    fam_pnl = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))   # day -> fam
    state = defaultdict(lambda: {"sol": [], "neg": []})
    for t in rows:
        try:
            d = _day_ct(t)
        except Exception:
            continue
        if t.get("type") == "buy":
            em = t.get("entry_meta") or {}
            s = em.get("sol_pc_h24")
            if isinstance(s, (int, float)):
                state[d]["sol"].append(float(s))
            p1 = em.get("pc_h1")
            if isinstance(p1, (int, float)):
                state[d]["neg"].append(1.0 if p1 < 0 else 0.0)
            continue
        if t.get("type") != "sell":
            continue
        if "cancelled on restart" in (t.get("reason") or "").lower():
            continue
        pp = t.get("pnl_pct")
        if isinstance(pp, (int, float)) and abs(pp) > 150:
            continue   # phantom class
        f = family_of(t.get("bot_id") or t.get("strategy") or "")
        if f:
            fam_pnl[d][f][0] += float(t.get("pnl") or 0)
            fam_pnl[d][f][1] += 1
    return fam_pnl, state


def score(fam_pnl, day_to_proposal, label):
    flat_tot, shad_tot, days_used = 0.0, 0.0, 0
    print(f"\n=== {label} ===")
    print(f"{'day':12s}{'flat $':>9s}{'shadow $':>10s}{'delta':>8s}  active multipliers")
    for d in sorted(fam_pnl):
        prop = day_to_proposal.get(d)
        if not prop:
            continue
        flat = sum(p for p, n in fam_pnl[d].values())
        shad = sum(p * prop.get(f, 1.0) for f, (p, n) in fam_pnl[d].items())
        flat_tot += flat
        shad_tot += shad
        days_used += 1
        act = {k: v for k, v in prop.items() if v != 1.0 and k in fam_pnl[d]}
        print(f"{d:12s}{flat:+9.0f}{shad:+10.0f}{shad-flat:+8.0f}  {act or '-'}")
    if days_used:
        print(f"\n{days_used} days | flat total {flat_tot:+.0f} | "
              f"shadow-weighted {shad_tot:+.0f} | edge {shad_tot - flat_tot:+.0f}")
    return days_used, flat_tot, shad_tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="_trades_cache.json")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--shadow", default=None)
    args = ap.parse_args()

    fam_pnl, state = load_days(args.cache)

    if args.backtest:
        day_prop = {}
        for d, st in state.items():
            sol = statistics.median(st["sol"]) if st["sol"] else None
            neg = statistics.mean(st["neg"]) if st["neg"] else None
            day_prop[d] = propose(sol, neg)
        score(fam_pnl, day_prop,
              "BACKTEST (IN-SAMPLE — table was suggested by part of this window)")

    if args.shadow:
        # daily proposal = median of the day's hourly snapshots (per family)
        snaps = defaultdict(list)
        for line in open(args.shadow):
            try:
                r = json.loads(line)
                d = (datetime.fromtimestamp(r["ts"], timezone.utc)
                     - timedelta(hours=5)).strftime("%Y-%m-%d")
                snaps[d].append(r["proposal"])
            except Exception:
                continue
        day_prop = {}
        for d, props in snaps.items():
            fams = {f for p in props for f in p}
            day_prop[d] = {f: statistics.median([p.get(f, 1.0) for p in props])
                           for f in fams}
        n, ft, st_ = score(fam_pnl, day_prop, "FORWARD SHADOW")
        if n:
            bar = n >= 14 and st_ > ft
            print(f"enforcement bar (>=14 days AND shadow>flat): "
                  f"{'MET' if bar else 'not met'} ({n} days)")


if __name__ == "__main__":
    main()
