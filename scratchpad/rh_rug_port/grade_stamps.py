# scratchpad/rh_rug_port/grade_stamps.py
"""Grade accrued rug-signal SHADOW stamps against labeled outcomes (offline).

Joins, per (pool, entry):
  {"ev":"rug_signals"} rows  (scripts/rh_paper_lane.py shadow stamper)
  x  realized outcomes  = worst sell pnl_pct on that pool AFTER the stamp ts
  x  +6h post-exit rows = scratchpad/robinhood_tapes/rh_postexit.jsonl (died /
     post6h_vs_exit_pct)
  x  (--absorb, RPC)    = pool_pct_of_supply NOW vs stamped -> the dump-class
     labeler validated on the retro set (rugs absorbed +14..+71pp into the
     pool; survivors drifted <= 0). This is the RH analog of "LP drained".

LABELS (a pool-entry is RUGGED when ANY of):
  * worst realized sell pnl_pct <= -60 on the pool after the stamp
  * post-exit +6h died (unquotable) or post6h_vs_exit_pct <= -80
  * pool absorption delta >= +15pp (--absorb only)

Grading gate math mirrors the Solana bar: for each candidate predicate,
catch = P(flag | RUGGED), winner-kill = P(flag | not RUGGED). NOTHING here
promotes a gate — n>=30 rugged + winner-kill<=5% + AxiS approval first.

Usage: python scratchpad/rh_rug_port/grade_stamps.py [--absorb]
"""
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
LEDGER = os.path.join(_ROOT, "scratchpad", "robinhood_tapes",
                      "rh_paper_trades.jsonl")
POSTEXIT = os.path.join(_ROOT, "scratchpad", "robinhood_tapes",
                        "rh_postexit.jsonl")

# candidate predicates to grade (stamp -> bool); extend freely — stamps carry
# the raw features, so new predicates grade retroactively over ALL history.
PREDICATES = {
    "top1_whale>=10": lambda s: (s.get("top1_pct") or 0) >= 10.0,
    "top1_whale>=20": lambda s: (s.get("top1_pct") or 0) >= 20.0,
    "pool_pct<25": lambda s: (s.get("pool_pct_of_supply") is not None
                              and s["pool_pct_of_supply"] < 25.0),
    "fat_shoulder>=0.6": lambda s: (s.get("shoulder_to_top10_ratio") or 0) >= 0.6,
    "thin_base<200": lambda s: (s.get("n_holders") is not None
                                and s["n_holders"] < 200),
    "visible_float>=60": lambda s: (s.get("visible_float_pct") or 0) >= 60.0,
    "lp_any_eoa": lambda s: s.get("lp_any_eoa_owner") is True,
    "creator_holds>=5": lambda s: (s.get("creator_pct") or 0) >= 5.0,
    "joint_dump_shape": lambda s: (
        (s.get("pool_pct_of_supply") is not None
         and s["pool_pct_of_supply"] < 25.0)
        and ((s.get("top1_pct") or 0) >= 10.0
             or (s.get("shoulder_to_top10_ratio") or 0) >= 0.6)),
}


def _rows(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    pass


def main():
    absorb = "--absorb" in sys.argv
    stamps, worst_sell, postexit = [], {}, {}
    for r in _rows(LEDGER):
        if r.get("ev") == "rug_signals":
            stamps.append(r)
        elif r.get("ev") == "sell" and r.get("pnl_pct") is not None:
            p = r.get("pool")
            key = (p, r.get("ts", ""))
            cur = worst_sell.get(p)
            if cur is None or r["pnl_pct"] < cur[0]:
                worst_sell[p] = (r["pnl_pct"], r.get("ts", ""))
    for r in _rows(POSTEXIT):
        p = r.get("pool")
        died = bool(r.get("died")) or ((r.get("post6h_vs_exit_pct") or 0) <= -80)
        postexit[p] = postexit.get(p, False) or died

    if not stamps:
        print("no rug_signals rows yet — stamps accrue as the lane trades")
        return

    rpc = None
    if absorb:
        sys.path.insert(0, os.path.join(_ROOT, "scripts"))
        from rh_chain_feed import Rpc, RPC_DEFAULT
        from core.rh_rug_signals import SEL_BALANCE_OF, SEL_TOTAL_SUPPLY
        rpc = Rpc(os.environ.get("RH_FEED_RPC", RPC_DEFAULT))

    graded = []
    for s in stamps:
        pool, tok = s.get("pool"), s.get("token")
        ws = worst_sell.get(pool)
        # only sells after the stamp's entry count toward its outcome
        realized = ws[0] if ws and ws[1] >= (s.get("entry_ts") or "") else None
        rugged = ((realized is not None and realized <= -60)
                  or postexit.get(pool, False))
        absorb_pp = None
        if rpc and s.get("pool_pct_of_supply") is not None:
            try:
                time.sleep(0.25)
                ts_r = rpc.call("eth_call", [{"to": tok,
                                              "data": SEL_TOTAL_SUPPLY}, "latest"])
                time.sleep(0.25)
                pb_r = rpc.call("eth_call", [{
                    "to": tok,
                    "data": SEL_BALANCE_OF + "0" * 24 + pool[2:]}, "latest"])
                supply, pb = int(ts_r, 16), int(pb_r, 16)
                if supply > 0:
                    absorb_pp = round(pb / supply * 100.0
                                      - s["pool_pct_of_supply"], 2)
                    rugged = rugged or absorb_pp >= 15.0
            except Exception:
                pass
        graded.append((s, rugged, realized, absorb_pp))

    n_rug = sum(1 for _, r, _, _ in graded if r)
    n_ok = len(graded) - n_rug
    print(f"stamps={len(graded)}  rugged={n_rug}  survived={n_ok}"
          f"  (need n>=30 rugged before any gate talk)")
    print(f"{'predicate':<22}{'catch':>10}{'winner-kill':>14}")
    for name, fn in PREDICATES.items():
        c = sum(1 for s, r, _, _ in graded if r and fn(s))
        k = sum(1 for s, r, _, _ in graded if not r and fn(s))
        print(f"{name:<22}"
              f"{(f'{c}/{n_rug}' if n_rug else '-'):>10}"
              f"{(f'{k}/{n_ok}' if n_ok else '-'):>14}")
    out = os.path.join(_HERE, "graded_stamps.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump([{"pool": s.get("pool"), "sym": s.get("sym"),
                    "entry_ts": s.get("entry_ts"), "rugged": r,
                    "worst_realized_pct": w, "absorb_pp": a,
                    "flags": {n: bool(fn(s)) for n, fn in PREDICATES.items()}}
                   for s, r, w, a in graded], f, indent=1)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
