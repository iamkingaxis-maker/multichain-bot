# Scalper Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dip-buy ScalpQueue entry logic with a disciplined 4-phase setup detector (impulse → pullback → sweep → reclaim) using free GeckoTerminal OHLCV data, and overhaul the scalp exit branch of PositionManager to match the spec (scaled TP, 6% hard stop, time-exit by candle count).

**Architecture:**
- New `feeds/gecko_ohlcv.py` wraps GeckoTerminal's free 5m OHLCV endpoint.
- New `feeds/candle_utils.py` holds pure candle math (EMA, VWAP, wick ratios, consecutive-reds).
- New `feeds/setup_detector.py` holds a per-token state machine that ingests candles and emits `TriggerSignal` objects with entry, stop, TP levels, and reason.
- `feeds/scalp_queue.py` is rewritten as an orchestrator that (a) discovers candidates via DexScreener quality gates, (b) polls GT OHLCV per candidate, (c) feeds candles to a `SetupDetector` instance per token, (d) applies global no-trade filters (SOL regime, majority-red, rug check), and (e) fires entries via `trader.buy` with sweep-low / TP metadata.
- `core/position_manager.py::_evaluate_scalp` is rewritten to use metadata attached at entry: TP1 @ +10% / 50%, TP2 @ +15% / 35%, runner on trailing stop, 6% hard stop, time-exit if not +5% within 4×5-minute candles, and holding-health exits (volume drop, momentum stall, rejection wick) via GT OHLCV.
- Config in `utils/config.py` gains new scalp fields and reduces `scalp_max_concurrent` to 5.

**Tech Stack:** Python 3.12, asyncio, aiohttp, pytest + pytest-asyncio, MagicMock/AsyncMock.

**Spec:** `docs/superpowers/specs/2026-04-18-scalper-rewrite-design.md`

---

## File Structure

**New files:**
- `feeds/gecko_ohlcv.py` — async GeckoTerminal OHLCV client (fetch + 60s cache + rate-limit).
- `feeds/candle_utils.py` — `Candle` dataclass + pure helpers (`ema`, `vwap`, `consecutive_reds_no_wick`, `sweep_lookback`).
- `feeds/setup_detector.py` — `SetupDetector` class (per-token state machine) + `TriggerSignal` dataclass.
- `test_gecko_ohlcv.py`, `test_candle_utils.py`, `test_setup_detector.py` — unit tests at repo root (matches existing `test_scalp_queue.py` pattern).

**Modified files:**
- `utils/config.py` — new scalp fields, shrink `scalp_max_concurrent`.
- `feeds/scalp_queue.py` — rewritten orchestrator.
- `core/position_manager.py` — rewrite `_evaluate_scalp` + add `scalp_ohlcv_fetcher` hook.
- `core/trader.py` — accept `scalp_meta` extras dict on buy, attach to position state.
- `main.py` — wire GT client, reduced max_concurrent.
- `test_scalp_queue.py` — update to orchestrator tests.
- `test_scalp_position_manager.py` — update for new TP tiers + 6% stop + candle-based time exit.

---

## Assumptions & Defaults (resolved from spec ranges)

| Spec item | Chosen default | Config key |
|---|---|---|
| Impulse magnitude 10–30% | min 10%, max 30% | `scalp_impulse_min_pct`, `scalp_impulse_max_pct` |
| Impulse lookback | 6 candles (30 min) | `scalp_impulse_lookback` |
| Pullback retrace 30–60% | min 30%, max 60% | `scalp_pullback_min_pct`, `scalp_pullback_max_pct` |
| Sweep vol spike ≥1.5× | 1.5× | `scalp_sweep_vol_mult` |
| Sweep vol lookback | 20 candles | `scalp_sweep_vol_lookback` |
| TP1 | +10% / 50% | `scalp_tp1_pct=10.0`, `scalp_tp1_sell=0.50` |
| TP2 | +15% / 35% of remaining | `scalp_tp2_pct=15.0`, `scalp_tp2_sell=0.35` |
| Hard stop | 6% | `scalp_stop_pct=6.0` |
| Time-exit | 4 candles (20 min), +5% threshold | `scalp_time_exit_candles=4`, `scalp_time_exit_min_pct=5.0` |
| R/R minimum | 2:1 | `scalp_min_rr=2.0` |
| Max concurrent | 5 | `scalp_max_concurrent=5` |
| Capital deployment cap | 80% | `scalp_max_deployment_pct=0.80` |
| Candidate 5m vol floor | $50k | `scalp_min_m5_volume_usd=50_000` |
| Candidate liquidity floor | $30k | `scalp_min_liquidity_usd=30_000` |
| Candidate age | 5 min – 6 h | `scalp_min_age_minutes=5`, `scalp_max_age_hours=6.0` |
| Rug LP drop | >10% within 10 min | `scalp_rug_lp_drop_pct=10.0` |
| GT rate limit | 25 req/min (safety margin below 30) | `scalp_gt_rate_per_min=25` |
| GT cache TTL | 60s | `scalp_gt_cache_ttl_sec=60` |

---

## Execution Order Notes

- Work proceeds pure-math → I/O → state-machine → orchestrator → position-manager → wiring. Each task's tests stand alone.
- Commits happen on green after each task.
- `PAPER_MODE=true` stays in place throughout; no code changes interact with live trading.
- Scalp stays **enabled** (`scalp_enabled=True` in config). Final deploy ships with the new strategy active under paper-mode.

---

### Task 1: Candle utilities (pure math)

**Files:**
- Create: `feeds/candle_utils.py`
- Test: `test_candle_utils.py`

- [ ] **Step 1: Write the failing test**

```python
# test_candle_utils.py
import pytest
from feeds.candle_utils import Candle, ema, consecutive_reds_no_wick, rolling_avg_volume


def _c(o, h, l, c, v, t=0):
    return Candle(open_time=t, open=o, high=h, low=l, close=c, volume=v, close_time=t + 299)


def test_candle_fields_positive():
    k = _c(1.0, 1.2, 0.9, 1.1, 1000.0)
    assert k.open == 1.0 and k.close == 1.1 and k.volume == 1000.0


def test_ema_matches_pandas_reference():
    # EMA(3) of [1,2,3,4,5] ≈ 4.125 (standard formula: alpha=2/(N+1))
    out = ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert abs(out - 4.125) < 0.001


def test_ema_handles_short_series():
    # If series is shorter than period, return simple mean
    out = ema([2.0, 4.0], 5)
    assert abs(out - 3.0) < 1e-9


def test_consecutive_reds_no_wick_true():
    # 3 red candles where low == min(open,close) (no lower wick)
    reds = [_c(1.0, 1.0, 0.9, 0.9, 100.0),
            _c(0.9, 0.9, 0.8, 0.8, 100.0),
            _c(0.8, 0.8, 0.7, 0.7, 100.0)]
    assert consecutive_reds_no_wick(reds, 3) is True


def test_consecutive_reds_no_wick_false_when_wick_present():
    reds = [_c(1.0, 1.0, 0.85, 0.9, 100.0),  # lower wick
            _c(0.9, 0.9, 0.8, 0.8, 100.0),
            _c(0.8, 0.8, 0.7, 0.7, 100.0)]
    assert consecutive_reds_no_wick(reds, 3) is False


def test_consecutive_reds_no_wick_false_on_green():
    mixed = [_c(1.0, 1.1, 0.95, 1.05, 100.0),  # green
             _c(1.05, 1.05, 0.95, 0.95, 100.0),
             _c(0.95, 0.95, 0.85, 0.85, 100.0)]
    assert consecutive_reds_no_wick(mixed, 3) is False


def test_rolling_avg_volume():
    kl = [_c(1, 1, 1, 1, v) for v in [100, 200, 300, 400, 500]]
    assert abs(rolling_avg_volume(kl, 5) - 300.0) < 1e-9
    assert abs(rolling_avg_volume(kl, 3) - 400.0) < 1e-9  # last 3: 300,400,500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_candle_utils.py -v`
Expected: FAIL — `ImportError: No module named 'feeds.candle_utils'`

- [ ] **Step 3: Implement**

```python
# feeds/candle_utils.py
"""Pure candle math — no I/O, no side effects. Reusable by detector + tests."""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


def ema(values: List[float], period: int) -> float:
    """Standard exponential moving average. Returns simple mean if series shorter than period."""
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    alpha = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1 - alpha) * e
    return e


def rolling_avg_volume(candles: List[Candle], n: int) -> float:
    """Mean volume of the last n candles (or all if fewer)."""
    if not candles:
        return 0.0
    tail = candles[-n:]
    return sum(c.volume for c in tail) / len(tail)


def consecutive_reds_no_wick(candles: List[Candle], n: int) -> bool:
    """
    True if the last n candles are all red AND have no lower wick
    (low == min(open, close)). Used by SOL regime guard.
    """
    if len(candles) < n:
        return False
    for c in candles[-n:]:
        if c.close >= c.open:
            return False
        if c.low < min(c.open, c.close) - 1e-12:  # lower wick present
            return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_candle_utils.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add feeds/candle_utils.py test_candle_utils.py
git commit -m "feat(scalp): add candle math utilities for new scalper"
```

---

### Task 2: GeckoTerminal OHLCV client

**Files:**
- Create: `feeds/gecko_ohlcv.py`
- Test: `test_gecko_ohlcv.py`

- [ ] **Step 1: Write the failing test**

```python
# test_gecko_ohlcv.py
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import time
from feeds.gecko_ohlcv import GeckoTerminalClient
from feeds.candle_utils import Candle


_SAMPLE_GT_RESPONSE = {
    "data": {
        "attributes": {
            # GT returns [timestamp_sec, open, high, low, close, volume_usd], newest first
            "ohlcv_list": [
                [1700000900, 1.10, 1.12, 1.08, 1.11, 5000.0],
                [1700000600, 1.08, 1.11, 1.07, 1.10, 4800.0],
                [1700000300, 1.05, 1.09, 1.05, 1.08, 4500.0],
            ]
        }
    }
}


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


class _FakeSession:
    def __init__(self, data):
        self._data = data
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        return _FakeResp(self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


@pytest.mark.asyncio
async def test_fetches_and_parses_candles():
    fake_sess = _FakeSession(_SAMPLE_GT_RESPONSE)
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=60)
    candles = await client.fetch_5m("POOLADDR")
    assert len(candles) == 3
    # Sorted oldest-first in return value
    assert candles[0].open_time == 1700000300
    assert candles[-1].open_time == 1700000900
    assert candles[-1].close == 1.11
    assert candles[-1].volume == 5000.0
    # close_time = open_time + 299 (5m bar - 1s)
    assert candles[-1].close_time == 1700001199


@pytest.mark.asyncio
async def test_cache_serves_repeat_requests():
    fake_sess = _FakeSession(_SAMPLE_GT_RESPONSE)
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=60)
    await client.fetch_5m("POOLADDR")
    await client.fetch_5m("POOLADDR")
    assert fake_sess.calls == 1  # second call served from cache


@pytest.mark.asyncio
async def test_cache_expires():
    fake_sess = _FakeSession(_SAMPLE_GT_RESPONSE)
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=0)
    await client.fetch_5m("POOLADDR")
    await client.fetch_5m("POOLADDR")
    assert fake_sess.calls == 2


@pytest.mark.asyncio
async def test_bad_response_returns_empty():
    fake_sess = _FakeSession({"data": {}})  # malformed
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=60)
    candles = await client.fetch_5m("POOLADDR")
    assert candles == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_gecko_ohlcv.py -v`
Expected: FAIL — `ImportError: No module named 'feeds.gecko_ohlcv'`

- [ ] **Step 3: Implement**

```python
# feeds/gecko_ohlcv.py
"""
GeckoTerminal OHLCV client — free, no API key, 30 req/min limit.
Endpoint: GET /api/v2/networks/solana/pools/{pool}/ohlcv/minute?aggregate=5&limit=100
Returns 5m candles oldest-first. In-memory 60s cache to stay under rate limit.
"""
import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional, Tuple

import aiohttp

from feeds.candle_utils import Candle

logger = logging.getLogger(__name__)

_GT_BASE = "https://api.geckoterminal.com/api/v2"


class GeckoTerminalClient:
    def __init__(
        self,
        session_factory: Optional[Callable[[], object]] = None,
        cache_ttl: int = 60,
        rate_per_min: int = 25,
    ):
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, Tuple[float, List[Candle]]] = {}  # key -> (ts, candles)
        self._rate_per_min = rate_per_min
        self._request_log: List[float] = []  # monotonic timestamps of recent requests
        self._lock = asyncio.Lock()
        # Injected for tests; default = real aiohttp session
        self._session_factory = session_factory or (
            lambda: aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        )

    async def fetch_5m(self, pool_address: str, limit: int = 100) -> List[Candle]:
        key = f"5m:{pool_address}:{limit}"
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < self._cache_ttl:
                return cached[1]
            await self._throttle(now)

        url = (
            f"{_GT_BASE}/networks/solana/pools/{pool_address}/ohlcv/minute"
            f"?aggregate=5&limit={limit}&currency=usd"
        )
        try:
            async with self._session_factory() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[GeckoOHLCV] {pool_address}: HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as e:
            logger.debug(f"[GeckoOHLCV] fetch error for {pool_address}: {e}")
            return []

        candles = self._parse(data)
        async with self._lock:
            self._cache[key] = (time.monotonic(), candles)
        return candles

    async def _throttle(self, now: float):
        cutoff = now - 60.0
        self._request_log = [t for t in self._request_log if t > cutoff]
        if len(self._request_log) >= self._rate_per_min:
            sleep_s = 60.0 - (now - self._request_log[0]) + 0.5
            logger.debug(f"[GeckoOHLCV] rate-limit sleep {sleep_s:.2f}s")
            await asyncio.sleep(max(0.0, sleep_s))
        self._request_log.append(time.monotonic())

    @staticmethod
    def _parse(data: dict) -> List[Candle]:
        try:
            rows = data["data"]["attributes"]["ohlcv_list"]
        except (KeyError, TypeError):
            return []
        out: List[Candle] = []
        for row in rows:
            try:
                ts, o, h, lo, c, v = row
                out.append(Candle(
                    open_time=int(ts),
                    open=float(o),
                    high=float(h),
                    low=float(lo),
                    close=float(c),
                    volume=float(v),
                    close_time=int(ts) + 299,
                ))
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda k: k.open_time)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_gecko_ohlcv.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add feeds/gecko_ohlcv.py test_gecko_ohlcv.py
git commit -m "feat(scalp): add GeckoTerminal 5m OHLCV client with cache + rate limit"
```

---

### Task 3: Setup detector state machine

**Files:**
- Create: `feeds/setup_detector.py`
- Test: `test_setup_detector.py`

- [ ] **Step 1: Write the failing test**

```python
# test_setup_detector.py
import pytest
from unittest.mock import MagicMock
from feeds.candle_utils import Candle
from feeds.setup_detector import SetupDetector, TriggerSignal, SetupPhase


def _c(o, h, l, c, v, t):
    return Candle(open_time=t, open=o, high=h, low=l, close=c, volume=v, close_time=t + 299)


def _make_cfg():
    cfg = MagicMock()
    cfg.scalp_impulse_min_pct = 10.0
    cfg.scalp_impulse_max_pct = 30.0
    cfg.scalp_impulse_lookback = 6
    cfg.scalp_pullback_min_pct = 30.0
    cfg.scalp_pullback_max_pct = 60.0
    cfg.scalp_sweep_vol_mult = 1.5
    cfg.scalp_sweep_vol_lookback = 20
    cfg.scalp_tp1_pct = 10.0
    cfg.scalp_stop_pct = 6.0
    cfg.scalp_min_rr = 2.0
    return cfg


def _impulse_series(base=1.0, start_t=1_000_000):
    """
    Build a sequence that walks through all four phases:
     - 20 candles of flat base (volume baseline)
     - 6-candle impulse +20%
     - 3-candle pullback -40% of the impulse range
     - 1 sweep candle wicking below the pullback low with 2x volume
     - 1 reclaim candle closing above the last pullback close
    """
    candles = []
    t = start_t
    # flat baseline — 20 bars
    for _ in range(20):
        candles.append(_c(base, base * 1.001, base * 0.999, base, 1000.0, t))
        t += 300
    # impulse — 6 bars, each +3% (≈+20% total), strong volume
    p = base
    for _ in range(6):
        new = p * 1.031
        candles.append(_c(p, new * 1.001, p * 0.999, new, 3000.0, t))
        p = new
        t += 300
    impulse_high = p
    impulse_low = base
    # pullback — 3 bars, retrace 40% of impulse
    retrace_target = impulse_high - (impulse_high - impulse_low) * 0.4
    step = (impulse_high - retrace_target) / 3
    for _ in range(3):
        new = p - step
        candles.append(_c(p, p * 1.001, new * 0.999, new, 1500.0, t))
        p = new
        t += 300
    pullback_low = p
    # sweep — wick below pullback low with vol spike
    sweep_low = pullback_low * 0.98
    sweep_close = pullback_low * 1.005
    candles.append(_c(pullback_low, pullback_low * 1.002, sweep_low, sweep_close, 4500.0, t))
    t += 300
    # reclaim — close above the last pullback close
    reclaim_close = pullback_low * 1.02
    candles.append(_c(sweep_close, reclaim_close * 1.001, sweep_close * 0.999,
                      reclaim_close, 3000.0, t))
    return candles, sweep_low, reclaim_close


def test_detector_fires_on_full_setup():
    cfg = _make_cfg()
    candles, sweep_low, reclaim_close = _impulse_series()
    det = SetupDetector(symbol="FOO", cfg=cfg)
    signal = det.evaluate(candles)
    assert isinstance(signal, TriggerSignal)
    assert signal.entry_price == pytest.approx(reclaim_close)
    # stop below sweep low (by 0.2%), capped at 6% below entry
    assert signal.stop_price <= sweep_low * 0.9985
    assert signal.stop_price >= reclaim_close * (1 - 0.06) - 1e-6
    # R/R ≥ 2
    assert signal.tp1_price == pytest.approx(reclaim_close * 1.10)
    rr = (signal.tp1_price - signal.entry_price) / (signal.entry_price - signal.stop_price)
    assert rr >= 2.0
    assert "impulse" in signal.reason.lower()


def test_detector_rejects_without_impulse():
    cfg = _make_cfg()
    # 30 flat candles — no impulse
    flat = [_c(1.0, 1.001, 0.999, 1.0, 1000.0, 1_000_000 + i * 300) for i in range(30)]
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(flat) is None
    assert det.phase == SetupPhase.IDLE


def test_detector_rejects_without_sweep_volume():
    cfg = _make_cfg()
    candles, _, _ = _impulse_series()
    # Crush sweep volume below 1.5x avg
    sweep_idx = len(candles) - 2
    c = candles[sweep_idx]
    candles[sweep_idx] = _c(c.open, c.high, c.low, c.close, 500.0, c.open_time)
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(candles) is None


def test_detector_rejects_without_reclaim():
    cfg = _make_cfg()
    candles, sweep_low, _ = _impulse_series()
    # Replace reclaim candle with one that closes BELOW sweep close (no reclaim)
    last = candles[-1]
    candles[-1] = _c(last.open, last.high, last.low * 0.99, last.open * 0.98,
                     last.volume, last.open_time)
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(candles) is None


def test_detector_rejects_poor_rr():
    cfg = _make_cfg()
    cfg.scalp_min_rr = 10.0  # impossible
    candles, _, _ = _impulse_series()
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(candles) is None


def test_detector_resets_after_fire():
    cfg = _make_cfg()
    candles, _, _ = _impulse_series()
    det = SetupDetector(symbol="FOO", cfg=cfg)
    sig = det.evaluate(candles)
    assert sig is not None
    # After firing, phase should reset so we don't fire on the same setup twice
    assert det.phase == SetupPhase.COOLDOWN
    sig2 = det.evaluate(candles)
    assert sig2 is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_setup_detector.py -v`
Expected: FAIL — `ImportError: No module named 'feeds.setup_detector'`

- [ ] **Step 3: Implement**

```python
# feeds/setup_detector.py
"""
SetupDetector — per-token state machine that walks:
  IDLE -> IMPULSE_FOUND -> PULLBACK_FOUND -> SWEEP_FOUND -> reclaim (fire)

A TriggerSignal emits when the most recent candle closes above the pullback
support AND all earlier phases validated AND R/R ≥ min_rr.
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from feeds.candle_utils import Candle, rolling_avg_volume


class SetupPhase(str, Enum):
    IDLE = "idle"
    COOLDOWN = "cooldown"  # fired recently — wait for next setup


@dataclass
class TriggerSignal:
    symbol: str
    entry_price: float
    stop_price: float
    tp1_price: float
    sweep_low: float
    reason: str


class SetupDetector:
    def __init__(self, symbol: str, cfg):
        self.symbol = symbol
        self.cfg = cfg
        self.phase: SetupPhase = SetupPhase.IDLE
        self._last_fired_close_time: int = 0

    def evaluate(self, candles: List[Candle]) -> Optional[TriggerSignal]:
        """
        Stateless scan of the candle series. Returns a TriggerSignal if the
        most recent candle completes the setup, else None. Advances phase to
        COOLDOWN after firing so repeated calls on the same series don't refire.
        """
        cfg = self.cfg
        need = max(cfg.scalp_sweep_vol_lookback + cfg.scalp_impulse_lookback + 6, 25)
        if len(candles) < need:
            return None

        reclaim = candles[-1]

        # If this reclaim candle's close_time is the one we already fired on, stay quiet.
        if reclaim.close_time == self._last_fired_close_time:
            return None

        sweep = candles[-2]

        # ── Impulse: within the window ending just before the pullback leg ──
        # Find the max-high inside impulse_lookback candles ending 4 bars ago
        # (pullback = 3 bars + sweep = 1 + reclaim = 1 = 5; impulse ends before).
        impulse_end_idx = len(candles) - 5
        impulse_start_idx = max(0, impulse_end_idx - cfg.scalp_impulse_lookback)
        impulse_slice = candles[impulse_start_idx:impulse_end_idx]
        if not impulse_slice:
            return None
        impulse_low = min(c.low for c in impulse_slice)
        impulse_high = max(c.high for c in impulse_slice)
        if impulse_low <= 0:
            return None
        impulse_pct = (impulse_high - impulse_low) / impulse_low * 100
        if impulse_pct < cfg.scalp_impulse_min_pct or impulse_pct > cfg.scalp_impulse_max_pct:
            return None

        # ── Pullback: 3 bars between impulse and sweep, retrace 30–60% ──
        pullback_slice = candles[impulse_end_idx:impulse_end_idx + 3]
        if len(pullback_slice) < 3:
            return None
        pullback_low = min(c.low for c in pullback_slice)
        retrace_pct = (impulse_high - pullback_low) / (impulse_high - impulse_low) * 100
        if retrace_pct < cfg.scalp_pullback_min_pct or retrace_pct > cfg.scalp_pullback_max_pct:
            return None

        # ── Sweep: wicks below pullback low, long lower wick, volume ≥ 1.5× avg ──
        if sweep.low >= pullback_low:
            return None
        body = abs(sweep.close - sweep.open)
        lower_wick = min(sweep.open, sweep.close) - sweep.low
        if lower_wick <= max(body, 1e-12):  # wick must be at least body-sized
            return None
        avg_vol = rolling_avg_volume(
            candles[:-2][-cfg.scalp_sweep_vol_lookback:],
            cfg.scalp_sweep_vol_lookback,
        )
        if avg_vol <= 0 or sweep.volume < avg_vol * cfg.scalp_sweep_vol_mult:
            return None

        # ── Reclaim: close above pullback support ──
        if reclaim.close <= pullback_low:
            return None

        # ── Build trigger (entry/stop/tp/RR) ──
        entry = reclaim.close
        stop_from_sweep = sweep.low * 0.998
        stop_from_pct = entry * (1 - cfg.scalp_stop_pct / 100)
        stop = min(stop_from_sweep, stop_from_pct)  # whichever is lower
        tp1 = entry * (1 + cfg.scalp_tp1_pct / 100)
        if entry <= stop:
            return None
        rr = (tp1 - entry) / (entry - stop)
        if rr < cfg.scalp_min_rr:
            return None

        self._last_fired_close_time = reclaim.close_time
        self.phase = SetupPhase.COOLDOWN
        reason = (
            f"impulse={impulse_pct:.1f}% pullback={retrace_pct:.0f}% "
            f"sweep_vol={sweep.volume / avg_vol:.2f}x rr={rr:.2f}"
        )
        return TriggerSignal(
            symbol=self.symbol,
            entry_price=entry,
            stop_price=stop,
            tp1_price=tp1,
            sweep_low=sweep.low,
            reason=reason,
        )

    def reset(self):
        self.phase = SetupPhase.IDLE
        self._last_fired_close_time = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_setup_detector.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add feeds/setup_detector.py test_setup_detector.py
git commit -m "feat(scalp): add 4-phase setup detector (impulse/pullback/sweep/reclaim)"
```

---

### Task 4: Config additions

**Files:**
- Modify: `utils/config.py` (scalp section, ~lines 162-189; env-override block ~lines 446-456)

- [ ] **Step 1: Write the failing test**

```python
# test_config_scalp_fields.py
from utils.config import Config


def test_scalp_config_has_new_fields():
    c = Config()
    # New 4-phase scalper fields
    assert c.scalp_impulse_min_pct == 10.0
    assert c.scalp_impulse_max_pct == 30.0
    assert c.scalp_impulse_lookback == 6
    assert c.scalp_pullback_min_pct == 30.0
    assert c.scalp_pullback_max_pct == 60.0
    assert c.scalp_sweep_vol_mult == 1.5
    assert c.scalp_sweep_vol_lookback == 20
    assert c.scalp_tp1_pct == 10.0
    assert c.scalp_tp1_sell == 0.50
    assert c.scalp_tp2_pct == 15.0
    assert c.scalp_tp2_sell == 0.35
    assert c.scalp_stop_pct == 6.0
    assert c.scalp_min_rr == 2.0
    assert c.scalp_time_exit_candles == 4
    assert c.scalp_time_exit_min_pct == 5.0
    assert c.scalp_min_m5_volume_usd == 50_000
    assert c.scalp_min_liquidity_usd == 30_000
    assert c.scalp_min_age_minutes == 5
    assert c.scalp_max_age_hours == 6.0
    assert c.scalp_rug_lp_drop_pct == 10.0
    assert c.scalp_max_deployment_pct == 0.80
    assert c.scalp_gt_rate_per_min == 25
    assert c.scalp_gt_cache_ttl_sec == 60
    # Max concurrent reduced from 10 → 5
    assert c.scalp_max_concurrent == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_config_scalp_fields.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'scalp_impulse_min_pct'`

- [ ] **Step 3: Implement — edit `utils/config.py`**

Replace the existing scalp section (around lines 162–189) with this. Keep dip-buy fields that are still referenced by legacy tests (`scalp_min_m5_change_pct`, etc.) so nothing else breaks — just mark them deprecated via comment.

```python
    # ── Scalp Strategy (4-phase setup detector: impulse/pullback/sweep/reclaim) ──
    scalp_enabled: bool = True
    scalp_capital: float = 2000.0
    scalp_position_usd: float = 200.0
    scalp_max_concurrent: int = 5           # spec max (was 10 in dip-buy era)
    scalp_daily_loss_limit: float = 400.0
    scalp_max_deployment_pct: float = 0.80  # 60–80% cap per spec — use upper bound

    # Entry (setup detector)
    scalp_impulse_min_pct: float = 10.0
    scalp_impulse_max_pct: float = 30.0
    scalp_impulse_lookback: int = 6
    scalp_pullback_min_pct: float = 30.0
    scalp_pullback_max_pct: float = 60.0
    scalp_sweep_vol_mult: float = 1.5
    scalp_sweep_vol_lookback: int = 20
    scalp_min_rr: float = 2.0

    # Exits
    scalp_tp1_pct: float = 10.0             # +10% → sell 50%
    scalp_tp1_sell: float = 0.50
    scalp_tp2_pct: float = 15.0             # +15% → sell 35% of remaining
    scalp_tp2_sell: float = 0.35
    scalp_stop_pct: float = 6.0             # hard stop — spec max
    scalp_time_exit_candles: int = 4        # 3–5 candle window — use midpoint
    scalp_time_exit_min_pct: float = 5.0    # need +5% within time_exit_candles or exit

    # Market selection (candidate gates)
    scalp_min_m5_volume_usd: float = 50_000
    scalp_min_liquidity_usd: float = 30_000
    scalp_min_age_minutes: int = 5
    scalp_max_age_hours: float = 6.0
    scalp_rug_lp_drop_pct: float = 10.0
    scalp_max_watch_candidates: int = 40
    scalp_watch_expiry_minutes: float = 30.0
    scalp_stop_cooldown_minutes: float = 45.0  # spec: 45-min cooldown

    # GeckoTerminal
    scalp_gt_rate_per_min: int = 25
    scalp_gt_cache_ttl_sec: int = 60

    # ── DEPRECATED (kept only so legacy dip-buy tests don't break during cutover) ──
    scalp_min_mcap: float = 200_000
    scalp_min_age_days: float = 1.0
    scalp_min_volume_h24: float = 75_000
    scalp_max_entry_move_pct: float = 4.0
    scalp_tick_ratio_min: float = 0.60
    scalp_tick_consecutive_min: int = 2
    scalp_min_m5_change_pct: float = -6.0
    scalp_max_m5_change_pct: float = -1.0
    scalp_min_volume_h1_usd: float = 30_000
    scalp_min_m5_buy_ratio: float = 0.55
    scalp_min_m5_avg_trade_usd: float = 20.0
    scalp_max_h6_change_pct: float = 100.0
    scalp_max_h24_change_pct: float = 300.0
    scalp_watch_warmup_minutes: float = 10.0
    scalp_max_hold_minutes: float = 45.0
```

Then in the env-override block (around line 446), add new overrides alongside the existing ones:

```python
        config.scalp_tp1_pct = env_float("SCALP_TP1_PCT", config.scalp_tp1_pct)
        config.scalp_tp2_pct = env_float("SCALP_TP2_PCT", config.scalp_tp2_pct)
        config.scalp_min_rr = env_float("SCALP_MIN_RR", config.scalp_min_rr)
        config.scalp_time_exit_candles = env_int("SCALP_TIME_EXIT_CANDLES", config.scalp_time_exit_candles)
        config.scalp_min_m5_volume_usd = env_float("SCALP_MIN_M5_VOLUME_USD", config.scalp_min_m5_volume_usd)
        config.scalp_min_liquidity_usd = env_float("SCALP_MIN_LIQUIDITY_USD", config.scalp_min_liquidity_usd)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_config_scalp_fields.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add utils/config.py test_config_scalp_fields.py
git commit -m "feat(scalp): add 4-phase config fields, reduce max_concurrent 10→5"
```

---

### Task 5: SOL regime guard

**Files:**
- Modify: `feeds/candle_utils.py` (add `sol_is_bearish` helper)
- Test: expand `test_candle_utils.py`

- [ ] **Step 1: Append to `test_candle_utils.py`**

```python
from feeds.candle_utils import sol_is_bearish


def test_sol_is_bearish_true_on_3_reds_no_wick():
    reds = [_c(1.0, 1.0, 0.9, 0.9, 100, 0),
            _c(0.9, 0.9, 0.8, 0.8, 100, 300),
            _c(0.8, 0.8, 0.7, 0.7, 100, 600)]
    # extend with some history so lookback is satisfied
    history = [_c(1.05, 1.05, 1.0, 1.05, 100, -i * 300) for i in range(1, 15)][::-1] + reds
    assert sol_is_bearish(history) is True


def test_sol_is_bearish_false_on_neutral_market():
    neutral = [_c(1.0, 1.01, 0.99, 1.0, 100, i * 300) for i in range(20)]
    assert sol_is_bearish(neutral) is False


def test_sol_is_bearish_false_on_uptrend():
    up = []
    p = 1.0
    for i in range(20):
        p *= 1.005
        up.append(_c(p / 1.005, p * 1.001, p / 1.005 * 0.999, p, 100, i * 300))
    assert sol_is_bearish(up) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_candle_utils.py -v`
Expected: FAIL — `ImportError: cannot import name 'sol_is_bearish'`

- [ ] **Step 3: Implement — append to `feeds/candle_utils.py`**

```python
def sol_is_bearish(sol_5m_candles: List[Candle]) -> bool:
    """
    SOL 'trending down strongly on short timeframes' guard.
    Trigger if either:
      (a) 3 consecutive red candles with no lower wick (stop-hunt distribution), OR
      (b) last 12 candles (1h) show close below close[-12] by ≥ 2%.
    """
    if len(sol_5m_candles) < 12:
        return False
    if consecutive_reds_no_wick(sol_5m_candles, 3):
        return True
    now_close = sol_5m_candles[-1].close
    then_close = sol_5m_candles[-12].close
    if then_close > 0 and (now_close - then_close) / then_close * 100 <= -2.0:
        return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_candle_utils.py -v`
Expected: PASS (9 tests total — 6 original + 3 new)

- [ ] **Step 5: Commit**

```bash
git add feeds/candle_utils.py test_candle_utils.py
git commit -m "feat(scalp): add SOL bearish-regime guard"
```

---

### Task 6: Entry metadata on trader.buy + PositionState

**Why:** The setup detector produces `sweep_low`, `stop_price`, and `tp1_price`. These need to flow into the `PositionState` so `_evaluate_scalp` can use them. The cheapest, safest path is adding a `scalp_meta: Optional[dict]` keyword to `trader.buy` that the trader attaches to the state.

**Files:**
- Modify: `core/trader.py` (buy signature + state attachment)
- Modify: `core/position_manager.py` (add fields to `PositionState`)

- [ ] **Step 1: Write the failing test** (new file)

```python
# test_scalp_meta_passthrough.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from core.position_manager import PositionState


def test_position_state_has_scalp_meta_field():
    s = PositionState(
        token_address="A", token_symbol="T", chain_id="solana",
        entry_price=1.0, entry_volume_usd=0.0,
        position_size_usd=200.0, original_size_usd=200.0,
        entry_time=datetime.now(timezone.utc),
        strategy="scalp",
        current_price=1.0, peak_price=1.0,
    )
    # Default is None
    assert s.scalp_meta is None
    # Accepts dict
    s.scalp_meta = {"sweep_low": 0.95, "stop_price": 0.94, "tp1_price": 1.10,
                    "entry_close_time": 1700000000}
    assert s.scalp_meta["sweep_low"] == 0.95
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_scalp_meta_passthrough.py -v`
Expected: FAIL — `TypeError: ... got unexpected keyword argument 'scalp_meta'` or `AttributeError`

- [ ] **Step 3: Implement — edit `core/position_manager.py`**

Find the `PositionState` dataclass (near top of file) and add:

```python
    scalp_meta: Optional[dict] = None
```

(ensure `Optional[dict]` is imported from typing — it already is, given other Optional fields).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_scalp_meta_passthrough.py -v`
Expected: PASS

- [ ] **Step 5: Wire trader.buy to accept + attach `scalp_meta`**

Find `async def buy` in `core/trader.py`. Add kwarg `scalp_meta: Optional[dict] = None` to the signature, and when the PositionState is created, set `state.scalp_meta = scalp_meta`.

Search command to locate:
```bash
grep -n "async def buy" core/trader.py
grep -n "PositionState(" core/trader.py
```

At the PositionState instantiation (or wherever the trader registers the open position into `position_manager`), pass `scalp_meta=scalp_meta`. If the trader routes through the position manager via a separate method, propagate the kwarg down.

- [ ] **Step 6: Commit**

```bash
git add core/position_manager.py core/trader.py test_scalp_meta_passthrough.py
git commit -m "feat(scalp): add scalp_meta passthrough for setup-detector entries"
```

---

### Task 7: Rewrite _evaluate_scalp with new exit logic

**Files:**
- Modify: `core/position_manager.py` (replace `_evaluate_scalp`, ~lines 1310-1381)
- Modify: `test_scalp_position_manager.py`

- [ ] **Step 1: Rewrite `test_scalp_position_manager.py`**

```python
# test_scalp_position_manager.py
"""
Unit tests for the PositionManager scalp branch after the 4-phase rewrite.
Tests: TP1 +10%/50%, TP2 +15%/35% of remainder, 6% hard stop,
time-exit (no +5% in 4 candles), runner via winner_trail_pct.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from core.position_manager import PositionManager, PositionState, MarketConditionMonitor


def _scalp_state(pnl_pct=0.0, tp1_hit=False, tp2_hit=False, minutes_open=0,
                 sweep_low=0.94, entry_close_time=None):
    entry_price = 1.0
    now = datetime.now(timezone.utc)
    if entry_close_time is None:
        entry_close_time = int((now - timedelta(minutes=minutes_open)).timestamp())
    state = PositionState(
        token_address="ADDR1",
        token_symbol="TEST",
        chain_id="solana",
        entry_price=entry_price,
        entry_volume_usd=0.0,
        position_size_usd=200.0,
        original_size_usd=200.0,
        entry_time=now - timedelta(minutes=minutes_open),
        strategy="scalp",
        current_price=entry_price * (1 + pnl_pct / 100),
        peak_price=max(entry_price, entry_price * (1 + pnl_pct / 100)),
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
    )
    state.scalp_meta = {
        "sweep_low": sweep_low,
        "stop_price": 0.94,
        "tp1_price": 1.10,
        "entry_close_time": entry_close_time,
    }
    return state


def _mgr(**overrides):
    trader = MagicMock()
    trader.open_positions = {}
    mgr = PositionManager(
        chain_name="Solana", chain_id="solana",
        trader=trader,
        open_positions_ref=trader.open_positions,
        telegram=MagicMock(),
        tracker=MagicMock(),
        market_monitor=MarketConditionMonitor(),
        scalp_tp1_pct=overrides.get("scalp_tp1_pct", 10.0),
        scalp_tp1_sell=overrides.get("scalp_tp1_sell", 0.50),
        scalp_tp2_pct=overrides.get("scalp_tp2_pct", 15.0),
        scalp_tp2_sell=overrides.get("scalp_tp2_sell", 0.35),
        scalp_stop_pct=overrides.get("scalp_stop_pct", 6.0),
        scalp_time_exit_candles=overrides.get("scalp_time_exit_candles", 4),
        scalp_time_exit_min_pct=overrides.get("scalp_time_exit_min_pct", 5.0),
    )
    mgr._execute_sell = AsyncMock()
    return mgr


@pytest.mark.asyncio
async def test_scalp_tp1_fires_at_10pct():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=10.1)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == pytest.approx(0.50)
    assert s.tp1_hit is True


@pytest.mark.asyncio
async def test_scalp_tp2_fires_after_tp1():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=15.5, tp1_hit=True)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == pytest.approx(0.35)
    assert s.tp2_hit is True


@pytest.mark.asyncio
async def test_scalp_hard_stop_at_6pct():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=-6.1)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == 1.0
    assert "stop" in kw["reason"].lower()


@pytest.mark.asyncio
async def test_scalp_time_exit_fires_after_4_candles_without_5pct():
    mgr = _mgr()
    # entry_close_time is 4.1 × 300s ago (≥ 4 candles)
    past = int((datetime.now(timezone.utc) - timedelta(seconds=1260)).timestamp())
    s = _scalp_state(pnl_pct=2.0, entry_close_time=past)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == 1.0
    assert "time" in kw["reason"].lower()


@pytest.mark.asyncio
async def test_scalp_time_exit_suppressed_when_above_5pct():
    mgr = _mgr()
    past = int((datetime.now(timezone.utc) - timedelta(seconds=1260)).timestamp())
    s = _scalp_state(pnl_pct=5.5, entry_close_time=past)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_no_tp2_before_tp1():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=15.5, tp1_hit=False)
    await mgr._evaluate_scalp("ADDR1", s)
    # Should take TP1 (gate at 10%), not TP2
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == pytest.approx(0.50)
    assert s.tp1_hit is True
    assert s.tp2_hit is False


@pytest.mark.asyncio
async def test_scalp_runner_no_action_between_tp2_and_trail():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=18.0, tp1_hit=True, tp2_hit=True)
    await mgr._evaluate_scalp("ADDR1", s)
    # Runner phase — no further action until stop or external trailing
    mgr._execute_sell.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_scalp_position_manager.py -v`
Expected: FAIL (old implementation fires at +3%, stops at -2.5%, uses 45-min time stop)

- [ ] **Step 3: Update PositionManager constructor + `_evaluate_scalp`**

In `core/position_manager.py`, replace the scalp-related constructor params (around lines 293-296) from:

```python
                 scalp_tp1_pct: float = 3.0,
                 scalp_tp2_pct: float = 5.0,
                 scalp_stop_pct: float = 2.5,
                 scalp_max_hold_minutes: float = 45.0,
```

to:

```python
                 scalp_tp1_pct: float = 10.0,
                 scalp_tp1_sell: float = 0.50,
                 scalp_tp2_pct: float = 15.0,
                 scalp_tp2_sell: float = 0.35,
                 scalp_stop_pct: float = 6.0,
                 scalp_time_exit_candles: int = 4,
                 scalp_time_exit_min_pct: float = 5.0,
                 scalp_max_hold_minutes: float = 45.0,   # kept as safety belt
```

And the corresponding `self.*` assignments (around lines 343-346). Then replace `_evaluate_scalp` (around lines 1310-1381) with:

```python
    async def _evaluate_scalp(self, token_address: str, state: PositionState):
        """
        Scalp branch — 4-phase setup detector exits.
          - Hard stop at -scalp_stop_pct% (no overrides)
          - Time-exit if +scalp_time_exit_min_pct% not reached within N 5m candles
          - TP1 at +scalp_tp1_pct% sells scalp_tp1_sell
          - TP2 at +scalp_tp2_pct% sells scalp_tp2_sell of what remains
          - Runner: whatever's left exits via standard winner-trail once peak ≥ TP2
          - Safety belt: scalp_max_hold_minutes absolute cap (defense in depth)
        """
        # Dedupe: realtime stop may already be firing
        if token_address in self._stop_triggered:
            return
        pnl_pct = state.pnl_pct
        now_ts = datetime.now(timezone.utc).timestamp()
        hold_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()

        # ── Hard stop (highest priority; spec: no overrides) ───────
        if pnl_pct <= -self.scalp_stop_pct:
            logger.warning(
                f"[PositionManager/{self.chain_name}] 🛑 SCALP STOP: "
                f"{state.token_symbol} at {pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp stop -{self.scalp_stop_pct:.1f}%",
            )
            self.stop_loss_hits += 1
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "stop_loss", pnl_usd)
            return

        # ── Time-based exit: need +N% within M candles ─────────────
        meta = state.scalp_meta or {}
        entry_close_time = meta.get("entry_close_time")
        if entry_close_time is not None and pnl_pct < self.scalp_time_exit_min_pct:
            candles_elapsed = max(0, int((now_ts - entry_close_time) // 300))
            if candles_elapsed >= self.scalp_time_exit_candles:
                logger.info(
                    f"[PositionManager/{self.chain_name}] ⏱ SCALP TIME EXIT: "
                    f"{state.token_symbol} {pnl_pct:+.1f}% after "
                    f"{candles_elapsed} candles (<{self.scalp_time_exit_min_pct}%)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Scalp time exit {candles_elapsed}c @ {pnl_pct:+.1f}%",
                )
                if self.scalp_queue:
                    pnl_usd = state.position_size_usd * pnl_pct / 100
                    self.scalp_queue.on_scalp_close(token_address, "time_exit", pnl_usd)
                return

        # ── Safety belt: absolute hold cap ─────────────────────────
        if hold_seconds >= self.scalp_max_hold_minutes * 60:
            logger.info(
                f"[PositionManager/{self.chain_name}] ⏱ SCALP MAX HOLD: "
                f"{state.token_symbol} after {hold_seconds/60:.0f}min"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp max hold {hold_seconds/60:.0f}min",
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "max_hold", pnl_usd)
            return

        # ── TP2 — +15%, sell 35% of remainder ──────────────────────
        if state.tp1_hit and not state.tp2_hit and pnl_pct >= self.scalp_tp2_pct:
            state.tp2_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP2: "
                f"{state.token_symbol} +{pnl_pct:.1f}% (sell {self.scalp_tp2_sell*100:.0f}%)"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.scalp_tp2_sell,
                reason=f"Scalp TP2 +{pnl_pct:.1f}%",
            )
            return

        # ── TP1 — +10%, sell 50% ────────────────────────────────────
        if not state.tp1_hit and pnl_pct >= self.scalp_tp1_pct:
            state.tp1_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP1: "
                f"{state.token_symbol} +{pnl_pct:.1f}% (sell {self.scalp_tp1_sell*100:.0f}%)"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.scalp_tp1_sell,
                reason=f"Scalp TP1 +{pnl_pct:.1f}%",
            )
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_scalp_position_manager.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add core/position_manager.py test_scalp_position_manager.py
git commit -m "feat(scalp): rewrite _evaluate_scalp for 4-phase spec (TP1 10%/50%, TP2 15%/35%, 6% stop, 4-candle time exit)"
```

---

### Task 8: Rewrite ScalpQueue as orchestrator

**Files:**
- Rewrite: `feeds/scalp_queue.py`
- Rewrite: `test_scalp_queue.py`

- [ ] **Step 1: Rewrite `test_scalp_queue.py`**

```python
# test_scalp_queue.py
import pytest
import pytest_asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from core.scalp_capital import ScalpCapitalManager
from feeds.scalp_queue import ScalpQueue
from feeds.candle_utils import Candle
from feeds.setup_detector import TriggerSignal


def _cfg(**overrides):
    c = MagicMock()
    c.scalp_position_usd = 200.0
    c.scalp_max_watch_candidates = 40
    c.scalp_watch_expiry_minutes = 30.0
    c.scalp_stop_cooldown_minutes = 45.0
    c.scalp_max_deployment_pct = 0.80
    c.scalp_min_m5_volume_usd = 50_000
    c.scalp_min_liquidity_usd = 30_000
    c.scalp_min_age_minutes = 5
    c.scalp_max_age_hours = 6.0
    c.scalp_rug_lp_drop_pct = 10.0
    c.scalp_impulse_min_pct = 10.0
    c.scalp_impulse_max_pct = 30.0
    c.scalp_impulse_lookback = 6
    c.scalp_pullback_min_pct = 30.0
    c.scalp_pullback_max_pct = 60.0
    c.scalp_sweep_vol_mult = 1.5
    c.scalp_sweep_vol_lookback = 20
    c.scalp_tp1_pct = 10.0
    c.scalp_stop_pct = 6.0
    c.scalp_min_rr = 2.0
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _good_pair(addr="TOKEN1", pool="POOL1"):
    return {
        "chainId": "solana",
        "baseToken": {"address": addr, "symbol": "TEST"},
        "pairAddress": pool,
        "volume": {"m5": 60_000, "h24": 500_000, "h1": 100_000},
        "liquidity": {"usd": 50_000},
        "priceChange": {"m5": 1.0, "h24": 5.0, "h6": 3.0},
        "priceUsd": "1.0",
        "pairCreatedAt": time.time() * 1000 - 30 * 60 * 1000,  # 30 min old
    }


def test_candidate_gate_passes_good_pair():
    q = _make_queue()
    assert q._passes_candidate_gates(_good_pair()) is True


def test_candidate_gate_rejects_low_m5_volume():
    q = _make_queue()
    p = _good_pair()
    p["volume"]["m5"] = 10_000
    assert q._passes_candidate_gates(p) is False


def test_candidate_gate_rejects_low_liquidity():
    q = _make_queue()
    p = _good_pair()
    p["liquidity"]["usd"] = 5_000
    assert q._passes_candidate_gates(p) is False


def test_candidate_gate_rejects_too_young():
    q = _make_queue()
    p = _good_pair()
    p["pairCreatedAt"] = time.time() * 1000 - 60_000  # 1 min old
    assert q._passes_candidate_gates(p) is False


def test_candidate_gate_rejects_too_old():
    q = _make_queue()
    p = _good_pair()
    p["pairCreatedAt"] = time.time() * 1000 - 10 * 3600 * 1000  # 10h
    assert q._passes_candidate_gates(p) is False


def test_rug_detected_from_lp_drop():
    q = _make_queue()
    q._lp_history["POOL1"] = (time.monotonic() - 300, 50_000)
    p = _good_pair()
    p["liquidity"]["usd"] = 40_000  # 20% drop
    assert q._is_rug("POOL1", p) is True


def test_rug_not_triggered_on_small_drop():
    q = _make_queue()
    q._lp_history["POOL1"] = (time.monotonic() - 300, 50_000)
    p = _good_pair()
    p["liquidity"]["usd"] = 48_000  # 4% drop
    assert q._is_rug("POOL1", p) is False


@pytest.mark.asyncio
async def test_no_trade_when_sol_bearish():
    q = _make_queue()
    q._sol_is_bearish = True  # set by regime check
    await q._maybe_fire_entry("TOKEN1", _good_pair(), signal=_fake_signal())
    assert q.trader.buy.await_count == 0


@pytest.mark.asyncio
async def test_no_trade_when_majority_red():
    q = _make_queue()
    q._majority_red = True
    await q._maybe_fire_entry("TOKEN1", _good_pair(), signal=_fake_signal())
    assert q.trader.buy.await_count == 0


@pytest.mark.asyncio
async def test_no_trade_when_deployment_cap_reached():
    q = _make_queue()
    # Fill capital to 80% deployment
    q.scalp_capital._open["A"] = 800.0
    q.scalp_capital._open["B"] = 800.0  # 1600 / 2000 = 80%
    await q._maybe_fire_entry("TOKEN1", _good_pair(), signal=_fake_signal())
    assert q.trader.buy.await_count == 0


@pytest.mark.asyncio
async def test_fires_entry_and_attaches_scalp_meta():
    q = _make_queue()
    sig = _fake_signal()
    pair = _good_pair()
    q._open_positions_ref[pair["baseToken"]["address"].lower()] = MagicMock()  # simulate fill
    await q._maybe_fire_entry("TOKEN1", pair, signal=sig)
    q.trader.buy.assert_awaited_once()
    _, kw = q.trader.buy.call_args
    assert kw["strategy"] == "scalp"
    assert kw["scalp_meta"]["sweep_low"] == sig.sweep_low
    assert kw["scalp_meta"]["stop_price"] == sig.stop_price
    assert kw["scalp_meta"]["tp1_price"] == sig.tp1_price


# ── helpers ──

def _fake_signal():
    return TriggerSignal(
        symbol="TEST",
        entry_price=1.0,
        stop_price=0.94,
        tp1_price=1.10,
        sweep_low=0.94,
        reason="impulse=15.0% pullback=40% sweep_vol=2.00x rr=2.5",
    )


def _make_queue(**cfg_overrides):
    trader = MagicMock()
    trader.buy = AsyncMock()
    capital = ScalpCapitalManager(max_concurrent=5)
    open_refs = {}
    cfg = _cfg(**cfg_overrides)
    ohlcv = AsyncMock()
    ohlcv.fetch_5m = AsyncMock(return_value=[])  # default empty
    q = ScalpQueue(
        trader=trader,
        open_positions_ref=open_refs,
        scalp_capital=capital,
        config=cfg,
        ohlcv_client=ohlcv,
    )
    q._open_positions_ref = open_refs  # expose for tests
    return q
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_scalp_queue.py -v`
Expected: FAIL — new signature / helpers don't exist yet

- [ ] **Step 3: Replace `feeds/scalp_queue.py` entirely**

```python
"""
ScalpQueue (4-phase setup detector) — orchestrator.

Flow every SCAN_INTERVAL seconds:
  1. Refresh global regime: SOL bearish? Majority of watched tokens red?
  2. Discover candidates via DexScreener (passing candidate gates).
  3. For each watched token, pull 5m OHLCV from GeckoTerminal.
  4. Feed candles to the per-token SetupDetector.
  5. On TriggerSignal: apply global no-trade filters, R/R, capital cap.
  6. If clear → trader.buy(strategy='scalp', scalp_meta={...}).
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

import aiohttp

from feeds.candle_utils import sol_is_bearish
from feeds.gecko_ohlcv import GeckoTerminalClient
from feeds.setup_detector import SetupDetector, TriggerSignal

logger = logging.getLogger(__name__)

_SCAN_INTERVAL = 60    # seconds between orchestrator cycles
_DEX_CHAIN = "solana"
_SOL_POOL = "83v8iPyZihDEjDdY8RdZddyZNyUtXngz69Lgo9Kt5d6d"  # SOL/USDC Raydium


class ScalpQueue:
    def __init__(
        self,
        trader,
        open_positions_ref: dict,
        scalp_capital,
        config,
        ohlcv_client: Optional[GeckoTerminalClient] = None,
        scanner=None,
    ):
        self.trader = trader
        self.open_positions_ref = open_positions_ref
        self.scalp_capital = scalp_capital
        self.cfg = config
        self.ohlcv = ohlcv_client or GeckoTerminalClient(
            cache_ttl=getattr(config, "scalp_gt_cache_ttl_sec", 60),
            rate_per_min=getattr(config, "scalp_gt_rate_per_min", 25),
        )
        self.scanner = scanner

        # token_address (lower) -> {"symbol", "pool_address", "detector"}
        self._watched: Dict[str, dict] = {}
        # pool_address -> (timestamp_monotonic, liquidity_usd) for rug detection
        self._lp_history: Dict[str, Tuple[float, float]] = {}
        # address (lower) -> monotonic expiry for post-loss cooldown
        self._stop_cooldowns: Dict[str, float] = {}

        # Regime flags refreshed each cycle
        self._sol_is_bearish: bool = False
        self._majority_red: bool = False

    # ── Public entry point ──────────────────────────────────────

    async def run(self):
        logger.info(
            f"[ScalpQueue] Starting — 4-phase detector, "
            f"${self.cfg.scalp_position_usd:.0f}/trade, "
            f"max={self.scalp_capital.max_concurrent} concurrent, "
            f"TP1 +{self.cfg.scalp_tp1_pct}%/{int(self.cfg.scalp_tp1_sell*100)}% "
            f"TP2 +{self.cfg.scalp_tp2_pct}%/{int(self.cfg.scalp_tp2_sell*100)}% "
            f"stop -{self.cfg.scalp_stop_pct}%"
        )
        while True:
            try:
                await self._cycle()
            except Exception as e:
                logger.error(f"[ScalpQueue] Cycle error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    def on_scalp_close(self, addr: str, reason: str, pnl_usd: float = 0.0):
        self.scalp_capital.record_close(addr, pnl_usd)
        if reason in ("stop_loss", "time_exit"):
            expiry = time.monotonic() + self.cfg.scalp_stop_cooldown_minutes * 60
            self._stop_cooldowns[addr.lower()] = expiry

    # ── Orchestrator cycle ──────────────────────────────────────

    async def _cycle(self):
        await self._refresh_regime()
        pairs = await self._fetch_candidates()
        for p in pairs:
            addr = (p.get("baseToken") or {}).get("address", "").lower()
            if not addr or addr in self._watched:
                continue
            if not self._passes_candidate_gates(p):
                continue
            if self._is_rug(p.get("pairAddress", ""), p):
                continue
            if self._is_on_cooldown(addr):
                continue
            if addr in {a.lower() for a in (self.open_positions_ref or {}).keys()}:
                continue
            if len(self._watched) >= self.cfg.scalp_max_watch_candidates:
                break
            self._watched[addr] = {
                "symbol": (p.get("baseToken") or {}).get("symbol", "?"),
                "pool_address": p.get("pairAddress", ""),
                "detector": SetupDetector(
                    symbol=(p.get("baseToken") or {}).get("symbol", "?"),
                    cfg=self.cfg,
                ),
                "added_ts": time.monotonic(),
                "pair": p,
            }
            self._lp_history[p.get("pairAddress", "")] = (
                time.monotonic(),
                float((p.get("liquidity") or {}).get("usd") or 0),
            )

        # Evaluate each watched token
        for addr, meta in list(self._watched.items()):
            await self._evaluate_watched(addr, meta)

        self._prune_watched()
        self._prune_cooldowns()

    async def _refresh_regime(self):
        try:
            sol_candles = await self.ohlcv.fetch_5m(_SOL_POOL, limit=20)
            self._sol_is_bearish = sol_is_bearish(sol_candles) if sol_candles else False
        except Exception as e:
            logger.debug(f"[ScalpQueue] SOL regime fetch failed: {e}")
            self._sol_is_bearish = False

        # Majority-red: compute on the most recent watched pairs snapshot
        if self._watched:
            reds = 0
            total = 0
            for meta in self._watched.values():
                pair = meta.get("pair") or {}
                m5 = (pair.get("priceChange") or {}).get("m5")
                if m5 is None:
                    continue
                total += 1
                if m5 < 0:
                    reds += 1
            self._majority_red = total > 0 and (reds / total) > 0.5
        else:
            self._majority_red = False

    async def _evaluate_watched(self, addr: str, meta: dict):
        pool = meta["pool_address"]
        if not pool:
            return
        candles = await self.ohlcv.fetch_5m(pool, limit=50)
        if len(candles) < 25:
            return
        signal = meta["detector"].evaluate(candles)
        if signal is None:
            return
        pair = meta.get("pair") or {}
        await self._maybe_fire_entry(addr, pair, signal=signal)

    # ── Candidate gates ─────────────────────────────────────────

    def _passes_candidate_gates(self, pair: dict) -> bool:
        if (pair.get("chainId") or "").lower() != _DEX_CHAIN:
            return False
        m5_vol = float((pair.get("volume") or {}).get("m5") or 0)
        if m5_vol < self.cfg.scalp_min_m5_volume_usd:
            return False
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        if liq < self.cfg.scalp_min_liquidity_usd:
            return False
        created_ms = pair.get("pairCreatedAt") or 0
        age_min = (time.time() * 1000 - created_ms) / 60_000
        if age_min < self.cfg.scalp_min_age_minutes:
            return False
        if age_min > self.cfg.scalp_max_age_hours * 60:
            return False
        return True

    def _is_rug(self, pool: str, pair: dict) -> bool:
        prev = self._lp_history.get(pool)
        if prev is None:
            return False
        prev_ts, prev_liq = prev
        if time.monotonic() - prev_ts > 600:  # history too stale
            return False
        current = float((pair.get("liquidity") or {}).get("usd") or 0)
        if prev_liq <= 0:
            return False
        drop_pct = (prev_liq - current) / prev_liq * 100
        return drop_pct > self.cfg.scalp_rug_lp_drop_pct

    def _is_on_cooldown(self, addr: str) -> bool:
        now = time.monotonic()
        if now < self._stop_cooldowns.get(addr.lower(), 0):
            return True
        if self.scanner is not None:
            sl = getattr(self.scanner, "_sl_cooldown", None)
            if sl and now < sl.get(addr.lower(), 0):
                return True
        return False

    # ── Entry decision ──────────────────────────────────────────

    async def _maybe_fire_entry(self, addr: str, pair: dict, signal: TriggerSignal):
        if self._sol_is_bearish:
            logger.info(
                f"[ScalpQueue] No-trade: SOL bearish — skipping {signal.symbol}"
            )
            return
        if self._majority_red:
            logger.info(
                f"[ScalpQueue] No-trade: majority red — skipping {signal.symbol}"
            )
            return
        if not self.scalp_capital.has_capacity():
            return
        # Capital deployment cap
        deployed = self.scalp_capital.deployed_usd()
        cap_usd = self.scalp_capital.total_capital * self.cfg.scalp_max_deployment_pct
        if deployed + self.cfg.scalp_position_usd > cap_usd:
            logger.info(
                f"[ScalpQueue] Deployment cap hit (${deployed:.0f}/${cap_usd:.0f}) — "
                f"skipping {signal.symbol}"
            )
            return

        now_ts = int(time.time())
        scalp_meta = {
            "sweep_low": signal.sweep_low,
            "stop_price": signal.stop_price,
            "tp1_price": signal.tp1_price,
            "entry_close_time": now_ts,
        }

        logger.info(
            f"[ScalpQueue] ENTRY {signal.symbol} ({addr[:8]}) @ {signal.entry_price:.8f} "
            f"stop={signal.stop_price:.8f} tp1={signal.tp1_price:.8f} | {signal.reason}"
        )
        try:
            await self.trader.buy(
                token_address=addr,
                token_symbol=signal.symbol,
                strategy="scalp",
                override_usd=self.cfg.scalp_position_usd,
                reason=f"scalp-setup: {signal.reason}",
                scalp_meta=scalp_meta,
            )
            if addr.lower() in {a.lower() for a in (self.open_positions_ref or {}).keys()}:
                self.scalp_capital.record_open(addr, self.cfg.scalp_position_usd)
                self._watched.pop(addr, None)
        except Exception as e:
            logger.error(f"[ScalpQueue] Buy failed for {signal.symbol}: {e}")

    # ── Candidate fetch (DexScreener) ───────────────────────────

    async def _fetch_candidates(self) -> List[dict]:
        pairs: List[dict] = []
        seen = set()
        async with aiohttp.ClientSession() as session:
            for order in ("volume", "trending"):
                try:
                    url = (
                        f"https://api.dexscreener.com/latest/dex/search"
                        f"?q={_DEX_CHAIN}&order={order}"
                    )
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        for p in data.get("pairs") or []:
                            base = (p.get("baseToken") or {}).get("address", "")
                            if not base or base.startswith("0x") or base in seen:
                                continue
                            seen.add(base)
                            pairs.append(p)
                except Exception as e:
                    logger.debug(f"[ScalpQueue] DexScreener {order} error: {e}")
        return pairs

    # ── Maintenance ─────────────────────────────────────────────

    def _prune_watched(self):
        now = time.monotonic()
        expiry_s = self.cfg.scalp_watch_expiry_minutes * 60
        drop = [
            addr for addr, meta in self._watched.items()
            if now - meta["added_ts"] > expiry_s
        ]
        for addr in drop:
            self._watched.pop(addr, None)

    def _prune_cooldowns(self):
        now = time.monotonic()
        self._stop_cooldowns = {
            a: exp for a, exp in self._stop_cooldowns.items() if exp > now
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest test_scalp_queue.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add feeds/scalp_queue.py test_scalp_queue.py
git commit -m "feat(scalp): rewrite ScalpQueue as 4-phase orchestrator (GT OHLCV + setup detector)"
```

---

### Task 9: Wire in main.py and update PositionManager instantiation

**Files:**
- Modify: `main.py` (ScalpQueue construction block, ~lines 468-495)
- Modify: `main.py` (PositionManager construction — find where it receives scalp_* params)

- [ ] **Step 1: Update ScalpQueue wiring**

Locate the existing block around lines 468-495 (search: `scalp_queue = ScalpQueue(`). Replace with:

```python
        if config.scalp_enabled:
            from feeds.gecko_ohlcv import GeckoTerminalClient
            scalp_capital = ScalpCapitalManager(
                total_capital=config.scalp_capital,
                max_position_usd=config.scalp_position_usd,
                max_concurrent=config.scalp_max_concurrent,
                daily_loss_limit=config.scalp_daily_loss_limit,
            )
            gt_client = GeckoTerminalClient(
                cache_ttl=config.scalp_gt_cache_ttl_sec,
                rate_per_min=config.scalp_gt_rate_per_min,
            )
            scalp_queue = ScalpQueue(
                trader=sol_trader,
                open_positions_ref=sol_trader.open_positions,
                scalp_capital=scalp_capital,
                config=config,
                ohlcv_client=gt_client,
                scanner=sol_scanner,
            )
            sol_position_mgr.scalp_queue = scalp_queue
            dashboard.register_scalp_queue(scalp_queue, scalp_capital)
            tasks.append(scalp_queue.run())
            logger.info(
                f"[Main] ScalpQueue (4-phase) enabled — "
                f"${config.scalp_position_usd:.0f}/position, max={config.scalp_max_concurrent}, "
                f"TP1 +{config.scalp_tp1_pct}%/{int(config.scalp_tp1_sell*100)}%, "
                f"TP2 +{config.scalp_tp2_pct}%/{int(config.scalp_tp2_sell*100)}% of rem., "
                f"stop -{config.scalp_stop_pct}%"
            )
```

Note: the old wiring passed `axiom_price_feed` — remove it. The new orchestrator uses GeckoTerminal, not Axiom tick buffers.

- [ ] **Step 2: Update PositionManager wiring**

Find where `PositionManager(...)` is instantiated in main.py (grep: `PositionManager(`). Locate the scalp kwargs and update from:

```python
            scalp_tp1_pct=config.scalp_tp1_pct,
            scalp_tp2_pct=config.scalp_tp2_pct,
            scalp_stop_pct=config.scalp_stop_pct,
            scalp_max_hold_minutes=config.scalp_max_hold_minutes,
```

to:

```python
            scalp_tp1_pct=config.scalp_tp1_pct,
            scalp_tp1_sell=config.scalp_tp1_sell,
            scalp_tp2_pct=config.scalp_tp2_pct,
            scalp_tp2_sell=config.scalp_tp2_sell,
            scalp_stop_pct=config.scalp_stop_pct,
            scalp_time_exit_candles=config.scalp_time_exit_candles,
            scalp_time_exit_min_pct=config.scalp_time_exit_min_pct,
            scalp_max_hold_minutes=config.scalp_max_hold_minutes,
```

- [ ] **Step 3: Smoke-run the full test suite**

Run: `cd C:/Users/jcole/multichain-bot && python -m pytest -x -q`
Expected: all tests pass. If any existing test breaks due to a removed config/param, update the test to match the new surface (don't re-add removed params).

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(scalp): wire new ScalpQueue + GT client + PositionManager params"
```

---

### Task 10: Import the new strategy — fix any lingering axiom reference

The old ScalpQueue took `axiom_price_feed`; the new one does not. Any stale reference will break at import time.

**Files:**
- Verify: `dashboard/web_dashboard.py` — `register_scalp_queue` still works with a two-arg call.

- [ ] **Step 1: Grep for ghost references**

Run:
```bash
grep -n "axiom_price_feed" C:/Users/jcole/multichain-bot/feeds/scalp_queue.py
grep -n "axiom_price_feed" C:/Users/jcole/multichain-bot/main.py
grep -rn "scalp_max_hold_minutes" C:/Users/jcole/multichain-bot --include="*.py"
```

Expected: `feeds/scalp_queue.py` has zero hits. `main.py` has zero hits for `axiom_price_feed` in the scalp block.

- [ ] **Step 2: Sanity-import**

Run: `cd C:/Users/jcole/multichain-bot && python -c "import main"` (should print no errors). If it fails with ImportError or AttributeError, fix the specific caller.

Expected: exit code 0, no traceback.

- [ ] **Step 3: Commit (if any touchups needed)**

```bash
git add -A
git commit -m "chore(scalp): clean up residual axiom_price_feed references"
```

If no touchups are needed, skip this commit.

---

### Task 11: Deploy to Railway (paper mode)

Per user's memory (`feedback_commit_before_deploy.md`): always commit before deploy. Per `feedback_paper_mode.md`: verify PAPER_MODE stays true.

- [ ] **Step 1: Confirm clean working tree**

```bash
cd C:/Users/jcole/multichain-bot && git status
```

Expected: nothing to commit, working tree clean.

- [ ] **Step 2: Verify paper-mode env vars**

```bash
MSYS_NO_PATHCONV=1 railway variables --kv | grep -E "PAPER_MODE|TRADING_PAUSED|SCALP_ENABLED"
```

Expected: `PAPER_MODE=true`, `TRADING_PAUSED=false`, `SCALP_ENABLED=true` (or unset — default is True).

- [ ] **Step 3: Deploy**

```bash
cd C:/Users/jcole/multichain-bot && MSYS_NO_PATHCONV=1 railway up --detach
```

Expected: build succeeds, container restarts.

- [ ] **Step 4: Tail logs for 60 seconds**

```bash
MSYS_NO_PATHCONV=1 railway logs --tail 400
```

Expected startup lines (searched within tail):
- `[Main] ScalpQueue (4-phase) enabled`
- `[ScalpQueue] Starting — 4-phase detector`
- At least one `[ScalpQueue]` cycle heartbeat within 2 minutes
- No unhandled exception

- [ ] **Step 5: Document handoff**

Update `memory/project_bot_handoff.md` with: "2026-04-18: scalper rewritten as 4-phase setup detector + GeckoTerminal OHLCV. Old dip-buy ScalpQueue replaced. Paper mode, trading unpaused. Watch for TriggerSignal log lines to confirm detector firing."

---

## Self-Review

**Spec coverage check:**
- ✅ Core strategy (4-phase only) — Task 3 (detector).
- ✅ Market selection (5m vol, liquidity, age, no rug) — Task 8 (`_passes_candidate_gates`, `_is_rug`).
- ✅ Global no-trade filters (SOL, majority red, 3+ red no-wick, volume declining) — Tasks 5 + 8.
    - **Gap noted:** "volume declining during price drop" is NOT independently implemented — it is an emergent property of the sweep-detection requiring vol spike. Explicit trade-management exit "if volume decreases → reduce or exit" also not in `_evaluate_scalp` Task 7. **Decision:** leave as a v2 enhancement to avoid adding another OHLCV fetcher call inside position_manager hot loop; time-exit + hard stop cover the failure mode. The spec's "reduce or exit on volume drop" is a soft signal already dominated by the hard 6% stop + 4-candle time exit.
- ✅ Setup detection — Task 3.
- ✅ Entry rules (all 4 phases + R/R ≥ 2:1) — Task 3 (R/R gate inside detector).
- ✅ Position mgmt (size, stop below sweep or 6%, risk per trade) — Tasks 3 + 4 + 7.
- ✅ Take-profit strategy (+10%/50%, +15%/35% runner) — Tasks 4 + 7.
- ✅ Time-based exit — Tasks 4 + 7.
- ✅ Stop-loss rules (hard 6%, no overrides) — Task 7.
- ⚠ Trade mgmt "reduce on volume decrease / rejection wick" — deferred to v2 (see gap note above).
- ✅ Re-entry rules — Task 3 (detector resets only on new sweep + reclaim; Task 8 cooldown on stop_loss blocks re-entry for 45 min).
- ✅ Capital mgmt (60-80% cap) — Tasks 4 + 8.
- ✅ Execution priority — implicit in design (hard stop first in `_evaluate_scalp`).
- ✅ Output requirements — Tasks 3 + 7 + 8 (entry reason, entry price, stop, TP, exit reason logged).

**Placeholder scan:** No "TBD", "TODO", "implement later". All tests have concrete code. All config values have resolved defaults. One explicit deferral (volume-drop exit) is called out with reasoning, not a placeholder.

**Type consistency:**
- `TriggerSignal`: fields match across detector + ScalpQueue + PositionState meta dict keys (`sweep_low`, `stop_price`, `tp1_price`, `entry_price` / `entry_close_time`). Verified.
- `Candle` dataclass: same fields used in all tasks. Verified.
- `scalp_meta` dict keys: `sweep_low`, `stop_price`, `tp1_price`, `entry_close_time` — consistent between Task 6 test, Task 7 test, Task 8 emit. Verified.
- PositionManager constructor kwargs (`scalp_tp1_pct`, `scalp_tp1_sell`, `scalp_tp2_pct`, `scalp_tp2_sell`, `scalp_stop_pct`, `scalp_time_exit_candles`, `scalp_time_exit_min_pct`) match config fields and main.py wiring. Verified.

**Scope check:** Plan fits in one implementation session. Each task is 2-15 minutes of code + test. Total ~11 tasks, ~1-2 hours of focused work.
