"""Missed-winner diagnosis (v2, 2026-06-08) — the false-negative half.

Cross-references the universe_dip_recorder corpus (every dip the fleet SAW) against
our actual buys to find HELD winners we MISSED, then replays EVERY enabled bot's
full config-gate stack to attribute the miss fleet-accurately: a token is only a
"gate miss" if EVERY bot would block it. Produces a counterfactual — which single
gate, relaxed fleet-wide, would recover the most missed held-winners.

HONEST SCOPE: only the reconstructable config gates are replayed (fleet 2h floor,
age, mcap, vol_h1, sol/btc macro, net_flow/bs entry-gate conds, rug). The 1m-
freshness entry gates, the filter stack, and trigger-firing are NOT in the recorder
feature set, so tokens that pass all reconstructable gates fall into a "downstream
(1m-gate / trigger / filter / timing)" bucket — the miss is below what we can see
from recorder data alone.

Usage: python scripts/missed_winner_diagnose.py [events.json] [trades.json] [--json]
Defaults to _univ.json + _bug.json in CWD.
"""
from __future__ import annotations
import json, glob, sys, collections


def num(e, k):
    v = e.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def load_configs():
    cfgs = []
    for f in glob.glob("config/bots/*.json"):
        try:
            d = json.load(open(f))
            if d.get("enabled", True):
                cfgs.append(d)
        except Exception:
            pass
    return cfgs


def is_held_winner(e):
    """Pumped >=5% and still up at the +30min outcome, not fully round-tripped."""
    ex = num(e, "exit_pct")
    return (ex is not None and ex >= 5) and (e.get("traj_round_trip") is not True)


def bot_block_gate(cfg, e, disable=None):
    """Return the FIRST reconstructable config gate that blocks this token for this
    bot, or None if it passes them all. `disable` = a gate name to skip (counterfactual)."""
    age, mc, vh1 = num(e, "age_hours"), num(e, "mcap"), num(e, "vol_h1")
    ub = num(e, "unique_buyers_n")
    yp = bool(cfg.get("young_token_probe"))

    def on(name):
        return disable != name

    # Fleet ~2h floor (young-probe bots trade <6h ONLY instead).
    if yp:
        if on("young_probe_window") and age is not None and age >= 6:
            return "young_probe_only<6h"
    elif on("fleet_floor_2h") and age is not None and age < 2:
        return "fleet_floor_2h"
    # Per-bot age
    amin, amax = cfg.get("age_h_min"), cfg.get("age_h_max")
    if on("age_min") and amin is not None and age is not None and age < amin:
        return f"age_min({amin:g}h)"
    if on("age_max") and amax is not None and age is not None and age > amax:
        return f"age_max({amax:g}h)"
    # mcap
    mmin, mmax = cfg.get("mcap_min"), cfg.get("mcap_max")
    if on("mcap_min") and mmin is not None and mc is not None and mc < mmin:
        return f"mcap_min({int(mmin)})"
    if on("mcap_max") and mmax is not None and mc is not None and mc > mmax:
        return "mcap_max"
    # vol_h1 floor
    vmin = cfg.get("vol_h1_min")
    if on("vol_h1_min") and vmin is not None and vh1 is not None and vh1 < vmin:
        return "vol_h1_min"
    # rug gate (fleet enforce)
    if on("rug_gate") and ub is not None and ub == 0:
        return "rug_gate"
    # sol macro (recorder has sol_pc_h1/h6)
    sh1, sh6 = num(e, "sol_pc_h1"), num(e, "sol_pc_h6")
    s1t, s6t = cfg.get("sol_macro_h1_block_threshold"), cfg.get("sol_macro_h6_block_threshold")
    if on("sol_macro") and s1t is not None and sh1 is not None and sh1 < s1t:
        return "sol_macro_h1"
    if on("sol_macro") and s6t is not None and sh6 is not None and sh6 < s6t:
        return "sol_macro_h6"
    # entry_gate (only features present in the recorder; fail-OPEN on missing — matches prod)
    for c in (cfg.get("entry_gate") or []):
        try:
            f, op, thr = c[0], c[1], float(c[2])
        except Exception:
            continue
        if not on(f"entry_gate:{f}"):
            continue
        v = num(e, f)
        if v is None:
            continue  # fail-open
        if op == ">=" and v < thr:
            return f"entry_gate:{f}>={thr:g}"
        if op == "<=" and v > thr:
            return f"entry_gate:{f}<={thr:g}"
    return None


def fleet_catchable(cfgs, e, disable=None):
    """True if AT LEAST ONE bot passes all reconstructable config gates."""
    return any(bot_block_gate(c, e, disable=disable) is None for c in cfgs)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ev_path = args[0] if args else "_univ.json"
    tr_path = args[1] if len(args) > 1 else "_bug.json"
    ev = json.load(open(ev_path))
    ev = ev.get("events", ev) if isinstance(ev, dict) else ev
    try:
        tr = json.load(open(tr_path))
        ours = {x.get("address") for x in tr if x.get("type") == "buy"}
    except Exception:
        ours = set()
    cfgs = load_configs()

    held = [e for e in ev if is_held_winner(e)]
    missed = [e for e in held if e.get("token_address") not in ours]

    # Split: reached >=1 bot's trigger stage (downstream miss) vs gate-blocked fleet-wide
    downstream = [e for e in missed if fleet_catchable(cfgs, e)]
    gate_blocked = [e for e in missed if not fleet_catchable(cfgs, e)]

    # Counterfactual: relaxing each gate fleet-wide, how many gate-blocked tokens recover?
    GATES = ["fleet_floor_2h", "age_min", "age_max", "mcap_min", "mcap_max",
             "vol_h1_min", "rug_gate", "sol_macro", "young_probe_window"]
    recover = {}
    for g in GATES:
        recover[g] = sum(1 for e in gate_blocked if fleet_catchable(cfgs, e, disable=g))

    out = {
        "events": len(ev), "held_winners": len(held), "missed_held": len(missed),
        "downstream_miss": len(downstream), "gate_blocked": len(gate_blocked),
        "recover_if_relaxed": dict(sorted(recover.items(), key=lambda kv: -kv[1])),
    }
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2)); return

    print(f"recorder events: {out['events']} | held winners: {out['held_winners']} | "
          f"MISSED held (not bought): {out['missed_held']}")
    print(f"\n  blocked at TRIGGER/FILTER/1m-freshness layer (passed all config gates "
          f"on >=1 bot): {len(downstream)} ({len(downstream)/max(len(missed),1):.0%})")
    print(f"  blocked by a CONFIG GATE on EVERY bot: {len(gate_blocked)} "
          f"({len(gate_blocked)/max(len(missed),1):.0%})")
    print(f"\n  COUNTERFACTUAL — missed winners recovered if we relaxed (fleet-wide):")
    for g, n in out["recover_if_relaxed"].items():
        if n:
            print(f"    {g:22s} +{n} recovered ({n/max(len(gate_blocked),1):.0%} of gate-blocked)")
    print(f"\n  NOTE: trigger-firing + the filter stack + 1m-freshness gates are NOT in the "
          f"recorder, so the {len(downstream)} 'downstream' misses can't be attributed "
          f"further from this data — that needs scan-time per-token verdict logging.")


if __name__ == "__main__":
    main()
