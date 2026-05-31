from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional, Literal
from core.bot_config import BotConfig


def paper_uncapped() -> bool:
    """Paper-mode data accelerator (2026-05-31): when on, the per-bot
    max_concurrent_positions cap is dropped so each bot takes EVERY qualifying
    signal (bounded only by its paper capital), accumulating per-trade EV data
    far faster — the binding constraint on confirming any edge at n>=50.

    Per-trade EV is cap-invariant (a trade's outcome doesn't depend on how many
    OTHER positions the bot holds), so the uncapped data transfers directly to
    the capped PRODUCTION config — the cap is a live-risk control, re-applied at
    production time, not a research need.

    HARD-GATED to paper: requires PAPER_UNCAPPED=1 AND PAPER_MODE on, so it can
    NEVER uncap a live bot. Capital (reserve_for_buy) and the duplicate-token
    guard still apply. Reversible by unsetting PAPER_UNCAPPED.
    """
    return (
        os.environ.get("PAPER_UNCAPPED", "0").strip().lower() in ("1", "true", "yes", "on")
        and os.environ.get("PAPER_MODE", "").strip().lower() in ("1", "true", "yes")
    )


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
        if (not bypass_max_concurrent and not paper_uncapped()
                and self.open_count >= self.config.max_concurrent_positions):
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

    def to_state_list(self) -> list[dict]:
        """Serialize the open-position book for durable persistence. Carries the
        full lifecycle state (tp1_hit/tp2_hit/peak/remaining_fraction) so a restart
        resumes management exactly — the old trades-reconstruction lost these and
        orphaned post-TP1 positions."""
        return [
            {
                "token": p.token, "entry_price": p.entry_price,
                "size_usd": p.size_usd, "entry_time": p.entry_time,
                "address": p.address, "pair_address": p.pair_address,
                "tp1_hit": p.tp1_hit, "tp2_hit": p.tp2_hit,
                "peak_pnl_pct": p.peak_pnl_pct, "peak_pnl_at_secs": p.peak_pnl_at_secs,
                "remaining_fraction": p.remaining_fraction,
                # state_blob carries slip_pct stashed at buy — without it a restored
                # position sells with the WRONG slippage fallback (fleet P&L error
                # after every deploy). 2026-05-27 audit.
                "state_blob": p.state_blob,
            }
            for p in self._positions.values()
        ]

    def load_state_list(self, items) -> int:
        """Replace the book from a persisted snapshot (lossless restore). Returns
        the number of positions loaded. Skips malformed entries (incl. entry_price<=0,
        which would make pnl_pct=inf and the stop never fire)."""
        self._positions = {}
        for it in items or []:
            tok = it.get("token")
            ep = float(it.get("entry_price") or 0.0)
            if not tok or tok in self._positions or ep <= 0:
                continue
            self._positions[tok] = OpenPosition(
                token=tok,
                entry_price=ep,
                size_usd=float(it.get("size_usd") or 0.0),
                entry_time=float(it.get("entry_time") or 0.0),
                address=it.get("address", "") or "",
                pair_address=it.get("pair_address", "") or "",
                tp1_hit=bool(it.get("tp1_hit", False)),
                tp2_hit=bool(it.get("tp2_hit", False)),
                peak_pnl_pct=float(it.get("peak_pnl_pct") or 0.0),
                peak_pnl_at_secs=int(it.get("peak_pnl_at_secs") or 0),
                remaining_fraction=float(it.get("remaining_fraction", 1.0) or 1.0),
                state_blob=dict(it.get("state_blob") or {}),
            )
        return len(self._positions)

    def last_close_times_dict(self) -> dict:
        """The reentry-cooldown map for persistence (else reentry_cooldown_secs is
        dead after every restart — 2026-05-27 audit)."""
        return dict(self._last_close_time)

    def load_last_close_times(self, d) -> None:
        if isinstance(d, dict):
            self._last_close_time = {k: float(v) for k, v in d.items()
                                     if isinstance(v, (int, float))}

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
        if p.entry_price <= 0 or exit_price <= 0:
            # Corrupted/glitch price — refuse to book a garbage (inf) realized P&L.
            raise ValueError(
                f"bot={self.config.bot_id} {token}: bad price entry={p.entry_price} exit={exit_price}"
            )
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
        # Guard bad prices: entry<=0 (corrupted state) makes pnl_pct=inf → all TPs
        # fire at once AND the stop never fires (inf <= -15 is False). current<=0 is
        # a dead/glitch tick. Skip this tick rather than book garbage. 2026-05-27 audit.
        if p.entry_price <= 0 or current_price <= 0:
            return []

        pnl_pct = round((current_price / p.entry_price - 1.0) * 100.0, 10)
        if pnl_pct > p.peak_pnl_pct:
            p.peak_pnl_pct = pnl_pct
            p.peak_pnl_at_secs = int(now - p.entry_time)

        # Give-back SHADOW (measure-only, 2026-05-31) — records whether a
        # position that went solidly GREEN (peak>=+3%) ever fell back to
        # breakeven (pnl<=0%) while still PRE-TP1. This is the give-back loser
        # cohort (scripts/giveback_analysis.py: 245 losers, 21% of all losses,
        # peaked +4.77% avg then reversed to -3.89% via slow_bleed/hard_stop —
        # the trail only arms post-TP1, leaving +3-5% peakers defenseless).
        # Captured (not acted on) so a future winner-kill audit can decide
        # whether a fast peak-aware breakeven RESCUE (exit at BE once peak>=3 AND
        # pnl<=0, pre-TP1) is safe to enforce: winners that hit this = the kill
        # cost, losers that hit it = the rescue benefit. NO ExitDecision here.
        # Fires once; the flag persists in state_blob (round-trips via
        # to/from_state_list). Stamped onto the sell record in _execute_bot_sell.
        if (
            not p.tp1_hit
            and not (p.state_blob or {}).get("gb_shadow_fired")
            and p.peak_pnl_pct >= 3.0
            and pnl_pct <= 0.0
        ):
            p.state_blob["gb_shadow_fired"] = True
            p.state_blob["gb_shadow_pnl_at_fire"] = round(pnl_pct, 4)
            p.state_blob["gb_shadow_peak_at_fire"] = round(p.peak_pnl_pct, 4)
            p.state_blob["gb_shadow_secs_at_fire"] = int(now - p.entry_time)

        # Never-green fast-stop SHADOW (measure-only, 2026-05-31) — the PRIMARY
        # avg-loss lever. Fires when a position that NEVER peaked >=2% ("never
        # showed strength" — a near-pure dying signal: only 4% of WINNERS ever
        # peak <2% vs 71% of LOSERS) is now <=-4%. These never-green losers bleed
        # to -8.27% avg over ~61min via slow_bleed/hard_stop and are 78% of total
        # loss $. Cutting them at -4 is the symmetric-R:R unlock (flips fleet EV
        # to ~+0.7%/tr at the current 60% WR). The peak<2 gate is what makes it
        # SAFE vs a flat tight stop (which whipsaws green-then-dip winners): it
        # cuts ONLY positions that never went green, so winner-kill is ~4%.
        # Captured (not acted on) so the winner-kill audit confirms before
        # enforcing. NO ExitDecision. Fires once; persists in state_blob; stamped
        # onto the sell record. Complements the give-back shadow (peak>=3, 12%).
        if (
            not p.tp1_hit
            and not (p.state_blob or {}).get("ng_faststop_fired")
            and p.peak_pnl_pct < 2.0
            and pnl_pct <= -4.0
        ):
            p.state_blob["ng_faststop_fired"] = True
            p.state_blob["ng_faststop_pnl_at_fire"] = round(pnl_pct, 4)
            p.state_blob["ng_faststop_peak_at_fire"] = round(p.peak_pnl_pct, 4)
            p.state_blob["ng_faststop_secs_at_fire"] = int(now - p.entry_time)

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

        # 3c. Stall exit (recycle never-launched corpses, pre-TP1). Fires when a
        # position (a) peaked low, (b) has been held a long time, AND (c) is now
        # drifting back down off that peak — capital bleeding below the slow_bleed
        # threshold in a position that never went anywhere. Distinct from
        # slow_bleed (pure loss threshold) and flat_exit (band-based, peak-blind).
        # Disabled when stall_exit_minutes is None. See rec #3, 7h-watch
        # (reference_correlated_death_clusters_2026_05_28 follow-up).
        if (
            self.config.stall_exit_minutes is not None
            and not p.tp1_hit
            and hold_minutes >= self.config.stall_exit_minutes
            and p.peak_pnl_pct < self.config.stall_exit_peak_max
            and pnl_pct <= p.peak_pnl_pct - self.config.stall_exit_drift_pp
        ):
            decisions.append(ExitDecision(
                token=token, kind="STALL_EXIT",
                reason=(
                    f"stall_exit hold={hold_minutes:.0f}min peak={p.peak_pnl_pct:.2f}%"
                    f" pnl={pnl_pct:.2f}% (drifted >="
                    f"{self.config.stall_exit_drift_pp}pp off low peak)"
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
