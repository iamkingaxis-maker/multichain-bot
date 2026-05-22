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

    def evaluate_all(self, bundle: FeatureBundle) -> list[BuyDecision]:
        decisions: list[BuyDecision] = []
        for ev in self.evaluators:
            if not ev.config.enabled:
                continue
            try:
                d = ev.evaluate(bundle)
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
