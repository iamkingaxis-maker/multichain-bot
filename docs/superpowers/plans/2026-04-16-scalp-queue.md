# ScalpQueue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a high-volume scalping strategy — $200 positions, 3% TP1 (50%) + 5% TP2 (50%), 2.5% hard stop, 45-min time stop — running independently from a dedicated $2000 capital pool.

**Architecture:** ScalpCapitalManager tracks independent capital. DexScreener REST (every 90s) feeds candidates into ScalpQueue's watch set. Axiom tick gate decides exact entry. PositionManager gets a new `scalp` branch between dip_buy and standard. Stop callbacks wire back through ScalpQueue for cooldown tracking.

**Tech Stack:** Python asyncio, aiohttp (DexScreener REST), existing Axiom price feed (`axiom_price_feed._tick_buffers`, `get_tick_trend`, `get_tick_count`), existing `trader.buy()`, `PositionManager`, `Config`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `core/scalp_capital.py` | Create | Independent $2000 pool: deployed capital, concurrent cap, daily loss limit |
| `utils/config.py` | Modify | Add `scalp_*` fields + env overrides |
| `feeds/scalp_queue.py` | Create | DexScreener feeder, watch set, Axiom tick gate, entry trigger, stop cooldowns |
| `core/position_manager.py` | Modify | Add `scalp` branch (TP1/TP2/stop/time stop), `scalp_queue` ref, scalp params |
| `main.py` | Modify | Instantiate ScalpCapitalManager + ScalpQueue, wire to position_manager, append task |
| `test_scalp_capital.py` | Create | Unit tests for capital manager logic |
| `test_scalp_queue.py` | Create | Unit tests for quality gates + tick gate |
| `test_scalp_position_manager.py` | Create | Unit tests for PositionState scalp branch |

---

## Task 1: ScalpCapitalManager

**Files:**
- Create: `core/scalp_capital.py`
- Create: `test_scalp_capital.py`

- [ ] **Step 1: Write the failing tests**

```python
# test_scalp_capital.py
import pytest
from core.scalp_capital import ScalpCapitalManager


def make_mgr(**kw):
    return ScalpCapitalManager(**kw)


def test_has_capacity_initially_true():
    mgr = make_mgr()
    assert mgr.has_capacity() is True


def test_has_capacity_false_when_full():
    mgr = make_mgr(max_concurrent=2)
    mgr.record_open("AAA", 200.0)
    mgr.record_open("BBB", 200.0)
    assert mgr.has_capacity() is False


def test_has_capacity_restored_after_close():
    mgr = make_mgr(max_concurrent=1)
    mgr.record_open("AAA", 200.0)
    assert mgr.has_capacity() is False
    mgr.record_close("AAA", pnl_usd=5.0)
    assert mgr.has_capacity() is True


def test_daily_loss_limit_blocks_capacity():
    mgr = make_mgr(daily_loss_limit=400.0)
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=-401.0)
    assert mgr.has_capacity() is False


def test_daily_loss_not_hit_on_smaller_loss():
    mgr = make_mgr(daily_loss_limit=400.0)
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=-100.0)
    assert mgr.has_capacity() is True


def test_deployed_usd():
    mgr = make_mgr()
    mgr.record_open("AAA", 200.0)
    mgr.record_open("BBB", 200.0)
    assert mgr.deployed_usd() == 400.0


def test_available_usd():
    mgr = make_mgr(total_capital=2000.0)
    mgr.record_open("AAA", 200.0)
    assert mgr.available_usd() == 1800.0


def test_record_close_removes_from_open():
    mgr = make_mgr()
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=0.0)
    assert mgr.deployed_usd() == 0.0


def test_daily_loss_cumulative():
    mgr = make_mgr(daily_loss_limit=400.0)
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=-200.0)
    mgr.record_open("BBB", 200.0)
    mgr.record_close("BBB", pnl_usd=-201.0)
    # cumulative -401 > 400 limit
    assert mgr.has_capacity() is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd C:\Users\jcole\multichain-bot
python -m pytest test_scalp_capital.py -v
```
Expected: `ModuleNotFoundError: No module named 'core.scalp_capital'`

- [ ] **Step 3: Create `core/scalp_capital.py`**

```python
"""
ScalpCapitalManager — independent $2000 capital pool for the scalp strategy.

Completely separate from RiskManager. Tracks deployed capital,
concurrent position count, and cumulative daily P&L.
"""

import datetime
import calendar
import time
from dataclasses import dataclass, field
from typing import Dict


def _next_midnight_utc() -> float:
    now = datetime.datetime.utcnow()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return float(calendar.timegm(tomorrow.timetuple()))


@dataclass
class ScalpCapitalManager:
    total_capital: float = 2000.0
    max_position_usd: float = 200.0
    max_concurrent: int = 10
    daily_loss_limit: float = 400.0

    _open: Dict[str, float] = field(default_factory=dict, init=False)
    _daily_pnl: float = field(default=0.0, init=False)
    _daily_loss_hit: bool = field(default=False, init=False)
    _day_reset_ts: float = field(default=0.0, init=False)

    def __post_init__(self):
        self._day_reset_ts = _next_midnight_utc()

    # ── Public API ──────────────────────────────────────────────

    def has_capacity(self) -> bool:
        self._check_day_reset()
        if self._daily_loss_hit:
            return False
        return len(self._open) < self.max_concurrent

    def record_open(self, addr: str, usd: float):
        self._open[addr] = usd

    def record_close(self, addr: str, pnl_usd: float):
        self._check_day_reset()
        self._open.pop(addr, None)
        self._daily_pnl += pnl_usd
        if self._daily_pnl <= -self.daily_loss_limit:
            self._daily_loss_hit = True

    def deployed_usd(self) -> float:
        return sum(self._open.values())

    def available_usd(self) -> float:
        return self.total_capital - self.deployed_usd()

    # ── Internal ────────────────────────────────────────────────

    def _check_day_reset(self):
        if time.time() >= self._day_reset_ts:
            self._daily_pnl = 0.0
            self._daily_loss_hit = False
            self._day_reset_ts = _next_midnight_utc()
```

- [ ] **Step 4: Run tests — expect all pass**

```
python -m pytest test_scalp_capital.py -v
```
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add core/scalp_capital.py test_scalp_capital.py
git commit -m "feat: add ScalpCapitalManager — independent $2000 scalp pool"
```

---

## Task 2: Config scalp_* fields

**Files:**
- Modify: `utils/config.py`

- [ ] **Step 1: Add scalp fields to the `Config` dataclass**

Add this block after the `# ── Dip Buyer ─` section (after line `dip_max_concurrent: int = 2`):

```python
    # ── Scalp Queue ──────────────────────────────────────────────
    scalp_enabled: bool = True
    scalp_capital: float = 2000.0
    scalp_position_usd: float = 200.0
    scalp_tp1_pct: float = 3.0
    scalp_tp2_pct: float = 5.0
    scalp_stop_pct: float = 2.5
    scalp_max_concurrent: int = 10
    scalp_max_hold_minutes: float = 45.0
    scalp_daily_loss_limit: float = 400.0
    scalp_min_mcap: float = 1_000_000
    scalp_min_age_days: float = 7.0
    scalp_min_volume_h24: float = 200_000
    scalp_max_watch_candidates: int = 25
    scalp_watch_expiry_minutes: float = 30.0
    scalp_max_entry_move_pct: float = 3.0
    scalp_tick_ratio_min: float = 0.65
    scalp_tick_consecutive_min: int = 3
    scalp_stop_cooldown_minutes: float = 30.0
```

- [ ] **Step 2: Add env overrides to `_apply_env_overrides`**

Add this block at the end of `_apply_env_overrides` (before the closing brace — after the `DIP_SCANNER_ENABLED` block):

```python
    # Scalp queue
    if os.environ.get("SCALP_ENABLED"):
        config.scalp_enabled = env_bool("SCALP_ENABLED", config.scalp_enabled)
    if os.environ.get("SCALP_CAPITAL"):
        config.scalp_capital = env_float("SCALP_CAPITAL", config.scalp_capital)
    if os.environ.get("SCALP_POSITION_USD"):
        config.scalp_position_usd = env_float("SCALP_POSITION_USD", config.scalp_position_usd)
    if os.environ.get("SCALP_STOP_PCT"):
        config.scalp_stop_pct = env_float("SCALP_STOP_PCT", config.scalp_stop_pct)
    if os.environ.get("SCALP_MAX_CONCURRENT"):
        config.scalp_max_concurrent = env_int("SCALP_MAX_CONCURRENT", config.scalp_max_concurrent)
```

- [ ] **Step 3: Verify config loads without error**

```
cd C:\Users\jcole\multichain-bot
python -c "from utils.config import Config; c = Config.load(); print(c.scalp_enabled, c.scalp_position_usd, c.scalp_stop_pct)"
```
Expected: `True 200.0 2.5`

- [ ] **Step 4: Commit**

```bash
git add utils/config.py
git commit -m "feat: add scalp_* config fields with env overrides"
```

---

## Task 3: ScalpQueue

**Files:**
- Create: `feeds/scalp_queue.py`
- Create: `test_scalp_queue.py`

- [ ] **Step 1: Write failing tests**

```python
# test_scalp_queue.py
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from core.scalp_capital import ScalpCapitalManager
from feeds.scalp_queue import ScalpQueue


def make_config(**overrides):
    cfg = MagicMock()
    cfg.scalp_position_usd = 200.0
    cfg.scalp_min_mcap = 1_000_000
    cfg.scalp_min_age_days = 7.0
    cfg.scalp_min_volume_h24 = 200_000
    cfg.scalp_max_watch_candidates = 25
    cfg.scalp_watch_expiry_minutes = 30.0
    cfg.scalp_max_entry_move_pct = 3.0
    cfg.scalp_tick_ratio_min = 0.65
    cfg.scalp_tick_consecutive_min = 3
    cfg.scalp_stop_cooldown_minutes = 30.0
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_pair(mcap=2_000_000, age_ms=None, vol_h24=500_000, change_h24=5.0, addr="ADDR1", symbol="TEST", price="0.001"):
    if age_ms is None:
        age_ms = time.time() * 1000 - 10 * 86_400 * 1000  # 10 days ago
    return {
        "baseToken": {"address": addr, "symbol": symbol},
        "marketCap": mcap,
        "pairCreatedAt": age_ms,
        "volume": {"h24": vol_h24},
        "priceChange": {"h24": change_h24},
        "priceUsd": price,
    }


def make_queue(**cfg_overrides):
    trader = MagicMock()
    trader.buy = AsyncMock()
    capital = ScalpCapitalManager()
    cfg = make_config(**cfg_overrides)
    q = ScalpQueue(
        trader=trader,
        axiom_price_feed=None,
        open_positions_ref={},
        scalp_capital=capital,
        config=cfg,
    )
    return q, trader, capital


# ── Quality gate tests ──────────────────────────────────────────

def test_gate_passes_good_pair():
    q, _, _ = make_queue()
    pair = make_pair()
    assert q._passes_quality_gates(pair, "ADDR1") is True


def test_gate_rejects_low_mcap():
    q, _, _ = make_queue()
    pair = make_pair(mcap=500_000)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_young_pair():
    q, _, _ = make_queue()
    pair = make_pair(age_ms=time.time() * 1000 - 3 * 86_400 * 1000)  # 3 days
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_low_volume():
    q, _, _ = make_queue()
    pair = make_pair(vol_h24=100_000)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_downtrend():
    q, _, _ = make_queue()
    pair = make_pair(change_h24=-2.0)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_already_in_open_positions():
    q, _, _ = make_queue()
    q.open_positions_ref["ADDR1"] = object()
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_stop_cooldown():
    q, _, _ = make_queue()
    q._stop_cooldowns["ADDR1"] = time.monotonic() + 1000
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_passes_after_cooldown_expires():
    q, _, _ = make_queue()
    q._stop_cooldowns["ADDR1"] = time.monotonic() - 1  # expired
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is True


def test_gate_rejects_when_watch_full():
    q, _, _ = make_queue(scalp_max_watch_candidates=2)
    q._watch["X"] = {}
    q._watch["Y"] = {}
    pair = make_pair(addr="ADDR3")
    assert q._passes_quality_gates(pair, "ADDR3") is False


# ── on_scalp_close tests ────────────────────────────────────────

def test_on_scalp_close_stop_sets_cooldown():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    q.on_scalp_close("ADDR1", "stop_loss", pnl_usd=-8.0)
    assert "ADDR1" in q._stop_cooldowns
    assert q._stop_cooldowns["ADDR1"] > time.monotonic()


def test_on_scalp_close_tp_no_cooldown():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    q.on_scalp_close("ADDR1", "scalp_tp2", pnl_usd=7.0)
    assert "ADDR1" not in q._stop_cooldowns


def test_on_scalp_close_updates_capital():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    assert capital.deployed_usd() == 200.0
    q.on_scalp_close("ADDR1", "scalp_tp2", pnl_usd=7.0)
    assert capital.deployed_usd() == 0.0


# ── Watch set pruning tests ─────────────────────────────────────

def test_prune_removes_expired_watches():
    q, _, _ = make_queue(scalp_watch_expiry_minutes=0.001)  # ~0.06s
    q._watch["OLD"] = {"symbol": "OLD", "entry_price": 0.001, "entry_ts": time.monotonic() - 10}
    q._prune_watch_set()
    assert "OLD" not in q._watch


def test_prune_keeps_fresh_watches():
    q, _, _ = make_queue()
    q._watch["NEW"] = {"symbol": "NEW", "entry_price": 0.001, "entry_ts": time.monotonic()}
    q._prune_watch_set()
    assert "NEW" in q._watch


# ── Buy/sell ratio calculation ──────────────────────────────────

def test_buy_sell_ratio_all_buys():
    q, _, _ = make_queue()
    now = time.monotonic()
    apf = MagicMock()
    apf._tick_buffers = {
        "ADDR1": [(now - 1, 0.001), (now - 2, 0.002), (now - 3, 0.003)]
    }
    ratio = q._get_buy_sell_ratio(apf, "ADDR1", 30)
    assert ratio == 1.0


def test_buy_sell_ratio_mixed():
    q, _, _ = make_queue()
    now = time.monotonic()
    apf = MagicMock()
    # 3 buys (positive price change), 1 sell (negative)
    apf._tick_buffers = {
        "ADDR1": [(now - 1, 0.001), (now - 2, 0.002), (now - 3, 0.003), (now - 4, -0.001)]
    }
    ratio = q._get_buy_sell_ratio(apf, "ADDR1", 30)
    assert ratio == pytest.approx(0.75)


def test_buy_sell_ratio_empty_buffer():
    q, _, _ = make_queue()
    apf = MagicMock()
    apf._tick_buffers = {}
    ratio = q._get_buy_sell_ratio(apf, "ADDR1", 30)
    assert ratio == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest test_scalp_queue.py -v
```
Expected: `ModuleNotFoundError: No module named 'feeds.scalp_queue'`

- [ ] **Step 3: Create `feeds/scalp_queue.py`**

```python
"""
ScalpQueue — high-volume scalp strategy feeder.

Two inputs:
  A. DexScreener REST scan every 90s (quality-gated candidates)
  B. (future) Axiom trending events

Candidates enter a watch set (max 25, 30-min expiry).
Axiom tick gate fires entry when:
  1. 3+ consecutive upticks in last 15s
  2. Buy/sell ratio > 0.65 over last 30s
  3. Positive tick trend over 30s
  4. Price movement since watch entry <= 3%
  5. ScalpCapitalManager has capacity
"""

import asyncio
import logging
import time
import aiohttp
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 90  # seconds
_DEX_CHAIN = "solana"
_SEARCH_TERMS = ["sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "ai", "pump", "baby"]


class ScalpQueue:
    def __init__(self,
                 trader,
                 axiom_price_feed,
                 open_positions_ref: dict,
                 scalp_capital,
                 config):
        self.trader = trader
        self.axiom_price_feed = axiom_price_feed
        self.open_positions_ref = open_positions_ref
        self.scalp_capital = scalp_capital
        self.cfg = config

        # addr -> {"symbol", "entry_price", "entry_ts"}
        self._watch: Dict[str, dict] = {}
        # addr -> monotonic expiry timestamp
        self._stop_cooldowns: Dict[str, float] = {}

    async def run(self):
        logger.info("[ScalpQueue] Starting — watching for scalp entries")
        while True:
            try:
                await self._scan_cycle()
                self._prune_watch_set()
            except Exception as e:
                logger.error(f"[ScalpQueue] Error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    def on_scalp_close(self, addr: str, reason: str, pnl_usd: float = 0.0):
        """Called by PositionManager on every scalp position close."""
        self.scalp_capital.record_close(addr, pnl_usd)
        if reason == "stop_loss":
            expiry = time.monotonic() + self.cfg.scalp_stop_cooldown_minutes * 60
            self._stop_cooldowns[addr] = expiry
            logger.info(
                f"[ScalpQueue] Stop cooldown: {addr[:8]} "
                f"({self.cfg.scalp_stop_cooldown_minutes:.0f}min)"
            )

    # ── Feeder A: DexScreener scan ──────────────────────────────

    async def _scan_cycle(self):
        if not self.scalp_capital.has_capacity():
            return
        if len(self._watch) >= self.cfg.scalp_max_watch_candidates:
            # Still check tick gate for existing watches
            for addr in list(self._watch.keys()):
                await self._check_tick_gate(addr)
            return

        pairs = await self._fetch_dex_candidates()
        for pair in pairs:
            addr = (pair.get("baseToken") or {}).get("address", "")
            symbol = (pair.get("baseToken") or {}).get("symbol", "?")
            if not addr or addr in self._watch:
                continue
            if not self._passes_quality_gates(pair, addr):
                continue
            price = float((pair.get("priceUsd") or "0") or 0)
            self._watch[addr] = {
                "symbol": symbol,
                "entry_price": price,
                "entry_ts": time.monotonic(),
            }
            logger.debug(f"[ScalpQueue] Watching {symbol} ({addr[:8]})")

        for addr in list(self._watch.keys()):
            await self._check_tick_gate(addr)

    def _passes_quality_gates(self, pair: dict, addr: str) -> bool:
        if addr in self.open_positions_ref:
            return False
        if time.monotonic() < self._stop_cooldowns.get(addr, 0):
            return False
        if not self.scalp_capital.has_capacity():
            return False
        if len(self._watch) >= self.cfg.scalp_max_watch_candidates:
            return False

        mcap = float(pair.get("marketCap") or 0)
        if mcap < self.cfg.scalp_min_mcap:
            return False

        pair_created_ms = pair.get("pairCreatedAt") or 0
        age_days = (time.time() * 1000 - pair_created_ms) / (86_400 * 1000)
        if age_days < self.cfg.scalp_min_age_days:
            return False

        volume_h24 = float((pair.get("volume") or {}).get("h24") or 0)
        if volume_h24 < self.cfg.scalp_min_volume_h24:
            return False

        price_change_h24 = float((pair.get("priceChange") or {}).get("h24") or 0)
        if price_change_h24 <= 0:
            return False

        return True

    # ── Tick gate ───────────────────────────────────────────────

    async def _check_tick_gate(self, addr: str):
        if addr not in self._watch:
            return

        meta = self._watch[addr]
        symbol = meta["symbol"]

        if addr in self.open_positions_ref:
            del self._watch[addr]
            return

        if not self.scalp_capital.has_capacity():
            return

        apf = self.axiom_price_feed
        if apf is None:
            return

        # Gate 4: price must not have moved > 3% from watch entry
        current_price = (getattr(apf, "_price_cache", {}) or {}).get(addr, 0)
        if current_price <= 0:
            return
        entry_price = meta["entry_price"]
        if entry_price > 0:
            move_pct = abs(current_price - entry_price) / entry_price * 100
            if move_pct > self.cfg.scalp_max_entry_move_pct:
                logger.debug(
                    f"[ScalpQueue] {symbol}: {move_pct:.1f}% move from watch — dropping"
                )
                del self._watch[addr]
                return

        # Gate 1: 3+ consecutive upticks in last 15s
        tick_count = (
            apf.get_tick_count(addr, 15) if hasattr(apf, "get_tick_count") else 0
        )
        if tick_count < self.cfg.scalp_tick_consecutive_min:
            return

        # Gate 3: positive tick trend over 30s
        trend = (
            apf.get_tick_trend(addr, 30) if hasattr(apf, "get_tick_trend") else 0
        )
        if trend <= 0:
            return

        # Gate 2: buy/sell ratio > 0.65 over last 30s
        ratio = self._get_buy_sell_ratio(apf, addr, 30)
        if ratio < self.cfg.scalp_tick_ratio_min:
            return

        # All gates passed — fire entry
        logger.info(
            f"[ScalpQueue] ENTRY {symbol} ({addr[:8]}) "
            f"ticks={tick_count} trend={trend:.3f} ratio={ratio:.2f}"
        )
        del self._watch[addr]

        await self.trader.buy(
            token_address=addr,
            token_symbol=symbol,
            strategy="scalp",
            override_usd=self.cfg.scalp_position_usd,
            reason=f"scalp: ticks={tick_count} trend={trend:.3f} ratio={ratio:.2f}",
        )
        self.scalp_capital.record_open(addr, self.cfg.scalp_position_usd)
        self._stop_cooldowns.pop(addr, None)

    def _get_buy_sell_ratio(self, apf, addr: str, seconds: int) -> float:
        buf = (getattr(apf, "_tick_buffers", {}) or {}).get(addr)
        if not buf:
            return 0.0
        now = time.monotonic()
        cutoff = now - seconds
        recent = [t for t in buf if t[0] >= cutoff]
        if not recent:
            return 0.0
        buys = sum(1 for t in recent if t[1] > 0)
        return buys / len(recent)

    # ── Watch set maintenance ───────────────────────────────────

    def _prune_watch_set(self):
        now_mono = time.monotonic()
        expiry_secs = self.cfg.scalp_watch_expiry_minutes * 60

        to_drop = [
            addr for addr, meta in self._watch.items()
            if now_mono - meta["entry_ts"] > expiry_secs
        ]
        for addr in to_drop:
            logger.debug(f"[ScalpQueue] Expired: {self._watch[addr]['symbol']}")
            del self._watch[addr]

        self._stop_cooldowns = {
            addr: exp for addr, exp in self._stop_cooldowns.items()
            if exp > now_mono
        }

    # ── DexScreener REST ────────────────────────────────────────

    async def _fetch_dex_candidates(self) -> list:
        pairs = []
        async with aiohttp.ClientSession() as session:
            for term in _SEARCH_TERMS[:5]:
                try:
                    url = (
                        f"https://api.dexscreener.com/latest/dex/search"
                        f"?q={term}&chain={_DEX_CHAIN}"
                    )
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pairs.extend(data.get("pairs") or [])
                except Exception as e:
                    logger.debug(f"[ScalpQueue] DexScreener error ({term}): {e}")
        return pairs
```

- [ ] **Step 4: Run tests — expect all pass**

```
python -m pytest test_scalp_queue.py -v
```
Expected: `18 passed`

- [ ] **Step 5: Commit**

```bash
git add feeds/scalp_queue.py test_scalp_queue.py
git commit -m "feat: add ScalpQueue — DexScreener feeder + Axiom tick gate"
```

---

## Task 4: PositionManager scalp branch

**Files:**
- Modify: `core/position_manager.py`
- Create: `test_scalp_position_manager.py`

- [ ] **Step 1: Write failing tests**

```python
# test_scalp_position_manager.py
"""
Unit tests for the PositionManager scalp branch.
Tests TP1 (3%/50%), TP2 (5%/100%), hard stop (2.5%), time stop (45min).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from core.position_manager import PositionManager, PositionState, MarketConditionMonitor


def make_state(strategy="scalp", pnl_pct_val=0.0, tp1_hit=False, minutes_open=0):
    entry_price = 1.0
    current_price = entry_price * (1 + pnl_pct_val / 100)
    entry_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_open)
    state = PositionState(
        token_address="ADDR1",
        token_symbol="TEST",
        chain_id="solana",
        entry_price=entry_price,
        entry_volume_usd=0.0,
        position_size_usd=200.0,
        original_size_usd=200.0,
        entry_time=entry_time,
        strategy=strategy,
        current_price=current_price,
        peak_price=max(entry_price, current_price),
        tp1_hit=tp1_hit,
    )
    return state


def make_mgr(**kwargs):
    trader = MagicMock()
    trader.open_positions = {}
    mgr = PositionManager(
        chain_name="Solana",
        chain_id="solana",
        trader=trader,
        open_positions_ref=trader.open_positions,
        telegram=MagicMock(),
        tracker=MagicMock(),
        market_monitor=MarketConditionMonitor(),
        scalp_tp1_pct=kwargs.get("scalp_tp1_pct", 3.0),
        scalp_tp2_pct=kwargs.get("scalp_tp2_pct", 5.0),
        scalp_stop_pct=kwargs.get("scalp_stop_pct", 2.5),
        scalp_max_hold_minutes=kwargs.get("scalp_max_hold_minutes", 45.0),
    )
    mgr._execute_sell = AsyncMock()
    return mgr


@pytest.mark.asyncio
async def test_scalp_tp1_fires_at_3pct():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=3.0)
    assert state.tp1_hit is False
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once_with("ADDR1", state, pct=0.5, reason=pytest.approx("Scalp TP1 +3.0%", abs=0))
    assert state.tp1_hit is True


@pytest.mark.asyncio
async def test_scalp_tp1_does_not_fire_below_3pct():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=2.9)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_tp2_fires_at_5pct_after_tp1():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=5.0, tp1_hit=True)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once()
    call_args = mgr._execute_sell.call_args
    assert call_args.kwargs.get("pct") == 1.0 or call_args.args[2] == 1.0


@pytest.mark.asyncio
async def test_scalp_tp2_does_not_fire_without_tp1():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=5.0, tp1_hit=False)
    await mgr._evaluate_scalp("ADDR1", state)
    # Should fire TP1, not TP2
    call_kwargs = mgr._execute_sell.call_args
    reason = call_kwargs.kwargs.get("reason", "") or call_kwargs.args[3]
    assert "TP1" in reason


@pytest.mark.asyncio
async def test_scalp_hard_stop_at_2pt5pct():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=-2.5)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once()
    call_kwargs = mgr._execute_sell.call_args
    reason = call_kwargs.kwargs.get("reason", "") or call_kwargs.args[3]
    assert "stop" in reason.lower()


@pytest.mark.asyncio
async def test_scalp_stop_does_not_fire_above_threshold():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=-2.4)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_time_stop_at_45min():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=0.0, minutes_open=46)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once()
    call_kwargs = mgr._execute_sell.call_args
    reason = call_kwargs.kwargs.get("reason", "") or call_kwargs.args[3]
    assert "time" in reason.lower()


@pytest.mark.asyncio
async def test_scalp_time_stop_does_not_fire_before_45min():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=0.0, minutes_open=44)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_stop_notifies_scalp_queue():
    mgr = make_mgr()
    scalp_queue = MagicMock()
    mgr.scalp_queue = scalp_queue
    state = make_state(pnl_pct_val=-2.5)
    await mgr._evaluate_scalp("ADDR1", state)
    scalp_queue.on_scalp_close.assert_called_once()
    call_args = scalp_queue.on_scalp_close.call_args
    assert call_args.args[0] == "ADDR1"
    assert call_args.args[1] == "stop_loss"


@pytest.mark.asyncio
async def test_scalp_tp_notifies_scalp_queue_on_tp2():
    mgr = make_mgr()
    scalp_queue = MagicMock()
    mgr.scalp_queue = scalp_queue
    state = make_state(pnl_pct_val=5.0, tp1_hit=True)
    await mgr._evaluate_scalp("ADDR1", state)
    scalp_queue.on_scalp_close.assert_called_once()
    call_args = scalp_queue.on_scalp_close.call_args
    assert call_args.args[1] == "scalp_tp2"
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest test_scalp_position_manager.py -v
```
Expected: `TypeError` or `AttributeError` — `PositionManager.__init__` doesn't accept `scalp_tp1_pct` yet, and `_evaluate_scalp` doesn't exist.

- [ ] **Step 3: Add scalp params to PositionManager.__init__**

In `core/position_manager.py`, find the `__init__` signature (around line 240). Add these params after the `dip_winner_trail_pct` param (before `scalper=None`):

```python
                 # Scalp strategy TP/SL
                 scalp_tp1_pct: float = 3.0,
                 scalp_tp2_pct: float = 5.0,
                 scalp_stop_pct: float = 2.5,
                 scalp_max_hold_minutes: float = 45.0,
```

Then in the `__init__` body (after `self.dip_winner_trail_pct = dip_winner_trail_pct`), add:

```python
        # Scalp
        self.scalp_tp1_pct = scalp_tp1_pct
        self.scalp_tp2_pct = scalp_tp2_pct
        self.scalp_stop_pct = scalp_stop_pct
        self.scalp_max_hold_minutes = scalp_max_hold_minutes
        self.scalp_queue = None  # set by main.py after construction
```

- [ ] **Step 4: Add `_evaluate_scalp` method to PositionManager**

Add this new method to the `PositionManager` class (after `_evaluate_position`, before `_execute_sell`):

```python
    async def _evaluate_scalp(self, token_address: str, state: PositionState):
        """Scalp branch: TP1 3%/50%, TP2 5%/100%, hard stop 2.5%, time stop 45min."""
        pnl_pct = state.pnl_pct
        hold_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()

        # Time stop — 45 minutes
        if hold_seconds >= self.scalp_max_hold_minutes * 60:
            logger.info(
                f"[PositionManager/{self.chain_name}] ⏱ SCALP TIME STOP: "
                f"{state.token_symbol} after {hold_seconds/60:.0f}min"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp time stop {hold_seconds/60:.0f}min"
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "scalp_time_stop", pnl_usd)
            return

        # Hard stop — 2.5%
        if pnl_pct <= -self.scalp_stop_pct:
            logger.warning(
                f"[PositionManager/{self.chain_name}] 🛑 SCALP STOP: "
                f"{state.token_symbol} at {pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp stop -{self.scalp_stop_pct:.1f}%"
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "stop_loss", pnl_usd)
            return

        # TP2 — 5%, sell remaining 50%
        if state.tp1_hit and pnl_pct >= self.scalp_tp2_pct:
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP2: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp TP2 +{pnl_pct:.1f}%"
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "scalp_tp2", pnl_usd)
            return

        # TP1 — 3%, sell 50%
        if not state.tp1_hit and pnl_pct >= self.scalp_tp1_pct:
            state.tp1_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP1: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=0.5,
                reason=f"Scalp TP1 +{pnl_pct:.1f}%"
            )
            return
```

- [ ] **Step 5: Add scalp branch dispatch in `_evaluate_position`**

In `_evaluate_position`, find the line `return  # End dip_buy path` (around line 942). Immediately after it, add:

```python
        # ═══════════════════════════════════════════════════════════════
        # SCALP POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.strategy == "scalp":
            await self._evaluate_scalp(token_address, state)
            return
```

- [ ] **Step 6: Run tests — expect all pass**

```
python -m pytest test_scalp_position_manager.py -v
```
Expected: `10 passed`

- [ ] **Step 7: Sanity check — existing tests still pass**

```
python -m pytest test_scalp_capital.py test_scalp_queue.py test_scalp_position_manager.py -v
```
Expected: all pass (no regressions in other test files if they exist).

- [ ] **Step 8: Commit**

```bash
git add core/position_manager.py test_scalp_position_manager.py
git commit -m "feat: add PositionManager scalp branch — TP1/TP2/stop/time stop"
```

---

## Task 5: main.py wiring

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add imports at the top of main.py**

In `main.py`, find the existing import block. Add after the `from feeds.dip_scanner import DipScanner` line:

```python
from feeds.scalp_queue import ScalpQueue
from core.scalp_capital import ScalpCapitalManager
```

- [ ] **Step 2: Wire ScalpQueue after the DipScanner block**

In `main.py`, find this block (around line 444):
```python
        if config.dip_scanner_enabled:
            dip_scanner = DipScanner(...)
            tasks.append(dip_scanner.run())
```

Immediately after that block (after `tasks.append(dip_scanner.run())`), add:

```python
        if config.scalp_enabled:
            scalp_capital = ScalpCapitalManager(
                total_capital=config.scalp_capital,
                max_position_usd=config.scalp_position_usd,
                max_concurrent=config.scalp_max_concurrent,
                daily_loss_limit=config.scalp_daily_loss_limit,
            )
            scalp_queue = ScalpQueue(
                trader=sol_trader,
                axiom_price_feed=axiom.price_feed if axiom.price_feed else None,
                open_positions_ref=sol_trader.open_positions,
                scalp_capital=scalp_capital,
                config=config,
            )
            sol_position_mgr.scalp_queue = scalp_queue
            tasks.append(scalp_queue.run())
            logger.info(
                f"[Main] ScalpQueue enabled — "
                f"${config.scalp_position_usd:.0f}/position, "
                f"TP1 +{config.scalp_tp1_pct}%/50%, "
                f"TP2 +{config.scalp_tp2_pct}%/50%, "
                f"stop -{config.scalp_stop_pct}%, "
                f"max {config.scalp_max_concurrent} concurrent"
            )
```

- [ ] **Step 3: Add scalp params to PositionManager instantiation in main.py**

Find the `sol_position_mgr = PositionManager(` block (around line 304). Add these params before `scalper=sol_scalper`:

```python
            scalp_tp1_pct=config.scalp_tp1_pct,
            scalp_tp2_pct=config.scalp_tp2_pct,
            scalp_stop_pct=config.scalp_stop_pct,
            scalp_max_hold_minutes=config.scalp_max_hold_minutes,
```

- [ ] **Step 4: Verify the bot starts without import errors**

```
cd C:\Users\jcole\multichain-bot
python -c "import main; print('OK')"
```
Expected: `OK` (no import errors)

- [ ] **Step 5: Run a dry-run start — verify ScalpQueue log line appears**

```
PAPER_MODE=true python main.py 2>&1 | head -60
```
Expected output includes:
```
[Main] ScalpQueue enabled — $200.0/position, TP1 +3.0%/50%, TP2 +5.0%/50%, stop -2.5%, max 10 concurrent
[ScalpQueue] Starting — watching for scalp entries
```

- [ ] **Step 6: Full test suite pass**

```
python -m pytest test_scalp_capital.py test_scalp_queue.py test_scalp_position_manager.py -v
```
Expected: all 37 tests pass.

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: wire ScalpQueue + ScalpCapitalManager into main.py"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered in task |
|---|---|
| $200 fixed position size | Config `scalp_position_usd=200`, Task 2 |
| TP1 3% → sell 50% | `_evaluate_scalp` TP1 block, Task 4 |
| TP2 5% → sell remaining 50% (100% of remaining) | `_evaluate_scalp` TP2 block, Task 4 |
| Hard stop 2.5% | `_evaluate_scalp` stop block, Task 4 |
| Max hold 45 minutes | `_evaluate_scalp` time stop, Task 4 |
| Max 10 concurrent | `ScalpCapitalManager.has_capacity()`, Task 1 |
| $2000 capital pool (independent) | `ScalpCapitalManager`, Task 1 |
| Daily loss limit $400 | `ScalpCapitalManager.record_close` + `_daily_loss_hit`, Task 1 |
| Quality gates (mcap/age/volume/uptrend) | `ScalpQueue._passes_quality_gates`, Task 3 |
| Not in open positions | `_passes_quality_gates` check, Task 3 |
| Stop-loss cooldown 30min | `on_scalp_close` sets `_stop_cooldowns`, Task 3 |
| Watch set cap at 25 | `scalp_max_watch_candidates` gate, Task 3 |
| Watch expiry 30min | `_prune_watch_set`, Task 3 |
| Drop if >3% move from watch entry | `_check_tick_gate` gate 4, Task 3 |
| 3+ consecutive upticks (15s) | `_check_tick_gate` gate 1, Task 3 |
| Buy/sell ratio >0.65 (30s) | `_check_tick_gate` gate 2 + `_get_buy_sell_ratio`, Task 3 |
| `axiom_price_feed.get_tick_trend(addr, 30) > 0` | `_check_tick_gate` gate 3, Task 3 |
| `ScalpCapitalManager.has_capacity()` before entry | gate in `_check_tick_gate`, Task 3 |
| `trader.buy(strategy="scalp", override_usd=200)` | `_check_tick_gate` fire, Task 3 |
| `scalp_capital.record_open(addr, 200)` after buy | `_check_tick_gate` fire, Task 3 |
| `scalp_queue.on_scalp_close` callback from PM | `_evaluate_scalp` all branches, Task 4 |
| Env overrides for 5 key vars | `_apply_env_overrides`, Task 2 |
| main.py wiring | Task 5 |

**Type consistency check:**
- `on_scalp_close(addr, reason, pnl_usd=0.0)` — used consistently in Task 3 (definition) and Task 4 (callers)
- `_evaluate_scalp(token_address, state)` — defined in Task 4 step 4, called in Task 4 step 5
- `ScalpCapitalManager` param names (`total_capital`, `max_position_usd`, `max_concurrent`, `daily_loss_limit`) — consistent between Task 1 definition and Task 5 instantiation
- `ScalpQueue` param names — consistent between Task 3 definition and Task 5 instantiation

**Placeholder scan:** No TBDs, no "implement later", all steps have code.
