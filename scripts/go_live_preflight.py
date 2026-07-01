#!/usr/bin/env python3
"""
go_live_preflight.py — ONE command to validate the Matrix B live configuration.

Run BEFORE any PAPER_MODE flip or live probe:
    PYTHONPATH=. python scripts/go_live_preflight.py            # env from Railway vars pasted/exported
    PYTHONPATH=. python scripts/go_live_preflight.py --env-file railway_env.txt

It checks (read-only): Matrix B env values, live roster (exactly one probe bot),
sizing/floors, and runs the pre-live invariants test suite. Prints PASS/FAIL per
item and an overall GO / NO-GO. See config/LIVE_MATRIX_B.md for rationale.

NOTE: this script cannot read Railway's env directly. Export the vars locally
(`railway variables` output pasted to a file, `KEY=VALUE` or the table format)
or run with --env-file. Anything unreadable is reported as UNKNOWN (= NO-GO).
"""
import json
import os
import re
import subprocess
import sys

REQUIRED_ENV = {
    # var: (required value(s), why)
    "PAPER_FIDELITY_MODE": (["enforce"], "honest fills — permanent"),
    "BUY_REPRICE_MODE": (["enforce"], "fresh-price entries — permanent"),
    "EXIT_REPRICE_MODE": (["enforce"], "fresh-price exits — permanent"),
    "SOL_MACRO_GATE_MODE": (["strict"], "loose-admits were -3.86pp/17.9% win"),
    "FULL_THESIS_COHORT_MODE": (["enforce"], "live trades only the +EV slice"),
    "OVERSOLD_HELD_MODE": (["enforce"], "loss-reducer layer"),
    "REGIME_BUY_GATE_MODE": (["enforce"], "crash protection"),
    "GREEN_DAY_MODE": (["enforce", "shadow"], "green-day gate (enforce once bar met)"),
    "EXIT_SLIP_LIQ_MODE": (["enforce"], "sellability modeling at flip"),
    "GAP_THROUGH_HAIRCUT_PCT": (["5", "5.0"], "default; the cut to 1 was unjustified"),
    "DIP_POSITION_USD": (["5", "5.0", "25", "25.0"], "ruin-safe sizing ($25 only after slip probe)"),
    "PROBE_AGG_DAILY_KILL_USD": (["10", "10.0"], "2-loser daily halt"),
    "STRATEGY_ALLOWLIST": (["dip_buy"], "fail-closed live routing"),
}
UNSET_REQUIRED = {
    "BUY_GATE_SOL_H24_OFF": ("-1", "the -4 relax admitted losers (unset or -1)"),
    "FILTERS_RELAX_LIST": ("none", "admitted 2 trades, both losers (unset or 'none')"),
}


def load_env_file(path):
    env = {}
    txt = open(path, encoding="utf-8", errors="replace").read()
    # accept KEY=VALUE lines or railway's table format
    for m in re.finditer(r"([A-Z][A-Z0-9_]+)\s*(?:=|│|\|)\s*([^\s║│|]+)", txt):
        env[m.group(1)] = m.group(2).strip()
    return env


def main():
    env = dict(os.environ)
    if "--env-file" in sys.argv:
        env.update(load_env_file(sys.argv[sys.argv.index("--env-file") + 1]))

    results = []

    def check(name, ok, detail):
        results.append((ok, name, detail))

    # 1. env values
    for var, (allowed, why) in REQUIRED_ENV.items():
        v = env.get(var)
        if v is None:
            check(f"env {var}", None, f"UNKNOWN (not visible) — need one of {allowed} [{why}]")
        else:
            check(f"env {var}", str(v) in allowed, f"{v!r} (need {allowed}) [{why}]")
    for var, (neutral, why) in UNSET_REQUIRED.items():
        v = env.get(var)
        ok = v is None or str(v) == neutral
        check(f"env {var} neutral", ok, f"{v!r} (need unset/{neutral}) [{why}]")

    # 2. WORKING_CAPITAL_FLOOR_USD sanity — must be a real number, and the operator
    # must confirm it equals ACTUAL starting capital (cannot auto-verify).
    wf = env.get("WORKING_CAPITAL_FLOOR_USD")
    check("WORKING_CAPITAL_FLOOR_USD set", wf is not None,
          f"{wf!r} — MUST equal actual starting capital (manual confirm; $500 vs a ~$12 wallet = sweep misbehaves)")

    # 3. roster: exactly one live_probe bot, and it is badday_fill_probe_live
    import glob
    live_bots = []
    for f in glob.glob("config/bots/*.json"):
        try:
            c = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if c.get("live_probe") and c.get("enabled"):
            live_bots.append(c.get("bot_id"))
    check("live roster == [badday_fill_probe_live]",
          live_bots == ["badday_fill_probe_live"],
          f"enabled live_probe bots: {live_bots}")

    # 4. pre-live invariants suite
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_pre_live_invariants.py", "-q"],
            capture_output=True, text=True, timeout=300)
        ok = r.returncode == 0
        tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        check("pre-live invariants", ok, tail)
    except Exception as e:
        check("pre-live invariants", False, f"could not run: {e}")

    # 5. the go-live BAR reminder (cannot be auto-verified — quote the scoreboard)
    check("go-live bar (MANUAL)", None,
          "scripts/honest_book.py scrubbed cohort mean >= +2pp over >=300 trades / "
          ">=50 tokens / >=5 days — attach the output to the go decision")

    print("=" * 78)
    print("GO-LIVE PREFLIGHT — Matrix B (see config/LIVE_MATRIX_B.md)")
    print("=" * 78)
    hard_fail = False
    unknown = False
    for ok, name, detail in results:
        if ok is True:
            tag = "PASS"
        elif ok is False:
            tag = "FAIL"; hard_fail = True
        else:
            tag = "?   "; unknown = True
        print(f"  [{tag}] {name:<40} {detail}")
    print("-" * 78)
    if hard_fail:
        print("VERDICT: NO-GO — fix the FAILs above.")
        sys.exit(1)
    if unknown:
        print("VERDICT: INCOMPLETE — resolve the '?' items (env visibility / manual confirms).")
        sys.exit(2)
    print("VERDICT: GO (env + roster + invariants clean; confirm the manual items).")


if __name__ == "__main__":
    main()
