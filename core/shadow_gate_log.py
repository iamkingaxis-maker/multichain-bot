"""Structured would-block EVENT EMITTER for shadow-mode entry gates.

Several entry gates run in SHADOW (they log "would-block" but the trade STILL
EXECUTES, so its realized outcome still lands in the ledger). This module emits
ONE structured jsonl line per shadow would-block so an offline joiner
(scripts/shadow_gate_pnl.py) can join each would-block to the realized CLOSED
trade it would have prevented and measure — per gate — the winner-kill count,
the bleed avoided, and the net edge of flipping that gate to ENFORCE.

CONTRACT (hard):
  * ADDRESS-keyed — token_address is the join key. NEVER rely on symbol alone
    (symbol cross-poisons same-ticker mints; see the SPCX collision lesson).
  * FAIL-OPEN / NEVER RAISES into a trading path — every write is wrapped in
    try/except and degrades to a debug log. This is pure observability; it must
    never block or alter a buy.
  * Flag-gated — SHADOW_GATE_LOG_MODE (on|off, default 'on'); 'off' = fully
    dormant (no file IO at all).

Writes append-only JSONL to DATA_DIR/shadow_gate_events.jsonl (the same DATA_DIR
+ raw-append pattern as fill_speed_forward.jsonl). The filename is on the
core/log_rotator.py allowlist so it auto-rotates and can never refill the disk.

Record shape (one line):
  {"ts": <iso>, "gate": str, "bot": str, "token_address": str, "symbol": str,
   "would_block": true, "ctx": {...}}
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LOG_BASENAME = "shadow_gate_events.jsonl"


def _enabled() -> bool:
    """on (default) | off. 'off' = fully dormant (no IO)."""
    return os.environ.get("SHADOW_GATE_LOG_MODE", "on").strip().lower() != "off"


def _log_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), LOG_BASENAME)


def log_shadow_block(gate: str, bot: str, token_address: str,
                     symbol: str, **ctx) -> None:
    """Append ONE shadow would-block record. FAIL-OPEN: any error is swallowed
    at debug level — this MUST NEVER raise into a trading path.

    ADDRESS-keyed: token_address is the join key (never symbol alone). If a site
    genuinely only has the symbol, pass token_address="" — the joiner will skip
    the (un-joinable) record rather than mis-attribute it to a same-ticker mint.
    """
    try:
        if not _enabled():
            return
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "gate": str(gate),
            "bot": str(bot),
            "token_address": str(token_address or ""),
            "symbol": str(symbol or ""),
            "would_block": True,
            "ctx": ctx,
        }
        with open(_log_path(), "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
    except Exception as e:  # pragma: no cover - defensive; never raise
        logger.debug("[shadow-gate-log] emit failed gate=%s bot=%s token=%s: %s",
                     gate, bot, token_address, e)
