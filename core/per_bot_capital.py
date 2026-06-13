from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

# Central Time tzinfo — the daily-loss floor (and per-day re-entry cap) MUST
# roll on the SAME boundary the rest of the system uses (goal_tracker,
# day_quality_dial, badday_scorecard all bucket by CT). Bucketing by UTC rolled
# the floor at 00:00 UTC = 7pm CT — mid-evening — so wins before 7pm CT didn't
# offset losses after, and a CT-evening session split across two floor-days.
# 2026-06-13: the chameleon's +$42 of wins (UTC Jun-12) were dropped from its
# floor budget while only its -$82 of losses (UTC Jun-13) counted, locking it
# out of its whole experiment. zoneinfo is DST-correct year-round; the fixed
# UTC-5 fallback matches the codebase convention and is correct for CDT.
try:
    from zoneinfo import ZoneInfo
    _CT = ZoneInfo("America/Chicago")
except Exception:
    _CT = None


def _ct_date_iso(now_iso: Optional[str] = None) -> str:
    """Return the YYYY-MM-DD CT calendar date of a timestamp (now if None)."""
    if now_iso is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    if _CT is not None:
        return dt.astimezone(_CT).date().isoformat()
    return (dt.astimezone(timezone.utc) - timedelta(hours=5)).date().isoformat()


# Backward-compat alias — the name is now a misnomer (returns CT) but kept so
# any external import keeps working; all internal callers use _ct_date_iso.
_utc_date_iso = _ct_date_iso


class PerBotCapital:
    """Paper capital tracker for one bot.

    Tracks balance, in-flight (open position cost), cumulative realized P&L,
    and daily P&L (which resets at CT 00:00 — see _ct_date_iso). NOT
    thread-safe — caller must serialize via asyncio.Lock per bot.
    """

    def __init__(self, bot_id: str, starting_balance_usd: float) -> None:
        self.bot_id = bot_id
        self.balance_usd = float(starting_balance_usd)
        self.in_flight_usd = 0.0
        self.realized_pnl_total_usd = 0.0
        self.daily_pnl_usd = 0.0
        self._daily_pnl_date = _ct_date_iso()
        # Per-bot re-baseline cutoff (2026-05-29). Set by a dashboard reset;
        # display aggregations ignore this bot's trades dated before it so a
        # reset bot reads as a clean slate. Durable across restarts.
        self.reset_after_iso = None

    def _check_daily_rollover(self, now_iso: Optional[str] = None) -> None:
        today = _ct_date_iso(now_iso)
        if today != self._daily_pnl_date:
            self.daily_pnl_usd = 0.0
            self._daily_pnl_date = today

    def reset_daily(self, now_iso: Optional[str] = None) -> None:
        """Zero today's daily-loss budget + stamp reset_after_iso so the boot
        re-derive won't re-pull pre-reset trades. For unblocking a bot whose
        floor was consumed by trades outside the experiment we want to run."""
        self.daily_pnl_usd = 0.0
        self._daily_pnl_date = _ct_date_iso(now_iso)
        self.reset_after_iso = now_iso or datetime.now(timezone.utc).isoformat()

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

    def daily_loss_breached(self, limit_usd, now_iso: Optional[str] = None) -> bool:
        """Phase-1 risk floor: True if today's REALIZED daily P&L is at/below
        -limit_usd (i.e. the bot has lost >= limit_usd today). Rolls over at CT
        00:00 first, so it auto-clears each day. limit_usd None/<=0 -> never (off).
        Realized-only (the existing daily_pnl_usd) — this gates OPENING more, not
        holding; open-position unrealized is out of scope by design."""
        self._check_daily_rollover(now_iso)
        try:
            lim = float(limit_usd)
        except (TypeError, ValueError):
            return False
        if lim <= 0:
            return False
        return self.daily_pnl_usd <= -lim

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "balance_usd": self.balance_usd,
            "in_flight_usd": self.in_flight_usd,
            "realized_pnl_total_usd": self.realized_pnl_total_usd,
            "daily_pnl_usd": self.daily_pnl_usd,
            "daily_pnl_date": self._daily_pnl_date,
            "reset_after_iso": self.reset_after_iso,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PerBotCapital":
        c = cls(bot_id=data["bot_id"], starting_balance_usd=data["balance_usd"])
        c.in_flight_usd = data["in_flight_usd"]
        c.realized_pnl_total_usd = data["realized_pnl_total_usd"]
        c.daily_pnl_usd = data["daily_pnl_usd"]
        c._daily_pnl_date = data["daily_pnl_date"]
        c.reset_after_iso = data.get("reset_after_iso")
        return c
