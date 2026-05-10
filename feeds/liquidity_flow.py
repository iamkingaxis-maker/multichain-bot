"""
Liquidity flow event tracker (stateful).

Memecoin-specific. On $1M-$100M FDV tokens, sudden liquidity additions
or removals are MASSIVE signals. LP additions = team support
(developer/community adding to the pool). LP withdrawals = either
profit-taking by LPs or a precursor to a soft-rug.

Stateless features (mcap, peak, etc.) miss this. We need to track
liquidity_usd per token over time and compute deltas.

State: { token_address: deque of (wall_ts, liquidity_usd) }

Each call to record() appends the current snapshot and prunes entries
older than the window (default 1h).

Each call to analyze() computes deltas at 5min, 15min, 60min horizons
and classifies into events:
  ADD_5MIN     liquidity rose >= 10% in last 5min
  ADD_15MIN    liquidity rose >= 15% in last 15min
  REMOVE_5MIN  liquidity dropped >= 10% in last 5min  (rug warning)
  REMOVE_15MIN liquidity dropped >= 15% in last 15min
  STABLE       no significant movement

Returns the most-significant event (largest absolute delta).

Persistence (added 2026-05-10): history is loaded from
$DATA_DIR/lp_flow_history.json on construction and saved atomically
every N record() calls (debounced to avoid disk thrash). Bridges bot
restarts so Gate E (clean_break + lp_delta_15m_pct) doesn't lose its
input feature on tokens it had already seen pre-restart.

Designed to be plugged into DipScanner: instance owns one
LiquidityFlowTracker per scanner. Each scan cycle calls record() with
the candidate's current liquidity, then analyze() to read the verdict.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Any, Deque, Tuple
import json
import logging
import os
import time

logger = logging.getLogger(__name__)


def _history_path() -> str:
    """Resolve persistence path. Uses $DATA_DIR (Railway volume) or cwd fallback."""
    data_dir = os.environ.get("DATA_DIR", "/data")
    if not os.path.isdir(data_dir):
        data_dir = "."
    return os.path.join(data_dir, "lp_flow_history.json")


class LiquidityFlowTracker:
    """Per-token rolling liquidity history for delta detection."""

    # Save to disk at most once every N seconds — debounced to avoid disk
    # thrash on every scan cycle. Tradeoff: up to N seconds of state can be
    # lost on crash. 30s is well under the 5/15-min delta windows that matter.
    _SAVE_INTERVAL_SECS = 30.0

    def __init__(self, window_secs: int = 3600, persist: bool = True):
        self._history: Dict[str, Deque[Tuple[float, float]]] = {}
        self._window_secs = window_secs
        self._persist = persist
        self._last_save_ts: float = 0.0
        if persist:
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        path = _history_path()
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            now = time.time()
            cutoff = now - self._window_secs
            loaded = 0
            for key, samples in data.items():
                if not isinstance(samples, list):
                    continue
                # Each sample is [ts, liquidity_usd]
                fresh = [(float(t), float(l)) for t, l in samples
                         if isinstance(t, (int, float)) and isinstance(l, (int, float))
                         and t >= cutoff and l > 0]
                if fresh:
                    self._history[key] = deque(fresh)
                    loaded += len(fresh)
            logger.info(
                f"[LiquidityFlow] Loaded {loaded} samples across "
                f"{len(self._history)} tokens from {path}"
            )
        except Exception as e:
            logger.warning(f"[LiquidityFlow] Failed to load history: {e}")

    def _save_to_disk(self) -> None:
        path = _history_path()
        out_dir = os.path.dirname(path) or "."
        try:
            os.makedirs(out_dir, exist_ok=True)
            # Convert deques to lists for JSON
            data = {key: list(hist) for key, hist in self._history.items()}
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning(f"[LiquidityFlow] Failed to save history: {e}")

    def record(self, token_address: str, liquidity_usd: float, *, ts: float | None = None) -> None:
        """Append a (ts, liquidity) sample for this token."""
        if not token_address or liquidity_usd is None or liquidity_usd <= 0:
            return
        key = token_address.lower()
        if ts is None:
            ts = time.time()
        hist = self._history.setdefault(key, deque())
        hist.append((float(ts), float(liquidity_usd)))
        # Prune
        cutoff = ts - self._window_secs
        while hist and hist[0][0] < cutoff:
            hist.popleft()
        # Debounced disk save — at most once per _SAVE_INTERVAL_SECS
        if self._persist and (ts - self._last_save_ts) >= self._SAVE_INTERVAL_SECS:
            self._save_to_disk()
            self._last_save_ts = ts

    def _value_at_age(self, hist: Deque[Tuple[float, float]], target_age_secs: float, now: float) -> float | None:
        """Return the recorded liquidity that's >= target_age_secs old, or None."""
        target_ts = now - target_age_secs
        # Find oldest entry that's still >= target age
        chosen: float | None = None
        for ts, liq in hist:
            if ts <= target_ts:
                chosen = liq
            else:
                break
        return chosen

    def analyze(self, token_address: str, current_liquidity_usd: float | None = None) -> Dict[str, Any]:
        """Return delta features + event verdict for a token."""
        blank = {
            "lp_event_verdict": "STABLE",
            "lp_delta_5m_pct": None,
            "lp_delta_15m_pct": None,
            "lp_delta_60m_pct": None,
            "lp_history_samples": 0,
        }
        if not token_address:
            return blank
        key = token_address.lower()
        hist = self._history.get(key)
        if not hist or len(hist) == 0:
            return blank

        now = time.time()
        cur = current_liquidity_usd
        if cur is None:
            # Use most recent recorded sample
            cur = hist[-1][1]
        if cur is None or cur <= 0:
            return {**blank, "lp_history_samples": len(hist)}

        def pct_delta(old: float | None) -> float | None:
            if old is None or old <= 0:
                return None
            return round(((cur - old) / old) * 100.0, 3)

        v_5m = self._value_at_age(hist, 300, now)
        v_15m = self._value_at_age(hist, 900, now)
        v_60m = self._value_at_age(hist, 3600, now)

        d_5m = pct_delta(v_5m)
        d_15m = pct_delta(v_15m)
        d_60m = pct_delta(v_60m)

        # Verdict — pick the most significant signed event
        verdict = "STABLE"
        if d_5m is not None and abs(d_5m) >= 10.0:
            verdict = "ADD_5MIN" if d_5m > 0 else "REMOVE_5MIN"
        elif d_15m is not None and abs(d_15m) >= 15.0:
            verdict = "ADD_15MIN" if d_15m > 0 else "REMOVE_15MIN"
        elif d_60m is not None and abs(d_60m) >= 25.0:
            verdict = "ADD_60MIN" if d_60m > 0 else "REMOVE_60MIN"

        return {
            "lp_event_verdict": verdict,
            "lp_delta_5m_pct": d_5m,
            "lp_delta_15m_pct": d_15m,
            "lp_delta_60m_pct": d_60m,
            "lp_history_samples": len(hist),
        }
