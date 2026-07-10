# core/post_exit_tracker.py
"""Solana post-exit tail tracker (2026-07-10) — telemetry, mirrors the RH
paper lane's POSTEXIT_* pattern (scripts/rh_paper_lane.py::_check_postexit).

The trail-width analysis needs monster-frequency measured CONTINUOUSLY: how
often does a token keep running after our full exit? On every FULL position
close the scanner queues a durable pending row (DATA_DIR/post_exit_pending.jsonl,
due at close + 6h); a ~10-min async sweep (feeds/dip_scanner.py::
_post_exit_sweep_loop) then does ONE price check per due row (batched) and
writes post6h_price / post6h_vs_exit_pct / died to
DATA_DIR/post_exit_results.jsonl. Unpriceable at +6h = died (the RH rule).

This module is the PURE half: row builders, due split, and bounded JSONL file
helpers (cap ~20k lines, oldest dropped first). No network, no asyncio — the
scanner owns scheduling and price fetch. Everything here is fail-soft by
design; callers wrap in try/except with a debug log.

Env:
  POST_EXIT_TRACK_MODE  on (default) | off  — kill switch for queue + sweep.
  DATA_DIR              file root (default /data).
"""
from __future__ import annotations

import json
import os
from typing import Optional

PENDING_BASENAME = "post_exit_pending.jsonl"
RESULTS_BASENAME = "post_exit_results.jsonl"
DUE_DELAY_SECS = 6 * 3600.0     # +6h, same horizon as the RH lane
SWEEP_SECS = 600.0              # sweep cadence (~10 min)
MAX_LINES = 20000               # bounded files: keep the NEWEST 20k rows


def track_mode_on() -> bool:
    """POST_EXIT_TRACK_MODE gate — default on; off/0/false/no disables."""
    return os.environ.get("POST_EXIT_TRACK_MODE", "on").strip().lower() not in (
        "off", "0", "false", "no")


def pending_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), PENDING_BASENAME)


def results_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), RESULTS_BASENAME)


def queue_row(bot_id: str, token: str, address: str, exit_price: float,
              exit_pnl_pct: float, exit_kind: str, close_ts: float,
              due_delay_secs: float = DUE_DELAY_SECS) -> dict:
    """Build one pending row for a FULL close. due_ts = close_ts + 6h."""
    return {
        "bot_id": str(bot_id or ""),
        "token": str(token or ""),
        "address": str(address or ""),
        "exit_price": float(exit_price or 0.0),
        "exit_pnl_pct": round(float(exit_pnl_pct or 0.0), 4),
        "exit_kind": str(exit_kind or ""),
        "close_ts": float(close_ts or 0.0),
        "due_ts": float(close_ts or 0.0) + float(due_delay_secs),
    }


def due_rows(rows, now: float):
    """Split pending rows into (due, keep). Malformed due_ts counts as due
    (never let a garbage row pin the pending file forever)."""
    due, keep = [], []
    for r in rows or []:
        try:
            is_due = float(now) >= float(r.get("due_ts") or 0.0)
        except (TypeError, ValueError):
            is_due = True
        (due if is_due else keep).append(r)
    return due, keep


def result_row(pending: dict, post_price: Optional[float],
               checked_ts: float) -> dict:
    """Join a due pending row with its +6h price check. Unpriceable/zero at
    +6h = died (mirrors the RH lane's unquotable-at-+6h rule)."""
    try:
        px = float(post_price) if post_price is not None else 0.0
    except (TypeError, ValueError):
        px = 0.0
    try:
        ex = float(pending.get("exit_price") or 0.0)
    except (TypeError, ValueError):
        ex = 0.0
    vs = ((px - ex) / ex * 100.0) if (ex > 0 and px > 0) else None
    return {
        **pending,
        "post6h_price": px,
        "post6h_vs_exit_pct": round(vs, 2) if vs is not None else None,
        "died": px <= 0,
        "checked_ts": float(checked_ts or 0.0),
    }


def read_rows(path: str) -> list:
    """Read a JSONL file into row dicts; missing file -> []. Skips malformed
    lines (a torn write must never kill the sweep)."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def rewrite_rows(path: str, rows) -> None:
    """Atomically replace a JSONL file's contents (tmp + os.replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows or []:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def append_row(path: str, row: dict, cap: int = MAX_LINES) -> None:
    """Append one row, keeping the file bounded to the NEWEST ``cap`` lines
    (oldest dropped first). Line count is checked per append — the files are
    small (<= ~20k short lines) and appends happen at close frequency."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    try:
        with open(path, encoding="utf-8") as f:
            n = sum(1 for _ in f)
        if n > cap:
            rows = read_rows(path)
            rewrite_rows(path, rows[-cap:])
    except OSError:
        pass
