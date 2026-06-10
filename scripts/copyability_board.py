"""COPYABILITY BOARD (2026-06-10) — select follow wallets by COPY TAX, not quality.

The strategy decode (500 elite round-trips): these wallets are convexity
machines — median hold 9.8min, 51% WR, winners' tail +107% p90, $3-$70 probes.
The best-LOOKING wallet (fast sprayer) is the LEAST copyable: its edge is
speed and probe-size we can't replicate at $100 fixed. The copyable wallets
are the ones whose edge survives our measured friction (median +0.79%
fire->fill chase, 28s latency).

COPY TAX per wallet = (their realized return on tokens we both traded)
                    - (our realized return on the fires they participated in).

Usage: python scripts/sync_trades_cache.py && python scripts/copyability_board.py
Reads /api/follow-logs (fires: which wallets triggered; exits: their own
round-trips) + the local trade cache (our fills on those fires).

Verdicts (n>=10 our-closes per wallet):
  COPYABLE   our $/close > 0           -> keep, candidates for K=2/K=1 pods
  TAXED      theirs > 0 > ours         -> their edge doesn't survive copying;
                                          keep only if tax shrinks post-WS/guard
  TOXIC      theirs <= 0 and ours <= 0 -> drop from watchlist (bench-swap)
"""
from __future__ import annotations
import gzip
import io
import json
import statistics
import sys
import urllib.request
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "https://gracious-inspiration-production.up.railway.app"
CACHE = "_trades_cache.json"


def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def main():
    fl = _get(f"{BASE}/api/follow-logs")
    sigs = [s for s in (fl.get("signals") or []) if s.get("wallets")]
    exits = fl.get("exits") or []
    trades = json.load(open(CACHE))

    # our realized P&L per token (smart_follow closes)
    buy_strat = {}
    for t in trades:
        if t.get("type") == "buy" and t.get("strategy"):
            buy_strat[(t.get("pair_address") or t.get("address") or "").lower()] = t["strategy"]
    ours_by_token = defaultdict(list)
    for t in trades:
        if t.get("type") != "sell":
            continue
        if "cancelled on restart" in (t.get("reason") or "").lower():
            continue
        k = (t.get("pair_address") or t.get("address") or "").lower()
        if not str(buy_strat.get(k, "")).startswith("smart_follow"):
            continue
        addr = (t.get("address") or "").lower()
        ours_by_token[addr].append(float(t.get("pnl") or 0))

    # their realized per (wallet, token)
    theirs = defaultdict(list)   # wallet -> [return_pct]
    theirs_tok = defaultdict(lambda: defaultdict(list))
    for e in exits:
        w = e.get("wallet")
        r = e.get("wallet_return_pct")
        tok = (e.get("token") or "").lower()
        if w and isinstance(r, (int, float)):
            theirs[w].append(r)
            theirs_tok[w][tok].append(r)

    # per wallet: fires it joined -> our P&L on those tokens
    board = defaultdict(lambda: {"fires": 0, "our_pnl": [], "their_ret_same": []})
    for s in sigs:
        tok = (s.get("token") or "").lower()
        for w in s.get("wallets") or []:
            b = board[w]
            b["fires"] += 1
            if tok in ours_by_token:
                b["our_pnl"].extend(ours_by_token[tok])
            if theirs_tok.get(w, {}).get(tok):
                b["their_ret_same"].extend(theirs_tok[w][tok])

    print(f"{'wallet':12s}{'fires':>6s}{'ourN':>6s}{'our$/cl':>9s}{'theirMed%':>10s}"
          f"{'theirWR':>8s}  verdict")
    for w in sorted(board, key=lambda x: -board[x]["fires"]):
        b = board[w]
        on = len(b["our_pnl"])
        od = statistics.mean(b["our_pnl"]) if b["our_pnl"] else None
        tr = theirs.get(w) or []
        tm = statistics.median(tr) if tr else None
        twr = (sum(1 for r in tr if r > 0) / len(tr)) if tr else None
        if on >= 10 and od is not None:
            if od > 0:
                verdict = "COPYABLE — keep, pod candidate"
            elif tm is not None and tm > 0:
                verdict = "TAXED — edge lost in copying (watch post-WS/guard)"
            else:
                verdict = "TOXIC — drop / bench-swap"
        else:
            verdict = f"(n={on} thin)"
        print(f"{w[:12]:12s}{b['fires']:6d}{on:6d}"
              f"{('%+9.2f' % od) if od is not None else '       --':>9s}"
              f"{('%+10.1f' % tm) if tm is not None else '        --':>10s}"
              f"{('%7.0f%%' % (twr*100)) if twr is not None else '     --':>8s}  {verdict}")
    print("\nNOTE: 'their' stats start 06-08 (exit log) and OUR stats include pre-overhaul"
          "\nfires — re-run daily; the verdicts that matter are post-2026-06-10 deploys"
          "\n(WS latency + chase guard + flush gate + elite-exit all change the tax).")


if __name__ == "__main__":
    main()
