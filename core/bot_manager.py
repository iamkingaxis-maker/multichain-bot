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
                     bot_allowlist: set[str] | frozenset[str] | None = None) -> list[BuyDecision]:
        """Evaluate all enabled bots against this bundle.

        ``realized_pnl_by_bot`` is an optional mapping from bot_id to current
        cumulative realized P&L. Bots configured with compound_mode use this
        to scale position size. Bots without compounding ignore it.

        (Per-bot daily-loss + per-token re-entry floors are enforced downstream
        in dip_scanner._execute_bot_buy via the Phase-1 risk-floor block.)

        ``bot_allowlist`` (optional): when not None, only bots whose bot_id is in
        the set are evaluated — used by the fast-watch loop to scope its fires.
        """
        realized = realized_pnl_by_bot or {}
        decisions: list[BuyDecision] = []
        for ev in self.evaluators:
            if not ev.config.enabled:
                continue
            if bot_allowlist is not None and ev.config.bot_id not in bot_allowlist:
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
