# core/watchlist_pruner.py
"""Auto-pruner for the user-curated watchlist (2026-06-03).

The user manually curates a watchlist of tokens to surface. Over time tokens on
it DIE (rug to near-zero, dry up to no volume, lose all liquidity) -- the base-rate
deep dive found ~70% of fresh grads round-trip to dust. A dead token on the
watchlist is wasted enrichment/egress and can still get bought on a fake-life
blip. This module decides which watchlist tokens are DEAD so the scanner can
auto-remove them (in-process, via remove_user_watchlist -- no dashboard auth path).

Liveness criterion (same family as the shipped range90 / dead-flatline gates):
a token is DEAD if it has rugged (mcap<=0 or liq<=0), is untradeable (liq below
floor), or has dried up (24h volume below floor). FAIL-OPEN: a token we could NOT
assess (no fresh data this cycle) is NEVER marked dead -- we only prune on positive
evidence of death. The scanner additionally requires N consecutive dead readings
(strikes) before removing, so a momentarily-quiet token is not pruned on one blip.

All functions are pure (env read at the edges) so they are unit-testable.
"""
from __future__ import annotations
import os
from typing import Optional


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _flag("WATCHLIST_AUTOPRUNE", "1")  # default ON


def min_vol_h24() -> float:
    try:
        return float(os.environ.get("WATCHLIST_PRUNE_MIN_VOL_H24", "25000"))
    except (TypeError, ValueError):
        return 25000.0


def min_liq() -> float:
    try:
        return float(os.environ.get("WATCHLIST_PRUNE_MIN_LIQ", "20000"))
    except (TypeError, ValueError):
        return 20000.0


def strikes_required() -> int:
    try:
        return max(1, int(os.environ.get("WATCHLIST_PRUNE_STRIKES", "3")))
    except (TypeError, ValueError):
        return 3


def interval_secs() -> float:
    try:
        return float(os.environ.get("WATCHLIST_PRUNE_INTERVAL_SECS", "1800"))  # 30 min
    except (TypeError, ValueError):
        return 1800.0


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def find_dead(tokens, min_vol: Optional[float] = None, min_liquidity: Optional[float] = None) -> list:
    """Return the addresses of DEAD tokens. tokens = list of dicts with keys
    address, liq_usd, vol_h24, mcap. FAIL-OPEN: a token whose liq AND vol are both
    unavailable is NOT marked dead (no evidence). Dead if rugged (mcap<=0 or liq<=0),
    untradeable (liq<floor), or dried up (vol_h24<floor)."""
    if min_vol is None:
        min_vol = min_vol_h24()
    if min_liquidity is None:
        min_liquidity = min_liq()
    dead = []
    for t in tokens:
        addr = t.get("address")
        if not addr:
            continue
        liq = _num(t.get("liq_usd"))
        vol = _num(t.get("vol_h24"))
        mc = _num(t.get("mcap"))
        if liq is None and vol is None:
            continue  # no evidence -> never prune
        # rugged / delisted
        if (mc is not None and mc <= 0) or (liq is not None and liq <= 0):
            dead.append(addr); continue
        # untradeable liquidity
        if liq is not None and liq < min_liquidity:
            dead.append(addr); continue
        # dried-up volume
        if vol is not None and vol < min_vol:
            dead.append(addr); continue
    return dead
