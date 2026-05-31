"""Correlated-cluster sizing brake (#2 from the 2026-05-31 recommendation).

The fat left tail of fleet P&L is not the never-green duds — it's the fleet
SWARMING a single token: TinyWorld -$565 (~24-30 correlated entries), IDLE
-$1123, SPCX -$968, BULL -$806, RICH -$802. When many bots pile into one token,
a single token's death wipes out weeks of dud-savings.

Validated lever (scripts/correlated_swarm_validate.py, n=1942 closed): realized
EV degrades MONOTONICALLY with how many fleet bots already hold the token at
entry —
    swarm 0 solo  EV -1.10%  WR 45%
    swarm 1-4     EV -1.25%  WR 43%
    swarm 5-9     EV -2.43%  WR 42%
    swarm 10-19   EV -2.57%  WR 44%
    swarm 20+     EV -6.14%  WR 30%   <- catastrophic
High-swarm (>=5) entries carry loser$ -4114 vs winner$ +840 (~5:1) — so sizing
DOWN (not blocking) is the right response: it cuts the fat-tail bleed while
preserving the winner participation a hard block would kill (53 of the >=+10%
winners live in >=5-swarm). Targets SAME-TOKEN fleet concentration only —
orthogonal to the bot's own multi-token concurrency, which predicts winners
(reference_concurrent_positions_alpha).

Modes (env CLUSTER_BRAKE_MODE): off | shadow | enforce.
  off     — no computation.
  shadow  — compute holders + multiplier, log/stamp the WOULD-BE size, apply
            NOTHING (measure-only, fleet-wide; the default).
  enforce — apply the multiplier to size_usd, but only for bots whose config has
            cluster_brake_gate=True (the A/B cohort).
"""
from __future__ import annotations
import os


def cluster_brake_mode() -> str:
    m = os.environ.get("CLUSTER_BRAKE_MODE", "shadow").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "shadow"


def cluster_brake_multiplier(holders: int) -> float:
    """Position-size multiplier as a function of how many OTHER fleet bots
    already hold the token at entry. Calibrated to the realized EV-by-swarm
    buckets: leave solo/low-swarm untouched (normal -1.1% EV), brake hard on the
    20+ swarm (catastrophic -6.14% EV). Monotone non-increasing in holders.
    """
    if holders >= 20:
        return 0.30
    if holders >= 10:
        return 0.50
    if holders >= 5:
        return 0.60
    return 1.0


def fleet_holders(bot_position_managers: dict, token: str, exclude_bot: str | None = None) -> int:
    """Count how many bots in the fleet currently hold an open position in
    `token` (one position per token per bot). Excludes `exclude_bot` (the bot
    about to enter — it does not hold yet), so the count is the swarm the new
    entrant would be JOINING. Pure read over existing per-bot state; no new
    tracking structure.
    """
    n = 0
    for bid, pm in bot_position_managers.items():
        if bid == exclude_bot:
            continue
        try:
            if pm.get_position(token) is not None:
                n += 1
        except Exception:
            continue
    return n
