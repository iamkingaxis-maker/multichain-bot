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

Designed to be plugged into DipScanner: instance owns one
LiquidityFlowTracker per scanner. Each scan cycle calls record() with
the candidate's current liquidity, then analyze() to read the verdict.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Any, Deque, Tuple
import time


class LiquidityFlowTracker:
    """Per-token rolling liquidity history for delta detection."""

    def __init__(self, window_secs: int = 3600):
        self._history: Dict[str, Deque[Tuple[float, float]]] = {}
        self._window_secs = window_secs

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
