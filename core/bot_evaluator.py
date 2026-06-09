from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.ng_scorer import (
    get_scorer as get_ng_scorer, scorer_mode as ng_scorer_mode,
    log_decision as ng_log_decision,
)

logger = logging.getLogger(__name__)


# Alpha-tier triggers that warrant 1.5x sizing (matches dip_scanner.py:12937).
ALPHA_TRIGGERS = frozenset({
    "1s_capit_reversal",
    "deep_1h_dip",
    "concurrent_alpha",
    "whale_concentrated_demand",
    "whale_recent_burst",
    "whale_p90_size",
    "textbook_pullback_vol_accel",
    "textbook_pullback_big_buyer",
    # Added 2026-05-29 (trigger-mine V3 winner — G10).
    # n=188, token-dedup +$3.86/tr, WR 94.7%, both days near-identical.
    "trigger_stable_compound_quality",
})

# Sizing-tier trigger sets (mirror the legacy single-bot tiers in dip_scanner).
# 2026-05-27 audit #6: wire the previously-dead premium_runner/marginal multipliers.
_PREMIUM_RUNNER_TRIGGER = "fresh_runner_factory"     # legacy 3x tier
_MARGINAL_FOR_SIZE = frozenset({                      # legacy 0.5x risk-gate tier
    "patient_bottom", "informed_cluster", "1s_capit_reversal",
    "whale_conviction", "grad_window_dip", "alpha_buyperscold",
    "net_flow_5m_demand", "fresh_pump_retrace",
})

# Layered defender filters added 2026-05-28 (perf-diff mine).
# Opt-in only: existing bots with filters_enforced=None do NOT enforce these.
# Bots opt in by adding the filter name to their filters_enforced list.
# See .perf_diff_drafts/SCHEMA_PROPOSAL.md for held-out validation results.
DEFENDER_FILTERS = frozenset({
    "filter_falling_pump",
    "filter_fusion_floor",
    "filter_btc_overheat",
    "filter_aged_corpse",
    "filter_wynn_killer",
    "filter_consec_red",
    "filter_dead_meme_lagging_pressure",
    "filter_dead_low_demand",
    "filter_dead_volume",
    "filter_huge_wick",
    # 2026-06-04: rolling_ng never-green model as a hard ENTRY BLOCK. Opt-in so only
    # bots that list it (pool_a_candidate, pool_c_tightexit) enforce it; the rest just
    # shadow-stamp. Held-out on the pools: blocks 20 fader-entries (10% WR, 0 runners),
    # keeps all 8 runners, avoids -$127 fader-loss for $6 winner-clip. Fail-open (no model).
    "filter_rolling_ng",
    # 2026-06-05: SOL-flicker capital-preservation gate. BLOCK entries when the SOL
    # macro gate flipped clear->block >=2x in the trailing hour (acute chop/crash
    # regime; causal/past-only). Self-gates to chop (flk_1h=0 on calm days). Net loss-
    # trimmer in % (+241pp; off-06-04 +68pp, ~2:1 save:kill, 4/5 non-nuke days). Opt-in:
    # acts on def2k/pool_a/pool_c_tightexit; preserves capital so the profit-sweep banks
    # higher realized peaks. Does NOT cut the catastrophic rug tail (separate defender).
    "filter_sol_flicker",
})


def _rug_gate_mode() -> str:
    """Global fleet-wide rug-structure gate mode. Env RUG_GATE_MODE in
    {off, shadow, enforce}; default 'enforce'. Lets the gate be downgraded
    without a deploy if it ever misfires."""
    m = os.environ.get("RUG_GATE_MODE", "enforce").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "enforce"


def _entry_stack_mode() -> str:
    """Fleet-wide validated entry-stack gate mode. Env ENTRY_STACK_MODE in
    {off, shadow, enforce}; default 'enforce'. Downgradeable without a deploy."""
    m = os.environ.get("ENTRY_STACK_MODE", "enforce").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "enforce"


# Control cohort: stays UNGATED so the gated-vs-ungated counterfactual keeps
# being measured forward (same held-out discipline as every other gate).
# Override via ENTRY_STACK_CONTROL_BOTS=csv.
_ENTRY_STACK_DEFAULT_CONTROL = frozenset({
    "baseline_v1", "no_filters", "pool_a_broad_control",
})


def _entry_stack_control_bots() -> frozenset:
    raw = os.environ.get("ENTRY_STACK_CONTROL_BOTS")
    if raw is None:
        return _ENTRY_STACK_DEFAULT_CONTROL
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def _entry_stack_violations(b: FeatureBundle) -> list:
    """Fleet-wide validated entry stack (2026-06-09 bleed-week decomposition,
    28d / 18,439 closed trades): on the 11 BLEED days the fleet lost $18,338
    while entries passing this stack lost only $395 — and violators lost 2.5x
    more PER TRADE at SMALLER median size than passers ($20 vs $30), so the
    bleed is ENTRY DECISIONS, not size. Binding gates are dip-depth (6,252
    bleed-day violations) and buy-side flow (5,827); age/mcap barely bind
    (166/136) but are kept as cheap validated bounds. Each check FAILS OPEN
    when its feature is missing/zero (coverage-safe, matches fleet convention).
    """
    fails = []
    rm = b.raw_meta or {}
    dd = rm.get("shape_90m_drawdown_from_max_pct")
    if isinstance(dd, (int, float)) and not isinstance(dd, bool) and dd > -16.0:
        fails.append(f"dip_shallow({dd:.1f}>-16)")
    nf = rm.get("net_flow_60s_usd")
    if isinstance(nf, (int, float)) and not isinstance(nf, bool) and nf < 100.0:
        fails.append(f"flow_weak({nf:.0f}<100)")
    age = b.age_hours
    if isinstance(age, (int, float)) and not isinstance(age, bool) and 0 < age < 24.0:
        fails.append(f"age_young({age:.1f}h<24)")
    mc = b.mcap_usd
    if (isinstance(mc, (int, float)) and not isinstance(mc, bool) and mc > 0
            and not (500_000 <= mc <= 10_000_000)):
        fails.append(f"mcap_out({mc/1e6:.2f}M)")
    return fails


# Throttle entry_stack INFO logs to once per (token, hour) — the gate evaluates
# for every bot x token x cycle and blocks are the COMMON case (~85% of
# candidates), so unthrottled logging would flood Railway.
_entry_stack_logged: set = set()


def _rug_structure_blocks(b: FeatureBundle) -> tuple[bool, str]:
    """Fleet-wide catastrophic-rug guard (2026-06-08). The STANDARD rug
    fundamentals (rugcheck_score, lp_locked_pct, lp_burned) do NOT catch the
    single-sided / no-demand rug: the GO -99.95% LP-pull (38s after entry) had a
    CLEAN rugcheck + 100% locked + burned LP. The real signature is LIQUIDITY
    STRUCTURE — a one-sided LP with zero real buyers is the dev's own liquidity,
    primed to pull (a lock is worthless if there's no counter-liquidity). Held-out
    across weeks of fleet history: `lp_single_sided OR unique_buyers_n==0` catches
    5/6 rugs (83%, incl GO) for ~5% survivor-kill — and those survivors are
    low-quality no-demand entries anyway. Fail-OPEN when both features are missing
    (coverage-safe). See the rug-separation analysis 2026-06-08."""
    ss = b.raw_meta.get("lp_single_sided")
    ub = b.raw_meta.get("unique_buyers_n")
    if ss is True:
        return True, "lp_single_sided=True (one-sided LP)"
    if isinstance(ub, (int, float)) and not isinstance(ub, bool) and ub == 0:
        return True, "unique_buyers_n=0 (no real buyers)"
    return False, ""


@dataclass
class BuyDecision:
    bot_id: str
    token: str
    address: str
    pair_address: str
    entry_price: float
    size_usd: float
    size_tier: str
    triggers_fired: tuple[str, ...]
    reason_summary: str


class BotEvaluator:
    """Per-bot decision engine.

    Pure function of (BotConfig, FeatureBundle) -> Optional[BuyDecision].
    No I/O. Safe to call N times per cycle.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def evaluate(self, b: FeatureBundle,
                 realized_pnl_usd: float = 0.0) -> Optional[BuyDecision]:
        # Momentum-continuation mode (#4.3) — a SEPARATE entry path that bypasses the dip
        # filter stack + dip triggers. Default off -> the dip path below is unchanged.
        if getattr(self.config, "momentum_mode", False):
            return self._evaluate_momentum(b, realized_pnl_usd)
        if self._trading_window_blocks(b):
            return None
        if self._rug_gate_blocks(b):
            return None
        if self._entry_stack_blocks(b):
            return None
        if self._drawdown_freeze_blocks(realized_pnl_usd):
            return None
        if self._sol_macro_blocks(b):
            return None
        if self._btc_macro_blocks(b):
            return None
        if not self._token_regime_passes(b):
            return None
        if self._effective_filter_blocks(b):
            return None

        effective_triggers = self._effective_triggers(b)
        if len(effective_triggers) < self.config.min_triggers_to_fire:
            return None
        if self.config.require_alpha_trigger:
            if not (set(effective_triggers) & ALPHA_TRIGGERS):
                return None

        # Rolling never-green scorer gate (opt-in per bot via ng_scorer_gate +
        # global env NG_SCORER_MODE). Fail-open: a missing model/feature never
        # blocks. Logs every decision in BOTH shadow and enforce so we keep the
        # forward record even while enforcing. See core/ng_scorer.py.
        if self.config.ng_scorer_gate:
            mode = ng_scorer_mode()
            if mode in ("shadow", "enforce"):
                try:
                    block, proba = get_ng_scorer().should_block(b.raw_meta)
                except Exception:
                    block, proba = False, None
                if proba is not None:
                    enforced_block = bool(block and mode == "enforce")
                    logger.info(
                        f"[ng_scorer] bot={self.config.bot_id} token={b.token} "
                        f"p_nevergreen={proba:.3f} thr={get_ng_scorer().threshold:.3f} "
                        f"mode={mode} block={enforced_block} "
                        f"triggers={','.join(effective_triggers)}"
                    )
                    # Persist the decision so the enforced gate is observable forward
                    # (blocks leave no trade record; Railway logs evaporate in ~30min).
                    ng_log_decision({
                        "t": datetime.now(timezone.utc).isoformat(),
                        "bot": self.config.bot_id, "token": b.token,
                        "addr": getattr(b, "address", None),
                        "p": round(proba, 4), "thr": round(get_ng_scorer().threshold, 4),
                        "blocked": enforced_block, "mode": mode,
                        "triggers": list(effective_triggers),
                    })
                    if enforced_block:
                        return None

        size_usd, size_tier = self._size_for(effective_triggers, b, realized_pnl_usd)

        return BuyDecision(
            bot_id=self.config.bot_id,
            token=b.token,
            address=b.address,
            pair_address=b.pair_address,
            entry_price=b.price_usd,
            size_usd=size_usd,
            size_tier=size_tier,
            triggers_fired=effective_triggers,
            reason_summary=f"triggers={','.join(effective_triggers)} tier={size_tier}",
        )

    def _evaluate_momentum(self, b: FeatureBundle,
                           realized_pnl_usd: float = 0.0) -> Optional[BuyDecision]:
        """Momentum-continuation entry path (#4.3). Bypasses the dip filter stack + dip
        triggers; enters on the momentum entry_gate (evaluated via _token_regime_passes,
        which includes the entry_gate AND-conditions). Keeps the trading-window +
        drawdown-freeze lifecycle gates. Emits a single 'momentum_continuation' trigger."""
        if self._trading_window_blocks(b):
            return None
        if self._rug_gate_blocks(b):
            return None
        if self._drawdown_freeze_blocks(realized_pnl_usd):
            return None
        if not self._token_regime_passes(b):   # = the momentum entry_gate conditions
            return None
        size_usd, _ = self._size_for(("momentum_continuation",), b, realized_pnl_usd)
        return BuyDecision(
            bot_id=self.config.bot_id,
            token=b.token,
            address=b.address,
            pair_address=b.pair_address,
            entry_price=b.price_usd,
            size_usd=size_usd,
            size_tier="momentum",
            triggers_fired=("momentum_continuation",),
            reason_summary="momentum_continuation",
        )

    def _drawdown_freeze_blocks(self, realized_pnl_usd: float) -> bool:
        """Pause buying when realized P&L is at or below the freeze threshold.

        Default threshold is None (disabled). When set, the bot stops opening
        new positions until realized P&L recovers above the threshold. Open
        positions are unaffected — only NEW buys are gated.
        """
        c = self.config
        if c.drawdown_freeze_threshold_usd is None:
            return False
        return realized_pnl_usd <= c.drawdown_freeze_threshold_usd

    def _trading_window_blocks(self, b: FeatureBundle) -> bool:
        """Block if FeatureBundle snapshot is outside the configured UTC window.

        Half-open interval [start, end). The default 0..24 always passes
        (fast-path). Wrap-around windows (start > end, e.g. 22..2 meaning
        22,23,0,1) are supported.

        Bug fix 2026-05-23: this method was missing entirely. trading_hour_utc_*
        was defined on BotConfig but never enforced, so the 4 tod_* bots
        had 100% token overlap with each other. See
        [[project_tod_bot_bug_2026_05_23]].
        """
        c = self.config
        if c.trading_hour_utc_start == 0 and c.trading_hour_utc_end == 24:
            return False
        if b.snapshot_ts is None:
            return False  # fail-open if no timestamp on the bundle
        hour = datetime.fromtimestamp(b.snapshot_ts, tz=timezone.utc).hour
        if c.trading_hour_utc_start <= c.trading_hour_utc_end:
            return not (c.trading_hour_utc_start <= hour < c.trading_hour_utc_end)
        # Wrap-around (start > end). Hour is in window if >= start OR < end.
        return not (hour >= c.trading_hour_utc_start or hour < c.trading_hour_utc_end)

    def _sol_macro_blocks(self, b: FeatureBundle) -> bool:
        c = self.config
        if (c.sol_macro_h6_block_threshold is not None
                and b.sol_pc_h6 is not None
                and b.sol_pc_h6 < c.sol_macro_h6_block_threshold):
            return True
        if (c.sol_macro_h1_block_threshold is not None
                and b.sol_pc_h1 is not None
                and b.sol_pc_h1 < c.sol_macro_h1_block_threshold):
            return True
        return False

    def _btc_macro_blocks(self, b: FeatureBundle) -> bool:
        c = self.config
        if (c.btc_macro_h1_block_threshold is not None
                and b.btc_pc_h1 is not None
                and b.btc_pc_h1 < c.btc_macro_h1_block_threshold):
            return True
        return False

    def _token_regime_passes(self, b: FeatureBundle) -> bool:
        c = self.config
        if c.pc_h24_max is not None and b.pc_h24 is not None and b.pc_h24 > c.pc_h24_max:
            return False
        if c.pc_h24_min is not None and b.pc_h24 is not None and b.pc_h24 < c.pc_h24_min:
            return False
        if c.pc_h1_max is not None and b.pc_h1 is not None and b.pc_h1 > c.pc_h1_max:
            return False
        if c.age_h_min is not None and b.age_hours < c.age_h_min:
            return False
        if c.age_h_max is not None and b.age_hours > c.age_h_max:
            return False
        if c.mcap_min is not None and b.mcap_usd < c.mcap_min:
            return False
        if c.mcap_max is not None and b.mcap_usd > c.mcap_max:
            return False
        if c.vol_h1_min is not None and (b.vol_h1_usd or 0) < c.vol_h1_min:
            return False
        # Dead-flatline reject (2026-06-02): a token below min_token_volatility_h24_pct
        # cannot MECHANICALLY produce the strategy's needed move (validated: 0 winners had
        # vol<5% in 22 days). Fail-OPEN when the feature is missing (coverage-safe; protects
        # young tokens lacking a 24h window).
        if c.min_token_volatility_h24_pct is not None:
            _vol = (b.raw_meta or {}).get("token_volatility_h24_pct")
            if (isinstance(_vol, (int, float)) and not isinstance(_vol, bool)
                    and _vol < c.min_token_volatility_h24_pct):
                return False
        # Range-floor reject (2026-06-03): trailing-90m high-low range below the floor =
        # flatlining/dead token. Held-out LOTO AUC 0.767; strict superset of the 5% vol
        # gate; 0.6% dollar winner-kill; TREND (90m range 35-95%) untouched. Fail-OPEN when
        # the feature is missing (token <90m old). REPLACES min_token_volatility_h24_pct.
        if c.min_shape_90m_range_pct is not None:
            _rng = (b.raw_meta or {}).get("shape_90m_range_pct")
            if (isinstance(_rng, (int, float)) and not isinstance(_rng, bool)
                    and _rng < c.min_shape_90m_range_pct):
                return False
        if c.require_real_pullback:
            # Held-out-validated entry-quality gate (2026-05-27): block EXTENDED
            # entries (the falling-knife signature behind the buy-into-downtrend
            # losses). Greens are bought after a REAL pullback on a LIVE token;
            # knives near the top of dead ones. Block if drawdown-from-90m-max
            # isn't deep enough OR h24 volatility is too low. Fail-OPEN when a
            # feature is missing (coverage-safe). Isolated to opt-in bots.
            _dd = b.raw_meta.get("shape_90m_drawdown_from_max_pct")
            _vol = b.raw_meta.get("token_volatility_h24_pct")
            if isinstance(_dd, (int, float)) and _dd > -7.5:
                return False
            if isinstance(_vol, (int, float)) and _vol < 30.0:
                return False
        if c.entry_gate:
            # Generic mined-compound gate: AND of [feature, op, threshold]
            # against raw_meta. Fail-OPEN per condition when feature missing.
            for _cond in c.entry_gate:
                try:
                    _f, _op, _thr = _cond[0], _cond[1], float(_cond[2])
                except (TypeError, ValueError, IndexError):
                    continue
                _v = b.raw_meta.get(_f)
                if not isinstance(_v, (int, float)):
                    continue
                if _op == ">=" and _v < _thr:
                    return False
                if _op == "<=" and _v > _thr:
                    return False
        return True

    def _rug_gate_blocks(self, b: FeatureBundle) -> bool:
        """Fleet-wide catastrophic-rug guard (2026-06-08). Applies to EVERY bot
        (not opt-in like the filter stack) — gated only by env RUG_GATE_MODE
        (off|shadow|enforce, default enforce). Logs every flagged entry in both
        shadow and enforce so blocks stay observable forward (Railway logs evaporate
        ~30min and a block leaves no trade record). See _rug_structure_blocks."""
        mode = _rug_gate_mode()
        if mode == "off":
            return False
        blocked, why = _rug_structure_blocks(b)
        if not blocked:
            return False
        enforced = (mode == "enforce")
        logger.info(
            f"[rug_gate] bot={self.config.bot_id} token={b.token} {why} "
            f"mode={mode} block={enforced}"
        )
        return enforced

    def _entry_stack_blocks(self, b: FeatureBundle) -> bool:
        """Fleet-wide validated entry-stack gate (2026-06-09). Applies to every
        bot on the dip path EXCEPT the control cohort (which stays ungated so
        the gated-vs-ungated counterfactual keeps being measured forward).
        Momentum-mode bots use a separate entry path and are not gated — the
        stack was validated on dip-style entries only.

        Evidence: bleed-week decomposition (28d, 18,439 closed) — gate-passing
        entries lost $395 across 11 bleed days vs fleet -$18,338; entry
        discipline alone removes ~98% of the bleed. See _entry_stack_violations.
        Env: ENTRY_STACK_MODE=off|shadow|enforce (default enforce),
        ENTRY_STACK_CONTROL_BOTS=csv (default baseline_v1,no_filters,
        pool_a_broad_control)."""
        mode = _entry_stack_mode()
        if mode == "off":
            return False
        if self.config.bot_id in _entry_stack_control_bots():
            return False
        fails = _entry_stack_violations(b)
        if not fails:
            return False
        enforced = (mode == "enforce")
        # Throttled observability: one INFO per (token, hour) fleet-wide.
        try:
            key = (b.token, int((b.snapshot_ts or 0) // 3600))
            if key not in _entry_stack_logged:
                if len(_entry_stack_logged) > 8000:
                    _entry_stack_logged.clear()
                _entry_stack_logged.add(key)
                logger.info(
                    f"[entry_stack] token={b.token} {';'.join(fails)} "
                    f"mode={mode} block={enforced} (1/token/hr)"
                )
        except Exception:
            pass
        return enforced

    def _effective_filter_blocks(self, b: FeatureBundle) -> bool:
        c = self.config
        if c.filters_enforced is None:
            disabled = set(c.filters_disabled)
            # Defender filters are OPT-IN only — excluded from default enforcement.
            # Existing bots with filters_enforced=None are unaffected by their addition.
            return any(
                f not in disabled and f not in DEFENDER_FILTERS
                for f in b.filters_block
            )
        enforced = set(c.filters_enforced)
        return any(f in enforced for f in b.filters_block)

    def _effective_triggers(self, b: FeatureBundle) -> tuple[str, ...]:
        c = self.config
        result = list(b.triggers_fired)

        # mcap_psych_level pc_h24 gate
        if (c.mcap_psych_pc_h24_max is not None
                and "mcap_psych_level" in result
                and b.pc_h24 is not None
                and b.pc_h24 >= c.mcap_psych_pc_h24_max):
            result = [t for t in result if t != "mcap_psych_level"]

        if c.triggers_allowed is not None:
            allow = set(c.triggers_allowed)
            result = [t for t in result if t in allow]

        if c.triggers_disabled:
            block = set(c.triggers_disabled)
            result = [t for t in result if t not in block]

        # Per-trigger token-state gates (2026-06-08): drop a FIRED trigger unless its
        # token-state conditions all pass against raw_meta. Fail-OPEN per condition when
        # the feature is missing (same convention as entry_gate, line ~302). Triggers
        # with no gate pass through ungated. A dropped trigger no longer counts toward
        # min_triggers_to_fire -> the bot only enters when a trigger fires IN the state
        # it was validated to win in. See reference_per_trigger_state_conditioning_2026_06_08.
        if c.trigger_state_gates:
            gates = {tg: conds for tg, conds in c.trigger_state_gates}
            kept = []
            for t in result:
                conds = gates.get(t)
                ok = True
                if conds:
                    for _cond in conds:
                        try:
                            _f, _op, _thr = _cond[0], _cond[1], float(_cond[2])
                        except (TypeError, ValueError, IndexError):
                            continue
                        _v = b.raw_meta.get(_f)
                        if not isinstance(_v, (int, float)):
                            continue  # fail-OPEN on missing feature
                        if _op == ">=" and _v < _thr:
                            ok = False
                            break
                        if _op == "<=" and _v > _thr:
                            ok = False
                            break
                if ok:
                    kept.append(t)
            result = kept

        return tuple(result)

    def _size_for(self, triggers: tuple[str, ...], b: FeatureBundle,
                  realized_pnl_usd: float = 0.0) -> tuple[float, str]:
        c = self.config
        is_alpha = bool(set(triggers) & ALPHA_TRIGGERS)
        # 1s_capit_reversal demoted from alpha at pc_h24 >= 80 (commit 9840ffe)
        if (
            "1s_capit_reversal" in triggers
            and b.pc_h24 is not None
            and b.pc_h24 >= 80.0
            and not (set(triggers) - {"1s_capit_reversal"}) & ALPHA_TRIGGERS
        ):
            is_alpha = False
        if is_alpha:
            base = c.base_position_usd * c.alpha_multiplier
            tier = "alpha_trigger"
        else:
            base = c.base_position_usd
            tier = "standard"
        # Honor premium_runner / marginal multipliers (2026-05-27 audit #6 — these
        # BotConfig fields were defined but never applied). Trigger-set based, so
        # impact is contained; bots that set them to 1.0 (e.g. cap2k) stay flat.
        # macro_up_multiplier is intentionally NOT wired: its legacy condition needs
        # sol_pc_m1, which the FeatureBundle doesn't carry — left explicit-N/A rather
        # than blindly applied to ~half the fleet's trades.
        if not is_alpha and _PREMIUM_RUNNER_TRIGGER in triggers:
            base *= c.premium_runner_multiplier
            tier = "premium_runner"
        elif not is_alpha and triggers and all(t in _MARGINAL_FOR_SIZE for t in triggers):
            base *= c.marginal_multiplier
            tier = "marginal"
        if c.compound_mode is not None:
            base = self._apply_compound(base, realized_pnl_usd)
            tier = f"{tier}+compound_{c.compound_mode}"
        if c.macro_conditional_mode is not None:
            base, macro_tag = self._apply_macro_conditional(base, b)
            tier = f"{tier}+{macro_tag}"
        if c.conviction_sizing_mode is not None:
            base, conv_tag = self._apply_conviction(base, triggers)
            tier = f"{tier}+{conv_tag}"
        return base, tier

    def _apply_conviction(self, base: float, triggers: tuple[str, ...]) -> tuple[float, str]:
        """Scale size by entry conviction. 'trigger_count' mode: more
        confluent triggers → bigger size, capped at conviction_max_mult."""
        c = self.config
        if c.conviction_sizing_mode == "trigger_count":
            n = len(triggers)
            mult = min(1.0 + c.conviction_step * max(0, n - 1), c.conviction_max_mult)
            return base * mult, f"conviction_x{mult:.2f}"
        return base, "conviction_off"

    def _apply_macro_conditional(self, base: float, b: FeatureBundle) -> tuple[float, str]:
        """Gradient sizing based on macro state. Currently supports 'sol_h6' mode:
        1.5x when sol_pc_h6 >= +0.3, 0.5x when sol_pc_h6 <= -0.1, 1.0x else.
        Other modes can be added later (btc, multi-asset, etc.)."""
        c = self.config
        if c.macro_conditional_mode == "sol_h6":
            sol = b.sol_pc_h6
            if sol is None:
                return base, "macro_neutral"
            if sol >= 0.3:
                return base * 1.5, "macro_bull"
            if sol <= -0.1:
                return base * 0.5, "macro_bear"
            return base, "macro_neutral"
        # Unknown mode → no-op
        return base, "macro_off"

    def _apply_compound(self, base: float, realized_pnl_usd: float) -> float:
        """Apply compounding multiplier per the bot's compound_mode.

        All modes are floored at 0.25x (never size below 25% of base, even
        on a brutal drawdown — lets the bot recover if it's right going
        forward) and capped at compound_max_multiplier (default 5x, prevents
        runaway growth from a single fluky win streak).
        """
        c = self.config
        starting = c.paper_capital_usd or 2000.0
        if c.compound_mode == "linear":
            mult = 1.0 + (realized_pnl_usd / starting)
        elif c.compound_mode == "winners_only":
            mult = 1.0 + (max(0.0, realized_pnl_usd) / starting)
        elif c.compound_mode == "threshold":
            # Step-additive: mult is computed against `base` so the formula
            # output stays in the same units the caller expects.
            steps = int(max(0.0, realized_pnl_usd) // c.compound_threshold_step_usd)
            if base <= 0:
                return base
            mult = 1.0 + (steps * c.compound_step_amount_usd) / base
        else:
            return base
        mult = max(0.25, min(mult, c.compound_max_multiplier))
        return base * mult
