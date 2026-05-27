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

    # Entry-quality gate (2026-05-27, held-out validated). When True the bot
    # blocks EXTENDED entries (the falling-knife signature behind the
    # buy-into-downtrend losses): requires a REAL pullback
    # (shape_90m_drawdown_from_max_pct <= -7.5) AND live volatility
    # (token_volatility_h24_pct >= 30). Fail-OPEN if a feature is missing.
    # Opt-in per bot (default False = no change to existing bots). See
    # reference_entry_separator_mine_2026_05_27.
    require_real_pullback: bool = False

    # Filter set — semantics: if filters_enforced is None, the bot uses
    # the project baseline filter set MINUS anything in filters_disabled.
    # If filters_enforced is a list, that's the EXACT enforced set and
    # filters_disabled is ignored.
    filters_enforced: Optional[tuple[str, ...]] = None
    filters_disabled: tuple[str, ...] = field(default_factory=tuple)

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

    # Re-entry cooldown (2026-05-25 — P-stack #4). Seconds a bot must wait
    # after fully closing a token before it may buy that token again. None or
    # 0 = immediate re-entry allowed (recycle into a re-firing runner). A
    # positive value throttles churn on the same token.
    reentry_cooldown_secs: Optional[float] = None

    def __post_init__(self) -> None:
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
