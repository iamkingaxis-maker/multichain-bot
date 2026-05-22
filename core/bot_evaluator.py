from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle


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
})


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

    def evaluate(self, b: FeatureBundle) -> Optional[BuyDecision]:
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

        size_usd, size_tier = self._size_for(effective_triggers, b)

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
        return True

    def _effective_filter_blocks(self, b: FeatureBundle) -> bool:
        c = self.config
        if c.filters_enforced is None:
            disabled = set(c.filters_disabled)
            return any(f not in disabled for f in b.filters_block)
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

        return tuple(result)

    def _size_for(self, triggers: tuple[str, ...], b: FeatureBundle) -> tuple[float, str]:
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
            return c.base_position_usd * c.alpha_multiplier, "alpha_trigger"
        return c.base_position_usd, "standard"
