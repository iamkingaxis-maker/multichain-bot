# core/rpc_budget.py
"""Shared-RPC-quota circuit breaker (2026-07-10 mogdog postmortem).

The free public Solana RPCs rate-limit per IP, and EVERY consumer on the box
shares that budget: bulk background chatter (decimals prewarm, reconciliation,
snapshots) competes with the calls that actually bank money (sell-path balance
reads, tx sends). The morning's prewarm storm burned the whole quota and the
mogdog trail exit's getTokenAccountsByOwner then 429'd across the entire URL
rotation — a profitable exit blocked by background noise.

This module gives the quota PRIORITY TIERS:
  - Any consumer reports 429s here (report_429, one line in _post_rpc).
  - BACKGROUND consumers ask background_allowed() before spending quota and
    skip their work while the breaker is tripped. Skipping is always safe for
    them by construction (prewarm = an optimization; reconciliation = retried
    on its own schedule).
  - CRITICAL consumers (sell path, tx send) never ask — they always proceed,
    and while backgrounds are paused the drained quota is theirs.

Trip rule: >= trip_429s 429s inside window_secs. Recovery: no 429 reported
for cooldown_secs. Pure logic, injectable clock, no imports beyond stdlib.
"""
import logging
import os
import time
from collections import deque

logger = logging.getLogger(__name__)


class RpcBudget:
    def __init__(self, window_secs: float = 30.0, trip_429s: int = 8,
                 cooldown_secs: float = 60.0):
        self.window_secs = float(window_secs)
        self.trip_429s = int(trip_429s)
        self.cooldown_secs = float(cooldown_secs)
        self._events = deque(maxlen=256)   # recent 429 timestamps
        self._tripped_at = None
        self._last_429 = None

    def report_429(self, now: float = None) -> None:
        now = time.time() if now is None else now
        self._events.append(now)
        self._last_429 = now
        if self._tripped_at is None and self._count_in_window(now) >= self.trip_429s:
            self._tripped_at = now
            logger.warning(
                "[rpc-budget] TRIPPED: %d+ RPC 429s in %.0fs — background RPC "
                "consumers paused so critical calls (sells/sends) get the quota",
                self.trip_429s, self.window_secs)

    def _count_in_window(self, now: float) -> int:
        cutoff = now - self.window_secs
        return sum(1 for t in self._events if t >= cutoff)

    def tripped(self, now: float = None) -> bool:
        now = time.time() if now is None else now
        if self._tripped_at is None:
            return False
        if self._last_429 is not None and (now - self._last_429) >= self.cooldown_secs:
            logger.info("[rpc-budget] recovered: no 429 for %.0fs — background "
                        "RPC consumers resumed", self.cooldown_secs)
            self._tripped_at = None
            self._events.clear()
            return False
        return True

    def background_allowed(self, now: float = None) -> bool:
        """Gate for OPTIONAL RPC consumers (prewarm, snapshots, resolvers)."""
        if os.environ.get("RPC_BUDGET_BREAKER", "on").strip().lower() in (
                "off", "0", "false"):
            return True
        return not self.tripped(now)


GLOBAL = RpcBudget()
