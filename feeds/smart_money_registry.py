"""Smart-money activity registry.

In-memory log of recent buys made by tracked "smart" wallets. Designed
as a publish/subscribe hub:

  - PUBLISHERS (wallet trackers) call ``register_buy()`` when a tracked
    wallet executes a buy.
  - CONSUMERS (dip_scanner, etc.) call ``recent_buys(token_addr)`` to
    check whether any smart wallet touched the candidate recently. The
    result is a list of (wallet, ts_mono, amount_sol) tuples sorted
    newest-first.

The registry is intentionally lightweight — no DB, no async I/O, just a
TTL-pruned dict of deques. Existing wallet-tracking modules (e.g.
AxiomSmartWalletTracker) can opt in by adding a single
``registry.register_buy(...)`` call inside their buy-event handler.

Performance: register_buy is O(1) amortized; recent_buys is O(k) where
k = entries for the token. Pruning runs on every register_buy call but
caps at WALLET_PRUNE_BUDGET tokens per pass to keep latency bounded.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

WALLET_HISTORY_LIMIT = 32        # per-token cap on retained buy events
WALLET_TTL_S = 24 * 3600         # entries older than this are pruned
WALLET_PRUNE_BUDGET = 50         # max tokens scanned per prune pass


class SmartMoneyRegistry:
    """Thread-safe in-memory smart-wallet activity log."""

    def __init__(self, ttl_s: int = WALLET_TTL_S,
                 per_token_cap: int = WALLET_HISTORY_LIMIT):
        self._ttl = ttl_s
        self._cap = per_token_cap
        self._buys: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._prune_cursor = 0  # round-robin prune index

    def register_buy(self, wallet: str, token_addr: str,
                     amount_sol: float, ts_mono: Optional[float] = None) -> None:
        """Record that ``wallet`` bought ``token_addr`` for ``amount_sol`` SOL.

        ``ts_mono`` defaults to ``time.monotonic()``. Pass an existing
        monotonic timestamp if the buy was observed earlier (e.g. WS
        replay).
        """
        if not wallet or not token_addr:
            return
        if ts_mono is None:
            ts_mono = time.monotonic()
        wallet = wallet.lower()
        token_addr = token_addr.lower()
        with self._lock:
            dq = self._buys.get(token_addr)
            if dq is None:
                dq = deque(maxlen=self._cap)
                self._buys[token_addr] = dq
            dq.append((wallet, ts_mono, float(amount_sol)))
        self._prune()

    def recent_buys(self, token_addr: str,
                    lookback_s: float = 180.0) -> list[tuple[str, float, float]]:
        """Return list of (wallet, ts_mono, amount_sol) within ``lookback_s``.

        Sorted newest-first. Returns empty list if no activity.
        """
        if not token_addr:
            return []
        token_addr = token_addr.lower()
        now = time.monotonic()
        cutoff = now - lookback_s
        with self._lock:
            dq = self._buys.get(token_addr)
            if not dq:
                return []
            out = [(w, t, a) for (w, t, a) in dq if t >= cutoff]
        out.sort(key=lambda x: -x[1])
        return out

    def smart_money_features(self, token_addr: str,
                             lookback_s: float = 300.0) -> dict:
        """Return entry_meta-ready features summarizing recent smart-wallet
        activity on this token.

        Fields:
          smart_buys_5m_count        — distinct wallets in lookback
          smart_buys_5m_total_sol    — sum of SOL across those buys
          smart_buys_5m_seconds_ago  — most recent buy (None if none)
        """
        events = self.recent_buys(token_addr, lookback_s=lookback_s)
        if not events:
            return {
                "smart_buys_5m_count": 0,
                "smart_buys_5m_total_sol": 0.0,
                "smart_buys_5m_seconds_ago": None,
            }
        unique_wallets = {w for (w, _t, _a) in events}
        total_sol = sum(a for (_w, _t, a) in events)
        now = time.monotonic()
        latest = max(t for (_w, t, _a) in events)
        return {
            "smart_buys_5m_count": len(unique_wallets),
            "smart_buys_5m_total_sol": round(total_sol, 4),
            "smart_buys_5m_seconds_ago": round(now - latest, 1),
        }

    def _prune(self) -> None:
        """Drop expired entries and empty deques. Bounded work per call."""
        now = time.monotonic()
        cutoff = now - self._ttl
        with self._lock:
            keys = list(self._buys.keys())
            if not keys:
                return
            n = len(keys)
            for _ in range(min(WALLET_PRUNE_BUDGET, n)):
                self._prune_cursor = (self._prune_cursor + 1) % n
                k = keys[self._prune_cursor]
                dq = self._buys.get(k)
                if not dq:
                    self._buys.pop(k, None)
                    continue
                while dq and dq[0][1] < cutoff:
                    dq.popleft()
                if not dq:
                    self._buys.pop(k, None)

    def stats(self) -> dict:
        """Diagnostic snapshot."""
        with self._lock:
            tokens = len(self._buys)
            events = sum(len(dq) for dq in self._buys.values())
        return {"tokens": tokens, "events": events}


_default: Optional[SmartMoneyRegistry] = None


def get_default_registry() -> SmartMoneyRegistry:
    global _default
    if _default is None:
        _default = SmartMoneyRegistry()
    return _default
