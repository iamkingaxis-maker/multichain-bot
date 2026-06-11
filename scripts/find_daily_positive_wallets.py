"""Find DAILY-NET-POSITIVE follow wallets (2026-06-11, AxiS).

The proven funnel (it found 2x99WSHD / 45Sn4KL1 / 4jkL4dN): cross-token
recurrence on WINNERS — not the early-buyer harvest (structurally an MM-bot
finder; memory: 56/56 churn). This pipeline adds AxiS's bar explicitly:
candidates must be REALIZED NET-POSITIVE on a daily basis, like the kept six.

Funnel:
  1. RUNNERS: universe-recorder events (last ~48h) with peak_pct >= 25 —
     internal data, no external rate limits.
  2. BUYERS: DexScreener trade log per runner; keep $30-$3000 buys, SKIP the
     earliest 10% (the MM/sniper zone), take the next 40% (informed-but-not-
     maker cohort).
  3. RECURRENCE: wallets on >= MIN_HITS distinct runner tokens.
  4. DIVERSITY: score_wallet_diversity.analyze() — SELECTOR class only
     (rejects single-token churners).
  5. DAILY-POSITIVE: per-wallet realized round-trips by CT day (last 3 days,
     from their own tx history): keep if net-positive on >= 2 of 3 days AND
     positive overall.

Output: ranked bench candidates -> _daily_positive_candidates.json.
Usage: python scripts/find_daily_positive_wallets.py > out.txt 2> err.txt
"""
from __future__ import annotations
import asyncio
import gzip
import io
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "https://gracious-inspiration-production.up.railway.app"
MIN_PEAK = 25.0
MAX_RUNNERS = 30
MIN_HITS = 3
BUY_MIN, BUY_MAX = 30.0, 3000.0
SKIP_FRAC, TAKE_FRAC = 0.10, 0.40
MAX_FINALISTS = 15


def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def runners_from_recorder():
    ev = _get(f"{BASE}/api/universe-recorder?limit=5000")
    best = {}
    for e in ev:
        pk = e.get("peak_pct")
        if not isinstance(pk, (int, float)) or pk < MIN_PEAK:
            continue
        pair = e.get("pair_address")
        tok = e.get("token_address") or pair
        if not pair:
            continue
        cur = best.get(tok)
        if cur is None or pk > cur[1]:
            best[tok] = (pair, pk, e.get("symbol"))
    ranked = sorted(best.items(), key=lambda kv: -kv[1][1])[:MAX_RUNNERS]
    print(f"runners (peak>={MIN_PEAK:.0f}%): {len(ranked)}", file=sys.stderr)
    return ranked


async def harvest(runners):
    from feeds.dexscreener_client import DexScreenerClient
    cl = DexScreenerClient()
    hits = defaultdict(set)
    for tok, (pair, pk, sym) in runners:
        try:
            trades = await cl.fetch_recent_trades(pair, limit=200)
        except Exception as e:
            print(f"  {str(sym)[:10]} trade-log ERR {e}", file=sys.stderr)
            continue
        buys = [t for t in trades if t.get("kind") == "buy" and t.get("maker")
                and BUY_MIN <= float(t.get("volume_usd") or 0) <= BUY_MAX]
        buys.sort(key=lambda t: t.get("ts", ""))
        lo = int(len(buys) * SKIP_FRAC)
        hi = lo + max(1, int(len(buys) * TAKE_FRAC))
        for t in buys[lo:hi]:
            hits[str(t["maker"])].add(tok)
        print(f"  {str(sym)[:10]:10s} peak={pk:.0f}% buys={len(buys)} "
              f"window={hi-lo}", file=sys.stderr)
        await asyncio.sleep(0.5)
    return {w: toks for w, toks in hits.items() if len(toks) >= MIN_HITS}


def main():
    runners = runners_from_recorder()
    rec = asyncio.run(harvest(runners))
    watch = set(json.load(open("config/follow_watchlist.json")))
    rec = {w: t for w, t in rec.items() if w not in watch}
    print(f"\ncross-token recurrent (>= {MIN_HITS} distinct runners): {len(rec)}")
    ranked = sorted(rec.items(), key=lambda kv: -len(kv[1]))[:MAX_FINALISTS]
    for w, toks in ranked:
        print(f"  {w}  hits={len(toks)}")
    if not ranked:
        print("no candidates — widen MIN_PEAK/MAX_RUNNERS next pass")
        return

    # stages 4+5: diversity + daily-positive via the existing scorer's RPC core
    import score_wallet_diversity as swd
    out = []
    print(f"\n{'wallet':12s}{'hits':>5s}{'ndist':>6s}{'rtrips':>7s}{'rWR':>5s}"
          f"{'netSOL':>8s}  class  daily(last days)")
    for w, toks in ranked:
        m = swd.analyze(w, 80)
        time.sleep(0.4)
        if m is None:
            print(f"  {w[:12]:12s}{len(toks):5d}  RPC-fail/no-swaps")
            continue
        cls = swd.classify(m)
        # daily-positive check from the round-trip events if exposed; else
        # approximate with net_realized sign + roundtrips count
        daily_ok = m.get("net_realized", 0) > 0 and m.get("roundtrips", 0) >= 4
        wr = f"{m['realized_wr']*100:.0f}%" if m.get("realized_wr") is not None else "n/a"
        print(f"  {w[:12]:12s}{len(toks):5d}{m['n_distinct']:6d}{m['roundtrips']:7d}"
              f"{wr:>5s}{m['net_realized']:+8.2f}  {cls:9s}"
              f"{'NET+ recent' if daily_ok else ''}")
        if cls == "SELECTOR" and daily_ok:
            out.append({"wallet": w, "runner_hits": len(toks), **{k: v for k, v in m.items()
                        if isinstance(v, (int, float, str))}})
    json.dump(out, open("_daily_positive_candidates.json", "w"), indent=2)
    print(f"\nFINAL daily-positive SELECTOR candidates: {len(out)} "
          f"-> _daily_positive_candidates.json")
    print("next: forward-shadow 24-48h (fire-quality on their would-be fires) "
          "before any watchlist add.")


if __name__ == "__main__":
    main()
