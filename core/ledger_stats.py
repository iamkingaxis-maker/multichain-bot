"""Ledger-derived per-bot stats — the leaderboard's sell aggregation, extracted
pure so ledger ROTATION (#496 memory cut) can be proven identity-preserving.

/api/leaderboard is the authoritative per-bot P&L (standing memory rule). Its
ledger-derived fields — total_pnl_realized, total_trades (positions), wins —
are computed here from the ACTIVE ledger's sells plus the per-bot aggregate
snapshot of rows rotated out to trades_multi_archive.jsonl
(ledger_rotation_stats.json, written at rotation time by
core/multi_bot_persistence.MultiBotTradeStore._rotate_ledger).

Rotation archives whole (bot_id, token) groups only (no position straddles),
so folding the archived aggregates is EXACT: totals are identical before and
after rotation (pinned by tests/test_ledger_rotation.py).
"""
from __future__ import annotations

from collections import defaultdict


def sell_stats(sells, archived: dict | None = None,
               reset_after_iso: str | None = None) -> tuple:
    """(total_pnl, n_positions, n_wins) for one bot.

    `sells` — the bot's sell rows AFTER the caller's population filters
    (MIN_TRADE_TIMESTAMP cutoff, 'cancelled on restart' skip, reset_after_iso)
    — exactly the list dashboard _build_bot_rows already builds.

    Positions aggregate sells by (token, entry_price) — one net outcome per
    position (TP1+TP2+trail legs merge), mirroring the 2026-05-27 audit #7 fix.

    `archived` — this bot's entry from ledger_rotation_stats.json ("pnl",
    "positions", "wins", "latest_time"), or None/{} when never rotated.

    `reset_after_iso` — the bot's dashboard re-baseline cutoff (bot_state).
    A reset newer than every archived row means the archived history is
    pre-reset: the fold is skipped, matching the un-rotated behavior where
    those rows would have been filtered out. (Rotation itself already excludes
    rows predating a reset that existed at rotation time.)
    """
    pos = defaultdict(float)
    for s in sells:
        pos[(s.get("token"), s.get("entry_price"))] += (s.get("pnl") or 0)
    total_pnl = sum(pos.values())
    n_positions = len(pos)
    n_wins = sum(1 for v in pos.values() if v > 0)

    a = archived or {}
    if a:
        skip = bool(reset_after_iso) and (
            str(reset_after_iso) > str(a.get("latest_time") or ""))
        if not skip:
            total_pnl += float(a.get("pnl") or 0.0)
            n_positions += int(a.get("positions") or 0)
            n_wins += int(a.get("wins") or 0)
    return total_pnl, n_positions, n_wins
