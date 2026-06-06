# core/bot_config.py
from __future__ import annotations
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class BotConfig:
    """Universal config schema for a single bot.

    See docs/superpowers/specs/2026-05-23-multi-bot-harness-design.md for
    semantics of each field. All thresholds are inclusive unless noted.
    """

    bot_id: str
    display_name: str
    enabled: bool = True

    # Capital & sizing
    paper_capital_usd: float = 2000.0
    base_position_usd: float = 20.0
    max_concurrent_positions: int = 3
    alpha_multiplier: float = 1.5
    macro_up_multiplier: float = 1.5
    premium_runner_multiplier: float = 3.0
    marginal_multiplier: float = 0.5

    # Macro gates (None disables)
    sol_macro_h6_block_threshold: Optional[float] = -0.3
    sol_macro_h1_block_threshold: Optional[float] = -0.7
    btc_macro_h1_block_threshold: Optional[float] = None

    # Token regime gates
    pc_h24_max: Optional[float] = None
    pc_h24_min: Optional[float] = None
    pc_h1_max: Optional[float] = None
    age_h_min: Optional[float] = None
    age_h_max: Optional[float] = None
    mcap_min: Optional[float] = None
    mcap_max: Optional[float] = None
    vol_h1_min: Optional[float] = 1000.0

    # Dead-flatline reject (2026-06-02). Block entries whose token_volatility_h24_pct is
    # below this — a token that barely moves (e.g. vRse... = 0.48% 24h vol, slow-bled across
    # 26 bots) CANNOT mechanically produce the +5-30% the strategy needs. Validated: 0
    # winners had vol<5% in 22 days (the <5% bucket is all TP1 scratches / dead). Fail-OPEN
    # when the feature is missing (coverage-safe). None = off. Set ~5.0 on production-track
    # bots; NOT young probes (24h vol is ill-defined for <6h tokens).
    min_token_volatility_h24_pct: Optional[float] = None

    # Range-floor reject (2026-06-03): block entries on tokens whose trailing 90-minute
    # high-low range is below this %. A flatlining/dead token (low range) cannot produce
    # the strategy's move. Held-out (8-Opus hunt, LOTO AUC 0.767, token-clustered null
    # p=0.009): this is a STRICT SUPERSET of the min_token_volatility_h24_pct=5 gate (blocks
    # everything it blocks + 389 more) with 0.6% dollar winner-kill; TREND (+2.8x) untouched
    # (90m range 35-95%). REPLACES the 5% vol floor. Fail-OPEN on missing (token <90m old).
    min_shape_90m_range_pct: Optional[float] = None

    # Entry-quality gate (2026-05-27, held-out validated). When True the bot
    # blocks EXTENDED entries (the falling-knife signature behind the
    # buy-into-downtrend losses): requires a REAL pullback
    # (shape_90m_drawdown_from_max_pct <= -7.5) AND live volatility
    # (token_volatility_h24_pct >= 30). Fail-OPEN if a feature is missing.
    # Opt-in per bot (default False = no change to existing bots). See
    # reference_entry_separator_mine_2026_05_27.
    require_real_pullback: bool = False

    # Generic per-bot entry gate (2026-05-27, held-out-validated compound mine).
    # Optional list of [feature, op, threshold] conditions ANDed against raw_meta
    # at entry; op in {">=", "<="}. Fail-OPEN per condition when the feature is
    # missing (coverage-safe). Lets a bot enforce a mined compound (e.g.
    # pc_h1<=-8 AND 1s_green_run_end>=2 AND <orthogonal axis>) with no new code.
    # Default None = no gate. See reference_entry_separator_mine_2026_05_27.
    entry_gate: Optional[tuple] = None

    # Filter set — semantics: if filters_enforced is None, the bot uses
    # the project baseline filter set MINUS anything in filters_disabled.
    # If filters_enforced is a list, that's the EXACT enforced set and
    # filters_disabled is ignored.
    filters_enforced: Optional[tuple[str, ...]] = None
    filters_disabled: tuple[str, ...] = field(default_factory=tuple)

    # Rolling never-green scorer opt-in (see core/ng_scorer.py). When True AND the
    # global env NG_SCORER_MODE is shadow/enforce, this bot's entries are scored by
    # the rolling model; in enforce mode a high never-green probability blocks the
    # buy. Default False (control bots stay ungated so the A/B stays measurable).
    ng_scorer_gate: bool = False

    # Triggers — same semantics as filters
    triggers_allowed: Optional[tuple[str, ...]] = None
    triggers_disabled: tuple[str, ...] = field(default_factory=tuple)
    min_triggers_to_fire: int = 1
    require_alpha_trigger: bool = False

    # Trigger-specific gates (evaluated after universal gates pass)
    mcap_psych_pc_h24_max: Optional[float] = 80.0

    # Exit ladder
    tp1_pct: float = 5.0
    tp1_sell_fraction: float = 0.75
    tp2_pct: float = 10.0
    tp2_sell_fraction: float = 0.25
    trail_pp: float = 3.0
    hard_stop_pct: float = -15.0
    pre_stop_bail_pnl_pct: float = -3.0
    pre_stop_bail_vol_m5_max: float = 500.0
    slow_bleed_minutes: int = 60
    slow_bleed_pnl_threshold: float = -8.0

    # Trading window (UTC hours, half-open: [start, end))
    trading_hour_utc_start: int = 0
    trading_hour_utc_end: int = 24

    # Compounding (2026-05-23 — experimental). Position size scales with
    # cumulative realized P&L. None disables (default). Modes:
    #   "linear"       — size = base * (1 + realized_pnl / starting_balance)
    #                    grows on wins, shrinks on losses (floored at 0.25x)
    #   "winners_only" — size = base * (1 + max(0, realized) / starting_balance)
    #                    grows on wins, never shrinks below base
    #   "threshold"    — size = base + floor(max(0, realized) / step_usd) * step_amount
    #                    discrete steps: +$step_amount per $step_usd of realized profit
    # All modes are capped at compound_max_multiplier to prevent runaway growth.
    compound_mode: Optional[str] = None
    compound_threshold_step_usd: float = 100.0
    compound_step_amount_usd: float = 5.0
    compound_max_multiplier: float = 5.0

    # Drawdown freeze (2026-05-23 — Deploy C). Pause buying when realized P&L
    # drops to or below this threshold. None disables. Buying resumes
    # automatically when realized recovers above the threshold.
    drawdown_freeze_threshold_usd: Optional[float] = None

    # Macro-conditional sizing (2026-05-23 — Deploy C). Scale position size
    # gradient-style based on sol_pc_h6 (overrides binary sol_macro block).
    # None disables. "sol_h6" mode: 1.5x when sol_pc_h6 >= +0.3, 0.5x when
    # sol_pc_h6 <= -0.1, 1.0x otherwise. The bot's sol_macro_h6_block_threshold
    # should typically be relaxed (e.g. None) when this is active so the
    # gradient sizing isn't pre-empted by the binary block.
    macro_conditional_mode: Optional[str] = None

    # Conviction-scaled sizing (2026-05-25 — P-stack #2). Scale position size
    # by entry conviction. None disables. "trigger_count" mode: size grows
    # with the number of triggers that fired (confluence) —
    #   mult = min(1 + conviction_step * (n_triggers - 1), conviction_max_mult)
    # Leans on the finding that confluent signals / concurrent positions are
    # the strongest big-winner predictors. Stacks AFTER alpha/compound/macro.
    conviction_sizing_mode: Optional[str] = None
    conviction_step: float = 0.5
    conviction_max_mult: float = 2.5

    # Velocity exit (2026-05-25 — P-stack #3). Recycle capital out of FLAT
    # positions that go nowhere, freeing it for new trades (max-volume lever).
    # None disables. When set, a pre-TP1 position held >= flat_exit_minutes
    # whose pnl is inside +/- flat_exit_band_pct (neither winning nor losing)
    # is closed. Distinct from slow_bleed (loss-based) — this targets dead
    # money, not losers.
    flat_exit_minutes: Optional[int] = None
    flat_exit_band_pct: float = 3.0

    # Stall exit (2026-05-29 — 7h-watch rec #3). Opt-in (None disables). Fires
    # pre-TP1 when a position (a) peaked low (peak_pnl_pct < stall_exit_peak_max),
    # (b) has been held >= stall_exit_minutes, AND (c) is now drifting back down
    # off that peak (pnl_pct <= peak_pnl_pct - stall_exit_drift_pp). Targets the
    # never-launched corpse that bleeds capital for hours below the slow_bleed
    # loss threshold. Distinct from flat_exit (band-based, ignores peak) and
    # slow_bleed (pure loss threshold). V3 validation: 328 losers (-$1275 pool)
    # peaked<5% held>90min; winner-clip risk ~$1.35 (11 of 13 at-risk winners
    # exited via trail = riding up, stall would not fire).
    stall_exit_minutes: Optional[int] = None
    stall_exit_peak_max: float = 5.0
    stall_exit_drift_pp: float = 2.0

    # Re-entry cooldown (2026-05-25 — P-stack #4). Seconds a bot must wait
    # after fully closing a token before it may buy that token again. None or
    # 0 = immediate re-entry allowed (recycle into a re-firing runner). A
    # positive value throttles churn on the same token.
    reentry_cooldown_secs: Optional[float] = None

    # Phase-1 risk floors (2026-06-01). Per-bot, None = off. Enforced only on the
    # production-candidate config (shadow-measured fleet-wide first). See
    # docs/superpowers/specs/2026-06-01-phase1-risk-floor-design.md.
    # daily_loss_limit_usd: halt NEW buys once today's realized daily_pnl_usd
    #   <= -this (sells always allowed; clears at UTC 00:00).
    # max_token_buys_per_day: cap re-entries into a single token per UTC day
    #   (the death-spiral was sequential re-buys; one bot bought SPCX 16x).
    daily_loss_limit_usd: Optional[float] = None
    max_token_buys_per_day: Optional[int] = None

    # Live measurement probe (2026-06-02). Per-bot, scaffolding for the
    # paper->live probe — see docs/superpowers/specs/2026-06-02-live-measurement-probe-design.md.
    # live_probe: when True AND USE_JUPITER_ULTRA AND a real private key is present,
    #   THIS bot's fills route through the live MEV-protected Ultra swap (the bridge,
    #   piece 1b) instead of the paper simulator. Default False = paper (the whole
    #   fleet stays paper unless a bot opts in AND the env gates are set). It can
    #   NEVER go live on flag alone — the bridge requires the env gates too.
    # (Size variants for the probe are SEPARATE fixed-size bots — probe_tightexit_live_{20,50,100}
    #  with multipliers neutralized — so each size is a clean per-token paired measurement,
    #  not a rotation-within-one-bot that entangles size with token/timing.)
    live_probe: bool = False

    # Shared no-same-token exclusion pool (2026-06-02). Bots that share the same
    # non-empty pool name may NOT hold the same token concurrently — the first to
    # open it claims it; pool siblings skip that token until it closes. This is the
    # de-concentration lever (spread a pool of bots across DISTINCT simultaneous
    # tokens instead of piling into one). None (default) = not in any pool = current
    # single-bot behavior, never blocked. See core/shared_token_registry.py and
    # the A/C design comparison (2026-06-02).
    exclusion_pool: Optional[str] = None

    # Never-runner exit (2026-06-02 mine — convergent across 3 of 8 agents). A
    # composed loss-avoidance exit for the cohort that NEVER went meaningfully
    # green: fires when peak_pnl_pct < never_runner_peak_max AND (pnl <=
    # never_runner_loss_floor [fast-bleeder arm] OR held >= never_runner_minutes
    # [flat-liner arm]). The peak gate makes it winner-safe BY CONSTRUCTION (it can
    # never touch a position that crossed the peak threshold), so the runner trail
    # is untouched (validated 0-winner-kill, held-out by token+time). SHADOW always
    # (state_blob never_runner_* stamped for phantom parity); ACTS only when
    # never_runner_exit_enabled. Default off = no behavior change. Forward-test the
    # minutes threshold (30/45/60) on the pool bots before enforcing on production.
    never_runner_exit_enabled: bool = False
    never_runner_peak_max: float = 3.0
    never_runner_loss_floor: float = -6.0
    never_runner_minutes: int = 45

    # Scale-in / staged entry (2026-06-05, 8-intervention SOL-flicker mine). The ONLY
    # lever that cut the "born-before-crater" never-green bleed without killing the
    # dip-buy edge. The fader and the dip-before-rip are inseparable at entry (proven 8
    # ways: SOL gate/velocity, early stop, drop-velocity, volume, rug-gate all fail), so
    # instead of trying to AVOID the bad trade we commit a HALF tranche at entry and
    # complete to full size only once the position CONFIRMS (pnl >= scalein_confirm_pct).
    # Faders never get the 2nd half (loss halved); winners reach FULL size on confirmation
    # (~0.5pp cost). NOT a de-size stop (winners are full size), NOT flat de-sizing.
    # Backtest (13d real, per-bot): champion_defender_2k -$821 -> ~-$55 (recovers +$766 at
    # CONF=1); outlier-robust (ex-top-5 +$545), held-out positive 10/14 days. PAPER-ONLY:
    # the 2nd-tranche LIVE execution is wired at go-live (gated by test_pre_live_invariants);
    # in live the bot opens full size = current behavior, so enabling it cannot mis-size a
    # live bot. Default off = no behavior change.
    scalein_enabled: bool = False
    scalein_confirm_pct: float = 1.0      # add the 2nd tranche when pnl first reaches this
    scalein_first_fraction: float = 0.5   # fraction of full size deployed at entry
    # FLASH-slip de-risk within scale-in (2026-06-05, flash-signature mine). Thin EXECUTABLE
    # depth at entry (slip_buy_2000_pct >= scalein_flash_slip_pct) is the orthogonal flash-
    # crash signature (AUC 0.80 vs nominal liq which failed) — but flash tokens are two-sided
    # (they also moon), so we DON'T block: enter an even SMALLER first tranche; the runner
    # still completes to FULL on confirm. Null slip (Jupiter quote unavailable on ~44%) ->
    # default fraction. Applies only where scalein_enabled. Winner-safe (confirm-to-full).
    scalein_flash_slip_pct: float = 6.0
    scalein_flash_first_fraction: float = 0.33

    # ng_faststop acting exit (2026-06-05 drawdown-mine LEVER 1). The never-green fast-stop
    # (peak<2 AND pnl<=-4) already runs as a SHADOW; promote it to ACT. It observes the
    # shallow -4 tick that the never_runner -6 floor GAPS PAST (slow never-green bleeders
    # jump -4->-16 in one 60s poll, skipping [-6,-15] -> book the -15 hard stop). Acting on
    # the -4 tick books ~-4.7 instead of -16.4: cuts ~23% of fleet loss / ~22% maxDD for
    # 0-2 winners (save:kill ~975:1, off-06-04-durable). Winner-safe BY CONSTRUCTION (peak<2
    # can't touch a runner); EXIT-ONLY (no size change — the user will not cut size). Default
    # off = no behavior change; opt-in per bot (pool_a/pool_c_tightexit/def2k).
    ng_faststop_exit_enabled: bool = False

    # Pool sizing de-rates (2026-06-02 fleet-mine, cap-respecting positive selection).
    # When True, position size is adjusted DOWN (never up — honors the $100 cap) for the
    # cohorts the fleet mine flagged, with the smart-money compound EXEMPT (kept at full
    # size = the only "up" we can do under a cap). Applied in dip_scanner before reserve:
    #   - on-chain concentration: capped top10_holder_pct < 50 -> x0.5 (held-out, 72 tok)
    #   - concurrent>1 in a DOWN regime (sol_pc_h6<0 or btc_pc_h1<0) -> x0.5 (regime-
    #     conditional; conc>1 alpha sign-flips in a crash)
    #   - smart-money compound (smart_wallet_count_total>=1 AND 5m_red_count>=6 AND
    #     net_flow_15s_usd>0): EXEMPT from de-rates (positive selection, stays full size)
    # Default False = no change. Enabled on the pool bots (the $100-cap forward A/B);
    # the production candidate stays clean until forward data + the blended-PnL re-run
    # firm the (provisional, ~3-day) magnitudes.
    pool_sizing_derates_enabled: bool = False

    # Young-token probe (2026-06-02, #4.1). When the global YOUNG_TOKEN_PROBE env flag is
    # on, a bot with young_token_probe=True trades YOUNG tokens ONLY (age < the young
    # threshold, surfaced past the fleet min_age gate with a liquidity floor); production
    # bots (False) SKIP young tokens. Default False + env off = no change. Tests whether the
    # universe-mine's age<=2 edge survives on realized dip-buy paths. See core/young_token_probe.py.
    young_token_probe: bool = False

    # Momentum-continuation mode (2026-06-02, #4.3). When True the bot uses a SEPARATE
    # entry path: it BYPASSES the dip-filter stack + dip triggers (which block 100% of
    # momentum candidates) and enters on the momentum entry_gate (e.g. pc_h1>=20 AND
    # pct_above_vwap_h24<=cap AND 1m_volume_spike>=0.40). The strongest decorrelated lead
    # from the broad sweep (+14pp WR, both regimes). Default False = the normal dip path,
    # unchanged. The trading-window + drawdown-freeze + entry_gate still apply.
    momentum_mode: bool = False

    # Low-mcap probe (2026-06-02). When LOW_MCAP_PROBE env is on, a low_mcap_probe=True bot
    # trades the [floor,$1M) mcap band ONLY (forward data: 500k-1M matches/beats the 1M-5M
    # band we trade, ~2x throughput); production bots SKIP sub-$1M tokens. Default False +
    # env off = no change. Tests lowering the global min_mcap to $500k on realized paths.
    low_mcap_probe: bool = False

    def __post_init__(self) -> None:
        # Normalize entry_gate to a hashable tuple-of-tuples (JSON yields
        # tuple-of-lists; the frozen dataclass's auto __hash__ chokes on lists).
        if self.entry_gate is not None:
            object.__setattr__(
                self, "entry_gate",
                tuple(tuple(c) for c in self.entry_gate),
            )
        if self.filters_enforced is not None and self.filters_disabled:
            raise ValueError(
                f"bot_id={self.bot_id}: filters_enforced is set, "
                "so filters_disabled must be empty (it is ignored when enforced is set)"
            )
        if self.triggers_allowed is not None and self.triggers_disabled:
            raise ValueError(
                f"bot_id={self.bot_id}: triggers_allowed is set, "
                "so triggers_disabled must be empty"
            )
        if self.tp1_sell_fraction + self.tp2_sell_fraction > 1.0 + 1e-9:
            raise ValueError(
                f"bot_id={self.bot_id}: tp1_sell_fraction "
                f"({self.tp1_sell_fraction}) + tp2_sell_fraction "
                f"({self.tp2_sell_fraction}) must be <= 1.0"
            )


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------

def _to_json_safe(value):
    """Convert tuples to lists for JSON; recurse into structures."""
    if isinstance(value, tuple):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    return value


def _from_json_safe(field_type, value):
    """Coerce JSON-deserialized lists back to tuples for tuple-typed fields.

    ``field_type`` is the string annotation (from __future__ annotations),
    e.g. ``"Optional[tuple[str, ...]]"`` or ``"tuple[str, ...]"``.
    """
    if value is None:
        return None
    type_str = str(field_type)
    if "tuple" in type_str and isinstance(value, list):
        return tuple(value)
    return value


def _add_json_methods(cls):
    def to_json(self, path):
        path = Path(path)
        data = {f.name: _to_json_safe(getattr(self, f.name))
                for f in dataclasses.fields(self)}
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def from_json(cls_, path):
        path = Path(path)
        text = path.read_text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {path}: {e}") from e
        known = {f.name: f for f in dataclasses.fields(cls_)}
        unknown = set(data.keys()) - set(known.keys())
        if unknown:
            raise ValueError(
                f"Unknown field(s) in {path.name}: {sorted(unknown)}"
            )
        coerced = {
            name: _from_json_safe(known[name].type, val)
            for name, val in data.items()
        }
        try:
            return cls_(**coerced)
        except TypeError as e:
            raise ValueError(
                f"Missing or invalid field in {path.name}: {e}"
            ) from e

    cls.to_json = to_json
    cls.from_json = from_json
    return cls


BotConfig = _add_json_methods(BotConfig)
