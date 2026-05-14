"""Full-population signal event recorder.

Attaches as a Python logging handler. Parses DipScanner log lines into
structured per-token events and writes JSONL to {DATA_DIR}/signal_events.jsonl.

Captures THE FULL POPULATION — every token that hits a "Signal:" line, regardless
of whether it ends up bought, blocked, or simply unevaluated. Lets us mine across
buys + rejects together, which trades.db (executed buys only) cannot.

Lifecycle per token per cycle:
  1. "Signal: TOKEN ..." — initialize new event dict, store features
  2. "CHART_READER: TOKEN ..." — add chart features
  3. "OBSERVATIONAL: TOKEN cycles_seen=N" — store cycles_seen
  4. "filter_X SHADOW would-block: TOKEN ..." — append filter_X to 'shadows'
  5. "FILTER_1M_SHADOW: TOKEN ... verdict=BLOCK/PASS" — store 1m_shadow verdict
  6. "BLOCKED by filter_X: TOKEN reasons=..." — outcome=BLOCK, flush
  7. "ENTRY via X: TOKEN ..." — store triggers_fired (multiple ENTRYs can stack)
  8. "Buying TOKEN ..." — outcome=BUY, flush

If a new "Signal: TOKEN" arrives while an event for that token is still pending,
flush the previous as outcome=CONTINUED (no terminal decision reached).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Regex patterns — match what DipScanner emits
_RE_SIGNAL = re.compile(
    r"\[DipScanner\] Signal: (\S+) mcap=\$([0-9.]+)M \| "
    r"24h=([+-][0-9.]+)% 1h=([+-][0-9.]+)% 5m=([+-][0-9.]+)% "
    r"vol24h=\$([0-9.]+)k bs_h6=([0-9.]+|inf) bs_h1=([0-9.]+|inf) bs_m5=([0-9.]+|inf)"
)
_RE_CHART = re.compile(
    r"\[DipScanner\] CHART_READER: (\S+) score=([0-9.]+) verdict=(\S+) "
    r"mtf=(\S+) sr_5m_supp=(\S+) pattern_5m=(\S+)"
)
_RE_OBS = re.compile(r"\[DipScanner\] OBSERVATIONAL: (\S+) cycles_seen=(\d+)")
_RE_SHADOW = re.compile(r"filter_(\S+) SHADOW would-block: (\S+) ")
_RE_1M_SHADOW = re.compile(
    r"\[DipScanner\] FILTER_1M_SHADOW: (\S+) 1m_cum3=([-+]?[0-9.]+) "
    r"1m_vol_spike=([0-9.]+) verdict=(\S+)"
)
_RE_BLOCKED = re.compile(r"\[DipScanner\] BLOCKED by filter_(\S+): (\S+) ")
# 2026-05-14: also capture Trader-level blocks (post-trigger, pre-Buying).
# These were silently killing trigger-fired signals and showing up as
# CONTINUED outcomes. Format: "[Trader] BLOCKED by filter_X: TOKEN reasons=..."
_RE_TRADER_BLOCKED = re.compile(r"\[Trader\] BLOCKED by filter_(\S+): (\S+) ")
# Volume dead-check has a different log format.
_RE_TRADER_VOL_DEAD = re.compile(r"\[Trader\] Volume dead-check: (\S+) ")
_RE_ENTRY_VIA = re.compile(r"\[DipScanner\] ENTRY via (\S+)( \(.*?\))?: (\S+) ")
_RE_BUYING = re.compile(r"Buying (\S+) — \$\d+ — (\w+):")


def _safe_float(s: str) -> float | None:
    if s == "inf":
        return float("inf")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


class SignalEventRecorder(logging.Handler):
    """Logging handler that captures the full population of signal evaluations.

    Thread-safe; can be attached to any logger. Writes to a JSONL file.
    """

    def __init__(self, output_path: str | None = None) -> None:
        super().__init__(level=logging.DEBUG)
        if output_path is None:
            data_dir = os.environ.get("DATA_DIR", ".")
            output_path = os.path.join(data_dir, "signal_events.jsonl")
        self._output_path = Path(output_path)
        # Ensure parent exists
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._records_written = 0

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            msg = record.getMessage()
        except Exception:
            return

        # Fast pre-filter — also include Trader-level block lines, which
        # silently kill trigger-fired signals between scanner and Buying.
        if (
            "[DipScanner]" not in msg
            and "Buying" not in msg
            and "[Trader] BLOCKED" not in msg
            and "[Trader] Volume dead-check" not in msg
        ):
            return

        try:
            self._process(msg, record.created)
        except Exception:
            # Never raise from a log handler
            pass

    def _process(self, msg: str, created_ts: float) -> None:
        # SIGNAL — start new event
        m = _RE_SIGNAL.search(msg)
        if m:
            tok = m.group(1)
            with self._lock:
                # Flush prior pending for same token (no terminal outcome)
                if tok in self._pending:
                    prev = self._pending.pop(tok)
                    prev.setdefault("outcome", "CONTINUED")
                    self._write(prev)
                self._pending[tok] = {
                    "ts": datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat(),
                    "token": tok,
                    "mcap_m": _safe_float(m.group(2)),
                    "pc_h24": _safe_float(m.group(3)),
                    "pc_h1": _safe_float(m.group(4)),
                    "pc_m5": _safe_float(m.group(5)),
                    "vol24h_k": _safe_float(m.group(6)),
                    "bs_h6": _safe_float(m.group(7)),
                    "bs_h1": _safe_float(m.group(8)),
                    "bs_m5": _safe_float(m.group(9)),
                    "shadows": [],
                    "triggers_fired": [],
                }
            return

        # CHART_READER — extend event
        m = _RE_CHART.search(msg)
        if m:
            tok = m.group(1)
            with self._lock:
                ev = self._pending.get(tok)
                if ev is not None:
                    ev["chart_score"] = _safe_float(m.group(2))
                    ev["chart_verdict"] = m.group(3)
                    ev["mtf"] = m.group(4)
                    ev["sr_5m_supp"] = (m.group(5) == "True")
                    p5m = m.group(6)
                    ev["pattern_5m"] = None if p5m == "None" else p5m
            return

        # OBSERVATIONAL cycles_seen
        m = _RE_OBS.search(msg)
        if m:
            tok = m.group(1)
            with self._lock:
                ev = self._pending.get(tok)
                if ev is not None:
                    try:
                        ev["cycles_seen"] = int(m.group(2))
                    except ValueError:
                        pass
            return

        # FILTER_1M_SHADOW
        m = _RE_1M_SHADOW.search(msg)
        if m:
            tok = m.group(1)
            with self._lock:
                ev = self._pending.get(tok)
                if ev is not None:
                    ev["1m_cum3"] = _safe_float(m.group(2))
                    ev["1m_vol_spike"] = _safe_float(m.group(3))
                    ev["1m_shadow_verdict"] = m.group(4)
            return

        # Shadow filter
        m = _RE_SHADOW.search(msg)
        if m:
            fname, tok = m.group(1), m.group(2)
            with self._lock:
                ev = self._pending.get(tok)
                if ev is not None:
                    ev["shadows"].append(fname)
            return

        # BLOCKED (scanner) — terminal, write+clear
        m = _RE_BLOCKED.search(msg)
        if m:
            fname, tok = m.group(1), m.group(2)
            with self._lock:
                ev = self._pending.pop(tok, None)
                if ev is not None:
                    ev["outcome"] = "BLOCK"
                    ev["block_filter"] = fname
                    self._write(ev)
            return

        # BLOCKED (trader) — terminal, write+clear. Prefix block_filter with
        # "trader_" so we can tell scanner vs trader blocks apart in analysis.
        m = _RE_TRADER_BLOCKED.search(msg)
        if m:
            fname, tok = m.group(1), m.group(2)
            with self._lock:
                ev = self._pending.pop(tok, None)
                if ev is not None:
                    ev["outcome"] = "BLOCK"
                    ev["block_filter"] = f"trader_{fname}"
                    self._write(ev)
            return

        # Volume dead-check (trader) — terminal
        m = _RE_TRADER_VOL_DEAD.search(msg)
        if m:
            tok = m.group(1)
            with self._lock:
                ev = self._pending.pop(tok, None)
                if ev is not None:
                    ev["outcome"] = "BLOCK"
                    ev["block_filter"] = "trader_volume_dead"
                    self._write(ev)
            return

        # ENTRY via TRIGGER — add to triggers_fired (may stack pre-buy)
        m = _RE_ENTRY_VIA.search(msg)
        if m:
            trig, tok = m.group(1), m.group(3)
            with self._lock:
                ev = self._pending.get(tok)
                if ev is not None:
                    # Trigger source includes underscores already; split if compound
                    for t in trig.split("_") if False else [trig]:
                        if t and t not in ev["triggers_fired"]:
                            ev["triggers_fired"].append(t)
            return

        # Buying — terminal
        m = _RE_BUYING.search(msg)
        if m:
            tok, strategy = m.group(1), m.group(2)
            with self._lock:
                ev = self._pending.pop(tok, None)
                if ev is not None:
                    ev["outcome"] = "BUY"
                    ev["strategy"] = strategy
                    self._write(ev)
            return

    def _write(self, ev: dict[str, Any]) -> None:
        """Append one JSONL record. Caller holds self._lock."""
        try:
            with self._output_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, separators=(",", ":")) + "\n")
            self._records_written += 1
            # Heartbeat every 100 records — visible in logs for verification
            if self._records_written % 100 == 0:
                # Use stderr-direct to avoid recursion into our own handler
                import sys
                sys.stderr.write(
                    f"[signal_event_recorder] {self._records_written} records "
                    f"written to {self._output_path.name}\n"
                )
        except Exception:
            pass

    @property
    def records_written(self) -> int:
        return self._records_written


_RECORDER: SignalEventRecorder | None = None


def install(logger: logging.Logger | None = None) -> SignalEventRecorder:
    """Install the recorder on the given logger (or root). Returns the handler.

    Idempotent — installing twice returns the same instance.
    """
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = SignalEventRecorder()
    target = logger if logger is not None else logging.getLogger()
    # Don't double-attach
    if _RECORDER not in target.handlers:
        target.addHandler(_RECORDER)
    return _RECORDER
