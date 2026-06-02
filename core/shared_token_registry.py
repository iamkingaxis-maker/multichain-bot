# core/shared_token_registry.py
"""Cross-bot no-same-token exclusion (pool de-concentration).

Bots sharing the same non-empty ``exclusion_pool`` (a string on BotConfig) may
not hold the same token concurrently — the first pool member to open a token
claims it; pool siblings are blocked from buying that token until it closes.

This is the de-concentration lever validated 2026-06-02: the fleet's bleed was
concentration (re-buying / piling into the same token), and spreading a pool of
bots across *distinct* simultaneous tokens roughly doubled the per-token edge.

Design: DERIVED / stateless. 'Held by the pool' is read live from each member's
position manager (``get_position(token)``), so closing a position auto-frees the
token — there is NO release bookkeeping and therefore no stuck-token / missed-
release bug. The position-managers dict is held by reference, so members added
after construction are seen.

Bots with ``exclusion_pool=None`` (the default) are NEVER blocked — existing
single-bot behavior is preserved untouched.
"""
from __future__ import annotations

from typing import Optional


def _pool_of_pm(pm) -> Optional[str]:
    return getattr(getattr(pm, "config", None), "exclusion_pool", None)


class SharedTokenRegistry:
    def __init__(self, position_managers: dict):
        # bot_id -> position manager. Must expose ``.config.exclusion_pool`` and
        # ``get_position(token)``. Held by reference (reflects live state).
        self._pms = position_managers

    def pool_for(self, bot_id: str) -> Optional[str]:
        pm = self._pms.get(bot_id)
        return _pool_of_pm(pm) if pm is not None else None

    def is_blocked(self, bot_id: str, token: str) -> bool:
        """True iff a DIFFERENT bot in the same exclusion pool currently holds
        ``token``. Bots not in a pool (or unknown bots) are never blocked."""
        pool = self.pool_for(bot_id)
        if not pool:
            return False
        for other_id, pm in self._pms.items():
            if other_id == bot_id:
                continue
            if _pool_of_pm(pm) != pool:
                continue
            if pm.get_position(token) is not None:
                return True
        return False

    def holder(self, token: str, pool: str) -> Optional[str]:
        """Return the bot_id in ``pool`` currently holding ``token``, else None."""
        if not pool:
            return None
        for bot_id, pm in self._pms.items():
            if _pool_of_pm(pm) != pool:
                continue
            if pm.get_position(token) is not None:
                return bot_id
        return None
