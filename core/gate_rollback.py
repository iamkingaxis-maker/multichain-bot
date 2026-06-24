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
MONITORED_GATES = ["falling_day_flush", "solpump_neg_gate", "structure_edge"]


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
        wr = stats.get("wr")
        avg = stats.get("block_avg")
        if wr is None or avg is None:
            return False, "missing wr/block_avg"
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


def is_rolled_back(gate: str) -> bool:
    """FAIL-SAFE: any error -> False (gate keeps ENFORCING). Never disable a
    loss-cut on an IO/parse glitch."""
    try:
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
