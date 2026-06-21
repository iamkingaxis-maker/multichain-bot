"""COMPLETE live-swap telemetry capture (gates the real-money live probe).

Every LIVE swap (buy AND sell) writes ONE durable, complete JSONL record to
DATA_DIR/live_swaps.jsonl so we can measure live fill LATENCY (per-step), fill
FIDELITY (slippage), diagnose FAILURES, reconcile COST, and observe RATE-LIMIT
behavior — all WITHOUT SSH (pulled via GET /api/live-swaps).

CONTRACT (hard):
  * FAIL-OPEN / NEVER RAISES into a trading path — every write is wrapped in
    try/except and degrades to a debug log. Telemetry must NEVER block, break,
    or slow a swap. Capture happens AFTER the swap completes wherever possible.
  * ADDRESS-keyed — token_address is the join key (symbol cross-poisons
    same-ticker mints; see the SPCX collision lesson).
  * COMPLETE — log_live_swap always writes EVERY field in REQUIRED_FIELDS. A
    field the code genuinely can't supply is written as null (never silently
    omitted) so the completeness gate can see exactly what's missing.
  * Flag-gated — LIVE_SWAP_LOG_MODE (on|off, default 'on'); 'off' = fully
    dormant (no file IO at all).
  * Monotonic clock for DURATIONS (ms); wall-clock ISO for the `ts` field.

Writes append-only JSONL (same DATA_DIR + raw-append pattern as
fill_speed_forward.jsonl / shadow_gate_events.jsonl). The basename is on the
core/log_rotator.py allowlist so it auto-rotates and can never refill the disk.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LOG_BASENAME = "live_swaps.jsonl"

# The COMPLETE field set — log_live_swap guarantees every one of these keys is
# present in the written record (null when unavailable). This is the contract the
# completeness gate enforces; keep it in sync with the user's required list.
REQUIRED_FIELDS = (
    # Identity / context
    "ts", "side", "bot_id", "token_address", "token_symbol", "pair_address",
    "trigger", "trigger_source", "size_usd", "size_sol", "lamports",
    "liquidity_usd", "mcap", "jupiter_api_base", "live_mode", "paper",
    # Latency per-step (ms; monotonic-derived)
    "decision_ts", "order_start_ts", "order_duration_ms", "sign_duration_ms",
    "execute_start_ts", "execute_duration_ms", "confirmed_ts", "total_latency_ms",
    # Prices / fidelity
    "decision_mid_price", "reprice_price", "reprice_runup_pct", "real_fill_price",
    "fill_vs_mid_slippage_pct", "ultra_reported_slippage_bps", "slippage_cap_bps",
    "cap_bound",
    # Execution outcome
    "success", "failure_reason", "error_text", "tx_signature", "in_amount",
    "out_amount", "decimals",
    # Rate-limit / retry
    "order_attempts", "order_429_count", "execute_429_count", "backoff_total_ms",
    # Cost reconciliation
    "sol_before", "sol_after", "sol_spent", "tokens_received",
    "priority_fee_lamports",
    # Debug (TRIMMED — never the full blob)
    "raw_order_response", "raw_execute_response",
)

# Canonical failure-reason enum (write one of these into failure_reason).
FAILURE_REASONS = (
    "ok", "revert", "timeout", "rate_limit", "slippage_exceeded", "other",
)


def _enabled() -> bool:
    """on (default) | off. 'off' = fully dormant (no IO)."""
    return os.environ.get("LIVE_SWAP_LOG_MODE", "on").strip().lower() != "off"


def _log_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), LOG_BASENAME)


def classify_failure_reason(success: bool, reason: object,
                            error_text: object = None) -> str:
    """Map a raw reason/error string onto the FAILURE_REASONS enum.

    Pure + defensive. success=True -> 'ok'. Otherwise inspect the reason/error
    text for known signatures (rate-limit / timeout / slippage / revert);
    anything unrecognized -> 'other'. Never raises."""
    try:
        if success:
            return "ok"
        blob = " ".join(str(x) for x in (reason, error_text) if x is not None).lower()
        if not blob:
            return "other"
        if "429" in blob or "rate" in blob and "limit" in blob or "too many" in blob:
            return "rate_limit"
        if "timeout" in blob or "timed out" in blob or "dropped" in blob:
            return "timeout"
        if "slippage" in blob or "slippage" in blob:
            return "slippage_exceeded"
        if "revert" in blob or "0x1" in blob or "failed on-chain" in blob or "custom program error" in blob:
            return "revert"
        return "other"
    except Exception:  # pragma: no cover - defensive
        return "other"


def log_live_swap(**fields) -> None:
    """Append ONE COMPLETE live-swap record to DATA_DIR/live_swaps.jsonl.

    Guarantees EVERY key in REQUIRED_FIELDS is present (null when not supplied),
    stamps a wall-clock ISO `ts` if the caller didn't, and normalizes
    failure_reason onto the enum. FAIL-OPEN: any error (bad path, serialization,
    full disk) is swallowed at debug level — this MUST NEVER raise into a
    trading path.

    Extra keys beyond REQUIRED_FIELDS are allowed (forward-compat) and written
    through. Unknown/missing required keys are filled with None.
    """
    try:
        if not _enabled():
            return
        rec = {k: fields.get(k, None) for k in REQUIRED_FIELDS}
        # Pass through any extra fields the caller supplied (forward-compat).
        for k, v in fields.items():
            if k not in rec:
                rec[k] = v
        # Always stamp a wall-clock ISO ts (caller may override by passing ts).
        if not rec.get("ts"):
            rec["ts"] = datetime.now(timezone.utc).isoformat()
        # Normalize the failure-reason onto the enum (defensive — never trust raw).
        rec["failure_reason"] = classify_failure_reason(
            bool(rec.get("success")), rec.get("failure_reason"), rec.get("error_text")
        )
        # token_address is the address-key; never let it be None in the record.
        if rec.get("token_address") is None:
            rec["token_address"] = ""
        with open(_log_path(), "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
    except Exception as e:  # pragma: no cover - defensive; never raise
        logger.debug("[live-swap-log] emit failed side=%s token=%s: %s",
                     fields.get("side"), fields.get("token_address"), e)


# ── Read-side summary (pure; unit-tested + used by GET /api/live-swaps) ────────
def _pctile(vals, q):
    """Nearest-rank percentile of a numeric list. None on empty."""
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def _nums(recs, key):
    """Pull the numeric (int/float, non-bool) values for a key across records."""
    out = []
    for r in recs:
        v = r.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(v)
    return out


def summarize_live_swaps(recs: list) -> dict:
    """Aggregate live-swap records into the probe summary. Pure + defensive.

    Returns n, success rate, median/p90 total_latency_ms + execute_duration_ms,
    median/mean fill_vs_mid_slippage_pct, 429 totals, and a failure_reason
    histogram. Empty input -> a zeroed summary (fail-open)."""
    import statistics as _stats
    recs = recs or []
    n = len(recs)
    if n == 0:
        return {
            "n": 0, "success_rate": None,
            "median_total_latency_ms": None, "p90_total_latency_ms": None,
            "median_execute_duration_ms": None, "p90_execute_duration_ms": None,
            "median_fill_vs_mid_slippage_pct": None,
            "mean_fill_vs_mid_slippage_pct": None,
            "order_429_total": 0, "execute_429_total": 0,
            "failure_reason_histogram": {},
            "by_side": {},
        }
    n_success = sum(1 for r in recs if bool(r.get("success")))
    total_lat = _nums(recs, "total_latency_ms")
    exec_dur = _nums(recs, "execute_duration_ms")
    slip = _nums(recs, "fill_vs_mid_slippage_pct")
    order_429 = sum(int(v) for v in _nums(recs, "order_429_count"))
    exec_429 = sum(int(v) for v in _nums(recs, "execute_429_count"))
    hist: dict = {}
    for r in recs:
        fr = r.get("failure_reason") or "other"
        hist[fr] = hist.get(fr, 0) + 1
    by_side: dict = {}
    for r in recs:
        s = r.get("side") or "?"
        by_side[s] = by_side.get(s, 0) + 1
    return {
        "n": n,
        "success_rate": round(n_success / n, 4),
        "median_total_latency_ms": (round(_stats.median(total_lat), 1) if total_lat else None),
        "p90_total_latency_ms": (round(_pctile(total_lat, 0.90), 1) if total_lat else None),
        "median_execute_duration_ms": (round(_stats.median(exec_dur), 1) if exec_dur else None),
        "p90_execute_duration_ms": (round(_pctile(exec_dur, 0.90), 1) if exec_dur else None),
        "median_fill_vs_mid_slippage_pct": (round(_stats.median(slip), 4) if slip else None),
        "mean_fill_vs_mid_slippage_pct": (round(_stats.mean(slip), 4) if slip else None),
        "order_429_total": order_429,
        "execute_429_total": exec_429,
        "failure_reason_histogram": hist,
        "by_side": by_side,
    }


def read_live_swaps(path: str) -> list:
    """Read all JSONL records from `path`. Fail-open: missing file -> []. Must
    be called OFF the event loop (via asyncio.to_thread) by the endpoint."""
    out: list = []
    try:
        if not os.path.exists(path):
            return out
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[live-swap-log] read failed %s: %s", path, e)
    return out
