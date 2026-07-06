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


def scalein_first_fraction(config, slip_buy_2000_pct):
    """First-tranche fraction for staged entry. Thin EXECUTABLE depth at entry
    (slip_buy_2000_pct >= config.scalein_flash_slip_pct) is the orthogonal flash-crash
    signature, so deploy an even SMALLER first tranche (config.scalein_flash_first_fraction);
    the runner still completes to FULL size on confirm. Null/unquotable slip falls back to
    the default scalein_first_fraction (Jupiter quote is unavailable on ~44% of entries).
    Winner-safe — never enlarges, and confirmation still reaches full size."""
    frac = config.scalein_first_fraction
    if slip_buy_2000_pct is not None:
        try:
            if float(slip_buy_2000_pct) >= float(config.scalein_flash_slip_pct):
                frac = min(frac, float(config.scalein_flash_first_fraction))
        except (TypeError, ValueError):
            pass
    return frac


def _trajectory_shape_features(traj, entry_price):
    """Phase-2a demand-trajectory SHAPE from a first-8min path [(secs, price, vol), ...].

    Minute-binned to match the universe continuation-model corpus definition
    (scripts/backfill_bot_trades.shape: closes/lows/vols per minute) so the live
    shape is comparable to what the model was trained on (held-out-by-token AUC
    0.765). SHAPE only — no price LEVEL (which would leak). Returns None if fewer
    than 4 minute-buckets (too thin to score). Scored OFFLINE by the analyzer; this
    just produces the features.
    """
    if not traj or entry_price is None or entry_price <= 0:
        return None
    buckets: dict = {}
    for sample in traj:
        try:
            secs, price, vol = sample[0], sample[1], sample[2]
        except (IndexError, TypeError):
            continue
        if price is None or price <= 0:
            continue
        m = int(secs // 60)
        b = buckets.get(m)
        if b is None:
            buckets[m] = {"close": price, "low": price, "vol": vol}
        else:
            b["close"] = price
            if price < b["low"]:
                b["low"] = price
            if vol is not None:
                b["vol"] = vol
    post = [buckets[m] for m in sorted(buckets)]
    if len(post) < 4:
        return None
    closes = [b["close"] for b in post]
    lows = [b["low"] for b in post]
    vols = [b["vol"] for b in post if b["vol"] is not None]
    n = len(post)
    pk = max(range(n), key=lambda i: closes[i])
    third = max(1, n // 3)
    vf = sum(vols[:third]) if vols else 0
    vl = sum(vols[-third:]) if vols else 0
    return {
        "peak_position": round(pk / (n - 1), 3) if n > 1 else 0.0,
        "minutes_to_peak": pk,
        "frac_above_entry": round(sum(1 for c in closes if c > entry_price) / n, 3),
        "higher_low_n": sum(1 for i in range(1, n) if lows[i] > lows[i - 1]),
        "vol_sustain_ratio": round(vl / vf, 3) if vf > 0 else None,
        "n": n,
    }


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
    kind: Literal["TP1", "TP2", "POST_TP1_TRAIL", "HARD_STOP", "PRE_STOP_BAIL", "FLAT_EXIT", "BREAKEVEN_LOCK"]
    reason: str
    sell_fraction: float  # 0.0 to 1.0; full exit = 1.0


class PerBotPositionManager:
    """Per-bot position state machine."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._positions: dict[str, OpenPosition] = {}
        # OHLCV-capture sidecar (#4.4): accumulate the per-cycle price path on each open
        # position (zero extra fetch) when OHLCV_CAPTURE_SIDECAR is on. Cached once at init.
        try:
            from core.ohlcv_sidecar import capture_enabled as _oc_on
            self._ohlcv_capture = _oc_on()
        except Exception:
            self._ohlcv_capture = False
        # token -> wall-clock time of its last FULL close. Used to enforce
        # reentry_cooldown_secs (P-stack #4): re-entry already works in the
        # multi-bot path (no dedup), so this is the *throttle* for churning the
        # same token. Empty/cooldown None = immediate re-entry (default).
        self._last_close_time: dict[str, float] = {}
        # LOSS-STREAK PAUSE state (2026-07-06): consecutive losing FULL closes
        # (position-level — legs accumulate in _pos_realized_accum so a TP1
        # winner with a red runner leg still counts as a WIN) and the ts of
        # the last streak-extending loss. See in_loss_streak_pause().
        self._pos_realized_accum: dict[str, float] = {}
        self._loss_streak: int = 0
        self._loss_streak_ts: float = 0.0
        # Phase-1 per-token re-entry counter (2026-06-01). token -> count of buys
        # this UTC day; resets at rollover. The death-spiral was SEQUENTIAL re-buys
        # (positions are one-per-(bot,token), so this is the controllable concentration
        # lever for a solo production bot). In-memory for the shadow phase; persist
        # before enforce. See the Phase-1 risk-floor spec.
        self._token_buys: dict[str, int] = {}
        self._token_buys_date: Optional[str] = None

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
        self._record_token_buy(token)
        return p

    def scalein_ready(self, token: str, pnl_pct: float) -> bool:
        """True if ``token`` is a pending scale-in that has now CONFIRMED (pnl reached
        scalein_confirm_pct) and should have its deferred 2nd tranche deployed. Winner-
        safe trigger by construction: it can only fire once the position is GREEN past
        the confirm threshold, so faders (which never get there) stay at the first
        tranche forever. See BotConfig.scalein_enabled."""
        p = self._positions.get(token)
        if p is None:
            return False
        sb = p.state_blob or {}
        if not sb.get("scalein_pending"):
            return False
        return pnl_pct >= float(sb.get("scalein_confirm_pct", 1.0))

    def complete_scalein(self, token: str, fill_price: float, add_usd: float) -> bool:
        """Deploy the deferred 2nd tranche: buy ``add_usd`` at ``fill_price`` and blend
        it into the position's cost basis (token-weighted average, so pnl_pct stays
        correct on the combined position). Rebases peak_pnl_pct onto the new (higher)
        entry so the never-green / never-runner peak gates remain consistent. Clears the
        pending flag and stamps the outcome for phantom parity. Returns True on success;
        the caller MUST have already reserved ``add_usd`` from capital."""
        p = self._positions.get(token)
        if p is None or fill_price <= 0 or add_usd <= 0:
            return False
        sb = p.state_blob
        if not sb.get("scalein_pending"):
            return False
        old_entry = p.entry_price
        tokens1 = (p.size_usd / old_entry) if old_entry > 0 else 0.0
        tokens2 = add_usd / fill_price
        if tokens1 + tokens2 <= 0:
            return False
        new_cost = p.size_usd + add_usd
        p.entry_price = new_cost / (tokens1 + tokens2)
        p.size_usd = new_cost
        # Rebase the peak onto the new entry so peak-gated exits (ng_faststop/never_runner)
        # don't see a stale peak relative to the old, lower entry.
        if p.peak_pnl_pct > 0 and old_entry > 0 and p.entry_price > 0:
            peak_price = old_entry * (1.0 + p.peak_pnl_pct / 100.0)
            p.peak_pnl_pct = max(0.0, (peak_price / p.entry_price - 1.0) * 100.0)
        sb["scalein_pending"] = False
        sb["scalein_completed"] = True
        sb["scalein_added_usd"] = round(add_usd, 4)
        sb["scalein_fill_price"] = fill_price
        return True

    def _record_token_buy(self, token: str, now_iso: Optional[str] = None) -> None:
        from core.per_bot_capital import _ct_date_iso
        today = _ct_date_iso(now_iso)
        if today != self._token_buys_date:
            self._token_buys = {}
            self._token_buys_date = today
        self._token_buys[token] = self._token_buys.get(token, 0) + 1

    def token_buys_today(self, token: str, now_iso: Optional[str] = None) -> int:
        """Phase-1 risk floor: how many times this bot has bought ``token`` so far
        this CT day (resets at CT 00:00 rollover). Drives the per-token re-entry cap."""
        from core.per_bot_capital import _ct_date_iso
        if _ct_date_iso(now_iso) != self._token_buys_date:
            return 0
        return self._token_buys.get(token, 0)

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

    def token_buys_state(self) -> dict:
        """Per-token daily buy counts for persistence (2026-06-12 audit: the
        per-token re-entry cap reset at every deploy — 'persist before
        enforce' was overdue; enforced on live_probe)."""
        return {"date": self._token_buys_date, "counts": dict(self._token_buys)}

    def load_token_buys_state(self, d) -> None:
        if isinstance(d, dict) and isinstance(d.get("counts"), dict):
            self._token_buys_date = d.get("date")
            self._token_buys = {k: int(v) for k, v in d["counts"].items()
                                if isinstance(v, (int, float))}

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
        # LOSS-STREAK accounting: judge the WHOLE position (sum of legs) on
        # its final close, never a single leg.
        self._pos_realized_accum[token] = (
            self._pos_realized_accum.get(token, 0.0) + pnl_usd)
        if fully_closed:
            self._positions.pop(token)
            self._last_close_time[token] = exit_time
            _total = self._pos_realized_accum.pop(token, 0.0)
            if _total < 0:
                self._loss_streak += 1
                self._loss_streak_ts = exit_time
            else:
                self._loss_streak = 0
        return CloseResult(
            token=token, cost_usd=sold_cost, proceeds_usd=proceeds,
            realized_pnl_usd=pnl_usd, pnl_pct=pnl_pct, reason=reason,
            hold_secs=exit_time - p.entry_time, peak_pnl_pct=p.peak_pnl_pct,
            entry_price=p.entry_price, sell_fraction=frac, fully_closed=fully_closed,
        )

    def in_loss_streak_pause(self, now: float) -> bool:
        """True while entries are paused after loss_streak_n consecutive
        losing full closes (session-discipline decode 2026-07-06: losses
        cluster in time; fleet join +1,626pp/9d, 16/17 bots positive).
        Config-gated per bot (loss_streak_pause, default OFF — young lane
        exempt) with a global env kill LOSS_STREAK_PAUSE_MODE=off. A winning
        close resets the streak; after the pause window expires the bot
        trades again (another loss re-pauses at streak n+1)."""
        if not getattr(self.config, "loss_streak_pause", False):
            return False
        import os
        if os.environ.get("LOSS_STREAK_PAUSE_MODE", "on").strip().lower() in (
                "off", "0", "false", "no"):
            return False
        n = int(getattr(self.config, "loss_streak_n", 3) or 3)
        pause = float(getattr(self.config, "loss_streak_pause_secs", 3600.0) or 3600.0)
        if self._loss_streak < n:
            return False
        return (float(now) - self._loss_streak_ts) < pause

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
        # EXCURSION INSTRUMENTATION SHADOW (2026-06-02 fleet-mine critic, read-only).
        # peak_pnl_pct is the max-FAVORABLE excursion (MFE); track the max-ADVERSE
        # excursion (MAE) + its timing here so future stop/exit mining can separate
        # "dipped then recovered" from "bled straight down" (the gap the mine flagged:
        # no intrabar trough captured). Pure state stamp, NO behavior change; stamped
        # onto the sell record in _execute_bot_sell. Round-trips as flat floats.
        _sb_mae = p.state_blob
        if _sb_mae is not None:
            _prev = _sb_mae.get("mae_pct")
            if _prev is None or pnl_pct < _prev:
                _sb_mae["mae_pct"] = round(pnl_pct, 4)
                _sb_mae["mae_at_secs"] = int(now - p.entry_time)
        # OHLCV-capture sidecar (#4.4): accumulate the per-cycle price path (zero extra
        # fetch — this is the price the tick loop already pulled) for deterministic backtest
        # replay. Sampled + capped in accumulate_point. Persisted on close in _execute_bot_sell.
        if getattr(self, "_ohlcv_capture", False) and p.state_blob is not None:
            from core.ohlcv_sidecar import accumulate_point
            accumulate_point(p.state_blob, now - p.entry_time, current_price)

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

        # NARROW giveback SHADOW (2026-06-05 drawdown-mine LEVER 2, MEASURE-ONLY). The
        # marginal-peak band [3,5)% that peaked then bled (WLM +3.7->-15.6, LOA +3.9->-11.4):
        # these are slow bleeds, not real runners, and the trail only arms post-TP1 so they
        # ride to the stop. Candidate winner-safe exit = cut peak in [3,5) at -5. Stamp the
        # would-fire for forward parity; ACT only after a 2-week shadow confirms catch-rate.
        # (Broad giveback rescue peak>=3 is winner-UNSAFE — kills the V-recovery tail — so
        # this is deliberately narrow.) NO ExitDecision here.
        if (
            not p.tp1_hit
            and not (p.state_blob or {}).get("gb_narrow_fired")
            and 3.0 <= p.peak_pnl_pct < 5.0
            and pnl_pct <= -5.0
        ):
            p.state_blob["gb_narrow_fired"] = True
            p.state_blob["gb_narrow_pnl_at_fire"] = round(pnl_pct, 4)
            p.state_blob["gb_narrow_peak_at_fire"] = round(p.peak_pnl_pct, 4)
            p.state_blob["gb_narrow_secs_at_fire"] = int(now - p.entry_time)

        # PEAK-ANCHORED BREAKEVEN-LOCK is now a shadow+enforce gate in the decisions
        # section below (see "0b") — recording + optional ExitDecision unified there.

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
            # Finer dip-moment signal capture (2026-05-31): the flat ng_faststop
            # kills ~40% of never-greens that RECOVER (deep-dip reversals = the
            # dip-buy edge). To build a FINER stop that cuts only the dying ones,
            # snapshot the market microstructure at the -4 fire so we can mine
            # which dip-features separate recoverers (e.g. JTVO/IDLE bounced to
            # +4.6) from diers (PENGUIN/Digi). Zero extra fetch — vol_m5 is
            # already passed; the rest is position state:
            #   vol_m5_at_fire    — is there still buying interest (liveness)?
            #   drop_velocity_pp_s — fast capitulation flush vs slow death grind
            #   secs_from_peak     — how long it took to roll over
            # Stamped on the sell record; mine vs the recover/die outcome.
            p.state_blob["ng_faststop_vol_m5_at_fire"] = vol_m5_usd
            _secs_from_peak = max(int(now - p.entry_time) - p.peak_pnl_at_secs, 1)
            p.state_blob["ng_faststop_secs_from_peak"] = _secs_from_peak
            p.state_blob["ng_faststop_drop_velocity_pp_s"] = round(
                (p.peak_pnl_pct - pnl_pct) / _secs_from_peak, 5
            )

        # TP1-KNEE SHADOW (measure-only, 2026-06-02) — records when a PRE-TP1
        # position first reaches the candidate tighter-TP1 knees (+3% / +4%), so the
        # analyzer can measure on FORWARD trades what a lower TP1 (vs the live 5%)
        # would have done. Round 2 saw a +4-6pp WR lift from TP1 5->3, but round 3
        # showed it collapses to noise under the REAL ladder fractions (f1=0.75) —
        # forward data is what resolves it. NO ExitDecision; each knee fires once;
        # persists in state_blob; stamped onto the sell record in _execute_bot_sell.
        if not p.tp1_hit:
            if not (p.state_blob or {}).get("tp1_knee_3_hit") and pnl_pct >= 3.0:
                p.state_blob["tp1_knee_3_hit"] = True
                p.state_blob["tp1_knee_3_secs"] = int(now - p.entry_time)
            if not (p.state_blob or {}).get("tp1_knee_4_hit") and pnl_pct >= 4.0:
                p.state_blob["tp1_knee_4_hit"] = True
                p.state_blob["tp1_knee_4_secs"] = int(now - p.entry_time)

        # TIME-STOP SHADOW (measure-only, 2026-06-02) — records the P&L at which a
        # 45-min time-stop on a slow-bleeding PRE-TP1 position WOULD have exited (the
        # slow_bleed pnl predicate, but at 45min vs the live slow_bleed at 60min).
        # Round 3 found a +45min time-stop kills ~43% of winners (slow-grind-to-TP1) —
        # this captures the FORWARD winner-kill so the analyzer confirms/refutes before
        # any enforce. NO action; fires once; persists; stamped onto the sell record.
        if (
            not p.tp1_hit
            and not (p.state_blob or {}).get("timestop45_fired")
            and (now - p.entry_time) >= 45 * 60
            and pnl_pct <= self.config.slow_bleed_pnl_threshold
        ):
            p.state_blob["timestop45_fired"] = True
            p.state_blob["timestop45_pnl_at_fire"] = round(pnl_pct, 4)
            p.state_blob["timestop45_peak_at_fire"] = round(p.peak_pnl_pct, 4)
            p.state_blob["timestop45_secs"] = int(now - p.entry_time)

        # TRAJECTORY SHADOW (Phase-2a, measure-only, 2026-06-02) — accumulate the
        # first-8min price/vol path, and at the +8min checkpoint stamp the demand-
        # trajectory SHAPE (peak_position, minutes_to_peak, frac_above_entry,
        # higher_low_n, vol_sustain_ratio, n). The +8min SHAPE predicts continuation
        # (held-out-by-token AUC 0.765 universe; 0.607 off-GACHA on bot trades, leak-free) —
        # the ONE signal that beat the entry-prediction wall. Scored OFFLINE by the analyzer
        # (NO model in the tick loop); join scalein_* shape -> realized outcome on the sell.
        # RE-AIMED 2026-06-02: the durable lever is DE-RISK the LOW-score cohort (realizes
        # -4.12%, jackknife-stable), NOT scale-in the HIGH cohort (break-even, sign-flips).
        # Phase-2b enforces hold-small / early-exit on the LOW cohort (loss-avoidance, +$7-19
        # /day). NO behavior change here; fires once; the raw path is freed after computation.
        sb = p.state_blob
        if sb is not None and not sb.get("scalein_shape_done"):
            secs = now - p.entry_time
            if secs <= 8.5 * 60:
                traj = sb.setdefault("scalein_traj", [])
                # ~1 sample / 12s over 8min (<=~40 pts) to bound state size
                if not traj or (secs - traj[-1][0]) >= 12.0:
                    traj.append([round(secs, 1), current_price, vol_m5_usd])
            if secs >= 8 * 60:
                try:
                    feats = _trajectory_shape_features(sb.get("scalein_traj") or [], p.entry_price)
                except Exception:
                    feats = None
                if feats:
                    sb["scalein_peak_position"] = feats["peak_position"]
                    sb["scalein_minutes_to_peak"] = feats["minutes_to_peak"]
                    sb["scalein_frac_above_entry"] = feats["frac_above_entry"]
                    sb["scalein_higher_low_n"] = feats["higher_low_n"]
                    sb["scalein_vol_sustain_ratio"] = feats["vol_sustain_ratio"]
                    sb["scalein_n"] = feats["n"]
                sb["scalein_shape_done"] = True
                sb.pop("scalein_traj", None)   # free the raw path; features kept

        decisions: list[ExitDecision] = []

        # 0. In-flight loss-floor (badday gap audit 2026-06-22, 35-agent verify) —
        # the flagship loss-cut. PRE-TP1 doomed legs currently ride the -9 fast-bail
        # /-12 hard-stop down to a mean -12.3%; a -7% MAE floor exits them ~5pp
        # earlier with ZERO winner-kill (worst-winner MAE -5.85% vs nearest loser
        # -6.01% = empty 1.15pp band; 98 losers / 0 winners on the ledger). A
        # never-green FAST collapse (peak<2 AND pnl<=-4 AND drop_vel>=0.012) bails
        # at the fire point before -7. Scoped to the badday family (the audit's
        # evidence boundary; timebox excluded). ENFORCE by default;
        # IN_FLIGHT_FLOOR_MODE=shadow (stamp-only) | off. Fires BEFORE the -12 hard
        # stop (it's tighter). NOTE: live microcap stops gap THROUGH, so a -55%
        # feed-gap leg still fills deep — that residual is a separate feed-gap guard.
        _iff_mode = os.environ.get("IN_FLIGHT_FLOOR_MODE", "enforce").lower()
        if (_iff_mode != "off" and not p.tp1_hit
                and str(getattr(self.config, "bot_id", "")).startswith("badday_")):
            try:
                from core.bot_evaluator import in_flight_floor_fires as _ifff
                _iff_floor = float(os.environ.get("IN_FLIGHT_FLOOR_PCT", "-7.0"))
                _iff_pps = float(os.environ.get("IN_FLIGHT_VELBAIL_PPS", "0.012"))
                _iff_sfp = max(int(now - p.entry_time) - p.peak_pnl_at_secs, 1)
                # per-bot velocity-bail threshold override (wickride A/B): -8
                # sits below the -7 MAE floor = velocity leg off, floor kept.
                _iff_vp = getattr(self.config, "velbail_pnl_pct", None)
                _iff_fire, _iff_why = _ifff(
                    pnl_pct, p.peak_pnl_pct, _iff_sfp,
                    floor_pct=_iff_floor, velbail_pps=_iff_pps,
                    velbail_pnl=(float(_iff_vp) if isinstance(_iff_vp, (int, float))
                                 and not isinstance(_iff_vp, bool) else -4.0))
            except Exception:
                _iff_fire, _iff_why = False, ""
            if _iff_fire:
                if p.state_blob is not None and not p.state_blob.get("iff_fired"):
                    p.state_blob["iff_fired"] = True
                    p.state_blob["iff_why"] = _iff_why
                    p.state_blob["iff_mode"] = _iff_mode
                    p.state_blob["iff_pnl_at_fire"] = round(pnl_pct, 4)
                    p.state_blob["iff_peak_at_fire"] = round(p.peak_pnl_pct, 4)
                    p.state_blob["iff_secs"] = int(now - p.entry_time)
                if _iff_mode == "enforce":
                    decisions.append(ExitDecision(
                        token=token, kind="IN_FLIGHT_FLOOR",
                        reason=f"in-flight {_iff_why} (floor {_iff_floor:.0f})",
                        sell_fraction=1.0,
                    ))
                    return decisions

        # 0b. PEAK-ANCHORED BREAKEVEN-LOCK (winner-comparison 2026-06-26). A PRE-TP1 leg
        # that CONFIRMED green (peak>=+7%) then round-tripped to <=0 is a give-back loser
        # the post-TP1 trail can't protect — lock ~breakeven instead of riding to the -7
        # floor / hard stop. Validated path-aware on the give-back cohort (n=82 @ peak>=7:
        # 70 saves / 12 winner-kills = +349pp net, winner-kill 0.15; vs 0.30 at peak>=3).
        # Fires at pnl<=0 (above the -7 IN_FLIGHT_FLOOR), so on a gradual round-trip it
        # pre-empts the floor; on a gap-through the floor above already caught it.
        # BREAKEVEN_LOCK_MODE=shadow (default, record-only) | enforce | off. PAPER
        # OVER-STATES this (deep stops gap through live) -> stays SHADOW until forward/
        # live-confirmed at n>=30. Records once; persists in state_blob; stamped on sell.
        _bel_mode = os.environ.get("BREAKEVEN_LOCK_MODE", "shadow").strip().lower()
        if _bel_mode != "off" and not p.tp1_hit:
            try:
                from core.bot_evaluator import breakeven_lock_fires as _belf
                _bel_min = float(os.environ.get("BREAKEVEN_LOCK_PEAK_MIN", "7.0"))
                _bel_fire, _bel_why = _belf(p.peak_pnl_pct, pnl_pct, p.tp1_hit, peak_min=_bel_min)
            except Exception:
                _bel_fire, _bel_why = False, ""
            if _bel_fire:
                if p.state_blob is not None and not p.state_blob.get("bel_shadow_fired"):
                    p.state_blob["bel_shadow_fired"] = True
                    p.state_blob["bel_shadow_pnl_at_fire"] = round(pnl_pct, 4)
                    p.state_blob["bel_shadow_peak_at_fire"] = round(p.peak_pnl_pct, 4)
                    p.state_blob["bel_shadow_secs"] = int(now - p.entry_time)
                    p.state_blob["bel_mode"] = _bel_mode
                if _bel_mode == "enforce":
                    decisions.append(ExitDecision(
                        token=token, kind="BREAKEVEN_LOCK",
                        reason=f"{_bel_why} (lock ~breakeven, pre-TP1)",
                        sell_fraction=1.0,
                    ))
                    return decisions

        # 0c. GIVEBACK-TRAIL SHADOW (2026-07-04 giveback-lever study). The
        # recoverable slice of the 91 token-pp/day giveback pool is the
        # PRE-TP1 peak-armed trail: arm at peak>=5, fire at pnl<=peak-2,
        # stand down once TP1 fills. Fleet-wide enforce is sign-ambiguous
        # (path-unobserved wick kills could flip it); allday-scoped net was
        # +15..+61pp/7d pessimistic (thin n=12). STAMP-ONLY here — the
        # decision gate is n>=30 shadow TP1-hitter joins with kills <50% of
        # recovered under -2pp fills. GIVEBACK_TRAIL_SHADOW=off disables.
        if (os.environ.get("GIVEBACK_TRAIL_SHADOW", "on").strip().lower() != "off"
                and not p.tp1_hit):
            try:
                _gb2_arm = float(os.environ.get("GIVEBACK_TRAIL_PEAK_MIN", "5.0"))
                _gb2_x = float(os.environ.get("GIVEBACK_TRAIL_PP", "2.0"))
                if (p.peak_pnl_pct >= _gb2_arm
                        and pnl_pct <= p.peak_pnl_pct - _gb2_x
                        and p.state_blob is not None
                        and not p.state_blob.get("gb2_shadow_fired")):
                    p.state_blob["gb2_shadow_fired"] = True
                    p.state_blob["gb2_shadow_pnl_at_fire"] = round(pnl_pct, 4)
                    p.state_blob["gb2_shadow_peak_at_fire"] = round(p.peak_pnl_pct, 4)
                    p.state_blob["gb2_shadow_secs"] = int(now - p.entry_time)
            except Exception:
                pass

        # 1. Hard stop (highest priority)
        if pnl_pct <= self.config.hard_stop_pct:
            decisions.append(ExitDecision(
                token=token, kind="HARD_STOP",
                reason=f"hard stop pnl={pnl_pct:.2f}% <= {self.config.hard_stop_pct}",
                sell_fraction=1.0,
            ))
            return decisions

        # 1b. TIME-BOX stop (2026-06-12, Dw5 archetype): full close at N min
        # regardless of pnl. Sits after the catastrophic hard stop, before
        # everything else — when armed, the box IS the loss management.
        if (self.config.time_stop_minutes is not None
                and (now - p.entry_time) >= self.config.time_stop_minutes * 60):
            decisions.append(ExitDecision(
                token=token, kind="TIME_STOP",
                reason=(f"time-box exit {self.config.time_stop_minutes:.0f}min "
                        f"(pnl={pnl_pct:+.2f}%)"),
                sell_fraction=1.0,
            ))
            return decisions

        # 1a. Giveback floor (2026-06-10 gap-through guard, pre-TP1): position
        # already proved demand (peak >= min) then round-tripped — exit at the
        # floor instead of riding to a gapped -15..-22% hard-stop fill
        # (momentum_shadow: 8/13 gap-stops peaked +3.8..+9.9 first).
        if (
            not p.tp1_hit
            and self.config.giveback_floor_pnl_pct is not None
            and self.config.giveback_floor_peak_min is not None
            and p.peak_pnl_pct >= self.config.giveback_floor_peak_min
            and pnl_pct <= self.config.giveback_floor_pnl_pct
        ):
            decisions.append(ExitDecision(
                token=token, kind="GIVEBACK_FLOOR",
                reason=(f"giveback floor pnl={pnl_pct:.2f}% after peak "
                        f"{p.peak_pnl_pct:+.1f}% (gap-through guard)"),
                sell_fraction=1.0,
            ))
            return decisions

        # 1b. Fast-dump bail (2026-06-10, pre-TP1): any-volume bail — the
        # volume-gated pre-stop bail below never fires on high-volume momentum
        # dumps, which gap the poll cadence straight through the hard stop.
        if (
            not p.tp1_hit
            and self.config.fast_bail_pnl_pct is not None
            and pnl_pct <= self.config.fast_bail_pnl_pct
        ):
            decisions.append(ExitDecision(
                token=token, kind="FAST_BAIL",
                reason=(f"fast-dump bail pnl={pnl_pct:.2f}% <= "
                        f"{self.config.fast_bail_pnl_pct} (any volume, gap-through guard)"),
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

        hold_minutes = (now - p.entry_time) / 60.0

        # 2a. ng-faststop ACTING exit (2026-06-05 drawdown-mine LEVER 1). Cut never-green
        # bleeders at the SHALLOW -4 tick that the -6 never_runner floor GAPS PAST (slow
        # never-greens jump -4 -> -16 in one 60s poll, skipping [-6,-15] -> book the -15 hard
        # stop). Same gate as the ng_faststop shadow stamped above (peak<2 AND pnl<=-4):
        # acting on the -4 tick books ~-4.7 instead of -16.4. Winner-safe BY CONSTRUCTION
        # (peak<2 can't be a runner); EXIT-ONLY (no size cut). Precedes never_runner (whose
        # -6 floor is missed on the gap). ACTS only when ng_faststop_exit_enabled.
        if (
            self.config.ng_faststop_exit_enabled
            and not p.tp1_hit
            and p.peak_pnl_pct < 2.0
            and pnl_pct <= -4.0
        ):
            decisions.append(ExitDecision(
                token=token, kind="NG_FASTSTOP",
                reason=f"ng_faststop peak={p.peak_pnl_pct:.2f}%<2 pnl={pnl_pct:.2f}%<=-4 (never-green fast cut)",
                sell_fraction=1.0,
            ))
            return decisions

        # 2b. Never-runner exit (2026-06-02 mine, convergent across 3 of 8 agents).
        # The cohort that NEVER crossed never_runner_peak_max: cut it via the
        # fast-bleeder arm (pnl <= loss_floor) OR the flat-liner arm (held >= minutes).
        # The peak<max gate is winner-safe BY CONSTRUCTION (cannot touch a position
        # that went meaningfully green) so the trail is untouched. SHADOW always
        # (stamp once for phantom parity); ACTS only when never_runner_exit_enabled.
        # Tighter/earlier than slow_bleed (-8%/60min) + stall_exit (peak<5/90min) for
        # this cohort, so it precedes them.
        _nr_cond = (
            not p.tp1_hit
            and p.peak_pnl_pct < self.config.never_runner_peak_max
            and (pnl_pct <= self.config.never_runner_loss_floor
                 or hold_minutes >= self.config.never_runner_minutes)
        )
        if _nr_cond and not (p.state_blob or {}).get("never_runner_fired"):
            p.state_blob["never_runner_fired"] = True
            p.state_blob["never_runner_arm"] = (
                "floor" if pnl_pct <= self.config.never_runner_loss_floor else "timebox"
            )
            p.state_blob["never_runner_pnl_at_fire"] = round(pnl_pct, 4)
            p.state_blob["never_runner_peak_at_fire"] = round(p.peak_pnl_pct, 4)
            p.state_blob["never_runner_secs"] = int(now - p.entry_time)
        if _nr_cond and self.config.never_runner_exit_enabled:
            decisions.append(ExitDecision(
                token=token, kind="NEVER_RUNNER",
                reason=(
                    f"never_runner peak={p.peak_pnl_pct:.2f}%<"
                    f"{self.config.never_runner_peak_max} pnl={pnl_pct:.2f}% "
                    f"hold={hold_minutes:.0f}min "
                    f"({p.state_blob.get('never_runner_arm')})"
                ),
                sell_fraction=1.0,
            ))
            return decisions

        # 3. Slow bleed (held too long at a loss, pre-TP1)
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

        # 3d. LET-WINNERS-RUN (solve-it army 2026-06-30). The fleet CAPS the fat
        # tail: TP1 sells ~75% at +6% then a tight ~2pp trail, so the deep-decliner
        # winners (the +11%/52%-win cohort) get clipped. For a CONFIRMED runner
        # (peak >= RUN_WINNERS_ARM_PCT) suppress the early-TP cap and ride a WIDE
        # trail (peak - RUN_WINNERS_TRAIL_PP) to harvest the tail. Validated on the
        # honest book: arm>=10 / trail 10pp lifted token-mean -1.83%->+24.3% (deep
        # cohort +5.0->+22.1%) with WIN-RATE PRESERVED — faders (peak<arm) keep the
        # normal exit below, untouched. RUN_WINNERS_MODE=off(default, byte-identical)|
        # shadow(stamp would-fire only)|enforce(override TP1 + ride wide trail). The
        # catastrophic loss floors above (hard_stop/fast_bail/pre_stop/in-flight) all
        # ran FIRST, so a runner is still protected on a gap-through. Fat-tail: mean
        # play (median ~0). Shadow stamps once; persists in state_blob.
        _rw_mode = os.environ.get("RUN_WINNERS_MODE", "off").strip().lower()
        if _rw_mode in ("shadow", "enforce"):
            # arm MUST engage at/before tp1_pct, else TP1 caps 75% before the runner
            # arms and the override is a no-op. Default = the bot's tp1_pct (so the
            # moment TP1 would fire, run-winners takes over and rides instead). This
            # is what makes the validated +23pp-mean (suppress-TP1, ride-full) real;
            # it is a FAT-TAIL play (win-rate drops ~36%->25%, mean way up).
            try:
                _rw_arm_env = os.environ.get("RUN_WINNERS_ARM_PCT", "")
                _rw_arm = (float(_rw_arm_env) if _rw_arm_env.strip()
                           else float(self.config.tp1_pct))
                _rw_trail = float(os.environ.get("RUN_WINNERS_TRAIL_PP", "10"))
            except (TypeError, ValueError):
                _rw_arm, _rw_trail = float(self.config.tp1_pct), 10.0
            if p.peak_pnl_pct >= _rw_arm:
                _rw_fire = pnl_pct <= (p.peak_pnl_pct - _rw_trail)
                if _rw_fire and p.state_blob is not None and not p.state_blob.get("run_winners_fired"):
                    p.state_blob["run_winners_fired"] = True
                    p.state_blob["run_winners_pnl_at_fire"] = round(pnl_pct, 4)
                    p.state_blob["run_winners_peak_at_fire"] = round(p.peak_pnl_pct, 4)
                    p.state_blob["run_winners_secs"] = int(now - p.entry_time)
                if _rw_mode == "enforce":
                    if _rw_fire:
                        decisions.append(ExitDecision(
                            token=token, kind="POST_TP1_TRAIL",
                            reason=(f"run-winners wide trail pnl={pnl_pct:.2f}% <= "
                                    f"peak({p.peak_pnl_pct:.2f}%)-{_rw_trail:.0f}pp"),
                            sell_fraction=1.0,
                        ))
                        return decisions
                    # confirmed runner, hasn't given back to the wide trail yet ->
                    # HOLD: skip the TP1/TP2 cap + tight trail this tick (let it run).
                    return decisions

        # 4. TP1
        if not p.tp1_hit and pnl_pct >= self.config.tp1_pct:
            p.tp1_hit = True
            # PEEL (2026-07-06, TP-peel replay scratchpad/_tp_peel_replay.md):
            # conditional runner. When the TP1 FILL lands below the wick
            # threshold (<+12: a normal fill, not a soft-cap wick fill), sell
            # the TP1 fraction and convert the remainder into an UNCAPPED
            # giveback-trailed runner: TP2 is skipped, the trail widens to
            # peel_giveback_pp. Wick fills (>=12) keep today's behavior — the
            # unconditional peel LOSES -59.6pp there (trail gives wick fills
            # back); the conditional gains +72pp/4.5d, both halves positive.
            if p.state_blob is not None:
                p.state_blob["tp1_fill_pnl"] = round(pnl_pct, 4)
                if (bool(getattr(self.config, "peel_exit", False))
                        and pnl_pct < float(getattr(self.config, "peel_threshold_pct", 12.0) or 12.0)):
                    p.state_blob["peel_active"] = True
            decisions.append(ExitDecision(
                token=token, kind="TP1",
                reason=f"TP1 pnl={pnl_pct:.2f}% >= {self.config.tp1_pct}",
                sell_fraction=self.config.tp1_sell_fraction,
            ))

        # 5. TP2 (skipped while a PEEL runner is active — no cap on the tail)
        _peel_on = bool((p.state_blob or {}).get("peel_active"))
        if p.tp1_hit and not p.tp2_hit and not _peel_on and pnl_pct >= self.config.tp2_pct:
            p.tp2_hit = True
            decisions.append(ExitDecision(
                token=token, kind="TP2",
                reason=f"TP2 pnl={pnl_pct:.2f}% >= {self.config.tp2_pct}",
                sell_fraction=self.config.tp2_sell_fraction,
            ))

        # 6. Post-TP1 trail (skip when trail_pp is None — e.g. probe_swing, a swing
        # bot that exits via time_stop/tp2, not a trailing stop. A None trail_pp
        # crashed the tick with `float - NoneType` here once a position hit TP1.)
        _trail_pp = self.config.trail_pp
        if _peel_on:
            # PEEL runner: wider giveback (default 5pp) replaces the tight
            # trail; the hard stop (-12) below still floors catastrophe.
            _trail_pp = float(getattr(self.config, "peel_giveback_pp", 5.0) or 5.0)
        if p.tp1_hit and not decisions and _trail_pp is not None:
            trail_threshold = p.peak_pnl_pct - _trail_pp
            if pnl_pct <= trail_threshold:
                decisions.append(ExitDecision(
                    token=token, kind="POST_TP1_TRAIL",
                    reason=(
                        f"{'peel-runner ' if _peel_on else ''}trail pnl={pnl_pct:.2f}% "
                        f"<= peak({p.peak_pnl_pct:.2f}%) - {_trail_pp}pp"
                    ),
                    sell_fraction=1.0,
                ))
        return decisions
