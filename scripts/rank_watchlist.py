"""Rank the current follow watchlist + new usable candidates by realized track record,
so 'keep only the best' is a data-driven cut (same net-SOL test as discovery).

Usage: python scripts/rank_watchlist.py [sigs=80]  > out.txt 2> err.txt
"""
from __future__ import annotations
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from score_candidate_track_record import track_record  # noqa: E402

NEW = json.load(open("_usable_wallets.json"))          # 6 robust new wallets
CUR = json.load(open("config/follow_watchlist.json"))  # current 12


def main():
    sigs = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    rows = []
    seen = set()
    for addr, src in [(a, "CURRENT") for a in CUR] + [(a, "NEW") for a in NEW]:
        if addr in seen:
            # wallet already in current watchlist AND in new set -> tag both
            for r in rows:
                if r[0] == addr:
                    r[1] = "CUR+NEW"
            continue
        seen.add(addr)
        r = track_record(addr, sigs)
        if r is None:
            print(f"  {addr[:12]:12s} {src:8s} RPC-FAIL", flush=True)
            rows.append([addr, src, None]); continue
        active = (r["buys"] + r["sells"]) >= 6 and r["sells"] >= 2
        net = r["net_sol"]
        flag = ("USABLE" if (active and net > 0) else ("net-neg" if active else "inactive"))
        print(f"  {addr[:12]:12s} {src:8s} ntx={r['n_tx']:3d} buy={r['buys']:3d} sell={r['sells']:3d} "
              f"net={net:+8.2f}  {flag}", flush=True)
        rows.append([addr, src, net, flag, r])
        time.sleep(0.3)

    scored = [r for r in rows if len(r) >= 4 and r[2] is not None]
    scored.sort(key=lambda r: -r[2])
    print(f"\n=== RANKED by net-SOL (sigs={sigs}) ===", flush=True)
    for r in scored:
        print(f"  {r[0]}  {r[1]:8s} net={r[2]:+8.2f}  {r[3]}", flush=True)

    keep = [r for r in scored if r[3] == "USABLE"]
    drop = [r for r in scored if r[3] != "USABLE"]
    print(f"\nnet-positive (KEEP): {len(keep)}  |  net-neg/inactive (CUT): {len(drop)}", flush=True)
    print("CUT candidates:", flush=True)
    for r in drop:
        print(f"  {r[0]}  {r[1]:8s} net={r[2]:+8.2f}  {r[3]}", flush=True)


if __name__ == "__main__":
    main()
