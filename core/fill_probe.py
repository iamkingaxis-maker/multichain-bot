"""QUOTE-BASED fill-accuracy shadow probe.

PURPOSE
-------
Validate — for FREE, no real money — whether PAPER's modeled fill matches the
REAL on-chain execution cost. At a sampled paper BUY we fetch the live Jupiter
quote (which returns the true ``priceImpactPct`` against live liquidity) and
record modeled-vs-real. This measures the SLIPPAGE/IMPACT + DRIFT portion of
fill accuracy — the part you can measure WITHOUT landing a tx.

Two halves, mirroring core/live_swap_log.py:
  * ``compute_fill_probe`` — PURE, fail-open math (TDD'd).
  * ``log_fill_probe`` — gated, fail-open JSONL recorder; ``FILL_PROBE_MODE``
    (on|off, default 'off' => ships DORMANT). Basename is on the
    core/log_rotator.py allowlist so it auto-rotates.

CONTRACT (hard):
  * PURE observability — NEVER alters what paper trades, NEVER touches a
    live/money path (only READS quotes, never swaps).
  * FAIL-OPEN everywhere — any error returns {} / swallows; never raises into
    the buy path.
  * ADDRESS-keyed — token_address is the join key.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LOG_BASENAME = "fill_probe.jsonl"


def _as_float(v):
    """float(v) or None if not a finite number (bool excluded). Mirrors
    core.top_bots._as_float — NaN/inf/zero-div-safe coercion."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def compute_fill_probe(decision_mid, fresh_price, real_impact_pct,
                       paper_modeled_fill, size_usd, liquidity_usd) -> dict:
    """Compare paper's modeled fill against the REAL quote-implied fill. PURE.

    real_fill_price     = fresh_price * (1 + real_impact_pct/100)   (priceImpactPct
                          is the % deviation from pool mid; applied to fresh price)
    real_drift_pct      = (fresh_price/decision_mid - 1)*100         (decision->fill move)
    real_total_cost_pct = (real_fill_price/decision_mid - 1)*100     (what live would pay)
    paper_total_cost_pct= (paper_modeled_fill/decision_mid - 1)*100  (what paper booked)
    model_error_pct     = paper_total_cost_pct - real_total_cost_pct
                          (>0 = paper too optimistic/cheap vs reality; <0 = too pessimistic)

    FAIL-OPEN: any non-numeric/NaN/zero-divide (notably decision_mid==0) -> {}.
    Never raises.
    """
    try:
        dm = _as_float(decision_mid)
        fp = _as_float(fresh_price)
        imp = _as_float(real_impact_pct)
        pmf = _as_float(paper_modeled_fill)
        if dm is None or fp is None or imp is None or pmf is None:
            return {}
        if dm == 0.0:
            return {}
        real_fill_price = fp * (1.0 + imp / 100.0)
        real_drift_pct = (fp / dm - 1.0) * 100.0
        real_total_cost_pct = (real_fill_price / dm - 1.0) * 100.0
        paper_total_cost_pct = (pmf / dm - 1.0) * 100.0
        model_error_pct = paper_total_cost_pct - real_total_cost_pct
        return {
            "decision_mid": dm,
            "fresh_price": fp,
            "real_impact_pct": imp,
            "real_fill_price": real_fill_price,
            "real_drift_pct": real_drift_pct,
            "real_total_cost_pct": real_total_cost_pct,
            "paper_modeled_fill": pmf,
            "paper_total_cost_pct": paper_total_cost_pct,
            "model_error_pct": model_error_pct,
            "size_usd": _as_float(size_usd),
            "liquidity_usd": _as_float(liquidity_usd),
        }
    except Exception:  # pragma: no cover - defensive; never raise
        return {}


# ── Recorder (mirrors core/live_swap_log.py: gated, fail-open) ─────────────────
def _enabled() -> bool:
    """on | off (default OFF — ships DORMANT, enabled per measurement window)."""
    return os.environ.get("FILL_PROBE_MODE", "off").strip().lower() == "on"


def _log_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), LOG_BASENAME)


def log_fill_probe(bot_id, token_address, symbol, probe: dict) -> None:
    """Append ONE probe record to DATA_DIR/fill_probe.jsonl. FAIL-OPEN.

    Record shape: {ts, bot_id, token_address, symbol, **probe fields}. Gated by
    FILL_PROBE_MODE (default off). NEVER raises into the buy path — any error
    (disabled, empty probe, bad path, serialization, full disk) degrades to a
    debug log. token_address is the address join-key (never None in the record).
    """
    try:
        if not _enabled():
            return
        if not probe:
            return
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "bot_id": bot_id,
            "token_address": (token_address or ""),
            "symbol": symbol,
        }
        rec.update(probe)
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
    except Exception as e:  # pragma: no cover - defensive; never raise
        logger.debug("[fill-probe] emit failed bot=%s token=%s: %s",
                     bot_id, token_address, e)


# ── Read-side summary (pure; used by GET /api/fill-probe) ──────────────────────
def _pctile(vals, q):
    """Nearest-rank percentile of a numeric list. None on empty."""
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def _nums(recs, key):
    out = []
    for r in recs:
        f = _as_float(r.get(key))
        if f is not None:
            out.append(f)
    return out


def _med(vals):
    if not vals:
        return None
    import statistics as _stats
    return round(_stats.median(vals), 4)


def _bucket_of(liq):
    """thin (<30k) / mid (30-100k) / deep (100k+). None liq -> 'unknown'."""
    f = _as_float(liq)
    if f is None:
        return "unknown"
    if f < 30000.0:
        return "thin"
    if f < 100000.0:
        return "mid"
    return "deep"


def _bucket_summary(recs) -> dict:
    """The per-bucket fill-accuracy block (n + the key metrics)."""
    me = _nums(recs, "model_error_pct")
    return {
        "n": len(recs),
        "median_real_impact_pct": _med(_nums(recs, "real_impact_pct")),
        "p90_real_impact_pct": (round(_pctile(_nums(recs, "real_impact_pct"), 0.90), 4)
                                if _nums(recs, "real_impact_pct") else None),
        "median_real_total_cost_pct": _med(_nums(recs, "real_total_cost_pct")),
        "median_model_error_pct": _med(me),
        "p90_model_error_pct": (round(_pctile(me, 0.90), 4) if me else None),
        "frac_abs_error_gt_2": (round(sum(1 for x in me if abs(x) > 2.0) / len(me), 4)
                                if me else None),
    }


def summarize_fill_probes(recs: list) -> dict:
    """Aggregate fill-probe records. PURE + defensive. Empty -> zeroed summary.

    Answers "is paper's fill model accurate, and WHERE is it wrong?":
      * median/p90 of real_impact_pct, real_total_cost_pct, real_drift_pct
      * KEY metric: model_error_pct median/p90 + frac with |error| > 2
      * the same broken out by liquidity bucket (thin/mid/deep)
    """
    recs = recs or []
    n = len(recs)
    if n == 0:
        return {
            "n": 0,
            "median_real_impact_pct": None, "p90_real_impact_pct": None,
            "median_real_total_cost_pct": None, "p90_real_total_cost_pct": None,
            "median_real_drift_pct": None, "p90_real_drift_pct": None,
            "median_model_error_pct": None, "p90_model_error_pct": None,
            "frac_abs_error_gt_2": None,
            "by_liquidity_bucket": {},
        }
    imp = _nums(recs, "real_impact_pct")
    tot = _nums(recs, "real_total_cost_pct")
    drift = _nums(recs, "real_drift_pct")
    me = _nums(recs, "model_error_pct")
    buckets: dict = {}
    grouped: dict = {}
    for r in recs:
        grouped.setdefault(_bucket_of(r.get("liquidity_usd")), []).append(r)
    for name, group in grouped.items():
        buckets[name] = _bucket_summary(group)
    return {
        "n": n,
        "median_real_impact_pct": _med(imp),
        "p90_real_impact_pct": (round(_pctile(imp, 0.90), 4) if imp else None),
        "median_real_total_cost_pct": _med(tot),
        "p90_real_total_cost_pct": (round(_pctile(tot, 0.90), 4) if tot else None),
        "median_real_drift_pct": _med(drift),
        "p90_real_drift_pct": (round(_pctile(drift, 0.90), 4) if drift else None),
        "median_model_error_pct": _med(me),
        "p90_model_error_pct": (round(_pctile(me, 0.90), 4) if me else None),
        "frac_abs_error_gt_2": (round(sum(1 for x in me if abs(x) > 2.0) / len(me), 4)
                                if me else None),
        "by_liquidity_bucket": buckets,
    }


def read_fill_probes(path: str) -> list:
    """Read all JSONL records from `path`. Fail-open: missing file -> []. Call
    OFF the event loop (asyncio.to_thread) from the endpoint."""
    out: list = []
    try:
        if not os.path.exists(path):
            return out
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[fill-probe] read failed %s: %s", path, e)
    return out
