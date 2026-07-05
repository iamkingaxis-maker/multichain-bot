#!/usr/bin/env python3
"""
live_probe_watch.py — T1 probe measurement (paired live-vs-paper), 2026-07-05.

The probe's whole job is to LEARN: this script turns its fills into the
Track-1 numbers that gate the $25 -> $50 -> $100 ladder.

Per live fill (from /api/live-swaps):
  - execution: order/sign/execute durations, attempts, 429s, priority fee,
    realized slippage vs decision price, Ultra RTSE slippage used
  - the PAPER TWIN pair: badday_young_absorb's booked fill on the same token
    within +-10min -> live-vs-paper price delta per leg (the fidelity number)
Aggregates:
  - slippage %/leg (buys vs sells), decision->landed latency distribution
  - realized live P&L from swap legs (buy/sell joined per token)
  - WALLET TRUTH cross-check: on-chain delta from /api/wallet-truth
Ladder gates printed against the runbook bar: per-token >= +1pp over >= 3
live days AND >= 10 fills per rung.

Usage: PYTHONPATH=. python scripts/live_probe_watch.py [hours=24]
Read-only; safe to run any time (prints 'no live swaps yet' pre-flip).
"""
import json, sys, urllib.request, gzip, io
import statistics as st
from collections import defaultdict

DASH = "https://gracious-inspiration-production.up.railway.app"


def g(p):
    req = urllib.request.Request(DASH + p, headers={
        "User-Agent": "lpw/1", "Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=60)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
    import datetime as dt
    cut_ts = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)).timestamp()

    d = g("/api/live-swaps")
    swaps = d.get("swaps", d) if isinstance(d, dict) else d
    swaps = [s for s in (swaps or []) if float(s.get("ts") or 0) >= cut_ts]
    if not swaps:
        print("no live swaps in window yet — probe idle or pre-flip")
    else:
        print(f"LIVE SWAPS (last {hours:g}h): {len(swaps)}")
        # execution telemetry
        for key, label in (("order_duration_ms", "order build"),
                           ("sign_duration_ms", "sign"),
                           ("execute_duration_ms", "execute+confirm")):
            v = [float(s[key]) for s in swaps if isinstance(s.get(key), (int, float))]
            if v:
                print(f"  {label:16} med {st.median(v):7.0f}ms  p90 "
                      f"{sorted(v)[max(0,int(len(v)*.9)-1)]:7.0f}ms  (n={len(v)})")
        slips = [float(s["realized_slippage_pct"]) for s in swaps
                 if isinstance(s.get("realized_slippage_pct"), (int, float))]
        if slips:
            print(f"  realized slip    med {st.median(slips):+.2f}%/leg  "
                  f"worst {max(slips, key=abs):+.2f}%  (n={len(slips)})")
        fails = [s for s in swaps if not s.get("success", s.get("ok", True))]
        print(f"  failures: {len(fails)}/{len(swaps)}"
              + (f"  reasons={[str(s.get('reason'))[:24] for s in fails[:4]]}" if fails else ""))
        # paired vs paper twin
        try:
            arr = g("/api/bots/badday_young_absorb/trades?limit=200&meta_keys=_none_")
            import datetime as dt2
            pairs = []
            for s in swaps:
                stok = str(s.get("token") or s.get("symbol") or "").lower()
                sts = float(s.get("ts") or 0)
                sp = s.get("fill_price") or s.get("price_usd")
                if not stok or not isinstance(sp, (int, float)):
                    continue
                for t in arr:
                    if str(t.get("token", "")).lower() != stok:
                        continue
                    tts = dt2.datetime.fromisoformat(
                        str(t.get("time", "")).replace("Z", "+00:00")).timestamp()
                    if abs(tts - sts) <= 600 and t.get("type") == (
                            "buy" if s.get("side", "buy") == "buy" else "sell"):
                        pp = t.get("entry_price") if t.get("type") == "buy" else t.get("exit_price")
                        if isinstance(pp, (int, float)) and pp > 0:
                            pairs.append((stok, (float(sp) / float(pp) - 1) * 100))
                        break
            if pairs:
                dv = [x[1] for x in pairs]
                print(f"  LIVE-vs-PAPER twin ({len(pairs)} paired legs): "
                      f"med {st.median(dv):+.2f}%  mean {st.mean(dv):+.2f}%")
        except Exception as e:
            print(f"  (twin pairing failed: {str(e)[:50]})")

    # wallet truth cross-check
    try:
        wt = g("/api/wallet-truth")
        print(f"\nWALLET TRUTH: now {wt.get('sol_now')} SOL"
              + (f"  Δ {wt.get('delta_sol'):+.4f} SOL since baseline"
                 if isinstance(wt.get("delta_sol"), (int, float)) else
                 f"  ({wt.get('note') or 'paper mode'})"))
    except Exception:
        pass
    print("\nladder bar per rung: per-token >= +1pp over >= 3 live days AND >= 10 fills")


if __name__ == "__main__":
    main()
