# Breakout Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone CEX breakout trading strategy (Binance.US, paper-mode) per spec `docs/superpowers/specs/2026-04-17-breakout-strategy-design.md`. Ships dormant (`BREAKOUT_ENABLED=false`).

**Architecture:** New `breakout/` package — data client, scanner, scoring primitives, strategy (entry), execution (position management), capital manager, paper-fill engine, persistence, shared state. Isolated SQLite DB at `DATA_DIR/breakout.db`. Own dashboard section. Independent of Solana strategies, independent of `TRADING_PAUSED`.

**Tech Stack:** Python 3.12, asyncio, aiohttp (Binance.US REST), sqlite3, pytest + MagicMock, Flask (dashboard endpoints piggy-back on existing `WebDashboard`).

**Repo:** `C:\Users\jcole\multichain-bot` (Windows/Git Bash — use forward slashes in paths when possible, never `sed -i`).

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `utils/config.py` | Modify | Add `breakout_*` fields + env overrides |
| `breakout/__init__.py` | Create | Package marker |
| `breakout/capital.py` | Create | `BreakoutCapitalManager` — $2000 isolated pool |
| `breakout/scoring.py` | Create | Pure functions: `ema`, `breakout_strength_score`, `is_bearish_engulfing`, `has_upper_wick_rejection`, `volume_drop` |
| `breakout/paper_fill.py` | Create | `PaperFillEngine` — simulate buys/sells against book |
| `breakout/data_client.py` | Create | Binance.US REST client: 24h tickers, klines, order book |
| `breakout/database.py` | Create | SQLite schema + CRUD for `breakout_positions`, `breakout_closed_positions`, `breakout_cooldowns` |
| `breakout/state.py` | Create | `BreakoutState` shared container — watchlist, open positions, cooldowns, counters |
| `breakout/scanner.py` | Create | Build top-5 watchlist every 10 min |
| `breakout/strategy.py` | Create | Per-coin 30s poll, candle-close gate, entry evaluation |
| `breakout/execution.py` | Create | `enter()`, `manage_positions()` — TP/stop/trail/early-exit/max-hold |
| `dashboard/web_dashboard.py` | Modify | Register `/api/breakout/*` endpoints, register breakout module |
| `dashboard/templates/index.html` | Modify | Add breakout section (cards + tables) |
| `main.py` | Modify | Wire breakout tasks behind `BREAKOUT_ENABLED` |
| `test_breakout_capital.py` | Create | `BreakoutCapitalManager` unit tests |
| `test_breakout_scoring.py` | Create | Pure-function scoring tests |
| `test_breakout_paper_fill.py` | Create | Paper fill slippage + fee tests |
| `test_breakout_data_client.py` | Create | Data client tests (aiohttp mocked) |
| `test_breakout_database.py` | Create | Schema + CRUD tests (tmp_path DB) |
| `test_breakout_scanner.py` | Create | Scanner filter + ranking tests (data_client mocked) |
| `test_breakout_strategy.py` | Create | Candle-close + gate tests |
| `test_breakout_execution.py` | Create | Entry + exit path tests |
| `test_breakout_integration.py` | Create | End-to-end with mocks |

**Convention:** test files live at repo root (matches existing pattern — `test_scalp_capital.py`, `test_scalp_queue.py`).

**Running tests:** `python -m pytest <file> -v` (from `C:\Users\jcole\multichain-bot`). No `pytest.ini` — tests are discovered by default convention.

**Commit style (observed):** concise imperative (`add`, `fix`, `wire`), one-liners, no Co-Authored-By trailer in recent commits. Follow suit.

---

## Task 1: Config fields + env overrides

**Files:**
- Modify: `utils/config.py`
- Create: `test_breakout_config.py`

- [ ] **Step 1: Write the failing test**

```python
# test_breakout_config.py
import os
from unittest.mock import patch
from utils.config import Config, _load_env_overrides


def test_breakout_defaults():
    c = Config()
    assert c.breakout_enabled is False
    assert c.breakout_capital == 2000.0
    assert c.breakout_position_usd == 500.0
    assert c.breakout_max_concurrent == 4
    assert c.breakout_cooldown_minutes == 45.0
    assert c.breakout_min_score == 7
    assert c.breakout_tp_pct == 4.0
    assert c.breakout_tp_sell_pct == 0.50
    assert c.breakout_stop_pct == 3.0
    assert c.breakout_trail_pct == 2.0
    assert c.breakout_max_hold_hours == 4.0
    assert c.breakout_scan_interval_min == 10.0
    assert c.breakout_scan_top_n == 200
    assert c.breakout_min_vol_24h_usd == 50_000_000
    assert c.breakout_change_24h_min_pct == 3.0
    assert c.breakout_change_24h_max_pct == 15.0
    assert c.breakout_change_6h_max_pct == 12.0
    assert c.breakout_watchlist_size == 5
    assert c.breakout_poll_interval_sec == 30.0
    assert c.breakout_candle_close_delay_sec == 2.0
    assert c.breakout_paper_taker_fee == 0.006
    assert "USDT" in c.breakout_excluded_bases


def test_breakout_env_overrides():
    env = {
        "BREAKOUT_ENABLED": "true",
        "BREAKOUT_CAPITAL": "5000",
        "BREAKOUT_POSITION_USD": "1000",
        "BREAKOUT_MAX_CONCURRENT": "8",
        "BREAKOUT_MIN_SCORE": "6",
        "BREAKOUT_TP_PCT": "5.0",
        "BREAKOUT_STOP_PCT": "2.5",
    }
    with patch.dict(os.environ, env, clear=False):
        c = Config()
        _load_env_overrides(c)
        assert c.breakout_enabled is True
        assert c.breakout_capital == 5000.0
        assert c.breakout_position_usd == 1000.0
        assert c.breakout_max_concurrent == 8
        assert c.breakout_min_score == 6
        assert c.breakout_tp_pct == 5.0
        assert c.breakout_stop_pct == 2.5
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_config.py -v
```
Expected: `AttributeError: 'Config' object has no attribute 'breakout_enabled'`

Note: if `_load_env_overrides` is not a module-level symbol, read `utils/config.py` to locate the function that applies env overrides (it lives below the `_validate` function or inside the `load_config()` flow). Use its actual name. If env overrides happen inline inside `load_config()`, extract the breakout-specific block into a helper named `_load_env_overrides(config)` so the test can drive it directly.

- [ ] **Step 3: Add fields to `Config` dataclass**

In `utils/config.py`, immediately after the existing `# ── Scalp Queue ──` block (after the last `scalp_*` field, currently around `scalp_min_volume_h1_usd`), add:

```python
    # ── Breakout Strategy (Binance.US) ───────────────────────
    breakout_enabled: bool = False              # BREAKOUT_ENABLED — independent kill switch
    breakout_capital: float = 2000.0
    breakout_position_usd: float = 500.0
    breakout_max_concurrent: int = 4
    breakout_cooldown_minutes: float = 45.0
    breakout_min_score: int = 7
    # exits
    breakout_tp_pct: float = 4.0
    breakout_tp_sell_pct: float = 0.50
    breakout_stop_pct: float = 3.0
    breakout_trail_pct: float = 2.0
    breakout_max_hold_hours: float = 4.0
    # scanner / watchlist
    breakout_scan_interval_min: float = 10.0
    breakout_scan_top_n: int = 200
    breakout_min_vol_24h_usd: float = 50_000_000
    breakout_change_24h_min_pct: float = 3.0
    breakout_change_24h_max_pct: float = 15.0
    breakout_change_6h_max_pct: float = 12.0
    breakout_watchlist_size: int = 5
    breakout_excluded_bases: List[str] = field(
        default_factory=lambda: ["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "GUSD", "PYUSD"]
    )
    # poll / timing
    breakout_poll_interval_sec: float = 30.0
    breakout_candle_close_delay_sec: float = 2.0
    # paper fill
    breakout_paper_taker_fee: float = 0.006     # 0.6% Binance.US retail taker
```

- [ ] **Step 4: Add env overrides**

In `utils/config.py`, find the function that applies env overrides (the block with `if os.environ.get("SCALP_ENABLED")` around line 408). If the existing env-override code lives inside `load_config()`, refactor it into a module-level `_load_env_overrides(config)` helper first — then add the breakout block at the end of that helper:

```python
    # Breakout strategy
    if os.environ.get("BREAKOUT_ENABLED"):
        config.breakout_enabled = env_bool("BREAKOUT_ENABLED", config.breakout_enabled)
    if os.environ.get("BREAKOUT_CAPITAL"):
        config.breakout_capital = env_float("BREAKOUT_CAPITAL", config.breakout_capital)
    if os.environ.get("BREAKOUT_POSITION_USD"):
        config.breakout_position_usd = env_float("BREAKOUT_POSITION_USD", config.breakout_position_usd)
    if os.environ.get("BREAKOUT_MAX_CONCURRENT"):
        config.breakout_max_concurrent = env_int("BREAKOUT_MAX_CONCURRENT", config.breakout_max_concurrent)
    if os.environ.get("BREAKOUT_COOLDOWN_MINUTES"):
        config.breakout_cooldown_minutes = env_float("BREAKOUT_COOLDOWN_MINUTES", config.breakout_cooldown_minutes)
    if os.environ.get("BREAKOUT_MIN_SCORE"):
        config.breakout_min_score = env_int("BREAKOUT_MIN_SCORE", config.breakout_min_score)
    if os.environ.get("BREAKOUT_TP_PCT"):
        config.breakout_tp_pct = env_float("BREAKOUT_TP_PCT", config.breakout_tp_pct)
    if os.environ.get("BREAKOUT_TP_SELL_PCT"):
        config.breakout_tp_sell_pct = env_float("BREAKOUT_TP_SELL_PCT", config.breakout_tp_sell_pct)
    if os.environ.get("BREAKOUT_STOP_PCT"):
        config.breakout_stop_pct = env_float("BREAKOUT_STOP_PCT", config.breakout_stop_pct)
    if os.environ.get("BREAKOUT_TRAIL_PCT"):
        config.breakout_trail_pct = env_float("BREAKOUT_TRAIL_PCT", config.breakout_trail_pct)
    if os.environ.get("BREAKOUT_MAX_HOLD_HOURS"):
        config.breakout_max_hold_hours = env_float("BREAKOUT_MAX_HOLD_HOURS", config.breakout_max_hold_hours)
    if os.environ.get("BREAKOUT_SCAN_INTERVAL_MIN"):
        config.breakout_scan_interval_min = env_float("BREAKOUT_SCAN_INTERVAL_MIN", config.breakout_scan_interval_min)
    if os.environ.get("BREAKOUT_MIN_VOL_24H_USD"):
        config.breakout_min_vol_24h_usd = env_float("BREAKOUT_MIN_VOL_24H_USD", config.breakout_min_vol_24h_usd)
    if os.environ.get("BREAKOUT_PAPER_TAKER_FEE"):
        config.breakout_paper_taker_fee = env_float("BREAKOUT_PAPER_TAKER_FEE", config.breakout_paper_taker_fee)
```

- [ ] **Step 5: Run the tests to verify they pass**

```
python -m pytest test_breakout_config.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add utils/config.py test_breakout_config.py
git commit -m "add breakout config fields + env overrides"
```

---

## Task 2: BreakoutCapitalManager

Mirrors `ScalpCapitalManager`: isolated pool, concurrent-cap, close-releases-capital, realized P&L tracking. **No daily loss limit, no daily trade cap** (removed per user).

**Files:**
- Create: `breakout/__init__.py` (empty)
- Create: `breakout/capital.py`
- Create: `test_breakout_capital.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_capital.py
import pytest
from breakout.capital import BreakoutCapitalManager


def make_mgr(**kw):
    return BreakoutCapitalManager(**kw)


def test_has_capacity_initially_true():
    assert make_mgr().has_capacity(500.0) is True


def test_has_capacity_false_when_max_concurrent_reached():
    mgr = make_mgr(max_concurrent=2)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.reserve("ETHUSDT", 500.0)
    assert mgr.has_capacity(500.0) is False


def test_has_capacity_false_when_insufficient_funds():
    mgr = make_mgr(total_capital=1000.0, max_concurrent=10)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.reserve("ETHUSDT", 500.0)
    assert mgr.has_capacity(500.0) is False  # 0 available


def test_reserve_moves_from_available_to_deployed():
    mgr = make_mgr(total_capital=2000.0)
    mgr.reserve("BTCUSDT", 500.0)
    assert mgr.available_usd() == 1500.0
    assert mgr.deployed_usd() == 500.0


def test_release_returns_proceeds_and_accumulates_pnl():
    mgr = make_mgr(total_capital=2000.0)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.release("BTCUSDT", proceeds_usd=520.0, cost_usd=500.0)
    assert mgr.available_usd() == 2020.0
    assert mgr.deployed_usd() == 0.0
    assert mgr.realized_pnl() == 20.0


def test_release_negative_pnl():
    mgr = make_mgr(total_capital=2000.0)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.release("BTCUSDT", proceeds_usd=480.0, cost_usd=500.0)
    assert mgr.realized_pnl() == -20.0


def test_release_unknown_symbol_noop():
    mgr = make_mgr()
    mgr.release("NOPE", proceeds_usd=0.0, cost_usd=0.0)
    assert mgr.deployed_usd() == 0.0


def test_stats_dict():
    mgr = make_mgr(total_capital=2000.0, max_concurrent=4)
    mgr.reserve("BTCUSDT", 500.0)
    s = mgr.stats()
    assert s["total_capital"] == 2000.0
    assert s["available"] == 1500.0
    assert s["deployed"] == 500.0
    assert s["open_count"] == 1
    assert s["max_concurrent"] == 4
    assert s["realized_pnl"] == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_capital.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout'`

- [ ] **Step 3: Create the package + capital manager**

```python
# breakout/__init__.py
```
(empty file)

```python
# breakout/capital.py
"""
BreakoutCapitalManager — independent $2000 capital pool for the breakout strategy.

Completely separate from RiskManager and ScalpCapitalManager. Tracks deployed
capital, concurrent position count, and cumulative realized P&L. No daily loss
limit — risk is managed per-position (3% stop) and by max_concurrent cap.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class BreakoutCapitalManager:
    total_capital: float = 2000.0
    max_concurrent: int = 4

    _deployed: Dict[str, float] = field(default_factory=dict, init=False)
    _realized_pnl: float = field(default=0.0, init=False)

    def has_capacity(self, position_usd: float) -> bool:
        if len(self._deployed) >= self.max_concurrent:
            return False
        return self.available_usd() >= position_usd

    def reserve(self, symbol: str, position_usd: float) -> None:
        self._deployed[symbol] = position_usd

    def release(self, symbol: str, proceeds_usd: float, cost_usd: float) -> None:
        if symbol not in self._deployed:
            return
        del self._deployed[symbol]
        self._realized_pnl += proceeds_usd - cost_usd

    def available_usd(self) -> float:
        return self.total_capital - self.deployed_usd()

    def deployed_usd(self) -> float:
        return sum(self._deployed.values())

    def realized_pnl(self) -> float:
        return self._realized_pnl

    def stats(self) -> dict:
        return {
            "total_capital": self.total_capital,
            "available": self.available_usd(),
            "deployed": self.deployed_usd(),
            "open_count": len(self._deployed),
            "max_concurrent": self.max_concurrent,
            "realized_pnl": self._realized_pnl,
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_capital.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/__init__.py breakout/capital.py test_breakout_capital.py
git commit -m "add BreakoutCapitalManager"
```

---

## Task 3: Scoring primitives — EMA + Kline dataclass

Starting with the smallest unit. `ema()` + a `Kline` dataclass that the rest of the package will share.

**Files:**
- Create: `breakout/scoring.py`
- Create: `test_breakout_scoring.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_scoring.py
import pytest
from breakout.scoring import ema, Kline


def test_kline_fields():
    k = Kline(
        open_time=1000, open=100.0, high=110.0, low=95.0,
        close=105.0, volume=2000.0, close_time=1900,
    )
    assert k.close == 105.0
    assert k.volume == 2000.0


def test_ema_single_value():
    assert ema([5.0], period=3) == 5.0


def test_ema_period_longer_than_data_falls_back_to_sma():
    # With fewer points than period, return simple mean
    assert ema([1.0, 2.0, 3.0], period=10) == pytest.approx(2.0)


def test_ema_known_values():
    # EMA with period=3, alpha=2/(3+1)=0.5, seeded by SMA of first 3
    prices = [1.0, 2.0, 3.0, 4.0, 5.0]
    # SMA-seed: (1+2+3)/3 = 2.0
    # step 4: 2.0 + 0.5*(4.0-2.0) = 3.0
    # step 5: 3.0 + 0.5*(5.0-3.0) = 4.0
    assert ema(prices, period=3) == pytest.approx(4.0)


def test_ema_flat_series_returns_that_value():
    assert ema([10.0] * 50, period=20) == pytest.approx(10.0)


def test_ema_empty_raises():
    with pytest.raises(ValueError):
        ema([], period=20)
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_scoring.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.scoring'`

- [ ] **Step 3: Create `breakout/scoring.py`**

```python
"""
Pure-function scoring primitives for the breakout strategy.

All functions are stateless, deterministic, unit-testable in isolation.
No network, no DB, no logging.
"""

from dataclasses import dataclass


@dataclass
class Kline:
    """Binance klines row, strongly typed."""
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


def ema(prices: list[float], period: int) -> float:
    """
    EMA of the last-N prices, seeded by SMA of the first `period` values.
    If `prices` is shorter than `period`, falls back to the simple mean.
    """
    if not prices:
        raise ValueError("ema() requires at least one price")
    n = len(prices)
    if n < period:
        return sum(prices) / n
    alpha = 2.0 / (period + 1)
    # seed with SMA of first `period` values
    seed = sum(prices[:period]) / period
    value = seed
    for p in prices[period:]:
        value = value + alpha * (p - value)
    return value
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_scoring.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/scoring.py test_breakout_scoring.py
git commit -m "add breakout scoring: Kline + ema"
```

---

## Task 4: `breakout_strength_score` + early-exit primitives

**Files:**
- Modify: `breakout/scoring.py`
- Modify: `test_breakout_scoring.py`

- [ ] **Step 1: Add failing tests**

Append to `test_breakout_scoring.py`:

```python
from breakout.scoring import (
    breakout_strength_score,
    is_bearish_engulfing,
    has_upper_wick_rejection,
    volume_drop,
)


def _k(o, h, l, c, v):
    return Kline(open_time=0, open=o, high=h, low=l, close=c, volume=v, close_time=0)


# ── breakout_strength_score ─────────────────────────────────────────

def test_score_max_ten():
    # Volume 2x avg (+3), body 0.8 (+2), breakout 0.6% (+2),
    # trend sep 1.5% (+2), tight consolidation 0.5% (+1) = 10
    candle = _k(100.0, 102.0, 99.8, 101.6, 2000.0)
    score, breakdown = breakout_strength_score(
        candle=candle,
        avg_volume_20=1000.0,
        resistance=101.0,      # breakout size = (101.6 - 101.0) / 101.0 = 0.59%
        ema50_1h=100.0,        # trend sep = (101.6 - 100.0) / 100.0 = 1.6%
        ema200_1h=98.0,
        consolidation_range=0.5,  # 0.5 / 101.0 = 0.49% → tight
    )
    assert score == 10
    assert breakdown["volume"] == 3
    assert breakdown["body"] == 2
    assert breakdown["breakout_size"] == 2
    assert breakdown["trend"] == 2
    assert breakdown["structure"] == 1


def test_score_zero_when_nothing_qualifies():
    candle = _k(100.0, 101.0, 99.0, 100.0, 500.0)
    score, _ = breakout_strength_score(
        candle=candle,
        avg_volume_20=1000.0,      # current vol below avg → 0
        resistance=101.0,          # close not above resistance → 0
        ema50_1h=105.0,            # price below ema50 → 0
        ema200_1h=100.0,
        consolidation_range=5.0,   # wide consolidation → 0
    )
    assert score == 0


def test_score_volume_tiers():
    base = dict(resistance=100.0, ema50_1h=100.0, ema200_1h=95.0, consolidation_range=10.0)
    # 1.0x → +1
    c1 = _k(100.0, 100.5, 100.0, 100.2, 1000.0)
    s1, _ = breakout_strength_score(candle=c1, avg_volume_20=1000.0, **base)
    # 1.2x → +2
    c2 = _k(100.0, 100.5, 100.0, 100.2, 1200.0)
    s2, _ = breakout_strength_score(candle=c2, avg_volume_20=1000.0, **base)
    # 1.5x → +3
    c3 = _k(100.0, 100.5, 100.0, 100.2, 1500.0)
    s3, _ = breakout_strength_score(candle=c3, avg_volume_20=1000.0, **base)
    assert s1 < s2 < s3


def test_score_handles_zero_range_candle():
    # high == low → avoid divide-by-zero in body_ratio
    candle = _k(100.0, 100.0, 100.0, 100.0, 1500.0)
    score, _ = breakout_strength_score(
        candle=candle, avg_volume_20=1000.0, resistance=100.0,
        ema50_1h=100.0, ema200_1h=95.0, consolidation_range=0.5,
    )
    # Must not crash; body score = 0 when no range.
    assert isinstance(score, int)


# ── is_bearish_engulfing ─────────────────────────────────────────────

def test_bearish_engulfing_true():
    prev = _k(100.0, 102.0, 99.5, 101.5, 1000.0)  # green
    curr = _k(101.8, 102.0, 98.0, 99.0, 1500.0)   # red, engulfs
    assert is_bearish_engulfing(prev, curr) is True


def test_bearish_engulfing_false_when_curr_green():
    prev = _k(100.0, 102.0, 99.5, 101.5, 1000.0)
    curr = _k(101.5, 103.0, 101.0, 102.5, 1500.0)
    assert is_bearish_engulfing(prev, curr) is False


def test_bearish_engulfing_false_when_prev_red():
    prev = _k(102.0, 102.5, 100.0, 100.5, 1000.0)
    curr = _k(100.5, 101.0, 99.0, 99.5, 1500.0)
    assert is_bearish_engulfing(prev, curr) is False


def test_bearish_engulfing_false_when_not_engulfed():
    prev = _k(100.0, 102.0, 99.5, 101.5, 1000.0)
    # curr body 101.3→100.2 does not cover prev body 100.0→101.5
    curr = _k(101.3, 101.5, 100.0, 100.2, 1500.0)
    assert is_bearish_engulfing(prev, curr) is False


# ── has_upper_wick_rejection ─────────────────────────────────────────

def test_upper_wick_rejection_detected():
    # upper wick = 1.8 of total range 2.0 → 90%
    candle = _k(100.0, 102.0, 99.9, 100.2, 1000.0)
    assert has_upper_wick_rejection(candle, threshold=0.6) is True


def test_upper_wick_rejection_not_detected():
    # small upper wick
    candle = _k(100.0, 100.3, 99.5, 100.2, 1000.0)
    assert has_upper_wick_rejection(candle, threshold=0.6) is False


def test_upper_wick_rejection_zero_range_returns_false():
    candle = _k(100.0, 100.0, 100.0, 100.0, 1000.0)
    assert has_upper_wick_rejection(candle) is False


# ── volume_drop ──────────────────────────────────────────────────────

def test_volume_drop_detected():
    assert volume_drop(current_vol=400.0, baseline_vol=1000.0, threshold=0.5) is True


def test_volume_drop_not_detected():
    assert volume_drop(current_vol=800.0, baseline_vol=1000.0, threshold=0.5) is False


def test_volume_drop_zero_baseline_returns_false():
    assert volume_drop(current_vol=0.0, baseline_vol=0.0) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_scoring.py -v
```
Expected: ImportError for `breakout_strength_score` etc.

- [ ] **Step 3: Add functions to `breakout/scoring.py`**

Append:

```python
def breakout_strength_score(
    *,
    candle: Kline,
    avg_volume_20: float,
    resistance: float,
    ema50_1h: float,
    ema200_1h: float,
    consolidation_range: float,
) -> tuple[int, dict]:
    """
    Returns (total_score 0-10, breakdown dict for logging).

    Per spec:
      - Volume expansion (0-3):  >=1.5x avg → +3, >=1.2x → +2, >=1.0x → +1
      - Candle body (0-2):       body_ratio > 0.7 → +2, > 0.5 → +1
      - Breakout size (0-2):     > 0.5% → +2, > 0.2% → +1
      - Trend sep (0-2):         > 1% above ema50 → +2, > 0 → +1
      - Clean structure (0-1):   consolidation_range/resistance < 1% → +1

    All gates are independent; final sum clipped at [0, 10].
    """
    # Volume
    if avg_volume_20 > 0:
        vol_ratio = candle.volume / avg_volume_20
    else:
        vol_ratio = 0.0
    if vol_ratio >= 1.5:
        vol_score = 3
    elif vol_ratio >= 1.2:
        vol_score = 2
    elif vol_ratio >= 1.0:
        vol_score = 1
    else:
        vol_score = 0

    # Body ratio
    candle_range = candle.high - candle.low
    if candle_range > 0:
        body_ratio = abs(candle.close - candle.open) / candle_range
    else:
        body_ratio = 0.0
    if body_ratio > 0.7:
        body_score = 2
    elif body_ratio > 0.5:
        body_score = 1
    else:
        body_score = 0

    # Breakout size
    if resistance > 0 and candle.close > resistance:
        breakout_pct = (candle.close - resistance) / resistance
    else:
        breakout_pct = 0.0
    if breakout_pct > 0.005:
        break_score = 2
    elif breakout_pct > 0.002:
        break_score = 1
    else:
        break_score = 0

    # Trend sep: only credit if price above ema50 AND ema50 > ema200
    if ema50_1h > 0 and candle.close > ema50_1h and ema50_1h > ema200_1h:
        trend_sep = (candle.close - ema50_1h) / ema50_1h
        if trend_sep > 0.01:
            trend_score = 2
        elif trend_sep > 0:
            trend_score = 1
        else:
            trend_score = 0
    else:
        trend_score = 0

    # Clean structure
    if resistance > 0:
        struct_ratio = consolidation_range / resistance
    else:
        struct_ratio = 1.0
    struct_score = 1 if struct_ratio < 0.01 else 0

    total = vol_score + body_score + break_score + trend_score + struct_score
    total = max(0, min(10, total))
    breakdown = {
        "volume": vol_score,
        "body": body_score,
        "breakout_size": break_score,
        "trend": trend_score,
        "structure": struct_score,
        "total": total,
    }
    return total, breakdown


def is_bearish_engulfing(prev: Kline, curr: Kline) -> bool:
    """Prev green, curr red, curr body engulfs prev body."""
    prev_green = prev.close > prev.open
    curr_red = curr.close < curr.open
    if not (prev_green and curr_red):
        return False
    # curr body covers prev body
    return curr.open >= prev.close and curr.close <= prev.open


def has_upper_wick_rejection(candle: Kline, threshold: float = 0.6) -> bool:
    """Upper wick > `threshold` of total range signals rejection."""
    r = candle.high - candle.low
    if r <= 0:
        return False
    body_top = max(candle.open, candle.close)
    upper_wick = candle.high - body_top
    return (upper_wick / r) > threshold


def volume_drop(current_vol: float, baseline_vol: float, threshold: float = 0.5) -> bool:
    """current_vol < threshold * baseline_vol."""
    if baseline_vol <= 0:
        return False
    return current_vol < threshold * baseline_vol
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_scoring.py -v
```
Expected: all tests pass (6 from Task 3 + 13 new = 19).

- [ ] **Step 5: Commit**

```bash
git add breakout/scoring.py test_breakout_scoring.py
git commit -m "add breakout scoring: strength score + engulfing + wick + volume drop"
```

---

## Task 5: PaperFillEngine

Taker-only paper fills with fixed Binance.US fee (0.6%) and book-derived slippage.

**Files:**
- Create: `breakout/paper_fill.py`
- Create: `test_breakout_paper_fill.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_paper_fill.py
import pytest
from unittest.mock import AsyncMock
from breakout.paper_fill import PaperFillEngine, Fill


@pytest.fixture
def book():
    # price, qty (Binance REST format: strings)
    return {
        "bids": [["99.90", "100"], ["99.80", "200"], ["99.70", "300"]],
        "asks": [["100.10", "100"], ["100.20", "200"], ["100.30", "300"]],
    }


@pytest.mark.asyncio
async def test_simulate_buy_at_ask_minus_fee(book):
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value=book)
    engine = PaperFillEngine(client, taker_fee=0.006)
    fill = await engine.simulate_buy("BTCUSDT", usd_amount=100.0)
    # With tiny usd_amount vs book depth, slippage impact is ~0
    assert isinstance(fill, Fill)
    assert fill.symbol == "BTCUSDT"
    assert fill.price == pytest.approx(100.10, rel=1e-3)
    # fee is part of usd_amount: qty = (usd_amount * (1-fee)) / fill_price
    expected_qty = (100.0 * (1 - 0.006)) / 100.10
    assert fill.qty == pytest.approx(expected_qty, rel=1e-3)
    assert fill.fee_usd == pytest.approx(100.0 * 0.006, rel=1e-3)
    assert fill.side == "buy"


@pytest.mark.asyncio
async def test_simulate_sell_at_bid_minus_fee(book):
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value=book)
    engine = PaperFillEngine(client, taker_fee=0.006)
    fill = await engine.simulate_sell("BTCUSDT", qty=1.0)
    assert fill.price == pytest.approx(99.90, rel=1e-3)
    # gross = qty * bid; proceeds = gross * (1 - fee)
    gross = 1.0 * 99.90
    assert fill.usd_proceeds == pytest.approx(gross * (1 - 0.006), rel=1e-3)
    assert fill.fee_usd == pytest.approx(gross * 0.006, rel=1e-3)
    assert fill.side == "sell"


@pytest.mark.asyncio
async def test_simulate_buy_applies_book_walk_slippage():
    # Large order consumes multiple levels — fill price is vwap
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value={
        "bids": [["99.90", "10"]],
        "asks": [["100.10", "10"], ["100.20", "10"], ["100.30", "10"]],
    })
    engine = PaperFillEngine(client, taker_fee=0.0)  # zero fee to isolate slippage
    # usd = 3000 → needs ~30 units across 3 levels:
    #   10 @ 100.10, 10 @ 100.20, 10 @ 100.30 → gross ~ 3006 → qty ~ 29.95
    fill = await engine.simulate_buy("BTCUSDT", usd_amount=3000.0)
    assert fill.price > 100.10
    assert fill.price < 100.30


@pytest.mark.asyncio
async def test_simulate_buy_empty_book_raises():
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value={"bids": [], "asks": []})
    engine = PaperFillEngine(client, taker_fee=0.006)
    with pytest.raises(ValueError):
        await engine.simulate_buy("BTCUSDT", usd_amount=100.0)
```

**Note:** this test file uses `@pytest.mark.asyncio`. If `pytest-asyncio` isn't installed, install it:
```
pip install pytest-asyncio
```
Add `asyncio_mode = "auto"` to `pytest.ini` if it exists, otherwise the `@pytest.mark.asyncio` decorator drives each test individually — which is what we're using here.

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_paper_fill.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.paper_fill'`

- [ ] **Step 3: Create `breakout/paper_fill.py`**

```python
"""
PaperFillEngine — simulate market orders against the live order book.

Walks the book to derive VWAP fill price for the requested size, then applies
a fixed taker fee. No network I/O itself — takes a data_client with
`fetch_order_book(symbol, depth)`.
"""

import time
from dataclasses import dataclass


@dataclass
class Fill:
    symbol: str
    side: str           # "buy" | "sell"
    price: float        # VWAP
    qty: float
    usd_cost: float = 0.0       # buy: gross usd (incl fee); sell: 0
    usd_proceeds: float = 0.0   # sell: net usd after fee; buy: 0
    fee_usd: float = 0.0
    timestamp: float = 0.0


class PaperFillEngine:
    def __init__(self, data_client, taker_fee: float = 0.006):
        self.data_client = data_client
        self.taker_fee = taker_fee

    async def simulate_buy(self, symbol: str, usd_amount: float) -> Fill:
        book = await self.data_client.fetch_order_book(symbol, depth=10)
        asks = book.get("asks") or []
        if not asks:
            raise ValueError(f"No asks in order book for {symbol}")

        # Budget: fee is withheld from usd_amount up front → spendable = usd * (1-fee)
        spendable = usd_amount * (1 - self.taker_fee)
        fee_usd = usd_amount * self.taker_fee

        # Walk asks accumulating qty until spendable is exhausted
        remaining_usd = spendable
        total_qty = 0.0
        total_cost = 0.0
        for price_s, qty_s in asks:
            price = float(price_s)
            level_qty = float(qty_s)
            level_cost = price * level_qty
            if remaining_usd <= level_cost:
                take_qty = remaining_usd / price
                total_qty += take_qty
                total_cost += take_qty * price
                remaining_usd = 0.0
                break
            total_qty += level_qty
            total_cost += level_cost
            remaining_usd -= level_cost
        else:
            # Walked entire book without filling — use what we got
            pass

        vwap = total_cost / total_qty if total_qty > 0 else float(asks[0][0])
        return Fill(
            symbol=symbol,
            side="buy",
            price=vwap,
            qty=total_qty,
            usd_cost=usd_amount,
            fee_usd=fee_usd,
            timestamp=time.time(),
        )

    async def simulate_sell(self, symbol: str, qty: float) -> Fill:
        book = await self.data_client.fetch_order_book(symbol, depth=10)
        bids = book.get("bids") or []
        if not bids:
            raise ValueError(f"No bids in order book for {symbol}")

        remaining_qty = qty
        total_gross = 0.0
        total_filled = 0.0
        for price_s, qty_s in bids:
            price = float(price_s)
            level_qty = float(qty_s)
            take = min(remaining_qty, level_qty)
            total_gross += take * price
            total_filled += take
            remaining_qty -= take
            if remaining_qty <= 0:
                break

        fee_usd = total_gross * self.taker_fee
        proceeds = total_gross - fee_usd
        vwap = total_gross / total_filled if total_filled > 0 else float(bids[0][0])
        return Fill(
            symbol=symbol,
            side="sell",
            price=vwap,
            qty=total_filled,
            usd_proceeds=proceeds,
            fee_usd=fee_usd,
            timestamp=time.time(),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_paper_fill.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/paper_fill.py test_breakout_paper_fill.py
git commit -m "add breakout PaperFillEngine (book-walked VWAP fills)"
```

---

## Task 6: Data client (Binance.US REST)

Public endpoints only — no auth needed for paper mode. `aiohttp.ClientSession` injected for testability.

**Files:**
- Create: `breakout/data_client.py`
- Create: `test_breakout_data_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_data_client.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.data_client import BinanceUSClient, parse_klines
from breakout.scoring import Kline


def test_parse_klines():
    raw = [
        [1000, "100.0", "102.0", "99.5", "101.5", "1234.5", 1899, "...", 0, "...", "...", "0"],
        [1900, "101.5", "103.0", "100.0", "102.5", "2000.0", 2799, "...", 0, "...", "...", "0"],
    ]
    klines = parse_klines(raw)
    assert len(klines) == 2
    assert isinstance(klines[0], Kline)
    assert klines[0].open_time == 1000
    assert klines[0].open == 100.0
    assert klines[0].close == 101.5
    assert klines[0].volume == 1234.5
    assert klines[0].close_time == 1899


@pytest.mark.asyncio
async def test_fetch_24h_tickers_calls_right_url():
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response([{"symbol": "BTCUSDT"}]))
    client = BinanceUSClient(session=session)
    result = await client.fetch_24h_tickers()
    assert result == [{"symbol": "BTCUSDT"}]
    session.get.assert_called_once()
    assert "api.binance.us/api/v3/ticker/24hr" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_fetch_klines_parses_into_kline_list():
    raw = [[1000, "100.0", "102.0", "99.0", "101.0", "500.0", 1899, "...", 0, "...", "...", "0"]]
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(raw))
    client = BinanceUSClient(session=session)
    result = await client.fetch_klines("BTCUSDT", interval="15m", limit=20)
    assert len(result) == 1
    assert isinstance(result[0], Kline)
    assert "BTCUSDT" in session.get.call_args[0][0]
    assert "15m" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_fetch_order_book_returns_payload():
    payload = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(payload))
    client = BinanceUSClient(session=session)
    result = await client.fetch_order_book("BTCUSDT", depth=5)
    assert result == payload


# ── helper ─────────────────────────────────────────────────

class _MockResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        pass


def _mock_response(payload):
    return _MockResp(payload)
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_data_client.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.data_client'`

- [ ] **Step 3: Create `breakout/data_client.py`**

```python
"""
BinanceUSClient — public REST endpoints (api.binance.us).

No auth. Rate-limit note: we sit well under 1200 WEIGHT/min budget.
Errors: raise for HTTP 4xx; callers can catch and count.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

from breakout.scoring import Kline

logger = logging.getLogger(__name__)

_BASE = "https://api.binance.us/api/v3"


def parse_klines(raw: list) -> list[Kline]:
    """Binance klines array → list[Kline]. Fields are strings in REST."""
    out = []
    for row in raw:
        out.append(Kline(
            open_time=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=int(row[6]),
        ))
    return out


class BinanceUSClient:
    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_24h_tickers(self) -> list[dict]:
        """GET /ticker/24hr (no symbol → all symbols)."""
        url = f"{_BASE}/ticker/24hr"
        async with (await self._get_session()).get(url) as r:
            r.raise_for_status()
            return await r.json()

    async def fetch_klines(
        self, symbol: str, interval: str = "15m", limit: int = 100
    ) -> list[Kline]:
        url = f"{_BASE}/klines?symbol={symbol}&interval={interval}&limit={limit}"
        async with (await self._get_session()).get(url) as r:
            r.raise_for_status()
            raw = await r.json()
        return parse_klines(raw)

    async def fetch_order_book(self, symbol: str, depth: int = 10) -> dict:
        url = f"{_BASE}/depth?symbol={symbol}&limit={depth}"
        async with (await self._get_session()).get(url) as r:
            r.raise_for_status()
            return await r.json()
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_data_client.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/data_client.py test_breakout_data_client.py
git commit -m "add breakout BinanceUSClient (public REST)"
```

---

## Task 7: Persistence (SQLite)

Isolated `breakout.db` at `DATA_DIR/breakout.db`. Creates schema at import time.

**Files:**
- Create: `breakout/database.py`
- Create: `test_breakout_database.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_database.py
import os
import pytest
from breakout.database import BreakoutDB


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "breakout_test.db")
    return BreakoutDB(path)


def test_schema_creates_all_tables(db):
    tables = db.list_tables()
    assert "breakout_positions" in tables
    assert "breakout_closed_positions" in tables
    assert "breakout_cooldowns" in tables


def test_insert_and_get_open_position(db):
    pos_id = db.insert_open_position(
        symbol="BTCUSDT",
        entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0,
        qty=5.0,
        cost_usd=500.0,
        score=8,
        score_breakdown='{"volume":3,"body":2}',
        resistance_level=99.5,
        tp_price=104.0,
        stop_price=97.0,
        entry_candle_volume=1234.0,
        peak_price=100.0,
    )
    assert pos_id > 0
    rows = db.get_open_positions()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["score"] == 8
    assert rows[0]["tp_hit"] == 0


def test_duplicate_symbol_rejected(db):
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_open_position(
            symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
            entry_price=101.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
            resistance_level=99.5, tp_price=104.0, stop_price=97.0,
            entry_candle_volume=1234.0, peak_price=100.0,
        )


def test_update_open_position(db):
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    db.update_open_position("BTCUSDT", peak_price=105.0, tp_hit=1)
    row = db.get_open_positions()[0]
    assert row["peak_price"] == 105.0
    assert row["tp_hit"] == 1


def test_close_position_moves_to_closed_table(db):
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    db.close_position(
        symbol="BTCUSDT",
        exit_time="2026-04-17T14:00:00Z",
        exit_price=104.0,
        proceeds_usd=520.0,
        pnl_usd=20.0,
        pnl_pct=4.0,
        reason_entry="score=8 breakout",
        reason_exit="tp1",
        fee_total_usd=3.0,
    )
    assert db.get_open_positions() == []
    closed = db.get_closed_positions()
    assert len(closed) == 1
    assert closed[0]["pnl_usd"] == 20.0
    assert closed[0]["reason_exit"] == "tp1"


def test_cooldown_set_and_query(db):
    db.set_cooldown("BTCUSDT", cooldown_until_ts="2026-04-17T15:00:00Z",
                    last_loss_pnl_usd=-15.0, last_loss_time="2026-04-17T14:00:00Z")
    assert db.is_in_cooldown("BTCUSDT", now_ts="2026-04-17T14:30:00Z") is True
    assert db.is_in_cooldown("BTCUSDT", now_ts="2026-04-17T15:30:00Z") is False


def test_cooldown_overwrites_previous(db):
    db.set_cooldown("BTCUSDT", "2026-04-17T14:00:00Z", -10.0, "2026-04-17T13:15:00Z")
    db.set_cooldown("BTCUSDT", "2026-04-17T16:00:00Z", -20.0, "2026-04-17T15:15:00Z")
    row = db.get_cooldown("BTCUSDT")
    assert row["cooldown_until_ts"] == "2026-04-17T16:00:00Z"
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_database.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.database'`

- [ ] **Step 3: Create `breakout/database.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_database.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/database.py test_breakout_database.py
git commit -m "add breakout SQLite persistence (positions + cooldowns)"
```

---

## Task 8: BreakoutState shared container

Mutable container shared across scanner, strategy, and execution tasks. In-memory — DB is the durable mirror.

**Files:**
- Create: `breakout/state.py`
- Create: `test_breakout_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_state.py
from breakout.state import BreakoutState, BreakoutPosition


def test_new_state_empty():
    s = BreakoutState()
    assert s.watchlist == []
    assert s.open_positions == {}
    assert s.last_seen_close == {}
    assert s.scan_counters == {}


def test_set_watchlist_replaces():
    s = BreakoutState()
    s.set_watchlist(["BTCUSDT", "ETHUSDT"])
    assert s.watchlist == ["BTCUSDT", "ETHUSDT"]
    s.set_watchlist(["SOLUSDT"])
    assert s.watchlist == ["SOLUSDT"]


def test_add_and_remove_open_position():
    s = BreakoutState()
    pos = BreakoutPosition(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0,
        score=8, resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    s.open_positions[pos.symbol] = pos
    assert "BTCUSDT" in s.open_positions
    del s.open_positions["BTCUSDT"]
    assert "BTCUSDT" not in s.open_positions


def test_position_tp_hit_defaults_false():
    pos = BreakoutPosition(
        symbol="X", entry_time="t", entry_price=1.0, qty=1.0, cost_usd=1.0,
        score=7, resistance_level=1.0, tp_price=1.04, stop_price=0.97,
        entry_candle_volume=1.0, peak_price=1.0,
    )
    assert pos.tp_hit is False


def test_bump_scan_counter():
    s = BreakoutState()
    s.bump("gate_no_breakout")
    s.bump("gate_no_breakout")
    s.bump("gate_score_too_low")
    assert s.scan_counters["gate_no_breakout"] == 2
    assert s.scan_counters["gate_score_too_low"] == 1


def test_reset_scan_counters():
    s = BreakoutState()
    s.bump("x")
    s.reset_scan_counters()
    assert s.scan_counters == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_state.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.state'`

- [ ] **Step 3: Create `breakout/state.py`**

```python
"""
BreakoutState — in-memory shared container.

Holds the live watchlist (set by scanner, read by strategy),
open positions (written by execution), last-seen candle close times
(strategy uses for edge detection), and a rolling counter dict
(diagnostic logging).
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class BreakoutPosition:
    symbol: str
    entry_time: str
    entry_price: float
    qty: float
    cost_usd: float
    score: int
    resistance_level: float
    tp_price: float
    stop_price: float
    entry_candle_volume: float
    peak_price: float
    tp_hit: bool = False
    score_breakdown: dict = field(default_factory=dict)
    reason_entry: str = ""


@dataclass
class BreakoutState:
    watchlist: List[str] = field(default_factory=list)
    open_positions: Dict[str, BreakoutPosition] = field(default_factory=dict)
    last_seen_close: Dict[str, int] = field(default_factory=dict)
    scan_counters: Dict[str, int] = field(default_factory=dict)

    def set_watchlist(self, symbols: list[str]) -> None:
        self.watchlist = list(symbols)

    def bump(self, key: str) -> None:
        self.scan_counters[key] = self.scan_counters.get(key, 0) + 1

    def reset_scan_counters(self) -> None:
        self.scan_counters = {}
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_state.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/state.py test_breakout_state.py
git commit -m "add BreakoutState shared container"
```

---

## Task 9: Scanner — build watchlist

Filter cascade → composite score → top-5. Data client mocked in tests.

**Files:**
- Create: `breakout/scanner.py`
- Create: `test_breakout_scanner.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_scanner.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.scanner import BreakoutScanner
from breakout.scoring import Kline
from breakout.state import BreakoutState


def _tkr(symbol, vol, pct24):
    """Build a minimal ticker dict mirror of Binance /ticker/24hr row."""
    return {
        "symbol": symbol,
        "quoteVolume": str(vol),
        "priceChangePercent": str(pct24),
        "lastPrice": "100.0",
    }


def _uptrend_klines_15m(n=25):
    # rising candles with last kline volume > avg
    return [Kline(0, 100+i*0.1, 100+i*0.1+0.5, 100+i*0.1-0.3, 100+i*0.1+0.2, 1000.0, 0)
            for i in range(n)]


def _uptrend_klines_1h(n=210):
    # monotone uptrend → ema50 > ema200
    return [Kline(0, 100+i*0.5, 100+i*0.5+1, 100+i*0.5-1, 100+i*0.5+0.3, 1000.0, 0)
            for i in range(n)]


def _make_config():
    c = MagicMock()
    c.breakout_scan_top_n = 200
    c.breakout_min_vol_24h_usd = 50_000_000
    c.breakout_change_24h_min_pct = 3.0
    c.breakout_change_24h_max_pct = 15.0
    c.breakout_change_6h_max_pct = 12.0
    c.breakout_watchlist_size = 5
    c.breakout_excluded_bases = ["USDT", "USDC", "BUSD"]
    return c


@pytest.mark.asyncio
async def test_scanner_filters_low_volume():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("BTCUSDT", vol=10_000_000, pct24=5.0),   # too low volume
        _tkr("ETHUSDT", vol=100_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        _uptrend_klines_15m() if interval == "15m" else _uptrend_klines_1h())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert "BTCUSDT" not in state.watchlist
    assert "ETHUSDT" in state.watchlist


@pytest.mark.asyncio
async def test_scanner_filters_pct_change_range():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("AAAUSDT", vol=100_000_000, pct24=1.0),   # below min
        _tkr("BBBUSDT", vol=100_000_000, pct24=20.0),  # above max
        _tkr("CCCUSDT", vol=100_000_000, pct24=8.0),   # in range
    ])
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        _uptrend_klines_15m() if interval == "15m" else _uptrend_klines_1h())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert state.watchlist == ["CCCUSDT"]


@pytest.mark.asyncio
async def test_scanner_excludes_stablecoins():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("USDCUSDT", vol=100_000_000, pct24=5.0),
        _tkr("BUSDUSDT", vol=100_000_000, pct24=5.0),
        _tkr("BTCUSDT", vol=100_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        _uptrend_klines_15m() if interval == "15m" else _uptrend_klines_1h())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert "USDCUSDT" not in state.watchlist
    assert "BUSDUSDT" not in state.watchlist
    assert "BTCUSDT" in state.watchlist


@pytest.mark.asyncio
async def test_scanner_caps_watchlist_size():
    cfg = _make_config()
    cfg.breakout_watchlist_size = 3
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr(f"COIN{i}USDT", vol=100_000_000, pct24=5.0 + i * 0.1) for i in range(10)
    ])
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        _uptrend_klines_15m() if interval == "15m" else _uptrend_klines_1h())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, cfg)
    await scanner.scan_once()
    assert len(state.watchlist) == 3


@pytest.mark.asyncio
async def test_scanner_prefers_usdt_on_duplicate_base():
    # Both BTCUSD and BTCUSDT pass filters; scanner picks the higher-volume one
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("BTCUSD", vol=100_000_000, pct24=5.0),
        _tkr("BTCUSDT", vol=200_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        _uptrend_klines_15m() if interval == "15m" else _uptrend_klines_1h())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    # At most one of the two survives the dedup step
    assert len([s for s in state.watchlist if s in ("BTCUSD", "BTCUSDT")]) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_scanner.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.scanner'`

- [ ] **Step 3: Create `breakout/scanner.py`**

```python
"""
BreakoutScanner — builds the top-5 watchlist every N minutes.

Filter cascade on /ticker/24hr → per-candidate klines fetch → composite scoring.
"""

import asyncio
import logging
import time
from typing import Optional

from breakout.scoring import ema
from breakout.state import BreakoutState

logger = logging.getLogger(__name__)

_USD_SUFFIXES = ("USDT", "USD")


def _base_asset(symbol: str) -> str:
    for suf in _USD_SUFFIXES:
        if symbol.endswith(suf):
            return symbol[: -len(suf)]
    return symbol


class BreakoutScanner:
    def __init__(self, data_client, state: BreakoutState, config):
        self.client = data_client
        self.state = state
        self.config = config

    async def run(self):
        logger.info("[BreakoutScanner] Starting")
        while True:
            try:
                await self.scan_once()
            except Exception as e:
                logger.error(f"[BreakoutScanner] Scan cycle error: {e}")
            await asyncio.sleep(self.config.breakout_scan_interval_min * 60)

    async def scan_once(self) -> None:
        """Runs one pass: fetch tickers → filter → score → top-N → publish."""
        cfg = self.config
        tickers = await self.client.fetch_24h_tickers()

        # Stage 1: quote-currency + stablecoin filter
        stage1 = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith(_USD_SUFFIXES):
                continue
            if _base_asset(sym) in cfg.breakout_excluded_bases:
                continue
            stage1.append(t)

        # Stage 2: 24h volume + 24h %-change range
        stage2 = []
        for t in stage1:
            try:
                qv = float(t.get("quoteVolume") or 0)
                pct = float(t.get("priceChangePercent") or 0)
            except (TypeError, ValueError):
                continue
            if qv < cfg.breakout_min_vol_24h_usd:
                continue
            if not (cfg.breakout_change_24h_min_pct <= pct <= cfg.breakout_change_24h_max_pct):
                continue
            stage2.append((t, qv, pct))

        # Stage 3: per-candidate klines → 6h-move, 1h-momentum, volume confirm, EMAs
        scored = []
        for t, qv, pct24 in stage2:
            sym = t["symbol"]
            try:
                k15 = await self.client.fetch_klines(sym, interval="15m", limit=25)
                k1h = await self.client.fetch_klines(sym, interval="1h", limit=210)
            except Exception as e:
                logger.debug(f"[BreakoutScanner] {sym} klines fetch failed: {e}")
                continue
            if len(k15) < 21 or len(k1h) < 6:
                continue

            # 6h move: from 6 hours ago to now using 1h closes
            close_6h_ago = k1h[-7].close if len(k1h) >= 7 else k1h[0].close
            change_6h_pct = (k1h[-1].close - close_6h_ago) / close_6h_ago * 100 if close_6h_ago > 0 else 0
            if abs(change_6h_pct) > cfg.breakout_change_6h_max_pct:
                continue

            # 1h momentum (last 1h close vs previous)
            if len(k1h) >= 2:
                change_1h_pct = (k1h[-1].close - k1h[-2].close) / k1h[-2].close * 100 if k1h[-2].close > 0 else 0
            else:
                change_1h_pct = 0
            if change_1h_pct <= 0:
                continue

            # Volume confirm: last 15m candle volume > avg of prior 20
            if len(k15) >= 21:
                recent = k15[-1]
                avg_vol = sum(x.volume for x in k15[-21:-1]) / 20
                if recent.volume <= avg_vol:
                    continue
                vol_ratio = recent.volume / avg_vol if avg_vol > 0 else 0
            else:
                continue

            # Trend strength: (price - ema50_1h) / ema50_1h when ema50 > ema200
            closes_1h = [k.close for k in k1h]
            ema50 = ema(closes_1h, 50)
            ema200 = ema(closes_1h, 200)
            if not (k1h[-1].close > ema50 > ema200):
                continue
            trend_sep = (k1h[-1].close - ema50) / ema50 if ema50 > 0 else 0

            # Composite score: simple 1:1:1 weighted sum
            composite = vol_ratio + change_1h_pct + (trend_sep * 100)
            scored.append((sym, composite, qv))

        # Dedup base assets (prefer higher volume)
        best_by_base: dict[str, tuple[str, float, float]] = {}
        for sym, composite, qv in scored:
            base = _base_asset(sym)
            cur = best_by_base.get(base)
            if cur is None or qv > cur[2]:
                best_by_base[base] = (sym, composite, qv)

        # Top-N by composite
        ranked = sorted(best_by_base.values(), key=lambda x: x[1], reverse=True)
        top = [sym for sym, _, _ in ranked[: cfg.breakout_watchlist_size]]

        self.state.set_watchlist(top)
        logger.info(
            f"[BreakoutScanner] tickers={len(tickers)} "
            f"→ stage1={len(stage1)} stage2={len(stage2)} scored={len(scored)} "
            f"→ watchlist={top}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_scanner.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/scanner.py test_breakout_scanner.py
git commit -m "add BreakoutScanner (top-5 watchlist builder)"
```

---

## Task 10: Strategy (entry engine)

Per-coin 30s poll. Detects 15m candle-close edges. Runs gates + scoring. Delegates to execution on pass.

**Files:**
- Create: `breakout/strategy.py`
- Create: `test_breakout_strategy.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_strategy.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.strategy import BreakoutStrategy
from breakout.scoring import Kline
from breakout.state import BreakoutState


def _k(close_time, o, h, l, c, v):
    return Kline(open_time=close_time - 899, open=o, high=h, low=l,
                 close=c, volume=v, close_time=close_time)


def _make_config():
    c = MagicMock()
    c.breakout_poll_interval_sec = 30.0
    c.breakout_candle_close_delay_sec = 0
    c.breakout_min_score = 7
    c.breakout_max_concurrent = 4
    c.breakout_position_usd = 500.0
    c.breakout_stop_pct = 3.0
    c.breakout_tp_pct = 4.0
    return c


def _uptrend_1h(n=210):
    return [Kline(0, 100+i, 100+i+1, 100+i-1, 100+i+0.5, 1000.0, 0) for i in range(n)]


def _consolidation_15m_then_breakout():
    # 20 flat candles around 100.0, then a big breakout candle at 102
    base = [_k(1000+i*900, 100.0, 100.2, 99.8, 100.0, 1000.0) for i in range(20)]
    breakout = _k(1000+20*900, 100.0, 102.5, 99.9, 102.1, 2000.0)
    return base + [breakout]


@pytest.mark.asyncio
async def test_strategy_no_entry_when_watchlist_empty():
    client = AsyncMock()
    state = BreakoutState()
    execution = AsyncMock()
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()


@pytest.mark.asyncio
async def test_strategy_no_entry_when_candle_not_new():
    client = AsyncMock()
    client.fetch_klines = AsyncMock(return_value=_consolidation_15m_then_breakout())
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    # Simulate that we've already seen the latest kline's close_time
    latest_close = _consolidation_15m_then_breakout()[-1].close_time
    state.last_seen_close["BTCUSDT"] = latest_close
    execution = AsyncMock()
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()


@pytest.mark.asyncio
async def test_strategy_fires_entry_on_high_score_breakout():
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_called_once()
    args, kwargs = execution.enter.call_args
    assert kwargs.get("symbol") == "BTCUSDT" or args[0] == "BTCUSDT"


@pytest.mark.asyncio
async def test_strategy_rejects_when_score_below_min():
    cfg = _make_config()
    cfg.breakout_min_score = 11  # impossible → always reject
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    strat = BreakoutStrategy(client, state, cfg, execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    # diagnostic counter bumped
    assert state.scan_counters.get("gate_score_too_low", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_rejects_duplicate_symbol():
    from breakout.state import BreakoutPosition
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    state.open_positions["BTCUSDT"] = BreakoutPosition(
        symbol="BTCUSDT", entry_time="t", entry_price=100.0, qty=1, cost_usd=500,
        score=8, resistance_level=99.5, tp_price=104, stop_price=97,
        entry_candle_volume=1000, peak_price=100)
    execution = AsyncMock()
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_duplicate", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_rejects_during_cooldown():
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=True)
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_cooldown", 0) >= 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_strategy.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.strategy'`

- [ ] **Step 3: Create `breakout/strategy.py`**

```python
"""
BreakoutStrategy — entry engine.

Polls each watchlist coin every ~30s. Detects 15m candle-close transitions
(using close_time > last_seen_close). On transition: compute EMAs,
resistance, avg volume, run all gates, score, and call execution.enter().
"""

import asyncio
import logging
from datetime import datetime, timezone

from breakout.scoring import (
    ema,
    breakout_strength_score,
)
from breakout.state import BreakoutState

logger = logging.getLogger(__name__)


class BreakoutStrategy:
    def __init__(self, data_client, state: BreakoutState, config, execution):
        self.client = data_client
        self.state = state
        self.config = config
        self.execution = execution

    async def run(self):
        logger.info("[BreakoutStrategy] Starting")
        while True:
            try:
                await self.poll_once()
            except Exception as e:
                logger.error(f"[BreakoutStrategy] Poll error: {e}")
            await asyncio.sleep(self.config.breakout_poll_interval_sec)

    async def poll_once(self) -> None:
        for symbol in list(self.state.watchlist):
            try:
                await self._evaluate_symbol(symbol)
            except Exception as e:
                logger.debug(f"[BreakoutStrategy] {symbol} evaluate error: {e}")
        # Log and reset counters each poll cycle
        if self.state.scan_counters:
            logger.info(f"[BreakoutStrategy] scan counters: {self.state.scan_counters}")
            self.state.reset_scan_counters()

    async def _evaluate_symbol(self, symbol: str) -> None:
        k15_latest = await self.client.fetch_klines(symbol, interval="15m", limit=2)
        if not k15_latest:
            return
        latest_close = k15_latest[-1].close_time
        last_seen = self.state.last_seen_close.get(symbol)
        if last_seen is not None and latest_close <= last_seen:
            return
        # First sighting — record and return so we only act on subsequent edges
        if last_seen is None:
            self.state.last_seen_close[symbol] = latest_close
            return
        # Edge detected — let exchange finalize
        if self.config.breakout_candle_close_delay_sec > 0:
            await asyncio.sleep(self.config.breakout_candle_close_delay_sec)

        self.state.last_seen_close[symbol] = latest_close

        # Re-fetch for scoring
        k15 = await self.client.fetch_klines(symbol, interval="15m", limit=25)
        k1h = await self.client.fetch_klines(symbol, interval="1h", limit=210)
        if len(k15) < 21 or len(k1h) < 50:
            return

        candle = k15[-1]
        prior = k15[-21:-1]
        resistance = max(k.high for k in prior)
        avg_volume_20 = sum(k.volume for k in prior) / 20

        closes_1h = [k.close for k in k1h]
        ema50_1h = ema(closes_1h, 50)
        ema200_1h = ema(closes_1h, 200)

        # Gates (each bumps a diagnostic counter on fail)
        if not (candle.close > ema50_1h):
            self.state.bump("gate_price_below_ema50")
            return
        if not (ema50_1h > ema200_1h):
            self.state.bump("gate_ema50_below_ema200")
            return
        if not (candle.close > resistance):
            self.state.bump("gate_no_breakout")
            return
        if not (candle.volume > avg_volume_20):
            self.state.bump("gate_vol_below_avg")
            return

        # Score
        # Consolidation range = max-min of prior 5 closes
        consolidation_range = max(k.close for k in k15[-6:-1]) - min(k.close for k in k15[-6:-1])
        score, breakdown = breakout_strength_score(
            candle=candle,
            avg_volume_20=avg_volume_20,
            resistance=resistance,
            ema50_1h=ema50_1h,
            ema200_1h=ema200_1h,
            consolidation_range=consolidation_range,
        )

        # Execution gates
        if score < self.config.breakout_min_score:
            self.state.bump("gate_score_too_low")
            return
        if symbol in self.state.open_positions:
            self.state.bump("gate_duplicate")
            return
        if hasattr(self.execution, "can_open") and not self.execution.can_open():
            self.state.bump("gate_max_concurrent")
            return
        if hasattr(self.execution, "is_in_cooldown") and self.execution.is_in_cooldown(symbol):
            self.state.bump("gate_cooldown")
            return

        # Fire entry
        reason = (
            f"score={score} vol={breakdown['volume']} body={breakdown['body']} "
            f"break={breakdown['breakout_size']} trend={breakdown['trend']} "
            f"struct={breakdown['structure']} resistance={resistance:.6f}"
        )
        logger.info(f"[BreakoutStrategy] ENTRY {symbol} | {reason}")
        await self.execution.enter(
            symbol=symbol,
            candle=candle,
            score=score,
            breakdown=breakdown,
            resistance=resistance,
            reason=reason,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_strategy.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/strategy.py test_breakout_strategy.py
git commit -m "add BreakoutStrategy (entry engine with candle-close gate + scoring)"
```

---

## Task 11: Execution — entry path

Opens positions: paper fill, capital reserve, state update, DB insert.

**Files:**
- Create: `breakout/execution.py`
- Create: `test_breakout_execution.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_breakout_execution.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.execution import BreakoutExecution
from breakout.scoring import Kline
from breakout.state import BreakoutState, BreakoutPosition
from breakout.capital import BreakoutCapitalManager


def _k(close, o=100.0, h=101.0, l=99.0, v=1500.0):
    return Kline(0, o, h, l, close, v, 0)


def _make_config():
    c = MagicMock()
    c.breakout_position_usd = 500.0
    c.breakout_tp_pct = 4.0
    c.breakout_tp_sell_pct = 0.50
    c.breakout_stop_pct = 3.0
    c.breakout_trail_pct = 2.0
    c.breakout_max_hold_hours = 4.0
    c.breakout_cooldown_minutes = 45.0
    c.breakout_paper_taker_fee = 0.006
    return c


@pytest.fixture
def execution(tmp_path):
    from breakout.database import BreakoutDB
    cfg = _make_config()
    state = BreakoutState()
    capital = BreakoutCapitalManager(total_capital=2000.0, max_concurrent=4)
    paper_fill = AsyncMock()
    paper_fill.simulate_buy = AsyncMock(return_value=MagicMock(
        price=100.0, qty=5.0, usd_cost=500.0, fee_usd=3.0))
    paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=104.0, qty=2.5, usd_proceeds=258.44, fee_usd=1.56))
    db = BreakoutDB(str(tmp_path / "breakout.db"))
    data_client = AsyncMock()
    return BreakoutExecution(
        data_client=data_client,
        paper_fill=paper_fill,
        capital=capital,
        state=state,
        db=db,
        config=cfg,
    )


@pytest.mark.asyncio
async def test_enter_creates_position(execution):
    candle = _k(close=100.5)
    await execution.enter(
        symbol="BTCUSDT", candle=candle, score=8,
        breakdown={"volume": 3, "body": 2, "breakout_size": 2, "trend": 1, "structure": 0, "total": 8},
        resistance=100.0, reason="score=8 breakout",
    )
    assert "BTCUSDT" in execution.state.open_positions
    pos = execution.state.open_positions["BTCUSDT"]
    assert pos.entry_price == 100.0
    assert pos.qty == 5.0
    assert pos.tp_price == pytest.approx(104.0)
    assert pos.stop_price == pytest.approx(97.0)
    assert pos.score == 8
    # Capital reserved
    assert execution.capital.deployed_usd() == 500.0
    # DB row exists
    assert len(execution.db.get_open_positions()) == 1


def test_can_open_true_when_capacity(execution):
    assert execution.can_open() is True


def test_can_open_false_when_no_capacity(execution):
    for i in range(4):
        execution.capital.reserve(f"COIN{i}", 500.0)
    assert execution.can_open() is False


def test_is_in_cooldown_false_by_default(execution):
    assert execution.is_in_cooldown("BTCUSDT") is False


def test_is_in_cooldown_true_after_set(execution):
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    execution.db.set_cooldown("BTCUSDT",
                              cooldown_until_ts=future,
                              last_loss_pnl_usd=-15.0,
                              last_loss_time=past)
    assert execution.is_in_cooldown("BTCUSDT") is True


def test_is_in_cooldown_expired(execution):
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    execution.db.set_cooldown("BTCUSDT",
                              cooldown_until_ts=past,
                              last_loss_pnl_usd=-15.0,
                              last_loss_time=past)
    assert execution.is_in_cooldown("BTCUSDT") is False


@pytest.mark.asyncio
async def test_enter_blocks_duplicate(execution):
    candle = _k(close=100.5)
    await execution.enter(
        symbol="BTCUSDT", candle=candle, score=8,
        breakdown={"volume": 3, "body": 2, "breakout_size": 2, "trend": 1, "structure": 0, "total": 8},
        resistance=100.0, reason="first",
    )
    # second attempt should no-op
    await execution.enter(
        symbol="BTCUSDT", candle=candle, score=8,
        breakdown={"volume": 3, "body": 2, "breakout_size": 2, "trend": 1, "structure": 0, "total": 8},
        resistance=100.0, reason="second",
    )
    assert execution.capital.deployed_usd() == 500.0
    assert len(execution.db.get_open_positions()) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_execution.py -v
```
Expected: `ModuleNotFoundError: No module named 'breakout.execution'`

- [ ] **Step 3: Create `breakout/execution.py` (entry path only)**

```python
"""
BreakoutExecution — opens & manages breakout positions.

This task (Task 11) covers `enter()` + cooldown helpers only.
Task 12 adds `manage_positions()` and exit paths.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from breakout.state import BreakoutPosition

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BreakoutExecution:
    def __init__(self, *, data_client, paper_fill, capital, state, db, config):
        self.client = data_client
        self.paper_fill = paper_fill
        self.capital = capital
        self.state = state
        self.db = db
        self.config = config

    # ── Entry ──────────────────────────────────────────────────────

    def can_open(self) -> bool:
        return self.capital.has_capacity(self.config.breakout_position_usd)

    def is_in_cooldown(self, symbol: str) -> bool:
        return self.db.is_in_cooldown(symbol, now_ts=_utcnow_iso())

    async def enter(self, *, symbol, candle, score, breakdown, resistance, reason) -> None:
        # Re-check guards inside (strategy may race)
        if symbol in self.state.open_positions:
            logger.info(f"[BreakoutExecution] duplicate {symbol} — no-op")
            return
        if not self.can_open():
            logger.info(f"[BreakoutExecution] no capacity — {symbol} skipped")
            return
        if self.is_in_cooldown(symbol):
            logger.info(f"[BreakoutExecution] in cooldown — {symbol} skipped")
            return

        position_usd = self.config.breakout_position_usd
        fill = await self.paper_fill.simulate_buy(symbol, usd_amount=position_usd)

        tp_price = fill.price * (1 + self.config.breakout_tp_pct / 100)
        stop_price = fill.price * (1 - self.config.breakout_stop_pct / 100)

        pos = BreakoutPosition(
            symbol=symbol,
            entry_time=_utcnow_iso(),
            entry_price=fill.price,
            qty=fill.qty,
            cost_usd=position_usd,
            score=score,
            resistance_level=resistance,
            tp_price=tp_price,
            stop_price=stop_price,
            entry_candle_volume=candle.volume,
            peak_price=fill.price,
            tp_hit=False,
            score_breakdown=dict(breakdown),
            reason_entry=reason,
        )

        self.capital.reserve(symbol, position_usd)
        self.state.open_positions[symbol] = pos

        self.db.insert_open_position(
            symbol=symbol,
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            qty=pos.qty,
            cost_usd=pos.cost_usd,
            score=pos.score,
            score_breakdown=json.dumps(breakdown),
            resistance_level=pos.resistance_level,
            tp_price=pos.tp_price,
            stop_price=pos.stop_price,
            entry_candle_volume=pos.entry_candle_volume,
            peak_price=pos.peak_price,
        )

        logger.info(
            f"[BreakoutExecution] ENTRY {symbol} "
            f"price={fill.price:.6f} qty={fill.qty:.4f} "
            f"tp={tp_price:.6f} stop={stop_price:.6f} score={score}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_execution.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add breakout/execution.py test_breakout_execution.py
git commit -m "add BreakoutExecution.enter() + cooldown checks"
```

---

## Task 12: Execution — manage_positions (exits)

TP1 / stop / trail / early-exit / max-hold. Cooldown set on aggregate-loss close.

**Files:**
- Modify: `breakout/execution.py`
- Modify: `test_breakout_execution.py`

- [ ] **Step 1: Append failing tests**

Append to `test_breakout_execution.py`:

```python
# ── manage_positions ────────────────────────────────────────

from unittest.mock import patch


def _seed_position(execution, symbol="BTCUSDT", entry=100.0, qty=5.0, score=8,
                   resistance=99.5, tp_pct=4.0, stop_pct=3.0):
    pos = BreakoutPosition(
        symbol=symbol, entry_time="2026-04-17T12:00:00+00:00",
        entry_price=entry, qty=qty, cost_usd=500.0, score=score,
        resistance_level=resistance,
        tp_price=entry * (1 + tp_pct / 100),
        stop_price=entry * (1 - stop_pct / 100),
        entry_candle_volume=1000.0, peak_price=entry,
    )
    execution.state.open_positions[symbol] = pos
    execution.capital.reserve(symbol, 500.0)
    execution.db.insert_open_position(
        symbol=symbol, entry_time=pos.entry_time,
        entry_price=pos.entry_price, qty=pos.qty, cost_usd=pos.cost_usd,
        score=pos.score, score_breakdown="{}",
        resistance_level=pos.resistance_level,
        tp_price=pos.tp_price, stop_price=pos.stop_price,
        entry_candle_volume=pos.entry_candle_volume, peak_price=pos.peak_price,
    )
    return pos


@pytest.mark.asyncio
async def test_stop_fires_when_price_hits_stop(execution):
    pos = _seed_position(execution)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=97.0, qty=5.0, usd_proceeds=5.0 * 97.0 * (1 - 0.006), fee_usd=5.0 * 97.0 * 0.006))
    # Price hit 96.0 (well below 97.0 stop)
    await execution._manage_one(pos, current_price=96.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "stop-loss"
    # Cooldown set because pnl < 0
    assert execution.is_in_cooldown("BTCUSDT") is True


@pytest.mark.asyncio
async def test_tp1_fires_sells_half_activates_trail(execution):
    pos = _seed_position(execution)
    # simulate_sell returns a partial-qty fill matching whatever we asked for
    execution.paper_fill.simulate_sell = AsyncMock(side_effect=lambda symbol, qty:
        MagicMock(price=104.0, qty=qty, usd_proceeds=qty*104.0*(1-0.006), fee_usd=qty*104.0*0.006))
    await execution._manage_one(pos, current_price=104.5, recent_k15=[])
    # Still open with half the qty
    assert "BTCUSDT" in execution.state.open_positions
    updated = execution.state.open_positions["BTCUSDT"]
    assert updated.tp_hit is True
    assert updated.qty == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_trail_exits_after_tp1(execution):
    pos = _seed_position(execution)
    pos.tp_hit = True
    pos.peak_price = 108.0
    pos.qty = 2.5  # already sold half
    execution.state.open_positions["BTCUSDT"] = pos
    execution.db.update_open_position("BTCUSDT", tp_hit=1, peak_price=108.0, qty=2.5)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=105.8, qty=2.5, usd_proceeds=2.5*105.8*(1-0.006), fee_usd=2.5*105.8*0.006))
    # 108.0 peak, trail 2% = 105.84 → 105.8 triggers
    await execution._manage_one(pos, current_price=105.8, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "trail"


@pytest.mark.asyncio
async def test_max_hold_exits_position(execution):
    pos = _seed_position(execution)
    # entry_time is 2026-04-17T12:00 (distant past)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=101.0, qty=5.0, usd_proceeds=5.0*101.0*(1-0.006), fee_usd=5.0*101.0*0.006))
    await execution._manage_one(pos, current_price=101.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "max-hold"


@pytest.mark.asyncio
async def test_winning_close_no_cooldown(execution):
    pos = _seed_position(execution)
    pos.tp_hit = True
    pos.qty = 2.5  # sold half at TP
    # Store the partial fee from TP1 in reason_entry area as a proxy — skip accounting for simplicity
    # Remaining sells at 102 → aggregate P&L positive
    execution.state.open_positions["BTCUSDT"] = pos
    execution.db.update_open_position("BTCUSDT", tp_hit=1, qty=2.5, peak_price=105.0)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=102.0, qty=2.5, usd_proceeds=2.5*102.0*(1-0.006), fee_usd=2.5*102.0*0.006))
    # Trigger trail (peak 105 → trail at 102.9 → 102.0 triggers)
    await execution._manage_one(pos, current_price=102.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    # tp1 credit covers fees → aggregate P&L positive
    assert execution.is_in_cooldown("BTCUSDT") is False


@pytest.mark.asyncio
async def test_breakout_failed_early_exit(execution):
    """Current price drops back below resistance_level → exit full."""
    pos = _seed_position(execution, resistance=99.5)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=99.0, qty=5.0, usd_proceeds=5.0*99.0*(1-0.006), fee_usd=5.0*99.0*0.006))
    await execution._manage_one(pos, current_price=99.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "breakout-failed"
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_execution.py -v
```
Expected: `AttributeError: 'BreakoutExecution' object has no attribute '_manage_one'`

- [ ] **Step 3: Extend `breakout/execution.py`**

Append these methods to the `BreakoutExecution` class:

```python
    # ── Position management ─────────────────────────────────────────

    async def run(self):
        logger.info("[BreakoutExecution] Starting manage loop")
        while True:
            try:
                await self.manage_positions()
            except Exception as e:
                logger.error(f"[BreakoutExecution] Manage error: {e}")
            await asyncio.sleep(self.config.breakout_poll_interval_sec)

    async def manage_positions(self) -> None:
        for symbol, pos in list(self.state.open_positions.items()):
            try:
                # Price: latest 15m close; recent_k15: last 2 closed candles for early-exit checks
                klines = await self.client.fetch_klines(symbol, interval="15m", limit=3)
                if not klines:
                    continue
                current_price = klines[-1].close
                recent = klines[:-1] if len(klines) >= 2 else []
                await self._manage_one(pos, current_price=current_price, recent_k15=recent)
            except Exception as e:
                logger.debug(f"[BreakoutExecution] manage {symbol} error: {e}")

    async def _manage_one(self, pos: BreakoutPosition, *, current_price: float, recent_k15: list) -> None:
        symbol = pos.symbol
        pos.peak_price = max(pos.peak_price, current_price)

        # 1. Hard stop
        if current_price <= pos.stop_price:
            await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="stop-loss")
            return

        # 2. Max hold
        from breakout.scoring import is_bearish_engulfing, has_upper_wick_rejection, volume_drop
        entry_dt = datetime.fromisoformat(pos.entry_time)
        hold_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
        if hold_hours >= self.config.breakout_max_hold_hours:
            await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="max-hold")
            return

        # 3. Early exits (only check if we have the candles)
        if len(recent_k15) >= 2:
            prev, curr = recent_k15[-2], recent_k15[-1]
            if current_price < pos.resistance_level:
                await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="breakout-failed")
                return
            if is_bearish_engulfing(prev, curr):
                await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="bearish-engulfing")
                return
            if has_upper_wick_rejection(curr):
                await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="wick-rejection")
                return
            if volume_drop(curr.volume, pos.entry_candle_volume):
                await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="volume-drop")
                return
        else:
            # fallback check using current price only
            if current_price < pos.resistance_level:
                await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="breakout-failed")
                return

        # 4. TP1 — sell half, activate trail
        if not pos.tp_hit and current_price >= pos.tp_price:
            half_qty = pos.qty * self.config.breakout_tp_sell_pct
            fill = await self.paper_fill.simulate_sell(symbol, qty=half_qty)
            # track partial proceeds + fees on the pos for aggregate accounting
            pos.qty -= fill.qty
            pos.tp_hit = True
            if not hasattr(pos, "_partial_proceeds"):
                pos._partial_proceeds = 0.0
                pos._partial_fees = 0.0
            pos._partial_proceeds += fill.usd_proceeds
            pos._partial_fees += fill.fee_usd
            self.db.update_open_position(symbol, qty=pos.qty, tp_hit=1, peak_price=pos.peak_price)
            logger.info(f"[BreakoutExecution] TP1 {symbol} sold {fill.qty:.4f} @ {fill.price:.6f}")
            return

        # 5. Trailing stop (only active after TP1)
        if pos.tp_hit:
            trail_stop = pos.peak_price * (1 - self.config.breakout_trail_pct / 100)
            if current_price <= trail_stop:
                await self._close(pos, exit_price_hint=current_price, qty_to_sell=pos.qty, reason="trail")
                return

        # No exit — persist updated peak_price
        self.db.update_open_position(symbol, peak_price=pos.peak_price)

    async def _close(self, pos: BreakoutPosition, *, exit_price_hint: float,
                     qty_to_sell: float, reason: str) -> None:
        symbol = pos.symbol
        fill = await self.paper_fill.simulate_sell(symbol, qty=qty_to_sell)

        # Aggregate accounting
        partial_proceeds = getattr(pos, "_partial_proceeds", 0.0)
        partial_fees = getattr(pos, "_partial_fees", 0.0)
        total_proceeds = partial_proceeds + fill.usd_proceeds
        total_fees = partial_fees + fill.fee_usd
        pnl_usd = total_proceeds - pos.cost_usd
        pnl_pct = (pnl_usd / pos.cost_usd) * 100 if pos.cost_usd > 0 else 0.0

        now_iso = _utcnow_iso()
        self.db.close_position(
            symbol=symbol,
            exit_time=now_iso,
            exit_price=fill.price,
            proceeds_usd=total_proceeds,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            reason_entry=pos.reason_entry,
            reason_exit=reason,
            fee_total_usd=total_fees,
        )

        # Set cooldown on aggregate loss
        if pnl_usd < 0:
            cooldown_until = (
                datetime.now(timezone.utc)
                + timedelta(minutes=self.config.breakout_cooldown_minutes)
            ).isoformat()
            self.db.set_cooldown(
                symbol=symbol,
                cooldown_until_ts=cooldown_until,
                last_loss_pnl_usd=pnl_usd,
                last_loss_time=now_iso,
            )

        self.capital.release(symbol, proceeds_usd=total_proceeds, cost_usd=pos.cost_usd)
        del self.state.open_positions[symbol]

        logger.info(
            f"[BreakoutExecution] EXIT {symbol} {reason} "
            f"pnl=${pnl_usd:+.2f} ({pnl_pct:+.2f}%) exit_price={fill.price:.6f}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_execution.py -v
```
Expected: all tests pass (7 from Task 11 + 6 new = 13).

- [ ] **Step 5: Commit**

```bash
git add breakout/execution.py test_breakout_execution.py
git commit -m "add BreakoutExecution.manage_positions (TP1/stop/trail/early-exits/max-hold)"
```

---

## Task 13: Dashboard API endpoints

New `/api/breakout/*` endpoints. Piggyback on existing `WebDashboard` Flask app.

**Files:**
- Modify: `dashboard/web_dashboard.py`
- Create: `test_breakout_dashboard_api.py`

- [ ] **Step 1: Inspect current dashboard structure**

Read `dashboard/web_dashboard.py` to understand how endpoints are registered (look for existing `@self.app.route("/api/trades")` or similar). You'll register new routes following the same pattern.

- [ ] **Step 2: Write the failing tests**

```python
# test_breakout_dashboard_api.py
import json
import pytest
from unittest.mock import MagicMock
from breakout.state import BreakoutState, BreakoutPosition
from breakout.capital import BreakoutCapitalManager


@pytest.fixture
def dashboard_with_breakout(tmp_path):
    from dashboard.web_dashboard import WebDashboard
    from breakout.database import BreakoutDB

    tracker = MagicMock()
    dash = WebDashboard(tracker=tracker, port=0)  # port=0 → flask test client only
    state = BreakoutState()
    capital = BreakoutCapitalManager()
    db = BreakoutDB(str(tmp_path / "breakout.db"))
    dash.register_breakout(state=state, capital=capital, db=db)
    return dash, state, capital, db


def test_api_breakout_state_returns_stats(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    with dash.app.test_client() as client:
        resp = client.get("/api/breakout/state")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_capital"] == 2000.0
        assert data["available"] == 2000.0
        assert data["deployed"] == 0.0
        assert data["open_count"] == 0


def test_api_breakout_watchlist(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    state.set_watchlist(["BTCUSDT", "ETHUSDT"])
    with dash.app.test_client() as client:
        resp = client.get("/api/breakout/watchlist")
        assert resp.status_code == 200
        assert resp.get_json() == ["BTCUSDT", "ETHUSDT"]


def test_api_breakout_positions_empty(dashboard_with_breakout):
    dash, *_ = dashboard_with_breakout
    with dash.app.test_client() as client:
        resp = client.get("/api/breakout/positions")
        assert resp.status_code == 200
        assert resp.get_json() == []


def test_api_breakout_positions_returns_open(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    pos = BreakoutPosition(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00+00:00",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8,
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1000.0, peak_price=103.0, tp_hit=False,
    )
    state.open_positions["BTCUSDT"] = pos
    with dash.app.test_client() as client:
        resp = client.get("/api/breakout/positions")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["symbol"] == "BTCUSDT"
        assert data[0]["score"] == 8
        assert data[0]["peak_price"] == 103.0


def test_api_breakout_closed_positions(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00+00:00",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1000.0, peak_price=100.0,
    )
    db.close_position(
        symbol="BTCUSDT", exit_time="2026-04-17T13:00:00+00:00",
        exit_price=104.0, proceeds_usd=520.0, pnl_usd=20.0, pnl_pct=4.0,
        reason_entry="score=8", reason_exit="tp1", fee_total_usd=3.0,
    )
    with dash.app.test_client() as client:
        resp = client.get("/api/breakout/closed")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["reason_exit"] == "tp1"
        assert data[0]["pnl_usd"] == 20.0
```

- [ ] **Step 3: Run the tests to verify they fail**

```
python -m pytest test_breakout_dashboard_api.py -v
```
Expected: `AttributeError: 'WebDashboard' object has no attribute 'register_breakout'`

- [ ] **Step 4: Add `register_breakout()` and endpoints to `dashboard/web_dashboard.py`**

Read the file to find where existing endpoints are defined, then add this method on `WebDashboard` and register routes in `__init__` or wherever routes live:

```python
    # ── Breakout strategy ────────────────────────────────────────

    def register_breakout(self, *, state, capital, db):
        """Wire breakout strategy state, capital manager, and DB to dashboard."""
        self._breakout_state = state
        self._breakout_capital = capital
        self._breakout_db = db

        @self.app.route("/api/breakout/state")
        def _api_breakout_state():
            return self.app.response_class(
                response=json.dumps(self._breakout_capital.stats()),
                mimetype="application/json",
            )

        @self.app.route("/api/breakout/watchlist")
        def _api_breakout_watchlist():
            return self.app.response_class(
                response=json.dumps(self._breakout_state.watchlist),
                mimetype="application/json",
            )

        @self.app.route("/api/breakout/positions")
        def _api_breakout_positions():
            out = []
            for pos in self._breakout_state.open_positions.values():
                out.append({
                    "symbol": pos.symbol,
                    "entry_time": pos.entry_time,
                    "entry_price": pos.entry_price,
                    "qty": pos.qty,
                    "cost_usd": pos.cost_usd,
                    "score": pos.score,
                    "resistance_level": pos.resistance_level,
                    "tp_price": pos.tp_price,
                    "stop_price": pos.stop_price,
                    "peak_price": pos.peak_price,
                    "tp_hit": pos.tp_hit,
                    "score_breakdown": pos.score_breakdown,
                    "reason_entry": pos.reason_entry,
                })
            return self.app.response_class(
                response=json.dumps(out), mimetype="application/json")

        @self.app.route("/api/breakout/closed")
        def _api_breakout_closed():
            from flask import request
            limit = int(request.args.get("limit", 50))
            rows = self._breakout_db.get_closed_positions(limit=limit)
            return self.app.response_class(
                response=json.dumps(rows), mimetype="application/json")
```

Make sure `import json` is already at the top of `dashboard/web_dashboard.py` (or add it).

- [ ] **Step 5: Run the tests to verify they pass**

```
python -m pytest test_breakout_dashboard_api.py -v
```
Expected: 5 passed.

If `WebDashboard(port=0)` isn't supported, adapt the test fixture to whatever constructor the real class takes — the fixture just needs a `.app` (Flask) attribute with `.test_client()`.

- [ ] **Step 6: Commit**

```bash
git add dashboard/web_dashboard.py test_breakout_dashboard_api.py
git commit -m "add /api/breakout/* endpoints + register_breakout()"
```

---

## Task 14: Dashboard UI — breakout section

Add a new section in the HTML template for stat cards + tables. Fetches populated via JS calls to the endpoints from Task 13.

**Files:**
- Modify: `dashboard/templates/index.html` (or wherever the dashboard HTML lives — find it with: `ls dashboard/templates/` or `grep -r "TOTAL P&L" dashboard/`)

- [ ] **Step 1: Locate the dashboard HTML**

```bash
ls C:/Users/jcole/multichain-bot/dashboard/templates/ 2>/dev/null || find C:/Users/jcole/multichain-bot/dashboard -name '*.html'
```

- [ ] **Step 2: Add a breakout section to the template**

After the existing stat-cards row / last panel, insert this section. Mirror the visual style of the existing Scalper or Overall section (read the nearby HTML before editing):

```html
<!-- Breakout Strategy -->
<section class="panel" id="breakout-panel" style="display:none;">
  <h2>BREAKOUT STRATEGY (Binance.US)</h2>

  <div class="cards">
    <div class="card"><div class="card-label">CAPITAL</div><div class="card-value" id="bk-capital">$0</div></div>
    <div class="card"><div class="card-label">AVAILABLE</div><div class="card-value" id="bk-available">$0</div></div>
    <div class="card"><div class="card-label">DEPLOYED</div><div class="card-value" id="bk-deployed">$0</div></div>
    <div class="card"><div class="card-label">REALIZED P&L</div><div class="card-value" id="bk-pnl">$0</div></div>
    <div class="card"><div class="card-label">OPEN</div><div class="card-value" id="bk-open">0 / 4</div></div>
  </div>

  <h3>WATCHLIST</h3>
  <ul id="bk-watchlist" class="watchlist"></ul>

  <h3>OPEN POSITIONS</h3>
  <table id="bk-positions" class="positions-table">
    <thead><tr>
      <th>Symbol</th><th>Entry</th><th>Qty</th><th>TP</th><th>Stop</th>
      <th>Peak</th><th>Score</th><th>TP Hit</th>
    </tr></thead>
    <tbody></tbody>
  </table>

  <h3>CLOSED (last 20)</h3>
  <table id="bk-closed" class="closed-table">
    <thead><tr>
      <th>Symbol</th><th>Entry</th><th>Exit</th><th>PnL $</th><th>PnL %</th><th>Reason</th>
    </tr></thead>
    <tbody></tbody>
  </table>
</section>

<script>
(async function refreshBreakout() {
  try {
    const [s, w, p, c] = await Promise.all([
      fetch("/api/breakout/state").then(r => r.json()).catch(() => null),
      fetch("/api/breakout/watchlist").then(r => r.json()).catch(() => []),
      fetch("/api/breakout/positions").then(r => r.json()).catch(() => []),
      fetch("/api/breakout/closed?limit=20").then(r => r.json()).catch(() => []),
    ]);
    if (!s) return;
    document.getElementById("breakout-panel").style.display = "";
    document.getElementById("bk-capital").textContent = "$" + s.total_capital.toFixed(0);
    document.getElementById("bk-available").textContent = "$" + s.available.toFixed(0);
    document.getElementById("bk-deployed").textContent = "$" + s.deployed.toFixed(0);
    document.getElementById("bk-pnl").textContent = (s.realized_pnl >= 0 ? "+" : "") + "$" + s.realized_pnl.toFixed(2);
    document.getElementById("bk-open").textContent = s.open_count + " / " + s.max_concurrent;

    const ul = document.getElementById("bk-watchlist");
    ul.innerHTML = "";
    for (const sym of w) { const li = document.createElement("li"); li.textContent = sym; ul.appendChild(li); }

    const tbody = document.querySelector("#bk-positions tbody");
    tbody.innerHTML = "";
    for (const row of p) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${row.symbol}</td><td>${row.entry_price.toFixed(6)}</td>
                      <td>${row.qty.toFixed(4)}</td><td>${row.tp_price.toFixed(6)}</td>
                      <td>${row.stop_price.toFixed(6)}</td><td>${row.peak_price.toFixed(6)}</td>
                      <td>${row.score}</td><td>${row.tp_hit ? "YES" : "NO"}</td>`;
      tbody.appendChild(tr);
    }

    const ctbody = document.querySelector("#bk-closed tbody");
    ctbody.innerHTML = "";
    for (const row of c) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${row.symbol}</td><td>${row.entry_price.toFixed(6)}</td>
                      <td>${row.exit_price.toFixed(6)}</td>
                      <td>${row.pnl_usd >= 0 ? "+" : ""}$${row.pnl_usd.toFixed(2)}</td>
                      <td>${row.pnl_pct >= 0 ? "+" : ""}${row.pnl_pct.toFixed(2)}%</td>
                      <td>${row.reason_exit}</td>`;
      ctbody.appendChild(tr);
    }
  } catch (e) { console.error("breakout refresh failed", e); }
  setTimeout(refreshBreakout, 10000);
})();
</script>
```

- [ ] **Step 3: Sanity-check — start dashboard locally and verify the panel renders empty (no breakout tasks running yet)**

```bash
python main.py   # Ctrl-C once dashboard serves at http://localhost:8080
```

Expected: dashboard comes up; breakout panel hidden (endpoints not registered yet) or shows empty state if registered. Either outcome is fine — the UI wiring is complete.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/index.html
git commit -m "add breakout panel to dashboard UI"
```

---

## Task 15: Wire everything into main.py

Instantiate modules, register with dashboard, append tasks — all behind `if config.breakout_enabled:`.

**Files:**
- Modify: `main.py`
- Create: `test_breakout_main_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# test_breakout_main_wiring.py
import os
import sys
import importlib
import asyncio
from unittest.mock import patch, MagicMock


def test_main_imports_breakout_when_enabled():
    """Ensure main.py's imports don't break when breakout_enabled=True."""
    with patch.dict(os.environ, {"BREAKOUT_ENABLED": "true"}, clear=False):
        # import-only test — we don't want main() to actually run
        if "main" in sys.modules:
            del sys.modules["main"]
        import main   # noqa: F401
        # No exception = pass


def test_breakout_module_imports():
    """All breakout submodules can be imported without side effects."""
    import breakout
    import breakout.capital
    import breakout.scoring
    import breakout.paper_fill
    import breakout.data_client
    import breakout.database
    import breakout.state
    import breakout.scanner
    import breakout.strategy
    import breakout.execution
```

- [ ] **Step 2: Run the tests to verify they fail**

```
python -m pytest test_breakout_main_wiring.py -v
```
At this point: `test_breakout_module_imports` should pass already. `test_main_imports_breakout_when_enabled` passes regardless of wiring — the test only verifies no ImportError from main.py. If your implementation adds `from breakout.X import Y` statements at module level, this test catches them.

- [ ] **Step 3: Add wiring to `main.py`**

Read `main.py` around lines 440-500 (where `dip_scanner` and `scalp_queue` are wired). Add the breakout block **after** `scalp_queue` wiring but **before** the `# DexScreener real-time WebSocket feed` comment (around line 494 currently):

```python
        # ── Breakout Strategy (Binance.US) ──────────────────────
        if config.breakout_enabled:
            from breakout.capital import BreakoutCapitalManager
            from breakout.data_client import BinanceUSClient
            from breakout.database import BreakoutDB
            from breakout.execution import BreakoutExecution
            from breakout.paper_fill import PaperFillEngine
            from breakout.scanner import BreakoutScanner
            from breakout.state import BreakoutState
            from breakout.strategy import BreakoutStrategy

            bk_state = BreakoutState()
            bk_capital = BreakoutCapitalManager(
                total_capital=config.breakout_capital,
                max_concurrent=config.breakout_max_concurrent,
            )
            bk_client = BinanceUSClient()
            bk_paper_fill = PaperFillEngine(
                data_client=bk_client,
                taker_fee=config.breakout_paper_taker_fee,
            )
            data_dir = os.environ.get("DATA_DIR", ".")
            bk_db = BreakoutDB(os.path.join(data_dir, "breakout.db"))

            bk_execution = BreakoutExecution(
                data_client=bk_client,
                paper_fill=bk_paper_fill,
                capital=bk_capital,
                state=bk_state,
                db=bk_db,
                config=config,
            )
            bk_scanner = BreakoutScanner(bk_client, bk_state, config)
            bk_strategy = BreakoutStrategy(bk_client, bk_state, config, bk_execution)

            dashboard.register_breakout(state=bk_state, capital=bk_capital, db=bk_db)

            tasks.append(bk_scanner.run())
            tasks.append(bk_strategy.run())
            tasks.append(bk_execution.run())

            logger.info(
                f"[Main] Breakout enabled — "
                f"${config.breakout_position_usd:.0f}/position, "
                f"TP +{config.breakout_tp_pct}%, stop -{config.breakout_stop_pct}%, "
                f"max {config.breakout_max_concurrent} concurrent"
            )
        else:
            logger.info("[Main] Breakout disabled (BREAKOUT_ENABLED=false)")
```

Make sure `import os` is already imported at the top of `main.py`.

- [ ] **Step 4: Run the tests to verify they pass**

```
python -m pytest test_breakout_main_wiring.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Smoke-test start with breakout disabled (default)**

```bash
python main.py
```

Watch the startup log: must show `[Main] Breakout disabled (BREAKOUT_ENABLED=false)`. Ctrl-C.

Expected: no ImportError, no breakout tasks started.

- [ ] **Step 6: Smoke-test start with breakout enabled (locally)**

```bash
BREAKOUT_ENABLED=true python main.py
```

Watch the log. Expected:
- `[Main] Breakout enabled — $500/position, TP +4.0%, stop -3.0%, max 4 concurrent`
- Within ~10 min: `[BreakoutScanner] tickers=N → stage1=… stage2=… scored=… → watchlist=[…]`
- No uncaught exceptions

Ctrl-C after confirming.

- [ ] **Step 7: Commit**

```bash
git add main.py test_breakout_main_wiring.py
git commit -m "wire breakout strategy into main.py (gated by BREAKOUT_ENABLED)"
```

---

## Task 16: Integration test — end-to-end with mocks

Drives scanner → strategy → execution through a full entry → TP1 → trail exit cycle with mocked data client and paper fill.

**Files:**
- Create: `test_breakout_integration.py`

- [ ] **Step 1: Write the integration test**

```python
# test_breakout_integration.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.capital import BreakoutCapitalManager
from breakout.database import BreakoutDB
from breakout.execution import BreakoutExecution
from breakout.paper_fill import PaperFillEngine
from breakout.scanner import BreakoutScanner
from breakout.scoring import Kline
from breakout.state import BreakoutState
from breakout.strategy import BreakoutStrategy


def _tkr(symbol, vol=100_000_000, pct24=5.0):
    return {
        "symbol": symbol,
        "quoteVolume": str(vol),
        "priceChangePercent": str(pct24),
        "lastPrice": "100.0",
    }


def _consolidation_then_breakout():
    base = [Kline(1000 + i*900, 100.0, 100.2, 99.8, 100.0, 1000.0,
                  1000 + (i+1)*900 - 1) for i in range(20)]
    breakout = Kline(1000 + 20*900, 100.0, 102.5, 99.9, 102.1, 2000.0,
                     1000 + 21*900 - 1)
    return base + [breakout]


def _uptrend_1h():
    return [Kline(0, 100+i, 100+i+1, 100+i-1, 100+i+0.5, 1000.0, 0) for i in range(210)]


def _book(bid, ask):
    return {"bids": [[str(bid), "1000"]], "asks": [[str(ask), "1000"]]}


def _make_config(**overrides):
    c = MagicMock()
    c.breakout_scan_top_n = 200
    c.breakout_min_vol_24h_usd = 50_000_000
    c.breakout_change_24h_min_pct = 3.0
    c.breakout_change_24h_max_pct = 15.0
    c.breakout_change_6h_max_pct = 12.0
    c.breakout_watchlist_size = 5
    c.breakout_excluded_bases = ["USDT", "USDC", "BUSD"]
    c.breakout_poll_interval_sec = 30.0
    c.breakout_candle_close_delay_sec = 0
    c.breakout_min_score = 6
    c.breakout_max_concurrent = 4
    c.breakout_position_usd = 500.0
    c.breakout_tp_pct = 4.0
    c.breakout_tp_sell_pct = 0.50
    c.breakout_stop_pct = 3.0
    c.breakout_trail_pct = 2.0
    c.breakout_max_hold_hours = 4.0
    c.breakout_cooldown_minutes = 45.0
    c.breakout_paper_taker_fee = 0.006
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


@pytest.mark.asyncio
async def test_full_cycle_entry_tp1_trail_exit(tmp_path):
    config = _make_config()
    state = BreakoutState()
    capital = BreakoutCapitalManager(total_capital=2000.0, max_concurrent=4)
    db = BreakoutDB(str(tmp_path / "breakout.db"))

    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[_tkr("BTCUSDT")])
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        _consolidation_then_breakout() if interval == "15m" else _uptrend_1h())
    client.fetch_order_book = AsyncMock(return_value=_book(bid=100.0, ask=100.1))

    paper_fill = PaperFillEngine(client, taker_fee=0.006)
    execution = BreakoutExecution(
        data_client=client, paper_fill=paper_fill,
        capital=capital, state=state, db=db, config=config,
    )
    scanner = BreakoutScanner(client, state, config)
    strategy = BreakoutStrategy(client, state, config, execution)

    # ── 1. Scanner populates watchlist ─────────────────────────
    await scanner.scan_once()
    assert "BTCUSDT" in state.watchlist

    # ── 2. Strategy first poll: establishes baseline close_time ──
    await strategy.poll_once()
    assert "BTCUSDT" in state.last_seen_close
    assert "BTCUSDT" not in state.open_positions  # first sighting — no entry

    # ── 3. Make the breakout candle "new" by advancing close_time ──
    def _newer_k15(sym, interval, limit):
        if interval != "15m":
            return _uptrend_1h()
        base = _consolidation_then_breakout()
        # Advance close_time by one candle so strategy detects a new close
        base[-1] = Kline(base[-1].open_time + 900, base[-1].open, base[-1].high,
                         base[-1].low, base[-1].close, base[-1].volume,
                         base[-1].close_time + 900)
        return base
    client.fetch_klines.side_effect = _newer_k15

    await strategy.poll_once()
    assert "BTCUSDT" in state.open_positions
    pos = state.open_positions["BTCUSDT"]
    assert pos.entry_price == pytest.approx(100.1, rel=1e-2)
    assert pos.tp_hit is False

    # ── 4. Manage at TP1: current price >= pos.tp_price ────────
    client.fetch_klines = AsyncMock(return_value=[
        Kline(0, 100.0, 105.0, 100.0, 104.5, 1200.0, 0),  # prev
        Kline(0, 104.5, 105.5, 104.0, 104.8, 1300.0, 0),  # curr
        Kline(0, 104.8, 105.6, 104.5, 104.6, 1400.0, 0),  # latest (current_price source)
    ])
    # book for sells
    client.fetch_order_book = AsyncMock(return_value=_book(bid=104.5, ask=104.8))
    await execution.manage_positions()
    assert pos.tp_hit is True
    assert pos.qty == pytest.approx(pos.cost_usd * (1 - 0.006) / 100.1 * 0.5, rel=5e-2)

    # ── 5. Manage at trail: price drops back below peak*0.98 ──
    pos.peak_price = 108.0  # force a peak
    # recent candles should not trigger breakout-failed (resistance ~ 100.2)
    client.fetch_klines = AsyncMock(return_value=[
        Kline(0, 106.0, 106.5, 105.5, 106.0, 1000.0, 0),
        Kline(0, 106.0, 106.2, 105.5, 105.8, 1000.0, 0),
        Kline(0, 105.8, 105.9, 105.4, 105.5, 1000.0, 0),  # current_price 105.5
    ])
    client.fetch_order_book = AsyncMock(return_value=_book(bid=105.5, ask=105.6))
    await execution.manage_positions()
    # 108.0 peak, trail 2% → 105.84 → 105.5 triggers
    assert "BTCUSDT" not in state.open_positions

    closed = db.get_closed_positions()
    assert len(closed) == 1
    assert closed[0]["reason_exit"] == "trail"
```

- [ ] **Step 2: Run the test to verify it passes**

```
python -m pytest test_breakout_integration.py -v
```
Expected: 1 passed.

If it fails, investigate — the integration test catches contract mismatches between components. Do not relax it to pass. Fix the underlying module(s), re-run unit tests for those modules, then re-run the integration test.

- [ ] **Step 3: Run the entire breakout test suite**

```
python -m pytest test_breakout_*.py -v
```
Expected: all tests pass (Task 1-16 combined: roughly 65+ tests).

- [ ] **Step 4: Commit**

```bash
git add test_breakout_integration.py
git commit -m "add breakout end-to-end integration test"
```

---

## Task 17: Ship dormant + final verification

Final deploy: code is live on Railway, `BREAKOUT_ENABLED=false` by default. Flip to `true` only via env var. Confirm no regression in existing strategies.

**Files:**
- None modified in code — this task is verification + deploy.

- [ ] **Step 1: Full test suite green**

```
python -m pytest -v
```
Expected: all tests pass, including any pre-existing tests. If anything regresses, stop and fix before proceeding.

- [ ] **Step 2: Confirm default is dormant**

Grep to confirm code default is `False`:

```bash
grep -n "breakout_enabled" utils/config.py
```

Expected output includes: `breakout_enabled: bool = False`

The flip happens **only** via the `BREAKOUT_ENABLED` env var on Railway — **never** change the code default to `True`.

- [ ] **Step 3: Verify local start (dormant)**

```bash
python main.py
```

Expected log line: `[Main] Breakout disabled (BREAKOUT_ENABLED=false)`. Ctrl-C.

- [ ] **Step 4: Commit any remaining changes**

```bash
git status
```

If untracked/uncommitted files remain from the plan (e.g., added imports missed earlier), add & commit them with a message describing what's left. If nothing is pending, skip this step.

- [ ] **Step 5: Deploy to Railway**

```bash
MSYS_NO_PATHCONV=1 railway up --detach
```

Expected: deploy succeeds, Railway logs show `[Main] Breakout disabled (BREAKOUT_ENABLED=false)` in the startup log.

- [ ] **Step 6: Watch Railway logs for clean startup**

```bash
MSYS_NO_PATHCONV=1 railway logs --tail 100
```

Verify:
- No ImportError
- Existing strategies (scalper, dip-buy, scanners) start normally
- `[Main] Breakout disabled (BREAKOUT_ENABLED=false)` appears once

- [ ] **Step 7: Browser check**

Open the dashboard URL. Confirm:
- Existing sections render unchanged
- Breakout section is hidden (panel `display:none` until `/api/breakout/state` returns) OR is visible with empty tables

- [ ] **Step 8: Final commit (if any drift)**

If the smoke tests surfaced fixes, commit them. Otherwise skip.

- [ ] **Step 9: Report done to user**

Reply to the user with:
- Deployed dormant, `BREAKOUT_ENABLED=false`
- To activate: set `BREAKOUT_ENABLED=true` in Railway Variables, redeploy
- Dashboard section is wired, will populate once enabled
- Test suite count (e.g., "75 tests passing")

---

## Checkpoints summary

| Checkpoint | After task | Outcome |
|-----------|-----------|---------|
| 1 | Task 2 | Config + capital pool standalone |
| 2 | Task 4 | Pure scoring functions complete |
| 3 | Task 5 | Paper fills work against mocked book |
| 4 | Task 6 | Live Binance.US data fetchable (public) |
| 5 | Task 7 | Isolated SQLite persistence works |
| 6 | Task 8 | Shared state container ready |
| 7 | Task 9 | Scanner produces top-5 from mocked data |
| 8 | Task 10 | Entry engine detects candle close + scores |
| 9 | Task 11 | Entries open correctly, cooldown helpers work |
| 10 | Task 12 | Full position management (all exit paths) |
| 11 | Task 13 | Dashboard API endpoints serve data |
| 12 | Task 14 | Dashboard UI panel renders |
| 13 | Task 15 | `main.py` wires all tasks behind `BREAKOUT_ENABLED` |
| 14 | Task 16 | End-to-end integration test passes |
| 15 | Task 17 | Deployed dormant to Railway — safe to flip on |

Each checkpoint is a commit on green tests. The repo is always shippable.

## Notes for implementer

- **Config defaults live in code; `BREAKOUT_ENABLED=false` is the ship state.** Never change the code default to `true` — activation is via env var on Railway.
- **No daily trade cap.** The user explicitly removed it. Don't add it back under any guise.
- **Independent from `TRADING_PAUSED`.** The breakout strategy has its own kill switch. Don't add `if config.trading_paused: return` anywhere in the `breakout/` package.
- **Paper mode only for v1.** Do not write any live-order code. If a task feels like it needs it, re-read the spec.
- **Windows/Git Bash:** use `python -m pytest` (not `pytest` bare), use Edit tool (not `sed -i`), use forward slashes or escaped backslashes in paths.
- **Don't skip failing tests.** If a test is wrong, fix the test. If the implementation is wrong, fix the implementation. If the plan is wrong, update the plan AND the spec.
- **Commit frequently.** Every green set of tests = commit. If a task has multiple logical pieces, it's fine to split its commit.
- **When in doubt, read the spec:** `docs/superpowers/specs/2026-04-17-breakout-strategy-design.md`
