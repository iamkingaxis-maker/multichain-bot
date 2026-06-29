#!/usr/bin/env python3
"""core/exit_trigger_recon_summary.py — EXIT-TRIGGER RECON read-only lens.

Summarizes the append-only shadow log DATA_DIR/exit_trigger_recon.jsonl, written by
feeds/dip_scanner.py:_maybe_exit_trigger_recon (EXIT_TRIGGER_RECON_MODE=shadow). Each
record captures, per held-position tick, the PAPER exit-trigger decision evaluated on
the ~150s-STALE main-scan DexScreener price vs a FRESH re-tick of the SAME pre-tick
position state on the ~2s Jupiter fast-watch price. This quantifies how often paper's
exit DECISION (peak/tp1_hit/tp2_hit/trail/stop/never_runner/floor/HOLD) diverges from
what a live bot watching fresh prices would actually do — and which way paper's booked
exit pnl is biased when they disagree.

Record fields written per line (verbatim from dip_scanner.py:5440):
  ts, bot, token, addr, stale_reason, stale_detail, stale_pnl, fresh_reason,
  fresh_detail, fresh_pnl, agree, pnl_delta, secs_stale, stale_price, fresh_price,
  peak_pnl_pct, tp1_hit, secs_since_entry

NOTE the record's own `pnl_delta` == fresh_pnl - stale_pnl. This lens defines the
fidelity delta the OTHER way to match the live-faithful convention (paper - fresh):
  pnl_delta_here = stale_pnl - fresh_pnl  (how much paper's exit pnl OVER/UNDER-states).

`summarize_exit_trigger_recon(records)` is pure: no I/O, no globals, no mutation.
Fail-open: a malformed record (missing/non-numeric fields) is skipped, never raises.
"""
import statistics


def _med(xs):
    return statistics.median(xs) if xs else None


def _mean(xs):
    return (sum(xs) / len(xs)) if xs else None


def _num(v):
    """Coerce to float or return None (fail-open)."""
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize_exit_trigger_recon(records):
    """Summarize exit-trigger-recon records (list of dict). Pure + fail-open.

    Returns:
      {
        "n": <records with a usable stale_reason & fresh_reason>,
        "agree_n": <count where stale trigger decision == fresh decision>,
        "agree_rate": <agree_n / n or None>,
        "pnl_delta": {"mean", "median", "n"}   # over DISAGREEMENTS, delta = stale - fresh
        "by_transition": {"<stale_reason>-><fresh_reason>": count, ...}  # disagreements
        "direction": "paper_OVERSTATES"|"paper_UNDERSTATES"|"neutral",
        "meta": {n_total, n_disagree, window_start, window_end},
      }

    agree == (stale_reason == fresh_reason): same fire/no-fire AND same trigger kind.
    pnl_delta (stale - fresh) is positive when paper books a HIGHER exit pnl than the
    fresh-repriced exit (paper OVERSTATES). Guards n=0 -> direction "neutral".
    """
    data = list(records or [])

    n = 0
    agree_n = 0
    n_total = len(data)
    deltas = []                 # stale - fresh, over DISAGREEMENTS only
    by_transition = {}
    times = []

    for r in data:
        if not isinstance(r, dict):
            continue
        ts = r.get("ts")
        if ts is not None:
            times.append(ts)
        stale_reason = r.get("stale_reason")
        fresh_reason = r.get("fresh_reason")
        if stale_reason is None or fresh_reason is None:
            # Can't compare the trigger decision -> skip (fail-open).
            continue
        n += 1
        agree = (stale_reason == fresh_reason)
        if agree:
            agree_n += 1
            continue
        # DISAGREEMENT: record the transition + the paper-vs-fresh pnl delta.
        key = "{}->{}".format(stale_reason, fresh_reason)
        by_transition[key] = by_transition.get(key, 0) + 1
        sp = _num(r.get("stale_pnl"))
        fp = _num(r.get("fresh_pnl"))
        if sp is not None and fp is not None:
            deltas.append(sp - fp)

    n_disagree = n - agree_n
    mean_delta = _mean(deltas)

    if not deltas or mean_delta is None:
        direction = "neutral"
    elif mean_delta > 0:
        direction = "paper_OVERSTATES"
    elif mean_delta < 0:
        direction = "paper_UNDERSTATES"
    else:
        direction = "neutral"

    return {
        "n": n,
        "agree_n": agree_n,
        "agree_rate": (agree_n / n) if n else None,
        "pnl_delta": {
            "mean": mean_delta,
            "median": _med(deltas),
            "n": len(deltas),
        },
        "by_transition": by_transition,
        "direction": direction,
        "meta": {
            "n_total": n_total,
            "n_disagree": n_disagree,
            "window_start": min(times) if times else None,
            "window_end": max(times) if times else None,
        },
    }
