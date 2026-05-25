from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal
from core.bot_config import BotConfig


@dataclass
class OpenPosition:
    token: str
    entry_price: float
    size_usd: float
    entry_time: float
    address: str = ""
    pair_address: str = ""
    tp1_hit: bool = False
    tp2_hit: bool = False
    peak_pnl_pct: float = 0.0
    peak_pnl_at_secs: int = 0
    # Fraction of the original position still held. Starts at 1.0; each
    # partial sell (TP1 sells tp1_sell_fraction, etc.) decrements it. The
    # position is removed only when this reaches ~0 (fully exited).
    remaining_fraction: float = 1.0
    state_blob: dict = field(default_factory=dict)


@dataclass
class CloseResult:
    token: str
    cost_usd: float
    proceeds_usd: float
    realized_pnl_usd: float
    pnl_pct: float
    reason: str
    hold_secs: float
    peak_pnl_pct: float
    entry_price: float = 0.0  # the position's entry price (for sell-record self-verification)
    sell_fraction: float = 1.0  # fraction of ORIGINAL position sold in THIS exit
    fully_closed: bool = True  # True if this exit emptied the position (removed from book)


@dataclass
class ExitDecision:
    token: str
    kind: Literal["TP1", "TP2", "POST_TP1_TRAIL", "HARD_STOP", "PRE_STOP_BAIL", "FLAT_EXIT"]
    reason: str
    sell_fraction: float  # 0.0 to 1.0; full exit = 1.0


class PerBotPositionManager:
    """Per-bot position state machine."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._positions: dict[str, OpenPosition] = {}
        # token -> wall-clock time of its last FULL close. Used to enforce
        # reentry_cooldown_secs (P-stack #4): re-entry already works in the
        # multi-bot path (no dedup), so this is the *throttle* for churning the
        # same token. Empty/cooldown None = immediate re-entry (default).
        self._last_close_time: dict[str, float] = {}

    @property
    def open_count(self) -> int:
        return len(self._positions)

    def open_position(self, token: str, entry_price: float, size_usd: float,
                      entry_time: float,
                      address: str = "", pair_address: str = "",
                      bypass_max_concurrent: bool = False) -> OpenPosition:
        """Open a position. Raises ValueError on duplicate token or when at
        max_concurrent (unless bypass_max_concurrent=True for restoration).

        Restoration MUST set bypass_max_concurrent=True. If pre-existing
        positions in trades.json exceed max_concurrent, silently dropping
        them creates ghost positions that lock in_flight forever (no way to
        close a position that isn't tracked). Better to temporarily exceed
        the cap on restore; the cap re-applies to NEW buys via the same
        path with bypass=False.
        """
        if not bypass_max_concurrent and self.open_count >= self.config.max_concurrent_positions:
            raise ValueError(
                f"bot={self.config.bot_id} max_concurrent reached "
                f"({self.config.max_concurrent_positions})"
            )
        if token in self._positions:
            raise ValueError(f"bot={self.config.bot_id} already holds {token}")
        p = OpenPosition(
            token=token, entry_price=entry_price,
            size_usd=size_usd, entry_time=entry_time,
            address=address, pair_address=pair_address,
        )
        self._positions[token] = p
        return p

    def get_position(self, token: str) -> Optional[OpenPosition]:
        return self._positions.get(token)

    def in_reentry_cooldown(self, token: str, now: float,
                            cooldown_secs: Optional[float]) -> bool:
        """True if ``token`` was fully closed within cooldown_secs of ``now``.
        None/0 cooldown → never in cooldown (immediate re-entry allowed)."""
        if not cooldown_secs or cooldown_secs <= 0:
            return False
        last = self._last_close_time.get(token)
        return last is not None and (now - last) < cooldown_secs

    def iter_positions(self):
        return list(self._positions.values())

    def close_position(self, token: str, exit_price: float, exit_time: float,
                       reason: str, sell_fraction: float = 1.0) -> CloseResult:
        """Sell ``sell_fraction`` of the ORIGINAL position size.

        sell_fraction < 1.0 is a partial: proceeds/cost/pnl reflect only the
        sold slice, the position stays open with its remaining_fraction
        reduced, and CloseResult.fully_closed is False. The position is
        removed (and fully_closed=True) only once remaining_fraction hits ~0
        — so a stop (sell_fraction=1.0) after a TP1 partial sells only the
        slice that's left. Default 1.0 preserves legacy full-close behavior.
        """
        if token not in self._positions:
            raise KeyError(f"bot={self.config.bot_id} no open position for {token}")
        p = self._positions[token]
        # Can't sell more than what's left; clamp to remaining.
        frac = min(max(sell_fraction, 0.0), p.remaining_fraction)
        sold_cost = p.size_usd * frac
        ratio = exit_price / p.entry_price
        proceeds = sold_cost * ratio
        pnl_usd = proceeds - sold_cost
        pnl_pct = (ratio - 1.0) * 100.0
        p.remaining_fraction -= frac
        fully_closed = p.remaining_fraction <= 1e-9
        if fully_closed:
            self._positions.pop(token)
            self._last_close_time[token] = exit_time
        return CloseResult(
            token=token, cost_usd=sold_cost, proceeds_usd=proceeds,
            realized_pnl_usd=pnl_usd, pnl_pct=pnl_pct, reason=reason,
            hold_secs=exit_time - p.entry_time, peak_pnl_pct=p.peak_pnl_pct,
            entry_price=p.entry_price, sell_fraction=frac, fully_closed=fully_closed,
        )

    def tick(self, token: str, current_price: float, now: float,
             vol_m5_usd: Optional[float] = None) -> list[ExitDecision]:
        """Evaluate exit decisions for one position at this price tick."""
        p = self._positions.get(token)
        if p is None:
            return []

        pnl_pct = round((current_price / p.entry_price - 1.0) * 100.0, 10)
        if pnl_pct > p.peak_pnl_pct:
            p.peak_pnl_pct = pnl_pct
            p.peak_pnl_at_secs = int(now - p.entry_time)

        decisions: list[ExitDecision] = []

        # 1. Hard stop (highest priority)
        if pnl_pct <= self.config.hard_stop_pct:
            decisions.append(ExitDecision(
                token=token, kind="HARD_STOP",
                reason=f"hard stop pnl={pnl_pct:.2f}% <= {self.config.hard_stop_pct}",
                sell_fraction=1.0,
            ))
            return decisions

        # 2. Pre-stop bail (volume-aware, only pre-TP1)
        if (
            not p.tp1_hit
            and vol_m5_usd is not None
            and pnl_pct <= self.config.pre_stop_bail_pnl_pct
            and vol_m5_usd <= self.config.pre_stop_bail_vol_m5_max
        ):
            decisions.append(ExitDecision(
                token=token, kind="PRE_STOP_BAIL",
                reason=(
                    f"pre-stop bail pnl={pnl_pct:.2f}% vol_m5=${vol_m5_usd:.0f}"
                    f" <= {self.config.pre_stop_bail_vol_m5_max}"
                ),
                sell_fraction=1.0,
            ))
            return decisions

        # 3. Slow bleed (held too long at a loss, pre-TP1)
        hold_minutes = (now - p.entry_time) / 60.0
        if (
            hold_minutes >= self.config.slow_bleed_minutes
            and pnl_pct <= self.config.slow_bleed_pnl_threshold
            and not p.tp1_hit
        ):
            decisions.append(ExitDecision(
                token=token, kind="HARD_STOP",
                reason=f"slow_bleed hold={hold_minutes:.0f}min pnl={pnl_pct:.2f}%",
                sell_fraction=1.0,
            ))
            return decisions

        # 3b. Velocity / flat exit (recycle dead money, pre-TP1). Fires when a
        # position held past flat_exit_minutes is going nowhere (pnl inside the
        # flat band) — frees capital for new trades. Distinct from slow_bleed
        # (loss-based). Disabled when flat_exit_minutes is None.
        if (
            self.config.flat_exit_minutes is not None
            and hold_minutes >= self.config.flat_exit_minutes
            and abs(pnl_pct) < self.config.flat_exit_band_pct
            and not p.tp1_hit
        ):
            decisions.append(ExitDecision(
                token=token, kind="FLAT_EXIT",
                reason=(
                    f"flat_exit hold={hold_minutes:.0f}min pnl={pnl_pct:.2f}%"
                    f" within +/-{self.config.flat_exit_band_pct}% (dead money)"
                ),
                sell_fraction=1.0,
            ))
            return decisions

        # 4. TP1
        if not p.tp1_hit and pnl_pct >= self.config.tp1_pct:
            p.tp1_hit = True
            decisions.append(ExitDecision(
                token=token, kind="TP1",
                reason=f"TP1 pnl={pnl_pct:.2f}% >= {self.config.tp1_pct}",
                sell_fraction=self.config.tp1_sell_fraction,
            ))

        # 5. TP2
        if p.tp1_hit and not p.tp2_hit and pnl_pct >= self.config.tp2_pct:
            p.tp2_hit = True
            decisions.append(ExitDecision(
                token=token, kind="TP2",
                reason=f"TP2 pnl={pnl_pct:.2f}% >= {self.config.tp2_pct}",
                sell_fraction=self.config.tp2_sell_fraction,
            ))

        # 6. Post-TP1 trail
        if p.tp1_hit and not decisions:
            trail_threshold = p.peak_pnl_pct - self.config.trail_pp
            if pnl_pct <= trail_threshold:
                decisions.append(ExitDecision(
                    token=token, kind="POST_TP1_TRAIL",
                    reason=(
                        f"trail pnl={pnl_pct:.2f}% <= peak({p.peak_pnl_pct:.2f}%)"
                        f" - {self.config.trail_pp}pp"
                    ),
                    sell_fraction=1.0,
                ))
        return decisions
