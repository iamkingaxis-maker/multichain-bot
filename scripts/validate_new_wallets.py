"""Bag-validate the freshly-discovered wallets (_new_wallet_candidates.json) on-chain.

Reuses the proven holdings/dead-bag logic. Measures the 3 keepers IN THE SAME RUN as
a live baseline bar, then flags which new wallets are at least as clean.

Usage: python scripts/validate_new_wallets.py  > out.txt 2> err.txt
"""
from __future__ import annotations
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from mine_quality_wallets import _holdings, _dead_ratio  # noqa: E402

KEEPERS = {
    "Abk9EfhWsLnxuMm7qJXvMYNyvKgtp8sfSoL45srMzP49": "Abk9Efh",
    "V21GW8PGcWRE5DnbjHZXhcjiBYhJxmjBHqAUkfBm2n9": "V21GW8P",
    "HmP3TxuVWkiJjS6mii9WPzRFeF9hnUjs1YMHCAB4AZm4": "HmP3Txu",
}


def run_one(addr):
    mints = _holdings(addr)
    if mints is None:
        return None
    dr, dead = _dead_ratio(mints) if mints else (0.0, 0)
    return len(mints), dead, dr


def main():
    cands = json.load(open("_new_wallet_candidates.json"))
    print(f"{'wallet':14s} {'role':7s} {'hits':>4s} {'early$':>8s} | {'held':>4s} {'dead':>4s} {'dead%':>6s}  verdict",
          flush=True)
    print("-" * 78, flush=True)

    base = {}
    for addr, lbl in KEEPERS.items():
        r = run_one(addr)
        if r:
            base[lbl] = r[2]
            print(f"  {lbl:12s} {'KEEPER':7s} {'-':>4s} {'-':>8s} | {r[0]:4d} {r[1]:4d} {r[2]*100:5.0f}%", flush=True)
        time.sleep(0.6)
    bar = max(base.values(), default=0.65)
    print(f"\n  >> baseline bar (worst keeper dead%): {bar*100:.0f}%\n", flush=True)

    results = []
    for c in cands:
        w = c["wallet"]; hits = c.get("runner_hits", 0); ev = c.get("early_vol_usd", 0)
        r = run_one(w)
        if r is None:
            print(f"  {w[:12]:12s} {'cand':7s} {hits:4d} {ev:8.0f} | RPC-FAIL", flush=True)
            continue
        held, dead, dr = r
        verdict = "STRONG" if (dr <= bar and dead <= 60) else (
            "candidate" if (dr <= bar + 0.10 and dead <= 100) else "bagger-skip")
        results.append((w, hits, ev, held, dead, dr, verdict))
        print(f"  {w[:12]:12s} {'cand':7s} {hits:4d} {ev:8.0f} | {held:4d} {dead:4d} {dr*100:5.0f}%  {verdict}",
              flush=True)
        time.sleep(0.6)

    keep = [r for r in results if r[6] in ("STRONG", "candidate")]
    keep.sort(key=lambda r: (r[6] != "STRONG", r[5], -r[1]))
    print(f"\n=== {len(keep)}/{len(results)} new wallets clear the keeper bar ===", flush=True)
    for w, hits, ev, held, dead, dr, v in keep:
        print(f"  {w}  hits={hits} early=${ev:.0f} dead%={dr*100:.0f} ({dead}/{held})  {v}", flush=True)
    print("\nNote: dead% is a current-holdings snapshot, NOT realized sell-through. "
          "Forward-track signals before adding to the K=1 set.", flush=True)


if __name__ == "__main__":
    main()
