"""Shared size-cap rotation for append-only JSONL logs (2026-05-27 audit).

The shadow/event recorders (signal_events, uptrend_shadow, filter_shadow_log,
pre_gate_events, ...) append forever with no rotation; combined they grow
~50-350 MB/day on the Railway volume (a contributor to the 80% incident).

cap_jsonl bounds a file by dropping the OLDEST half once it exceeds max_mb.
Atomic (temp + os.replace) and never raises — a housekeeping failure must not
break the recorder. Cheap on the common path (one stat()); the readlines/rewrite
only runs when the cap is actually crossed (rare).
"""
from __future__ import annotations
import os
from pathlib import Path


def cap_jsonl(path, max_mb: float = 200.0) -> bool:
    """Trim ``path`` to its most-recent half if it exceeds max_mb. Returns True
    if it rotated."""
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size <= max_mb * 1024 * 1024:
            return False
        with p.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        keep = lines[len(lines) // 2:]
        tmp = p.with_name(p.name + ".rot.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, p)
        return True
    except Exception:
        return False
