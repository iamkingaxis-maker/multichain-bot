"""
BreakoutDB — isolated SQLite store for the breakout strategy.

Lives at DATA_DIR/breakout.db (or the path passed to the constructor).
Schema created on first open. Callers are synchronous — use run_in_executor
from asyncio contexts if blocking becomes a concern (unlikely for v1).
"""

import os
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS breakout_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    qty REAL NOT NULL,
    cost_usd REAL NOT NULL,
    score INTEGER NOT NULL,
    score_breakdown TEXT,
    resistance_level REAL NOT NULL,
    tp_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    entry_candle_volume REAL NOT NULL,
    tp_hit INTEGER NOT NULL DEFAULT 0,
    peak_price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS breakout_closed_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    qty REAL NOT NULL,
    cost_usd REAL NOT NULL,
    proceeds_usd REAL NOT NULL,
    pnl_usd REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    score INTEGER NOT NULL,
    score_breakdown TEXT,
    reason_entry TEXT,
    reason_exit TEXT,
    fee_total_usd REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS breakout_cooldowns (
    symbol TEXT PRIMARY KEY,
    cooldown_until_ts TEXT NOT NULL,
    last_loss_pnl_usd REAL,
    last_loss_time TEXT
);
"""


class BreakoutDB:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self):
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def list_tables(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        return [r[0] for r in cur.fetchall()]

    # ── Positions ─────────────────────────────────────────────

    def insert_open_position(
        self, *, symbol, entry_time, entry_price, qty, cost_usd,
        score, score_breakdown, resistance_level, tp_price, stop_price,
        entry_candle_volume, peak_price,
    ) -> int:
        with self._conn:
            cur = self._conn.execute(
                """INSERT INTO breakout_positions (
                    symbol, entry_time, entry_price, qty, cost_usd, score,
                    score_breakdown, resistance_level, tp_price, stop_price,
                    entry_candle_volume, peak_price
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (symbol, entry_time, entry_price, qty, cost_usd, score,
                 score_breakdown, resistance_level, tp_price, stop_price,
                 entry_candle_volume, peak_price),
            )
        return cur.lastrowid

    def get_open_positions(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM breakout_positions ORDER BY entry_time ASC"
        )
        return [dict(r) for r in cur.fetchall()]

    def update_open_position(self, symbol: str, **fields) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [symbol]
        with self._conn:
            self._conn.execute(
                f"UPDATE breakout_positions SET {assignments} WHERE symbol = ?",
                values,
            )

    def close_position(
        self, *, symbol, exit_time, exit_price, proceeds_usd,
        pnl_usd, pnl_pct, reason_entry, reason_exit, fee_total_usd,
    ) -> None:
        row = self._conn.execute(
            "SELECT * FROM breakout_positions WHERE symbol = ?", (symbol,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No open position for {symbol}")
        with self._conn:
            self._conn.execute(
                """INSERT INTO breakout_closed_positions (
                    symbol, entry_time, exit_time, entry_price, exit_price,
                    qty, cost_usd, proceeds_usd, pnl_usd, pnl_pct,
                    score, score_breakdown, reason_entry, reason_exit,
                    fee_total_usd
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (symbol, row["entry_time"], exit_time,
                 row["entry_price"], exit_price, row["qty"], row["cost_usd"],
                 proceeds_usd, pnl_usd, pnl_pct, row["score"],
                 row["score_breakdown"], reason_entry, reason_exit,
                 fee_total_usd),
            )
            self._conn.execute(
                "DELETE FROM breakout_positions WHERE symbol = ?", (symbol,)
            )

    def get_closed_positions(self, limit: int = 100) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM breakout_closed_positions ORDER BY exit_time DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    # ── Cooldowns ──────────────────────────────────────────────

    def set_cooldown(
        self, symbol: str, cooldown_until_ts: str,
        last_loss_pnl_usd: float, last_loss_time: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """INSERT INTO breakout_cooldowns
                   (symbol, cooldown_until_ts, last_loss_pnl_usd, last_loss_time)
                   VALUES (?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     cooldown_until_ts = excluded.cooldown_until_ts,
                     last_loss_pnl_usd = excluded.last_loss_pnl_usd,
                     last_loss_time = excluded.last_loss_time""",
                (symbol, cooldown_until_ts, last_loss_pnl_usd, last_loss_time),
            )

    def get_cooldown(self, symbol: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM breakout_cooldowns WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    def is_in_cooldown(self, symbol: str, now_ts: str) -> bool:
        row = self.get_cooldown(symbol)
        if row is None:
            return False
        return now_ts < row["cooldown_until_ts"]
