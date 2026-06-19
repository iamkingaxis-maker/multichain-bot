"""SAFE telemetry-log rotator.

The Railway /data volume (5GB) fills mainly from unbounded append-only
TELEMETRY/SHADOW logs (.jsonl). This module caps those files so they can
never refill the disk.

CRITICAL SAFETY
---------------
This module operates on an EXPLICIT ALLOWLIST of telemetry basenames
(`_TELEMETRY_LOGS`). It will NEVER touch trade/position STATE files:
trades_multi.json, trades.json, bot_state/, closed_positions.csv,
*.joblib, meta_sensor_state.json, or anything not on the allowlist.

The allowlist is the hard guard: rotate_all REFUSES any file whose
basename is not in `_TELEMETRY_LOGS`, so even a mis-call cannot rotate
trade state. Rewrites are atomic (tmp + os.replace) so a crash
mid-rotate can never corrupt a file, and every function is
exception-safe (logs and returns 0 / continues rather than raising).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# --- EXPLICIT allowlist of telemetry/shadow logs eligible for rotation. ---
# ONLY these basenames may ever be trimmed. Append-only telemetry — safe.
# NEVER add a trade/position STATE file (trades_multi.json, trades.json,
# bot_state, closed_positions.csv, *.joblib, meta_sensor_state.json) here.
_TELEMETRY_LOGS = frozenset({
    "position_ticks.jsonl",
    "filter_shadow_log.jsonl",
    "uptrend_shadow.jsonl",
    "signal_events.jsonl",
    "ohlcv_sidecar.jsonl",
    "follow_signals.jsonl",
    "follow_exits.jsonl",
})

_MB = 1024 * 1024


def tail_bytes(path: str, keep_bytes: int) -> bytes:
    """Return the last `keep_bytes` of `path`, aligned to a line boundary.

    Reads up to keep_bytes from the end of the file, then advances past the
    next NEWLINE so the returned bytes never start with a partial line. If
    keep_bytes >= file size, the whole file is returned unchanged.

    Pure helper (no side effects). Returns b"" on any error.
    """
    try:
        size = os.path.getsize(path)
        if keep_bytes <= 0:
            return b""
        if keep_bytes >= size:
            with open(path, "rb") as f:
                return f.read()
        with open(path, "rb") as f:
            f.seek(size - keep_bytes)
            chunk = f.read(keep_bytes)
        # Drop the (likely partial) first line: keep from after the first \n.
        nl = chunk.find(b"\n")
        if nl == -1:
            # No newline in the tail window — the tail is one giant partial
            # line; return it whole rather than nothing (it is still a line).
            return chunk
        return chunk[nl + 1:]
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[log-rotator] tail_bytes failed for %s: %s", path, e)
        return b""


def rotate_file(path: str, max_bytes: int, keep_bytes: int) -> int:
    """Rotate a single file if it exceeds max_bytes; return bytes freed.

    If os.path.getsize(path) > max_bytes, atomically rewrite the file keeping
    only the last keep_bytes (aligned to a line boundary): write the tail to
    path + '.tmp', then os.replace(tmp, path). Returns bytes_freed (0 if the
    file is missing, under the cap, or on any error). Never raises.
    """
    tmp = path + ".tmp"
    try:
        if not os.path.exists(path):
            return 0
        size_before = os.path.getsize(path)
        if size_before <= max_bytes:
            return 0
        tail = tail_bytes(path, keep_bytes)
        with open(tmp, "wb") as f:
            f.write(tail)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX and Windows
        size_after = os.path.getsize(path)
        freed = max(0, size_before - size_after)
        logger.info(
            "[log-rotator] rotated %s: %d -> %d bytes (freed %d)",
            os.path.basename(path), size_before, size_after, freed,
        )
        return freed
    except Exception as e:
        logger.warning("[log-rotator] rotate_file failed for %s: %s", path, e)
        # Best-effort cleanup of a stray tmp; never raise.
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return 0


def rotate_all(data_dir: str, max_mb: float, keep_mb: float) -> dict:
    """Rotate every allowlisted telemetry log under data_dir.

    Returns {basename: bytes_freed} for files that were actually rotated.
    Hard guard: only basenames in `_TELEMETRY_LOGS` are ever touched
    (defense-in-depth — never operates on a path outside the allowlist).
    """
    max_bytes = int(max_mb * _MB)
    keep_bytes = int(keep_mb * _MB)
    freed: dict = {}
    try:
        for name in _TELEMETRY_LOGS:
            # Hard allowlist guard: refuse anything not explicitly listed.
            if name not in _TELEMETRY_LOGS:  # pragma: no cover - invariant
                continue
            path = os.path.join(data_dir, name)
            # Ensure the resolved basename still matches the allowlist entry
            # (belt-and-suspenders against path trickery).
            if os.path.basename(path) not in _TELEMETRY_LOGS:
                continue
            if not os.path.exists(path):
                continue
            n = rotate_file(path, max_bytes, keep_bytes)
            if n > 0:
                freed[name] = n
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[log-rotator] rotate_all failed in %s: %s", data_dir, e)
    return freed


@dataclass
class RotatorConfig:
    mode: str = "on"          # 'off' disables the rotator entirely
    max_mb: float = 80.0      # rotate a file once it grows past this
    keep_mb: float = 40.0     # bytes retained (most recent) after rotation
    interval_secs: int = 3600

    @classmethod
    def from_env(cls) -> "RotatorConfig":
        def _f(key, default):
            try:
                return float(os.environ.get(key, default))
            except (TypeError, ValueError):
                return float(default)

        def _i(key, default):
            try:
                return int(float(os.environ.get(key, default)))
            except (TypeError, ValueError):
                return int(default)

        mode = os.environ.get("LOG_ROTATE_MODE", "on").strip().lower() or "on"
        return cls(
            mode=mode,
            max_mb=_f("LOG_ROTATE_MAX_MB", 80),
            keep_mb=_f("LOG_ROTATE_KEEP_MB", 40),
            interval_secs=_i("LOG_ROTATE_INTERVAL_SECS", 3600),
        )


class LogRotator:
    """Async periodic rotator. No-op when mode=off; never crashes the process."""

    def __init__(self, config: RotatorConfig | None = None):
        self.config = config or RotatorConfig.from_env()

    async def run(self, data_dir: str) -> None:
        import asyncio

        cfg = self.config
        if cfg.mode == "off":
            logger.info("[log-rotator] mode=off — rotator disabled")
            return
        logger.info(
            "[log-rotator] started: data_dir=%s max=%sMB keep=%sMB every %ss",
            data_dir, cfg.max_mb, cfg.keep_mb, cfg.interval_secs,
        )
        while True:
            try:
                freed = rotate_all(data_dir, cfg.max_mb, cfg.keep_mb)
                total = sum(freed.values())
                if total > 0:
                    logger.info(
                        "[log-rotator] freed %.1f MB across %d files",
                        total / _MB, len(freed),
                    )
            except Exception as e:
                logger.warning("[log-rotator] cycle error: %s", e)
            await asyncio.sleep(max(1, cfg.interval_secs))


async def run(data_dir: str, config: RotatorConfig | None = None) -> None:
    """Module-level convenience wrapper around LogRotator.run."""
    await LogRotator(config).run(data_dir)
