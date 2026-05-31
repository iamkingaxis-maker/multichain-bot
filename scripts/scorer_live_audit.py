#!/usr/bin/env python
"""Live audit of the enforced never-green scorer: does it block DUDS, not winners?

Joins the scorer's decision log (/api/ng-scorer-decisions) to the universe
recorder's forward peaks (/api/universe-recorder) by token symbol + nearest time.
A BLOCKED entry was "correct" if that token actually went never-green (forward
peak < 2%); a PASSED entry that went never-green is a miss. Computes the live
precision of the gate (blocked-NG-rate) vs the passed-NG-rate and the base rate —
the real proof that the gate cuts duds without killing would-be winners.

Blocks leave no trade record, so the universe recorder (which tracks forward peaks
for ALL scanned tokens, not just bought ones) is the only source of the
counterfactual outcome. Run repeatedly as live decisions accumulate.
"""
from __future__ import annotations
import json, urllib.request, time
from datetime import datetime

BASE = "https://gracious-inspiration-production.up.railway.app"
WINDOW_S = 1800  # join a decision to a recorder event within +/-30min


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=90))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def _epoch(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def main():
    decs = _get("/api/ng-scorer-decisions?limit=5000")
    uni = _get("/api/universe-recorder?limit=30000")
    uni = uni if isinstance(uni, list) else uni.get("events", uni.get("records", uni))
    # index recorder events by symbol -> [(epoch, peak_pct)]
    by_sym = {}
    for e in uni:
        if not isinstance(e, dict):
            continue
        sym = e.get("symbol") or e.get("token")
        pk = e.get("peak_pct")
        ts = _epoch(e.get("detected_at_iso") or e.get("event_ts"))
        if sym is None or pk is None or ts is None:
            continue
        by_sym.setdefault(str(sym), []).append((ts, float(pk)))

    def forward_peak(token, t_iso):
        cand = by_sym.get(str(token))
        if not cand:
            return None
        te = _epoch(t_iso)
        if te is None:
            return None
        best = min(cand, key=lambda x: abs(x[0] - te))
        return best[1] if abs(best[0] - te) <= WINDOW_S else None

    joined = 0
    blk_ng = blk_tot = pass_ng = pass_tot = 0
    examples = []
    for d in decs:
        pk = forward_peak(d.get("token"), d.get("t"))
        if pk is None:
            continue
        joined += 1
        ng = pk < 2.0
        if d.get("blocked"):
            blk_tot += 1; blk_ng += ng
            examples.append((True, d.get("token"), d.get("p"), pk, ng))
        else:
            pass_tot += 1; pass_ng += ng

    print(f"decisions {len(decs)} | joined to recorder outcomes {joined} "
          f"(blocked {blk_tot}, passed {pass_tot})")
    base = (blk_ng + pass_ng) / max(joined, 1)
    print(f"base never-green rate among scored tokens: {100*base:.0f}%")
    if blk_tot:
        print(f"BLOCKED -> never-green rate (live precision): {100*blk_ng/blk_tot:.0f}% "
              f"({blk_ng}/{blk_tot})  [want HIGH — blocking duds]")
    if pass_tot:
        print(f"PASSED  -> never-green rate:                 {100*pass_ng/pass_tot:.0f}% "
              f"({pass_ng}/{pass_tot})  [want LOWER than blocked]")
    if blk_tot and pass_tot:
        lift = (blk_ng/blk_tot) / max(pass_ng/pass_tot, 1e-9)
        print(f"separation (blocked-NG / passed-NG): {lift:.1f}x")
    print("\nrecent blocked decisions w/ forward outcome (peak<2 = correctly blocked a dud):")
    for b, tok, p, pk, ng in examples[-12:]:
        print(f"  {str(tok)[:12]:12} p={p} fwd_peak={pk:+.1f}%  {'DUD✓' if ng else 'would-have-run✗'}")


if __name__ == "__main__":
    main()
