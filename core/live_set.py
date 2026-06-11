"""WALK-FORWARD LIVE SET, server-side (2026-06-11, AxiS: offense dial).

The goal meter's headline is the walk-forward live set — bots already
net-positive over the trailing 7d with >=3 closes BEFORE today started.
This module computes the SAME set on-bot (single source: goal_tracker's
build_daily/live_set_for_day) so sizing can treat qualified bots
differently: they earned the regime dial's OFFENSE (1.5x on good days);
everyone else stays defense-only probes.

Cached 30min; fail-soft to empty set (= defense-only, prior behavior).
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class LiveSetTracker:
    def __init__(self):
        self._tracker = None
        self._store = None
        self._cached: set = set()
        self._cached_at = 0.0

    def set_sources(self, tracker, trade_store):
        self._tracker = tracker
        self._store = trade_store

    def members(self) -> set:
        now = time.monotonic()
        if now - self._cached_at < 1800 and self._cached_at > 0:
            return self._cached
        try:
            from scripts.goal_tracker import build_daily, live_set_for_day
            trades = []
            if self._tracker is not None:
                try:
                    trades = list(self._tracker.get_all_trades())
                except Exception:
                    pass
            if self._store is not None:
                try:
                    trades = trades + self._store.load_trades()
                except Exception:
                    pass
            if not trades:
                return self._cached
            daily = build_daily(trades)
            today_ct = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")
            self._cached = live_set_for_day(daily, today_ct)
            self._cached_at = now
            logger.info(f"[LiveSet] walk-forward live set ({today_ct}): "
                        f"{sorted(self._cached) or 'empty'}")
        except Exception as e:
            logger.warning(f"[LiveSet] compute failed (defense-only fallback): {e}")
        return self._cached


_TRACKER = LiveSetTracker()


def get_live_set() -> LiveSetTracker:
    return _TRACKER
