"""Paper-vs-live SKIP-INSTRUMENTATION scoreboard (the 1:1 fidelity proof).

For every paper BUY decision we record, durably, whether LIVE would have taken
the same trade and — when it would NOT — exactly WHY (the skip_reason). This is
the data that (a) proves paper and live agree 1:1 on the trades they share and
(b) explains every trade live legitimately skips (liquidity floor, rug bundle,
not allowlisted, reprice run-up, etc.).

Mirrors core/live_swap_log.py:
  * FAIL-OPEN / NEVER RAISES into a trading path — every write is wrapped in
    try/except and degrades to a debug log. Telemetry must NEVER block or break
    a trade.
  * ADDRESS-keyed — token_address is the join key (symbol cross-poisons
    same-ticker mints; see the SPCX collision lesson). Never None in the record.
  * Flag-gated — PAPER_LIVE_RECONCILE_MODE (on|off, default 'on'); 'off' = fully
    dormant (no file IO at all).
  * Wall-clock ISO `ts` stamped on every record.

Writes append-only JSONL to DATA_DIR/paper_live_reconcile.jsonl. The basename is
on the core/log_rotator.py allowlist so it auto-rotates and can never refill the
disk. Read off the event loop (via asyncio.to_thread) by GET /api/paper-live-skips.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LOG_BASENAME = "paper_live_reconcile.jsonl"


def _enabled() -> bool:
    """on (default) | off. 'off' = fully dormant (no IO)."""
    return os.environ.get("PAPER_LIVE_RECONCILE_MODE", "on").strip().lower() != "off"


def _log_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), LOG_BASENAME)


def log_paper_live_decision(token_address, token_symbol, paper_took,
                            live_would_take, skip_reason, fresh_source,
                            delta_pct) -> None:
    """Append ONE paper-vs-live decision record to DATA_DIR/paper_live_reconcile.jsonl.

    Stamps a wall-clock ISO `ts`. token_address is the address-key and is never
    written as None (coerced to ""). FAIL-OPEN: any error (bad path,
    serialization, full disk) is swallowed at debug level — this MUST NEVER
    raise into a trading path.
    """
    try:
        if not _enabled():
            return
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "token_address": token_address if token_address is not None else "",
            "token_symbol": token_symbol,
            "paper_took": bool(paper_took),
            "live_would_take": bool(live_would_take),
            "skip_reason": skip_reason,
            "fresh_source": fresh_source,
            "delta_pct": delta_pct,
        }
        with open(_log_path(), "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
    except Exception as e:  # pragma: no cover - defensive; never raise
        logger.debug("[paper-live-reconcile] emit failed token=%s: %s",
                     token_address, e)


def read_paper_live_reconcile(path: str) -> list:
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
        logger.debug("[paper-live-reconcile] read failed %s: %s", path, e)
    return out


def summarize_reconcile(recs: list) -> dict:
    """Aggregate reconcile records into the 1:1 scoreboard. Pure + defensive.

    Returns:
      {"n": N,
       "paper_only_n": X,                # paper_took=True AND live_would_take=False
       "by_skip_reason": {reason: count}}  # histogram of skip_reason over paper-only

    A paper-only record with a missing/None skip_reason buckets under "unknown".
    Empty/None input -> a zeroed dict. Non-dict junk records are skipped."""
    recs = recs or []
    n = 0
    paper_only_n = 0
    by_skip_reason: dict = {}
    for r in recs:
        if not isinstance(r, dict):
            continue
        n += 1
        if bool(r.get("paper_took")) and not bool(r.get("live_would_take")):
            paper_only_n += 1
            reason = r.get("skip_reason")
            if reason is None:
                reason = "unknown"
            by_skip_reason[reason] = by_skip_reason.get(reason, 0) + 1
    return {"n": n, "paper_only_n": paper_only_n, "by_skip_reason": by_skip_reason}
