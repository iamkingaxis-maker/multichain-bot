from __future__ import annotations
import logging
from typing import Iterable
from core.bot_evaluator import BotEvaluator, BuyDecision
from core.feature_bundle import FeatureBundle


logger = logging.getLogger(__name__)


class BotManager:
    """Orchestrates fan-out of a FeatureBundle to all bot evaluators.

    Per-bot exceptions are caught, logged, and swallowed — one bot
    crashing must never affect the others.
    """

    def __init__(self, evaluators: Iterable[BotEvaluator]) -> None:
        self.evaluators: list[BotEvaluator] = list(evaluators)

    def evaluate_all(self, bundle: FeatureBundle,
                     realized_pnl_by_bot: dict[str, float] | None = None,
                     blocked_bot_ids: set[str] | None = None) -> list[BuyDecision]:
        """Evaluate all enabled bots against this bundle.

        ``realized_pnl_by_bot`` is an optional mapping from bot_id to current
        cumulative realized P&L. Bots configured with compound_mode use this
        to scale position size. Bots without compounding ignore it.

        ``blocked_bot_ids`` is an optional set of bot_ids to skip entirely this
        cycle — used by the daily-loss circuit-breaker (a bot that has hit its
        daily_loss_limit_usd opens no new positions until the UTC-day rollover).
        """
        realized = realized_pnl_by_bot or {}
        blocked = blocked_bot_ids or set()
        decisions: list[BuyDecision] = []
        for ev in self.evaluators:
            if not ev.config.enabled:
                continue
            if ev.config.bot_id in blocked:
                continue
            try:
                d = ev.evaluate(bundle, realized_pnl_usd=realized.get(ev.config.bot_id, 0.0))
                if d is not None:
                    decisions.append(d)
            except Exception as e:
                logger.error(
                    "[BotManager] bot=%s evaluate failed: %s",
                    ev.config.bot_id, e,
                    exc_info=True,
                )
                continue
        return decisions

    def enabled_bot_ids(self) -> list[str]:
        return [e.config.bot_id for e in self.evaluators if e.config.enabled]
