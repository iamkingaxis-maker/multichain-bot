"""
ScalpCapitalManager — independent $2000 capital pool for the scalp strategy.

Completely separate from RiskManager. Tracks deployed capital,
concurrent position count, and cumulative daily P&L.
"""

import datetime
import calendar
import time
from dataclasses import dataclass, field
from typing import Dict


def _next_midnight_utc() -> float:
    now = datetime.datetime.now(datetime.UTC)
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return float(calendar.timegm(tomorrow.timetuple()))


@dataclass
class ScalpCapitalManager:
    total_capital: float = 2000.0
    max_position_usd: float = 200.0
    max_concurrent: int = 10
    daily_loss_limit: float = 400.0

    _open: Dict[str, float] = field(default_factory=dict, init=False)
    _daily_pnl: float = field(default=0.0, init=False)
    _daily_loss_hit: bool = field(default=False, init=False)
    _day_reset_ts: float = field(default=0.0, init=False)

    def __post_init__(self):
        self._day_reset_ts = _next_midnight_utc()
        # Deploy-amnesia fix (2026-06-12 audit): the daily breaker reset to
        # zero at every cutover (~10/day) — a pool that blew its $400 stop
        # resumed fresh. Persist pnl+flag with a same-day guard.
        import json, os
        self._state_path = os.path.join(os.environ.get("DATA_DIR", "."),
                                        "scalp_capital.json")
        try:
            with open(self._state_path) as f:
                d = json.load(f)
            if d.get("day") == datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d"):
                self._daily_pnl = float(d.get("daily_pnl", 0.0))
                self._daily_loss_hit = bool(d.get("daily_loss_hit", False))
        except Exception:
            pass

    def _persist(self):
        import json
        try:
            with open(self._state_path, "w") as f:
                json.dump({"day": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d"),
                           "daily_pnl": round(self._daily_pnl, 4),
                           "daily_loss_hit": self._daily_loss_hit}, f)
        except Exception:
            pass

    # ── Public API ──────────────────────────────────────────────

    def has_capacity(self) -> bool:
        self._check_day_reset()
        if self._daily_loss_hit:
            return False
        return len(self._open) < self.max_concurrent

    def record_open(self, addr: str, usd: float):
        self._open[addr] = usd

    def record_close(self, addr: str, pnl_usd: float):
        self._check_day_reset()
        self._open.pop(addr, None)
        self._daily_pnl += pnl_usd
        if self._daily_pnl <= -self.daily_loss_limit:
            self._daily_loss_hit = True
        self._persist()

    def deployed_usd(self) -> float:
        return sum(self._open.values())

    def available_usd(self) -> float:
        return self.total_capital - self.deployed_usd()

    # ── Internal ────────────────────────────────────────────────

    def _check_day_reset(self):
        if time.time() >= self._day_reset_ts:
            self._daily_pnl = 0.0
            self._daily_loss_hit = False
            self._day_reset_ts = _next_midnight_utc()
