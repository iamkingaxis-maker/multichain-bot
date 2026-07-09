# scripts/fill_tail_report.py
"""Fill-quality tail + full-chain latency report (2026-07-09).

AxiS: "I want the full start-to-finish time and how far off our fills are vs
what we wanted to fill" + "eliminate that 4% as much as possible."

Decomposition (from 22 live buys): decision->fire ~-0.4% (fine); fire->fill
(the swap) = +4.06% median, of which ~2.5% is the structural Ultra slippage
cap (study-optimized, don't chase) and the attackable part is the FAST-MOVER
TAIL (+8..15%: DONALD +15.77, Vaaland +8.4). The retrace-micro gate attacks
that tail via SELECTION. This report proves/disproves it forward:

  1. FULL CHAIN per trade: detect(price_age) -> decision -> fire -> fill
     (price_age from entry_meta.latency_price_age_secs; fire/fill from swaps)
  2. FILL TAIL: fill_vs_mid distribution, split by retrace_micro_avoid_block
     (did the gate flag it?) — the tail should live in the flagged cohort.

Pulls each API once (egress discipline). Usage:
  python scripts/fill_tail_report.py            # live dashboard pull
  python scripts/fill_tail_report.py <trades.json> <live_swaps.json>  # local
"""
import json
import os
import statistics as st
import sys
import urllib.request

BASE = os.environ.get(
    "DASH_BASE", "https://gracious-inspiration-production.up.railway.app")
AUTH = os.environ.get("DASH_AUTH", "jcole:pMIwPSmRmoPfteWViuGgjaTdnx5JfO-g-e6-_zjdlmo")


def _get(path):
    import base64
    req = urllib.request.Request(BASE + path)
    req.add_header("Authorization",
                   "Basic " + base64.b64encode(AUTH.encode()).decode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _pctl(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _fmt(v, suf="%"):
    return ("%+.2f%s" % (v, suf)) if v is not None else "n/a"


def main():
    if len(sys.argv) >= 3:
        trades = json.load(open(sys.argv[1], encoding="utf-8"))
        swaps = json.load(open(sys.argv[2], encoding="utf-8"))
    else:
        trades = _get("/api/trades?full=1&limit=5000")
        swaps = _get("/api/live-swaps")
    trades = trades if isinstance(trades, list) else trades.get("trades", [])
    swaps = swaps.get("recent", swaps if isinstance(swaps, list) else [])

    # ── 1. FULL CHAIN: detection age at decision (all buys carrying the stamp) ──
    ages = []
    flagged_ages = []
    for t in trades:
        if t.get("type") != "buy":
            continue
        em = t.get("entry_meta") or {}
        a = em.get("latency_price_age_secs")
        if isinstance(a, (int, float)):
            ages.append(a)
            if em.get("retrace_micro_avoid_block"):
                flagged_ages.append(a)
    print("=== FULL CHAIN: detect -> decision (freshest-price age at decision) ===")
    if ages:
        print("  n=%d | median %.2fs | p90 %.2fs | max %.2fs"
              % (len(ages), st.median(ages), _pctl(ages, 0.9), max(ages)))
    else:
        print("  (no stamped buys yet — accrues from the next deploy's fills)")

    # ── 2. FILL TAIL: live fill_vs_mid split by the retrace-micro flag ─────────
    # Join live swaps -> the trade's entry_meta flag by (token, nearest buy).
    flag_by_tok = {}
    for t in trades:
        if t.get("type") == "buy":
            em = t.get("entry_meta") or {}
            if em.get("retrace_micro_avoid_block") is not None:
                flag_by_tok[(t.get("bot_id"), t.get("token"))] = bool(
                    em.get("retrace_micro_avoid_block"))
    fv_all, fv_flag, fv_pass, lat = [], [], [], []
    for s in swaps:
        if s.get("side") != "buy":
            continue
        fv = s.get("fill_vs_mid_slippage_pct")
        if fv is None:
            continue
        fv_all.append(fv)
        if s.get("total_latency_ms"):
            lat.append(s["total_latency_ms"] / 1000.0)
        f = flag_by_tok.get((s.get("bot_id"), s.get("token_symbol")))
        (fv_flag if f else fv_pass).append(fv) if f is not None else None
    print("\n=== FILL vs DECISION (live buys) — the 4% target ===")
    if fv_all:
        print("  ALL      n=%d | median %s | p90 %s | worst %s"
              % (len(fv_all), _fmt(st.median(fv_all)), _fmt(_pctl(fv_all, 0.9)),
                 _fmt(max(fv_all))))
        if fv_flag or fv_pass:
            if fv_pass:
                print("  micro-PASS n=%d | median %s | worst %s"
                      % (len(fv_pass), _fmt(st.median(fv_pass)), _fmt(max(fv_pass))))
            if fv_flag:
                print("  micro-FLAG n=%d | median %s | worst %s  <- tail should live here"
                      % (len(fv_flag), _fmt(st.median(fv_flag)), _fmt(max(fv_flag))))
        else:
            print("  (no live buys carry the retrace-micro flag yet — the split "
                  "accrues once live trading resumes post-gate)")
    else:
        print("  (no live buys in window)")
    if lat:
        print("  decision->fill exec: median %.2fs" % st.median(lat))
    print("\nHONEST NOTE: ~2.5% of the median is the structural Ultra slippage "
          "cap (study-optimized). The attackable part is the tail; it shrinks "
          "via SELECTION (retrace-micro), not a slippage knob.")


if __name__ == "__main__":
    main()
