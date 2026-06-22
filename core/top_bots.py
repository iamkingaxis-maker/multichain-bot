"""core.top_bots — the curated "top-bots scoreboard" helper.

Replaces noisy fleet-daily P&L with a clean per-bot view of the PROVEN top
bots, measured the durable way: realized $/trade + WR + downside tail at
n>=30 (NOT daily totals).

PURE + FAIL-OPEN: ``compute_top_bots`` never raises — it wraps aggregation,
skips malformed records, and always returns one entry per requested bot (a
zeroed entry when the bot has no closed sells, so the UI can still show it).
Address/bot-keyed; no money path, no shared mutable state.
"""
from __future__ import annotations

import os
import statistics
from typing import Any

# The curated core set — the proven top bots (realized-positive, durable).
TOP_BOTS_DEFAULT = [
    "badday_flush_conviction",
    "badday_flush_conviction_demand",
    "badday_flush",
    "badday_flush_nf15",
    "timebox_probe_5mgreen",
]

ENOUGH_N = 30  # n>=30 closed sells before the numbers are "durable"


def top_bots_set() -> list[str]:
    """Return the bot set to score: env ``TOP_BOTS`` (comma-separated) else
    ``TOP_BOTS_DEFAULT``. Fail-open — falls back to the default on any error."""
    try:
        raw = os.environ.get("TOP_BOTS")
        if raw:
            bots = [b.strip() for b in raw.split(",") if b.strip()]
            if bots:
                return bots
    except Exception:
        pass
    return list(TOP_BOTS_DEFAULT)


def _zeroed() -> dict:
    return {
        "n": 0,
        "realized_usd": 0,
        "pnl_per_tr": 0,
        "wr": 0,
        "median_pnl_pct": 0,
        "worst_decile_pnl_pct": 0,
        "max_loss_usd": 0,
        "enough_n": False,
    }


def _as_float(v: Any):
    """Return float(v) or None if not a finite number (bool excluded)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _is_sell(rec: dict) -> bool:
    t = rec.get("type")
    if t is None:
        t = rec.get("side")
    return t == "sell"


def _bot_of(rec: dict):
    b = rec.get("bot_id")
    if b is None:
        b = rec.get("strategy")
    return b


def _nearest_rank(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile (1-indexed). pct in [0,100]."""
    n = len(sorted_vals)
    if n == 0:
        return 0
    import math
    rank = max(1, math.ceil(pct / 100.0 * n))
    rank = min(rank, n)
    return sorted_vals[rank - 1]


def compute_top_bots(trades: list, bots: list) -> dict:
    """PURE, fail-open. For each bot in ``bots`` aggregate its CLOSED sells
    (records where ``(type|side)=='sell'`` and ``pnl_pct`` is numeric and
    ``(bot_id|strategy)==bot``) and return per-bot metrics.

    Never raises: malformed records are skipped; per-bot aggregation is wrapped
    so one bad bot can't break the others. A bot with no closed sells gets a
    zeroed (but present) entry.
    """
    out: dict[str, dict] = {}
    try:
        wanted = [b for b in (bots or []) if b is not None]
    except Exception:
        wanted = []

    # Bucket valid sells by bot in a single pass (fail-open per record).
    buckets: dict[str, list[tuple[float, float]]] = {b: [] for b in wanted}
    wanted_set = set(wanted)
    try:
        for rec in (trades or []):
            try:
                if not isinstance(rec, dict):
                    continue
                if not _is_sell(rec):
                    continue
                bot = _bot_of(rec)
                if bot not in wanted_set:
                    continue
                pct = _as_float(rec.get("pnl_pct"))
                if pct is None:
                    continue
                usd = _as_float(rec.get("pnl_usd"))
                if usd is None:
                    usd = _as_float(rec.get("pnl"))
                if usd is None:
                    usd = 0.0
                buckets[bot].append((pct, usd))
            except Exception:
                continue
    except Exception:
        pass

    for bot in wanted:
        try:
            rows = buckets.get(bot, [])
            n = len(rows)
            if n == 0:
                out[bot] = _zeroed()
                continue
            pcts = [r[0] for r in rows]
            usds = [r[1] for r in rows]
            realized = sum(usds)
            wins = sum(1 for p in pcts if p > 0)
            sorted_pcts = sorted(pcts)
            out[bot] = {
                "n": n,
                "realized_usd": round(realized, 2),
                "pnl_per_tr": round(realized / n, 2),
                "wr": round(100.0 * wins / n, 1),
                "median_pnl_pct": round(statistics.median(pcts), 2),
                "worst_decile_pnl_pct": round(_nearest_rank(sorted_pcts, 10), 2),
                "max_loss_usd": round(min(usds), 2),
                "enough_n": n >= ENOUGH_N,
            }
        except Exception:
            out[bot] = _zeroed()

    return out
