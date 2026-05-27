from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional


def _utc_date_iso(now_iso: Optional[str] = None) -> str:
    """Return the YYYY-MM-DD portion of a UTC ISO timestamp.

    If now_iso is None, uses the current wall clock.
    """
    if now_iso is None:
        return datetime.now(timezone.utc).date().isoformat()
    dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).date().isoformat()


class PerBotCapital:
    """Paper capital tracker for one bot.

    Tracks balance, in-flight (open position cost), cumulative realized P&L,
    and daily P&L (which resets at UTC 00:00). NOT thread-safe — caller must
    serialize via asyncio.Lock per bot.
    """

    def __init__(self, bot_id: str, starting_balance_usd: float) -> None:
        self.bot_id = bot_id
        self.balance_usd = float(starting_balance_usd)
        self.in_flight_usd = 0.0
        self.realized_pnl_total_usd = 0.0
        self.daily_pnl_usd = 0.0
        self._daily_pnl_date = _utc_date_iso()

    def _check_daily_rollover(self, now_iso: Optional[str] = None) -> None:
        today = _utc_date_iso(now_iso)
        if today != self._daily_pnl_date:
            self.daily_pnl_usd = 0.0
            self._daily_pnl_date = today

    def reserve_for_buy(self, size_usd: float, now_iso: Optional[str] = None) -> None:
        self._check_daily_rollover(now_iso)
        if size_usd > self.balance_usd:
            raise ValueError(
                f"bot={self.bot_id} insufficient capital: "
                f"requested={size_usd} balance={self.balance_usd}"
            )
        self.balance_usd -= size_usd
        self.in_flight_usd += size_usd

    def realize_sell(
        self,
        cost_usd: float,
        proceeds_usd: float,
        now_iso: Optional[str] = None,
    ) -> None:
        self._check_daily_rollover(now_iso)
        pnl = proceeds_usd - cost_usd
        # Clamp at 0: float drift over many partial sells (1/3 + 1/3 + 1/3 != 1.0)
        # can push in_flight slightly negative, silently breaking the
        # balance+in_flight-realized==capital invariant. 2026-05-27 audit.
        self.in_flight_usd = max(0.0, self.in_flight_usd - cost_usd)
        self.balance_usd += proceeds_usd
        self.realized_pnl_total_usd += pnl
        self.daily_pnl_usd += pnl

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "balance_usd": self.balance_usd,
            "in_flight_usd": self.in_flight_usd,
            "realized_pnl_total_usd": self.realized_pnl_total_usd,
            "daily_pnl_usd": self.daily_pnl_usd,
            "daily_pnl_date": self._daily_pnl_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PerBotCapital":
        c = cls(bot_id=data["bot_id"], starting_balance_usd=data["balance_usd"])
        c.in_flight_usd = data["in_flight_usd"]
        c.realized_pnl_total_usd = data["realized_pnl_total_usd"]
        c.daily_pnl_usd = data["daily_pnl_usd"]
        c._daily_pnl_date = data["daily_pnl_date"]
        return c
