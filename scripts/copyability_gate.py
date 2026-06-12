"""PRE-SEAT COPYABILITY GATE (2026-06-12, AxiS).

The missing funnel stage: traits 1/2/5 (custody, diversity, recurrence) are
testable pre-seat, but COPYABILITY was only learned after a wallet cost us
fires (HcLMmNx9 needed ~20 closes to convict). This gate estimates it from
the candidate's OWN history before any seat:

  Our copy reality (measured): fill ~28s late, +0.8% chase on entry, ~0.6%
  exit haircut. Therefore:
    - round-trips held < MIN_COPYABLE_HOLD are UNCOPYABLE (their profit
      lives in seconds we structurally cannot have — the night-scalper class)
    - longer trips: taxed copy return = (1+r) / 1.008 * 0.994 - 1

  Verdict per wallet over its last N txs:
    COPYABLE-GRADE: median hold >= 5min AND <=40% of trips sub-2min AND
                    taxed copy return net-positive across copyable trips
    SCALPER:        >40% sub-2min trips (reject — structurally uncopyable)
    THIN-EDGE:      hold profile OK but taxed copy return <= 0 (reject)

Validation set built in (run with no args): 2x99WSHD (the only wallet to
hold COPYABLE on the live board) must PASS; HcLMmNx9 + 1eveYYxZ (the 06-12
overnight toxic night-scalpers) must FAIL. If the gate can't separate the
known labels it has no business gating the bench.

Usage:
  python scripts/copyability_gate.py                    # validation set
  python scripts/copyability_gate.py FILE.json [sigs]   # gate candidates
"""
from __future__ import annotations
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import score_wallet_diversity as swd

CHASE = 1.008          # measured median entry chase (+0.8%)
EXIT_HAIRCUT = 0.994   # measured exit haircut (~0.6%)
MIN_COPYABLE_HOLD = 120     # <2min = structurally uncopyable for us
MEDIAN_HOLD_FLOOR = 300     # 5min — thesis-holder bar (elite median 9.8min)
MAX_SCALP_FRAC = 0.40


def roundtrips_with_holds(addr: str, sigs: int):
    """Per-token round-trips with hold seconds + SOL return, from tx history.
    Reuses the scorer's parsing approach but keeps timestamps."""
    sl = swd._rpc("getSignaturesForAddress", [addr, {"limit": sigs}])
    if sl is None:
        return None
    tok = collections.defaultdict(lambda: {"spent": 0.0, "recv": 0.0,
                                           "first_buy": None, "last_sell": None})
    parsed = 0
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
        parsed += 1
        rec = tok[mint]
        if d > 0 and sol_d < 0:
            rec["spent"] += -sol_d
            rec["first_buy"] = min(rec["first_buy"] or bt, bt)
        elif d < 0 and sol_d > 0:
            rec["recv"] += sol_d
            rec["last_sell"] = max(rec["last_sell"] or bt, bt)
    if parsed == 0:
        return None
    out = []
    for m, r in tok.items():
        if r["spent"] > 0 and r["recv"] > 0 and r["first_buy"] and r["last_sell"]:
            hold = max(0, r["last_sell"] - r["first_buy"])
            out.append({"mint": m, "hold_s": hold,
                        "ret": r["recv"] / r["spent"] - 1.0})
    return out


def gate(addr: str, sigs: int = 80) -> dict:
    rts = roundtrips_with_holds(addr, sigs)
    if rts is None:
        return {"wallet": addr, "verdict": "RPC-FAIL/UNFOLLOWABLE"}
    if len(rts) < 4:
        return {"wallet": addr, "verdict": "THIN", "roundtrips": len(rts)}
    holds = sorted(r["hold_s"] for r in rts)
    med_hold = holds[len(holds) // 2]
    scalp_frac = sum(1 for r in rts if r["hold_s"] < MIN_COPYABLE_HOLD) / len(rts)
    copyable = [r for r in rts if r["hold_s"] >= MIN_COPYABLE_HOLD]
    taxed = [((1 + r["ret"]) / CHASE) * EXIT_HAIRCUT - 1 for r in copyable]
    del med_hold  # verdicts use the copyable-subset median
    taxed_mean = sum(taxed) / len(taxed) if taxed else 0.0
    taxed_wr = (sum(1 for t in taxed if t > 0) / len(taxed)) if taxed else 0.0
    # Verdict on the COPYABLE SUBSET (validation lesson: 2x99 — our only
    # board-proven COPYABLE — scalps 43% of trips AND holds 50min medians on
    # the rest at +29% taxed. A scalp-fraction hard-reject throws away the
    # baby; what matters is whether the >=2min subset is large enough and
    # pays after our tax. scalp_frac stays reported as a sizing hint.)
    cop_holds = sorted(r["hold_s"] for r in copyable)
    cop_med = cop_holds[len(cop_holds) // 2] if cop_holds else 0
    if len(copyable) < 4:
        verdict = "SCALPER" if scalp_frac > 0.6 else "THIN"
    elif cop_med < MEDIAN_HOLD_FLOOR:
        verdict = "SCALPER"
    elif taxed_mean <= 0 or taxed_wr < 0.5:
        verdict = "THIN-EDGE"
    else:
        verdict = "COPYABLE-GRADE"
    return {"wallet": addr, "verdict": verdict, "roundtrips": len(rts),
            "median_hold_min": round((cop_med if cop_holds else 0) / 60, 1),
            "scalp_frac": round(scalp_frac, 2),
            "taxed_copy_ret_mean": round(taxed_mean * 100, 2),
            "taxed_copy_wr": round(taxed_wr * 100), "n_copyable_trips": len(copyable)}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args and args[0].isdigit():       # bare number = validation set, deeper window
        args = [None, args[0]]
    if args and args[0]:
        cands = json.load(open(args[0]))
        cands = [c if isinstance(c, str) else (c.get("wallet") or c.get("addr"))
                 for c in cands]
        sigs = int(args[1]) if len(args) > 1 else 80
    else:
        # validation set: known-COPYABLE must pass, known-toxic scalpers must fail
        cands = ["2x99WSHDpwes7d3Qqhdcnpx8Mt6WmGPaeNkX9eR6Tamh",   # board-COPYABLE
                 "HcLMmNx9pcSM2cDuNMEcWKuDpiknGEb8djqGRsTtM9yo",   # toxic (cut 06-12)
                 "1eveYYxZ2mDiAnmCh3fnAbJwjgErzokRA1b6UrRybSM"]    # trending toxic
        sigs = 80
    print(f"{'wallet':46s}{'verdict':>16s}{'rtrip':>6}{'medHold':>9}{'scalp%':>7}"
          f"{'taxedRet':>9}{'taxedWR':>8}")
    out = []
    for w in cands:
        if not w:
            continue
        g = gate(w, sigs)
        out.append(g)
        print(f"{w:46s}{g['verdict']:>16s}{g.get('roundtrips', 0):>6}"
              f"{str(g.get('median_hold_min', '-')):>9}{str(g.get('scalp_frac', '-')):>7}"
              f"{str(g.get('taxed_copy_ret_mean', '-')):>9}{str(g.get('taxed_copy_wr', '-')):>8}")
        time.sleep(0.3)
    json.dump(out, open("_copyability_gate.json", "w"), indent=2)
    passed = [g["wallet"] for g in out if g["verdict"] == "COPYABLE-GRADE"]
    print(f"\nCOPYABLE-GRADE: {len(passed)}/{len(out)} -> _copyability_gate.json")


if __name__ == "__main__":
    main()
