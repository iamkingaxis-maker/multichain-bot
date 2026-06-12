"""WALLET DECODE — the standing intelligence instrument (2026-06-12, AxiS:
"we seem to get our best info lately from other wallets").

Generalizes the Dw5Vykxu study that produced the time-box archetype + exposed
the pp_launch discovery gap. Input: a wallet. Output: its decoded SYSTEM —
trade map with timestamps, hold/return distributions, exit-style detection
(time-boxer? strength-seller? price-stopper? scalper?), entry-state joins
against our recorder, and overlap vs our own books (did we see/trade its
tokens — and who exited better).

Track record of this method: elite decode -> convex wing; copy-tax physics ->
the copyability gate; Dw5 decode -> time_stop_minutes + the firehose lane.

Usage:
  python scripts/wallet_decode.py <WALLET> [sigs=150]
"""
from __future__ import annotations
import collections
import datetime
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import score_wallet_diversity as swd

UTC = datetime.UTC


def trade_map(addr: str, sigs: int):
    """Full per-token trade map with buy/sell timestamps and SOL amounts."""
    sl = swd._rpc("getSignaturesForAddress", [addr, {"limit": sigs}]) or []
    tok = collections.defaultdict(lambda: {"spent": 0.0, "recv": 0.0,
                                           "buys": [], "sells": []})
    for s in sl:
        sig, bt = s.get("signature"), s.get("blockTime")
        if not sig or s.get("err") or not bt:
            continue
        tx = swd._rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0,
                                               "encoding": "jsonParsed"}])
        time.sleep(0.06)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in (meta.get("preTokenBalances") or []) if b.get("owner") == addr}
        post = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in (meta.get("postTokenBalances") or []) if b.get("owner") == addr}
        try:
            keys = [k if isinstance(k, str) else k.get("pubkey")
                    for k in tx["transaction"]["message"]["accountKeys"]]
            wi = keys.index(addr)
            sol_d = (meta["postBalances"][wi] - meta["preBalances"][wi]) / 1e9
        except Exception:
            continue
        deltas = {m: post.get(m, 0) - pre.get(m, 0)
                  for m in set(list(pre) + list(post)) if m not in swd.STABLE}
        deltas = {m: d for m, d in deltas.items() if abs(d) > 0}
        if not deltas:
            continue
        mint = max(deltas, key=lambda m: abs(deltas[m]))
        d = deltas[mint]
        if d > 0 and sol_d < 0:
            tok[mint]["buys"].append((bt, -sol_d)); tok[mint]["spent"] += -sol_d
        elif d < 0 and sol_d > 0:
            tok[mint]["sells"].append((bt, sol_d)); tok[mint]["recv"] += sol_d
    return tok


def decode(addr: str, sigs: int = 150):
    tok = trade_map(addr, sigs)
    if not tok:
        print("no parseable trades (UNFOLLOWABLE custody or RPC fail)")
        return
    fmt = lambda ts: datetime.datetime.fromtimestamp(ts, UTC).strftime("%m-%d %H:%M")
    trips, opens, sizes, holds, rets = [], 0, [], [], []
    for m, r in tok.items():
        if not r["buys"]:
            continue
        b0 = min(b[0] for b in r["buys"])
        sizes.append(sum(b[1] for b in r["buys"]))
        if not r["sells"]:
            opens += 1
            continue
        s1 = max(s[0] for s in r["sells"])
        hold = max(0, s1 - b0)
        ret = (r["recv"] / r["spent"] - 1) * 100 if r["spent"] else None
        holds.append(hold)
        if ret is not None:
            rets.append(ret)
        trips.append((b0, s1, m, hold, ret))
    trips.sort()
    print(f"=== DECODE {addr[:16]}… | {len(tok)} tokens, {len(trips)} closed, {opens} open ===")
    if sizes:
        med_sz = statistics.median(sizes)
        fixed = statistics.pstdev(sizes) / med_sz < 0.15 if med_sz else False
        print(f"SIZING: median {med_sz:.2f} SOL/token | "
              f"{'FIXED-size (sprayer)' if fixed else 'variable (conviction sizer)'}")
    if holds:
        hs = sorted(holds)
        med_h = hs[len(hs) // 2]
        print(f"HOLDS: median {med_h/60:.0f}min | p25 {hs[len(hs)//4]/60:.0f}m "
              f"| p75 {hs[3*len(hs)//4]/60:.0f}m")
        # time-box detection: do LOSERS cluster at one duration?
        lh = sorted(h for (_, _, _, h, r) in trips if r is not None and r < 0)
        if len(lh) >= 5:
            lmed = lh[len(lh) // 2]
            tight = sum(1 for h in lh if abs(h - lmed) < 600) / len(lh)
            if tight >= 0.6:
                print(f"  ** TIME-BOX SIGNATURE: {tight:.0%} of losers exit at "
                      f"~{lmed/60:.0f}min (the Dw5 archetype)")
            else:
                print(f"  loser exits dispersed (price/discretion-stopped, not time-boxed)")
    if rets:
        w = [r for r in rets if r > 0]; l = [r for r in rets if r <= 0]
        print(f"RETURNS: WR {len(w)/len(rets):.0%} | win med "
              f"{statistics.median(w) if w else 0:+.1f}% | loss med "
              f"{statistics.median(l) if l else 0:+.1f}% | best {max(rets):+.1f}%")
    # overlap vs our books
    try:
        tr = json.load(open("_trades_cache.json"))
        ours = {(t.get("address") or "").lower() for t in tr if t.get("type") == "buy"}
        import urllib.request, gzip, io
        req = urllib.request.Request(
            "https://gracious-inspiration-production.up.railway.app/api/universe-recorder?limit=5000",
            headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        ev = json.loads(raw)
        rows = ev if isinstance(ev, list) else ev.get("events", [])
        seen = {e["token_address"].lower() for e in rows if e.get("token_address")}
        mints = {m.lower() for m in tok}
        print(f"OVERLAP: our scanner saw {len(mints & seen)}/{len(mints)} of its tokens "
              f"({len(mints & seen)/max(1,len(mints)):.0%}) | we traded "
              f"{len(mints & ours)}/{len(mints)}")
        if len(mints & seen) / max(1, len(mints)) < 0.5:
            print("  ** DISCOVERY GAP: most of its pond is invisible to our feeds")
    except Exception as e:
        print(f"(overlap check unavailable: {e})")
    print("\nlast 12 closed trips:")
    for b0, s1, m, hold, ret in trips[-12:]:
        print(f"  {fmt(b0)} -> {fmt(s1)} hold={hold/60:6.0f}min "
              f"ret={f'{ret:+.1f}%' if ret is not None else '--':>8s}  {m[:10]}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        sys.exit("usage: python scripts/wallet_decode.py <WALLET> [sigs]")
    decode(args[0], int(args[1]) if len(args) > 1 else 150)
