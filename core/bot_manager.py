from __future__ import annotations
import asyncio
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

    async def evaluate_all_async(
        self, bundle: FeatureBundle,
        realized_pnl_by_bot: dict[str, float] | None = None,
        bot_allowlist: set[str] | frozenset[str] | None = None,
        yield_every: int = 15,
    ) -> list[BuyDecision]:
        """Async, cooperatively-yielding twin of ``evaluate_all``.

        Returns the SAME decisions in the SAME order as the sync version — the
        ONLY added behaviour is an ``await asyncio.sleep(0)`` every ``yield_every``
        *evaluated* bots so the single event loop breathes between bursts (the
        70-bot fan-out is pure-Python CPU that otherwise contiguously starves the
        loop). The yield is GIL-correct because the loop actually runs other ready
        tasks (fills, dashboard, the next tick) during the sleep(0) — the bot eval
        itself is still synchronous Python.

        ``yield_every <= 0`` disables yielding (degrades to a sync-equivalent
        single pass). Per-bot exceptions are isolated exactly as in evaluate_all.
        """
        realized = realized_pnl_by_bot or {}
        decisions: list[BuyDecision] = []
        n_evaluated = 0
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
            n_evaluated += 1
            if yield_every > 0 and (n_evaluated % yield_every == 0):
                await asyncio.sleep(0)
        return decisions

    def has_momentum_bot(
        self, bot_allowlist: set[str] | frozenset[str] | None = None,
    ) -> bool:
        """True iff any ENABLED (and, if an allowlist is given, allowlisted) bot
        runs in momentum_mode.

        Momentum bots fire WITHOUT any dip trigger (they enter on the entry_gate),
        so the heavy-eval pre-screen must NOT skip a token just because no dip
        trigger fired when a momentum bot could still buy it. A disabled bot can
        never fire, so it never defeats the pre-screen.
        """
        for ev in self.evaluators:
            if not ev.config.enabled:
                continue
            if bot_allowlist is not None and ev.config.bot_id not in bot_allowlist:
                continue
            if getattr(ev.config, "momentum_mode", False):
                return True
        return False

    def enabled_bot_ids(self) -> list[str]:
        return [e.config.bot_id for e in self.evaluators if e.config.enabled]
