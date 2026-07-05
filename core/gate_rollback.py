"""Forward-validation AUTO-ROLLBACK for enforced entry-veto gates.

We enforce some gates (falling_day_flush, solpump_neg_gate) on IN-SAMPLE fits.
The production risk: an in-sample-clean veto silently clips FORWARD winners. This
watcher reads each gate's forward-candle BLOCKED-cohort outcome (filter_shadow_pnl.json,
the same source /api/filter-shadow surfaces) and, if a gate's blocked tokens are
forward-WINNING (we're killing winners, not losers), AUTO-REVERTS it to shadow
(writes a rollback flag the gate checks at enforce time) + alarms.

Design choices:
  * FAIL-SAFE: any IO/parse error in ``is_rolled_back`` returns False → the gate
    KEEPS ENFORCING. A read glitch must never silently disable a loss-cut.
  * STICKY: rollback is one-directional. Once a gate is rolled back (winner-clip
    detected) it STAYS shadow until a human resets it (delete the flag). The
    watcher never auto-RE-enforces — that would flap a money-path gate on noisy
    forward data. Safer to fail toward not-blocking-winners.
  * SCOPE: only ENTRY VETOES measured by forward-candle are protected here. Exit/
    size gates (in-flight loss-floor, conviction down-size) have no forward
    counterfactual once enforced and are validated by their own telemetry + the
    top-bots scoreboard, not by this watcher.

PURE core (``evaluate_gate_rollback``) + a small file-backed state store.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Forward-candle-measured entry vetoes this watcher guards.
MONITORED_GATES = ["falling_day_flush", "solpump_neg_gate", "structure_edge",
                   "liquidity_exit_floor", "consec_red_knife", "not_dipping",
                   "pump_retrace_gate"]


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "gate_rollback.json")


def evaluate_gate_rollback(stats, *, min_n: int = 20, winner_wr: float = 50.0,
                           winner_avg: float = 0.0):
    """PURE. Decide whether a gate's BLOCKED cohort is forward-WINNING (=> the veto
    is clipping winners => roll it back).

    ``stats`` is one gate's entry from filter_shadow_pnl.json: needs ``block_n``,
    ``wr`` (the blocked cohort's forward win-rate — for a pure-BLOCK gate wr==block
    WR), and ``block_avg`` (mean forward pnl_pct of blocked tokens). Rollback only
    when BOTH a majority won (wr >= winner_wr) AND the mean is positive
    (block_avg > winner_avg) over a non-thin sample (block_n >= min_n) — both
    guards so one big winner can't trip it. Returns (should_rollback, reason).
    Fail-safe: missing/thin/garbage -> (False, reason)."""
    try:
        if not isinstance(stats, dict):
            return False, "no stats"
        bn = stats.get("block_n") or 0
        if bn < min_n:
            return False, f"thin (block_n={bn}<{min_n})"
        # BLACKOUT RCA 2026-07-05: this previously consumed stats["wr"], which
        # the scorer computes over PASS+BLOCK COMBINED — a gate whose passes
        # win (i.e. a WORKING gate) read as "blocked cohort winning" and
        # latched itself off (structure_edge sat rolled-back for days while
        # its logs said BLOCK; 1074/1589 buys violated it). Consume the
        # blocked-cohort win rate ONLY; if the scorer hasn't emitted it,
        # DON'T roll back (fail-safe: a gate stays enforcing unless the
        # correct cohort proves it clips winners).
        wr = stats.get("block_wr")
        avg = stats.get("block_avg")
        if wr is None or avg is None:
            return False, "missing block_wr/block_avg (no rollback without blocked-cohort stats)"
        wr = float(wr)
        avg = float(avg)
        if wr >= winner_wr and avg > winner_avg:
            return True, (f"blocked cohort forward-WINNING (wr={wr:.0f}% "
                          f"avg={avg:+.1f}% n={bn}) — clipping winners, roll back")
        return False, f"blocked cohort forward-losing/mixed (wr={wr:.0f}% avg={avg:+.1f}%)"
    except Exception as e:  # pragma: no cover - defensive
        return False, f"eval err {e}"


def read_rollback_state() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


_cleared_once = False


def _maybe_clear_latches() -> None:
    """BLACKOUT RCA 2026-07-05: latches set by the OLD mixed-cohort wr bug
    must be clearable without shell access to /data. GATE_ROLLBACK_CLEAR
    (comma-separated gate names, or 'all') removes those latches ONCE per
    process. Idempotent; fail-safe (errors leave state untouched)."""
    global _cleared_once
    if _cleared_once:
        return
    _cleared_once = True
    raw = os.environ.get("GATE_ROLLBACK_CLEAR", "").strip()
    if not raw:
        return
    try:
        st = read_rollback_state()
        targets = (list(st.keys()) if raw.lower() == "all"
                   else [g.strip() for g in raw.split(",") if g.strip()])
        changed = [g for g in targets if st.get(g, {}).get("rolled_back")]
        if not changed:
            return
        for g in changed:
            st[g] = {"rolled_back": False,
                     "reason": "cleared via GATE_ROLLBACK_CLEAR (RCA 2026-07-05: "
                               "latch was set by the mixed-cohort wr bug)",
                     "stats": {},
                     "ts": datetime.now(timezone.utc).isoformat()}
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, _path())
        logging.getLogger(__name__).warning(
            "[gate-rollback] CLEARED latches via env: %s", changed)
    except Exception:
        pass


def is_rolled_back(gate: str) -> bool:
    """FAIL-SAFE: any error -> False (gate keeps ENFORCING). Never disable a
    loss-cut on an IO/parse glitch."""
    try:
        _maybe_clear_latches()
        return bool(read_rollback_state().get(gate, {}).get("rolled_back"))
    except Exception:
        return False


def set_rollback(gate: str, rolled_back: bool, reason: str, stats=None) -> bool:
    try:
        st = read_rollback_state()
        st[gate] = {
            "rolled_back": bool(rolled_back),
            "reason": reason,
            "stats": stats or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, _path())
        return True
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[gate-rollback] set err %s: %s", gate, e)
        return False


def run_gate_rollback_check(fwd_pnl_by_filter, gates=None) -> list:
    """Read the forward-candle output and STICKY-roll-back any monitored gate whose
    blocked cohort is forward-winning. Never auto-re-enforces. Logs/alarms on a NEW
    rollback. Returns [(gate, rolled_back_now, reason)] for the caller to surface."""
    gates = gates or MONITORED_GATES
    prev = read_rollback_state()
    out = []
    for g in gates:
        already = bool(prev.get(g, {}).get("rolled_back"))
        if already:
            out.append((g, True, "already rolled back (sticky)"))
            continue
        stats = (fwd_pnl_by_filter or {}).get(g) or {}
        should, reason = evaluate_gate_rollback(stats)
        if should:
            set_rollback(g, True, reason,
                         {k: stats.get(k) for k in ("block_n", "wr", "block_avg")})
            logger.warning("[gate-rollback] %s -> ROLLBACK to shadow: %s", g, reason)
        out.append((g, should, reason))
    return out
