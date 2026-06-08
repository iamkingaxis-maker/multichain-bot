"""Shared missed-winner reject diagnosis (v3, 2026-06-08).

Given a universe_dip_recorder dip event's features, returns which of OUR fleet
gates/filters would block it — so the recorder can STAMP each recorded dip with the
reject reason at source (continuous, no hot-path cost; the recorder is a low-frequency
separate process), and the offline analyzer can reuse the same logic.

Covers the RECONSTRUCTABLE layers:
  - fleet config gates: ~2h floor, per-bot age/mcap/vol_h1, sol-macro, entry_gate
    conditions (evaluable features only), rug (unique_buyers==0)
  - MODULAR filters: stale_drift, buyer_concentration

NOT reconstructable here (they live inline in dip_scanner's evaluate loop): the
inline trigger-firing logic and the inline filter stack. A dip that passes all of
the above is stamped 'passed_reconstructable (inline trigger/filter/timing)' — that
bucket is the honest remaining gap that only scanner-side per-token logging can crack.
"""
import glob
import json

_CFGS = None


def _configs():
    global _CFGS
    if _CFGS is None:
        _CFGS = []
        for f in glob.glob("config/bots/*.json"):
            try:
                d = json.load(open(f))
                if d.get("enabled", True):
                    _CFGS.append(d)
            except Exception:
                pass
    return _CFGS


def _num(d, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
    return None


def _bot_blocks(cfg, ev):
    """First reconstructable config gate that blocks this token for this bot, or None."""
    age = _num(ev, "age_hours", "lifecycle_age_hours")
    mc = _num(ev, "mcap")
    vh1 = _num(ev, "vol_h1")
    ub = _num(ev, "unique_buyers_n")
    yp = bool(cfg.get("young_token_probe"))
    if yp:
        if age is not None and age >= 6:
            return "young_probe_only<6h"
    elif age is not None and age < 2:
        return "fleet_floor_2h"
    amin, amax = cfg.get("age_h_min"), cfg.get("age_h_max")
    if amin is not None and age is not None and age < amin:
        return f"age_min({amin:g}h)"
    if amax is not None and age is not None and age > amax:
        return f"age_max({amax:g}h)"
    mmin, mmax = cfg.get("mcap_min"), cfg.get("mcap_max")
    if mmin is not None and mc is not None and mc < mmin:
        return f"mcap_min({int(mmin)})"
    if mmax is not None and mc is not None and mc > mmax:
        return "mcap_max"
    vmin = cfg.get("vol_h1_min")
    if vmin is not None and vh1 is not None and vh1 < vmin:
        return "vol_h1_min"
    if ub is not None and ub == 0:
        return "rug_gate:no_buyers"
    sh1, sh6 = _num(ev, "sol_pc_h1"), _num(ev, "sol_pc_h6")
    s1t, s6t = cfg.get("sol_macro_h1_block_threshold"), cfg.get("sol_macro_h6_block_threshold")
    if s1t is not None and sh1 is not None and sh1 < s1t:
        return "sol_macro_h1"
    if s6t is not None and sh6 is not None and sh6 < s6t:
        return "sol_macro_h6"
    for c in (cfg.get("entry_gate") or []):
        try:
            f, op, thr = c[0], c[1], float(c[2])
        except Exception:
            continue
        v = _num(ev, f)
        if v is None:
            continue  # fail-open (feature not in recorder)
        if op == ">=" and v < thr:
            return f"entry_gate:{f}>={thr:g}"
        if op == "<=" and v > thr:
            return f"entry_gate:{f}<={thr:g}"
    return None


def _modular_filters(ev):
    """Modular filter BLOCKs reconstructable from recorder features."""
    blocks = []
    meta = dict(ev)
    meta.setdefault("lifecycle_age_hours", ev.get("age_hours"))
    try:
        from core.stale_drift import stale_drift_verdict
        if stale_drift_verdict(meta)[0] == "BLOCK":
            blocks.append("stale_drift")
    except Exception:
        pass
    try:
        from core.buyer_concentration import buyer_concentration_verdict
        if buyer_concentration_verdict(meta)[0] == "BLOCK":
            blocks.append("buyer_concentration")
    except Exception:
        pass
    return blocks


def diagnose_reject(ev):
    """Return {fleet_gate_blocked, binding_gate, modular_filters, verdict}.

    fleet_gate_blocked = EVERY enabled bot blocked at a config gate (a true gate miss).
    If any bot passes the config gates, the miss is downstream (modular filter, inline
    trigger, or timing). Fail-soft: never raises."""
    try:
        cfgs = _configs()
        if not cfgs:
            return {"verdict": "no_configs"}
        blocks = [_bot_blocks(c, ev) for c in cfgs]
        passed_some = any(b is None for b in blocks)
        mods = _modular_filters(ev)
        if not passed_some:
            from collections import Counter
            binding = Counter(b for b in blocks if b).most_common(1)[0][0]
            return {"fleet_gate_blocked": True, "binding_gate": binding,
                    "modular_filters": mods, "verdict": f"gate:{binding}"}
        if mods:
            return {"fleet_gate_blocked": False, "binding_gate": None,
                    "modular_filters": mods, "verdict": f"modular:{'+'.join(mods)}"}
        return {"fleet_gate_blocked": False, "binding_gate": None, "modular_filters": [],
                "verdict": "passed_reconstructable(inline_trigger/filter/timing)"}
    except Exception as e:
        return {"verdict": "error", "error": str(e)[:80]}
