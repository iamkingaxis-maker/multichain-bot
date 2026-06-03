# core/ohlcv_sidecar.py
"""Post-entry price-path capture sidecar (#4.4 follow-up, 2026-06-02).

Makes the unified backtester (scripts/backtest.py) DETERMINISTIC + reproducible by
persisting the price path each position actually experienced, instead of re-fetching
candles live from GeckoTerminal (fragile, rate-limited, ages out, egress-heavy).

Zero extra fetch: the per-bot tick loop ALREADY pulls each open position's price every
cycle for exit management — we just accumulate that (secs_since_entry, price) series on
the position (capped) and persist it on close. Because it's the exact per-cycle price
series the LIVE bot ticked on (no synthetic intra-bar high/low), replaying it through
PerBotPositionManager.tick reproduces the real exit path faithfully.

Env-gated (OHLCV_CAPTURE_SIDECAR, default OFF) so it's a deliberate, bounded opt-in.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

MAX_PATH_POINTS = 400      # cap path length (bounds state_blob + sidecar storage)
MIN_GAP_SECS = 20.0        # sample at most ~1 point / 20s (tick cycle is ~30s)


def capture_enabled() -> bool:
    return os.environ.get("OHLCV_CAPTURE_SIDECAR", "0").strip().lower() in ("1", "true", "yes", "on")


def sidecar_path(data_dir: str) -> str:
    return str(Path(data_dir) / "ohlcv_sidecar.jsonl")


def accumulate_point(state_blob: dict, secs: float, price: float,
                     max_points: int = MAX_PATH_POINTS, min_gap_s: float = MIN_GAP_SECS) -> None:
    """Append (secs, price) to state_blob['ohlcv_path'], sampled + capped. Mutates in place.
    Keeps the FIRST max_points (the decision-relevant early path) once capped."""
    if state_blob is None or price is None or price <= 0:
        return
    path = state_blob.setdefault("ohlcv_path", [])
    if path and (secs - path[-1][0]) < min_gap_s:
        return
    if len(path) >= max_points:
        return
    path.append([round(float(secs), 1), float(price)])


def path_to_candles(path, entry_time_ms: int = 0):
    """[(secs, price), ...] -> [[ts_ms, o, h, l, c, v], ...] degenerate candles
    (o=h=l=c=price, v=0). This IS the per-cycle price series the live bot ticked on, so a
    backtest replay over it reproduces the live exit path (no fabricated intra-bar extremes)."""
    out = []
    for pt in path or []:
        try:
            secs, price = float(pt[0]), float(pt[1])
        except (TypeError, ValueError, IndexError):
            continue
        ts = int(entry_time_ms + secs * 1000)
        out.append([ts, price, price, price, price, 0])
    return out


def append_episode(store_path: str, record: dict) -> None:
    """Append one closed-episode record as a JSONL line. Fail-soft."""
    try:
        with open(store_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def load_episodes(store_path: str) -> list[dict]:
    p = Path(store_path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def episodes_to_backtest_dataset(episodes) -> list[dict]:
    """Convert persisted episodes -> the dataset shape scripts.backtest.backtest expects:
    [{token, entry_price, ohlcv_after}, ...]."""
    ds = []
    for e in episodes:
        ep = e.get("entry_price")
        path = e.get("path")
        if not ep or not path:
            continue
        ds.append({
            "token": e.get("address") or e.get("token"),
            "entry_price": ep,
            "ohlcv_after": path_to_candles(path, int(e.get("entry_time_ms", 0) or 0)),
        })
    return ds
