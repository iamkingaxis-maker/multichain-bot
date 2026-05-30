# dip_runner Parallel Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a parallel `dip_runner` strategy implementing the "claude ideas 2" doc — loose entry filters, SOL+time+circuit regime gates, asymmetric exit ladder (SL -7%, TP1 +8%/33%, TP2 +20%/33%, runner trail) — running side-by-side with existing `dip_buy` so A/B comparison is visible on the dashboard.

**Architecture:** Bot already supports multi-strategy via `strategy=` parameter on `trader.buy()`. Add a new `strategy="dip_runner"` path with: (1) regime gates evaluated pre-buy, (2) loose filter cascade (skip most trader filters), (3) separate exit ladder in `position_manager.py`. Scanner fires BOTH strategy paths per signal — they make independent decisions. Capital is unlimited (paper $1M pool). Max 10 concurrent dip_runner positions. Dashboard renders a second per-strategy block.

**Tech Stack:** Python 3.12 (existing), aiohttp web dashboard, no new dependencies.

---

## File Structure

**New files:**
- `core/regime_gates.py` — SOL trend tier, time-of-day tier, circuit breaker class. Pure functions + small stateful class. ~150 lines.
- `tests/test_regime_gates.py` — unit tests for all three gate functions.

**Modified files:**
- `core/position_manager.py` — add dip_runner exit params; add `_check_runner_exit_ladder` branch (~80 new lines).
- `core/trader.py` — add `strategy == "dip_runner"` branches in filter cascade (skip aggressive filters); accept regime gate output.
- `feeds/dip_scanner.py` — fire `trader.buy(strategy="dip_runner", ...)` in parallel to existing `dip_buy` call when regime gates pass.
- `dashboard/web_dashboard.py` — new `/api/strategy-stats` endpoint; render second per-strategy block in HTML.
- `scripts/live_forward_test.py` — phantom parity for regime gates + exit ladder.
- `main.py` — wire regime gate state holders into Trader and PositionManager init.

---

## Why dip_buy stays untouched

This is a parallel A/B test, not a replacement. The `dip_buy` strategy keeps all current behavior (filters, fast-path, triggers shipped earlier today). `dip_runner` is purely additive code. No commits modify dip_buy's entry logic, filter set, or exit ladder.

---

### Task 1: Time-of-day gate function

**Files:**
- Create: `core/regime_gates.py`
- Test: `tests/test_regime_gates.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_regime_gates.py
import pytest
from datetime import datetime, timezone, timedelta
from core.regime_gates import time_of_day_tier

CT = timezone(timedelta(hours=-5))  # America/Chicago CDT (May)

def test_time_of_day_hard_on_midday():
    # 11am CT = hard_on
    t = datetime(2026, 5, 17, 11, 0, tzinfo=CT)
    assert time_of_day_tier(t) == "hard_on"

def test_time_of_day_hard_off_evening():
    # 8pm CT = hard_off
    t = datetime(2026, 5, 17, 20, 0, tzinfo=CT)
    assert time_of_day_tier(t) == "hard_off"

def test_time_of_day_soft_morning():
    # 7:30am CT = soft (7-9am window)
    t = datetime(2026, 5, 17, 7, 30, tzinfo=CT)
    assert time_of_day_tier(t) == "soft"

def test_time_of_day_soft_evening():
    # 6:30pm CT = soft (6-7pm window)
    t = datetime(2026, 5, 17, 18, 30, tzinfo=CT)
    assert time_of_day_tier(t) == "soft"

def test_time_of_day_hard_off_predawn():
    # 5am CT = hard_off (in 7pm-7am dead zone)
    t = datetime(2026, 5, 17, 5, 0, tzinfo=CT)
    assert time_of_day_tier(t) == "hard_off"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_regime_gates.py::test_time_of_day_hard_on_midday -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.regime_gates'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/regime_gates.py
"""Regime gates for dip_runner strategy — from "claude ideas 2" doc.

Three independent gates evaluated pre-buy in trader.buy(strategy="dip_runner"):
  1) time_of_day_tier — hour-of-day from US CT mapping
  2) sol_regime_tier  — SOL price action vs EMAs/VWAP
  3) circuit_breaker  — pause N hours after consecutive SLs

User confirmed (2026-05-17): wins 10am-6pm CT, loses after 7pm CT.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

# CT is UTC-6 (CST) / UTC-5 (CDT). We follow the 7pm-7am dead-zone with
# soft buffer windows (7-9am, 6-7pm) per the doc.
def time_of_day_tier(now_ct: datetime) -> str:
    """Returns 'hard_on', 'soft', or 'hard_off' based on CT hour-of-day.

    hard_on:  09:00 - 18:00 CT  (peak liquidity, proven win window)
    soft:     07:00 - 09:00 CT  (ramp) and 18:00 - 19:00 CT (cooldown)
    hard_off: 19:00 - 07:00 CT  (dead zone — losses cluster here)
    """
    h = now_ct.hour
    if 9 <= h < 18:
        return "hard_on"
    if (7 <= h < 9) or (18 <= h < 19):
        return "soft"
    return "hard_off"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_regime_gates.py -v -k time_of_day`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/regime_gates.py tests/test_regime_gates.py
git commit -m "feat(regime): time-of-day tier — hard_on 9am-6pm CT, hard_off 7pm-7am"
```

---

### Task 2: SOL regime tier function

**Files:**
- Modify: `core/regime_gates.py`
- Test: `tests/test_regime_gates.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_regime_gates.py`:

```python
from core.regime_gates import sol_regime_tier

def test_sol_tier_a_all_green():
    sol = {"sol_pc_h1": 0.5, "sol_above_ema15m_50": True, "sol_above_vwap_4h": True}
    assert sol_regime_tier(sol) == "A"

def test_sol_tier_c_all_red():
    sol = {"sol_pc_h1": -0.4, "sol_above_ema15m_50": False, "sol_above_vwap_4h": False}
    assert sol_regime_tier(sol) == "C"

def test_sol_tier_b_mixed():
    sol = {"sol_pc_h1": 0.2, "sol_above_ema15m_50": False, "sol_above_vwap_4h": True}
    assert sol_regime_tier(sol) == "B"

def test_sol_tier_c_min_viable_block():
    # Per doc: hard block when sol_pc_h1 < 0 AND below 15m_50_ema
    sol = {"sol_pc_h1": -0.3, "sol_above_ema15m_50": False, "sol_above_vwap_4h": True}
    assert sol_regime_tier(sol) == "C"

def test_sol_tier_missing_features():
    # If feature missing, fail-open to B (don't block on data gaps)
    assert sol_regime_tier({}) == "B"
    assert sol_regime_tier({"sol_pc_h1": 0.5}) == "B"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_regime_gates.py -v -k sol_tier`
Expected: FAIL with `ImportError: cannot import name 'sol_regime_tier'`

- [ ] **Step 3: Write minimal implementation**

Append to `core/regime_gates.py`:

```python
def sol_regime_tier(sol_features: dict) -> str:
    """Returns 'A', 'B', or 'C' based on SOL trend signal.

    A (full operation): sol_pc_h1 > 0 AND above 15m 50 EMA AND above 4H VWAP
    B (reduced):        mixed signals (some bullish some bearish, or partial data)
    C (no entries):     sol_pc_h1 < 0 AND below 15m 50 EMA  (per doc's
                        "minimum viable hard rule")

    Fail-open to B when features missing — don't block on data gaps.
    """
    h1 = sol_features.get("sol_pc_h1")
    above_ema = sol_features.get("sol_above_ema15m_50")
    above_vwap = sol_features.get("sol_above_vwap_4h")

    if h1 is None or above_ema is None:
        return "B"

    if h1 < 0 and above_ema is False:
        return "C"

    if h1 > 0 and above_ema is True and above_vwap is True:
        return "A"

    return "B"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_regime_gates.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add core/regime_gates.py tests/test_regime_gates.py
git commit -m "feat(regime): SOL trend tier — A/B/C from pc_h1 + EMA + VWAP"
```

---

### Task 3: Circuit breaker class

**Files:**
- Modify: `core/regime_gates.py`
- Test: `tests/test_regime_gates.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_regime_gates.py`:

```python
from core.regime_gates import CircuitBreaker

def test_breaker_initial_state_active():
    cb = CircuitBreaker(loss_streak_threshold=2, pause_hours=4.0)
    assert cb.is_active(now_ts=1000.0) is True

def test_breaker_trips_on_2_consecutive_sls():
    cb = CircuitBreaker(loss_streak_threshold=2, pause_hours=4.0)
    cb.record_exit(pnl_pct=-7.0, now_ts=1000.0)
    assert cb.is_active(now_ts=1001.0) is True  # 1 SL — still active
    cb.record_exit(pnl_pct=-7.0, now_ts=1010.0)
    assert cb.is_active(now_ts=1011.0) is False  # 2 consecutive SLs — paused

def test_breaker_resets_on_win():
    cb = CircuitBreaker(loss_streak_threshold=2, pause_hours=4.0)
    cb.record_exit(pnl_pct=-7.0, now_ts=1000.0)
    cb.record_exit(pnl_pct=+8.0, now_ts=1010.0)
    cb.record_exit(pnl_pct=-7.0, now_ts=1020.0)
    assert cb.is_active(now_ts=1021.0) is True  # streak broken by win

def test_breaker_recovers_after_pause():
    cb = CircuitBreaker(loss_streak_threshold=2, pause_hours=4.0)
    cb.record_exit(pnl_pct=-7.0, now_ts=1000.0)
    cb.record_exit(pnl_pct=-7.0, now_ts=1010.0)
    # 4 hours pass
    assert cb.is_active(now_ts=1010.0 + 4*3600 + 1) is True

def test_breaker_only_counts_real_losses():
    # tiny losses < 1% don't count as "SL"
    cb = CircuitBreaker(loss_streak_threshold=2, pause_hours=4.0, min_loss_pct=1.0)
    cb.record_exit(pnl_pct=-0.5, now_ts=1000.0)
    cb.record_exit(pnl_pct=-0.5, now_ts=1010.0)
    assert cb.is_active(now_ts=1011.0) is True  # both losses too small
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_regime_gates.py -v -k breaker`
Expected: FAIL with `ImportError: cannot import name 'CircuitBreaker'`

- [ ] **Step 3: Write minimal implementation**

Append to `core/regime_gates.py`:

```python
class CircuitBreaker:
    """Pauses entries after N consecutive significant losses.

    Per doc: '2 consecutive SLs = pause new entries for 4 hours minimum.
    This alone often converts -X% days into -1% days without affecting
    winning days.'

    Significant loss = pnl_pct <= -min_loss_pct (default 1%). Avoids
    false trips from tiny -0.5% noise exits.

    Stateful: maintains in-memory streak count + pause-until timestamp.
    Per-strategy instance — dip_runner has its own breaker.
    """
    def __init__(
        self,
        loss_streak_threshold: int = 2,
        pause_hours: float = 4.0,
        min_loss_pct: float = 1.0,
    ):
        self.loss_streak_threshold = loss_streak_threshold
        self.pause_seconds = pause_hours * 3600.0
        self.min_loss_pct = min_loss_pct
        self._consecutive_losses = 0
        self._paused_until_ts: float = 0.0

    def record_exit(self, pnl_pct: float, now_ts: float) -> None:
        if pnl_pct <= -self.min_loss_pct:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.loss_streak_threshold:
                self._paused_until_ts = now_ts + self.pause_seconds
        elif pnl_pct >= self.min_loss_pct:
            # Real win breaks the streak
            self._consecutive_losses = 0
        # else: tiny noise exit — neither counts nor resets

    def is_active(self, now_ts: float) -> bool:
        return now_ts >= self._paused_until_ts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_regime_gates.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add core/regime_gates.py tests/test_regime_gates.py
git commit -m "feat(regime): CircuitBreaker — 2 SLs pause 4h (per-strategy)"
```

---

### Task 4: SOL feature enrichment (above_ema15m_50 + above_vwap_4h)

**Files:**
- Modify: `feeds/dip_scanner.py` (sol_features computation block, lines ~1248-1311)

- [ ] **Step 1: Locate existing SOL feature block**

Read `feeds/dip_scanner.py` lines 1248-1311 to confirm structure. Search for `sol_features["sol_pc_h1"]` assignment.

- [ ] **Step 2: Add EMA15m_50 + VWAP_4h enrichment**

Find the line that assigns `sol_features["sol_pc_h1"]` (~line 1269) and insert below the SOL 5m bars section:

```python
                # 2026-05-17 — dip_runner regime gate inputs.
                # sol_above_ema15m_50: SOL last close vs 50-period EMA on 15m candles.
                # sol_above_vwap_4h:   SOL last close vs 4h-window VWAP (cumulative vol-weighted price).
                try:
                    # Need 15m candles for EMA — derive from 5m by aggregation.
                    # Use last ~50 fifteen-minute closes = ~750min of 5m data.
                    if len(sol_5m) >= 150:  # 150 * 5m = 750min = 50 15m bars
                        # Aggregate every 3 5m bars into 1 15m close.
                        bars15 = [sol_5m[i*3:(i+1)*3] for i in range(len(sol_5m)//3)]
                        closes15 = [b[-1].close for b in bars15 if b]
                        if len(closes15) >= 50:
                            # EMA50 — standard formula, k = 2/(N+1).
                            k = 2.0 / (50 + 1)
                            ema = closes15[-50]  # seed
                            for c in closes15[-49:]:
                                ema = c * k + ema * (1 - k)
                            sol_features["sol_above_ema15m_50"] = bool(closes15[-1] > ema)
                    # VWAP-4h — cumulative (price*vol)/vol over last 4h.
                    bars_4h = sol_5m[-48:]  # 48 5m bars = 240min = 4h
                    if bars_4h:
                        num = sum(b.close * (b.volume or 1.0) for b in bars_4h)
                        den = sum((b.volume or 1.0) for b in bars_4h)
                        if den > 0:
                            vwap_4h = num / den
                            sol_features["sol_above_vwap_4h"] = bool(bars_4h[-1].close > vwap_4h)
                except Exception as _e:
                    logger.debug(f"[DipScanner] sol regime enrich err: {_e}")
```

- [ ] **Step 3: Smoke test syntax**

Run: `python -c "import ast; ast.parse(open('feeds/dip_scanner.py', encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add feeds/dip_scanner.py
git commit -m "feat(scanner): SOL EMA15m_50 + VWAP_4h enrichment for dip_runner gates"
```

---

### Task 5: dip_runner exit ladder params in position_manager

**Files:**
- Modify: `core/position_manager.py` (lines ~360-432 — exit-param defaults block)

- [ ] **Step 1: Locate dip exit params**

Read `core/position_manager.py` lines 360-435. Find the `dip_tp1_pct: float = 3.0,` parameter and surrounding sibling params.

- [ ] **Step 2: Add runner exit params**

After the dip params block (around line 432, after `self.dip_winner_trail_pct = dip_winner_trail_pct`), add to `__init__` signature:

```python
                 # dip_runner exit ladder (claude ideas 2 doc) — asymmetric:
                 # TP1 +8% / 33%, TP2 +20% / 33%, runner 34% trailing.
                 # Hard SL at -7%. Designed for power-law tail capture.
                 runner_stop_loss_pct: float = -7.0,
                 runner_tp1_pct: float = 8.0,
                 runner_tp1_size_pct: float = 33.0,
                 runner_tp2_pct: float = 20.0,
                 runner_tp2_size_pct: float = 33.0,
                 runner_trail_pct: float = 10.0,  # 10% trail from peak on remaining 34%
```

And in the `__init__` body (around line 432):

```python
        self.runner_stop_loss_pct = runner_stop_loss_pct
        self.runner_tp1_pct = runner_tp1_pct
        self.runner_tp1_size_pct = runner_tp1_size_pct
        self.runner_tp2_pct = runner_tp2_pct
        self.runner_tp2_size_pct = runner_tp2_size_pct
        self.runner_trail_pct = runner_trail_pct
```

- [ ] **Step 3: Smoke test**

Run: `python -c "from core.position_manager import PositionManager; print('import OK')"`
Expected: `import OK`

- [ ] **Step 4: Commit**

```bash
git add core/position_manager.py
git commit -m "feat(pos): dip_runner exit ladder params — TP1 +8/33, TP2 +20/33, trail 10pp"
```

---

### Task 6: dip_runner exit ladder evaluation branch

**Files:**
- Modify: `core/position_manager.py`

- [ ] **Step 1: Locate dip_buy exit evaluation**

Search for `state.strategy == "dip_buy"` at line ~2466 and ~1739. The exit ladder for dip_buy is in `_check_dip_buy_exits` (or similar). Find the dispatch site that selects which exit handler to call based on strategy.

- [ ] **Step 2: Add dispatch branch + handler**

In the strategy dispatch block (around line 2466):

```python
            elif state.strategy == "dip_runner":
                # 2026-05-17 — claude ideas 2 exit ladder. Asymmetric:
                #   SL -7%, TP1 +8% / 33%, TP2 +20% / 33%, runner 34% trail 10pp.
                # No breakeven stop, no early TPs, no manual interventions
                # encoded — those are convention, not code.
                await self._check_runner_exits(state, current_price, now_ts)
```

Then add the handler method below the existing `_check_dip_buy_exits`:

```python
    async def _check_runner_exits(self, state, current_price: float, now_ts: float) -> None:
        """dip_runner exit ladder — claude ideas 2 doc.

        Stop: -7% hard SL (no breakeven stop — kills runners).
        TP1:  +8% — close 33% of position.
        TP2:  +20% — close 33% of position (remaining 34% becomes the runner).
        Runner: trailing 10pp from peak on remaining 34%.

        State flags (added to PositionState):
          - runner_tp1_fired: bool
          - runner_tp2_fired: bool
          - runner_peak_after_tp2: float (price peak observed after TP2 fires)
        """
        entry = state.entry_price
        pnl_pct = (current_price / entry - 1.0) * 100.0

        # Hard SL — full close, regardless of TP state.
        if pnl_pct <= self.runner_stop_loss_pct:
            await self._close_position(
                state, current_price,
                reason=f"Runner SL {pnl_pct:.2f}%<={self.runner_stop_loss_pct}%",
                size_pct=100.0,
            )
            return

        # TP1 — first partial.
        if not getattr(state, "runner_tp1_fired", False) and pnl_pct >= self.runner_tp1_pct:
            await self._close_position(
                state, current_price,
                reason=f"Runner TP1 +{pnl_pct:.2f}%",
                size_pct=self.runner_tp1_size_pct,
            )
            state.runner_tp1_fired = True
            return

        # TP2 — second partial, activates trailing on remainder.
        if not getattr(state, "runner_tp2_fired", False) and pnl_pct >= self.runner_tp2_pct:
            await self._close_position(
                state, current_price,
                reason=f"Runner TP2 +{pnl_pct:.2f}%",
                size_pct=self.runner_tp2_size_pct,
            )
            state.runner_tp2_fired = True
            state.runner_peak_after_tp2 = current_price
            return

        # Trailing on runner (post-TP2 only).
        if getattr(state, "runner_tp2_fired", False):
            peak = getattr(state, "runner_peak_after_tp2", current_price)
            if current_price > peak:
                state.runner_peak_after_tp2 = current_price
                peak = current_price
            drop_from_peak_pct = (peak / current_price - 1.0) * 100.0
            if drop_from_peak_pct >= self.runner_trail_pct:
                await self._close_position(
                    state, current_price,
                    reason=f"Runner trail -{drop_from_peak_pct:.2f}% from peak (final 34%)",
                    size_pct=100.0,
                )
                return
```

- [ ] **Step 3: Smoke test imports + init**

Run: `python -c "from core.position_manager import PositionManager; pm = PositionManager.__new__(PositionManager); print(hasattr(PositionManager, '_check_runner_exits'))"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add core/position_manager.py
git commit -m "feat(pos): _check_runner_exits — TP1+8/TP2+20/SL-7/trail-10pp ladder"
```

---

### Task 7: dip_runner trader.buy() loose-filter path

**Files:**
- Modify: `core/trader.py`

- [ ] **Step 1: Add regime gate check at top of buy() for dip_runner**

In `core/trader.py`, inside the `async def buy(...)` method (the one that dispatches `strategy == "dip_buy"`), add a regime gate check immediately after the strategy parameter is read. Search for the existing strategy dispatch block and add:

```python
            # ── dip_runner regime gates (2026-05-17, claude ideas 2 doc) ──
            # SOL trend (Tier C blocks) + Time-of-day (hard_off blocks)
            # + Circuit breaker (paused after 2 consecutive SLs).
            # Evaluated ONLY for dip_runner — dip_buy unaffected.
            if strategy == "dip_runner":
                from core.regime_gates import sol_regime_tier, time_of_day_tier
                from datetime import datetime, timezone, timedelta
                # SOL tier
                _sol_tier = sol_regime_tier(entry_meta or {})
                entry_meta["dip_runner_sol_tier"] = _sol_tier
                if _sol_tier == "C":
                    logger.info(f"[Trader/dip_runner] BLOCKED SOL-tier-C: {token_symbol}")
                    return
                # Time-of-day tier (use server clock converted to America/Chicago).
                # Bot runs in UTC; CT is UTC-5 (CDT). Hardcoded offset: tracker tasks
                # validate the DST shift at next clock change.
                _now_ct = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
                _tod = time_of_day_tier(_now_ct)
                entry_meta["dip_runner_tod"] = _tod
                if _tod == "hard_off":
                    logger.info(f"[Trader/dip_runner] BLOCKED time-of-day hard_off: {token_symbol} hour_ct={_now_ct.hour}")
                    return
                # Circuit breaker (instance lives on Trader — initialized in main.py).
                if self.runner_circuit_breaker is not None:
                    if not self.runner_circuit_breaker.is_active(now_ts=time.time()):
                        logger.info(f"[Trader/dip_runner] BLOCKED circuit-breaker paused: {token_symbol}")
                        return
```

- [ ] **Step 2: Skip aggressive filters for dip_runner**

Find the filter cascade entries in `core/trader.py` (lines ~1476, 1556, 1726, 1795, 1822 — the `if strategy == "dip_buy"` guards). For each filter that's wrapped in `if _verdict == "BLOCK" and strategy == "dip_buy"`, the `dip_runner` strategy will naturally skip them. **No change needed** — they're already strategy-gated. Verify:

```bash
grep -n "and strategy == \"dip_buy\"" core/trader.py
```
Expected: 5+ matches showing filter_quad, filter_top10_holder, filter_combo_v2, filter_chart_bear, others — all only apply to dip_buy.

- [ ] **Step 3: Add minimal safety filters for dip_runner**

The doc says "Just enough to avoid obvious garbage (rugs, dead liquidity, honeypots)." We keep:
- Rug check (already runs for ALL strategies pre-buy)
- Volume dead-check (lines 1857-1866, applies to all)
- Freshness gate (in scanner, applies to all)

No new safety filters needed — existing universal checks suffice.

- [ ] **Step 4: Add CircuitBreaker init slot on Trader**

Add to `core/trader.py` `__init__` (line ~168):

```python
        # Circuit breaker for dip_runner strategy (claude ideas 2).
        # Wired in main.py — None means no breaker (skip the gate).
        self.runner_circuit_breaker = None
```

- [ ] **Step 5: Smoke test**

Run: `python -c "import ast; ast.parse(open('core/trader.py', encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add core/trader.py
git commit -m "feat(trader): dip_runner regime gates + loose-filter path (SOL/TOD/breaker)"
```

---

### Task 8: Wire CircuitBreaker into trader on close

**Files:**
- Modify: `core/trader.py` (record_sell path)

- [ ] **Step 1: Find sell-side P&L hook**

Search for `self.risk_manager.record_sell` in `core/trader.py` (lines ~2371, 2543). The exit completion path.

- [ ] **Step 2: Add CB exit recording**

Below each `self.risk_manager.record_sell(...)` call, add:

```python
                # Update dip_runner circuit breaker on full closes only.
                if (
                    strategy == "dip_runner"
                    and self.runner_circuit_breaker is not None
                    and size_pct >= 99.0  # only on FULL closes (not partial TP1/TP2)
                ):
                    self.runner_circuit_breaker.record_exit(
                        pnl_pct=pnl_pct, now_ts=time.time()
                    )
```

(Find the appropriate context for `strategy`, `size_pct`, `pnl_pct` at each call site — they should be locals.)

- [ ] **Step 3: Wire CB instance in main.py**

Modify `main.py` (around line 247, the Trader init):

```python
        from core.regime_gates import CircuitBreaker
        sol_trader = Trader(config.solana_private_key, config.solana_rpc_url, ...)
        sol_trader.runner_circuit_breaker = CircuitBreaker(
            loss_streak_threshold=2, pause_hours=4.0, min_loss_pct=1.0
        )
```

- [ ] **Step 4: Commit**

```bash
git add core/trader.py main.py
git commit -m "feat(trader): wire CircuitBreaker exit recording for dip_runner"
```

---

### Task 9: dip_scanner parallel-strategy fork

**Files:**
- Modify: `feeds/dip_scanner.py` (around line 11461 — the existing trader.buy call)

- [ ] **Step 1: Locate existing buy call**

Read `feeds/dip_scanner.py` lines 11461-11490 — confirm the existing `await self.trader.buy(strategy="dip_buy", ...)` call structure.

- [ ] **Step 2: Add parallel dip_runner buy**

Immediately after the existing `await self.trader.buy(strategy="dip_buy", ...)` call (around line 11490), add:

```python
            # 2026-05-17 — Parallel dip_runner strategy fire (claude ideas 2).
            # Same signal, separate filter cascade + exit ladder. Regime gates
            # evaluated inside trader.buy(). 10 max concurrent. Capital pool
            # shared (paper mode is effectively unlimited at $1M).
            try:
                # Count current dip_runner positions to enforce 10-concurrent cap.
                runner_count = sum(
                    1 for p in self.trader.open_positions.values()
                    if getattr(p, "strategy", "") == "dip_runner"
                )
                if runner_count < 10:
                    await self.trader.buy(
                        token_address=token_address,
                        token_symbol=token_symbol,
                        chain_id="solana",
                        override_usd=self.position_usd,  # standard size, no sizing tiers
                        reason=(
                            f"dip_runner: 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% "
                            f"5m={pc_m5:+.1f}% bs_h6={ratio_h6:.2f} "
                            f"triggers={_triggers_fired}"
                        ),
                        strategy="dip_runner",
                        entry_meta=dict(entry_meta_dict),  # COPY — independent stamping
                    )
                else:
                    logger.info(
                        f"[DipScanner/dip_runner] concurrency cap: "
                        f"{runner_count}/10 open — skipping {token_symbol}"
                    )
            except Exception as _runner_e:
                logger.warning(f"[DipScanner/dip_runner] buy failed: {_runner_e}")
```

- [ ] **Step 3: Smoke test syntax**

Run: `python -c "import ast; ast.parse(open('feeds/dip_scanner.py', encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add feeds/dip_scanner.py
git commit -m "feat(scanner): parallel dip_runner buy fork — 10 concurrent cap"
```

---

### Task 10: Dashboard per-strategy stats endpoint

**Files:**
- Modify: `dashboard/web_dashboard.py`

- [ ] **Step 1: Find existing /api/stats handler**

Search `dashboard/web_dashboard.py` for `_handle_stats` definition. Confirm it aggregates across all strategies.

- [ ] **Step 2: Add per-strategy endpoint**

Add a new handler `_handle_strategy_stats` and route registration `self.app.router.add_get("/api/strategy-stats", self._handle_strategy_stats)`:

```python
    async def _handle_strategy_stats(self, request):
        """Per-strategy P&L + open-position + WR snapshot.

        Used by dashboard side-by-side blocks for dip_buy vs dip_runner A/B.
        Returns: {"dip_buy": {n_open, today_pnl, today_wr, lifetime_pnl, ...},
                  "dip_runner": {...}}
        """
        try:
            stats = {}
            for strat in ("dip_buy", "dip_runner"):
                # Open positions for this strategy
                open_pos = [
                    p for p in self.trader.open_positions.values()
                    if getattr(p, "strategy", "") == strat
                ]
                # Closed trades for this strategy (today)
                today_start = int(time.time()) - (int(time.time()) % 86400)
                today_trades = [
                    t for t in self.tracker.trades
                    if t.get("strategy") == strat
                    and t.get("type") == "sell"
                    and t.get("timestamp", 0) >= today_start
                ]
                pnl_today = sum(t.get("pnl", 0) for t in today_trades)
                wins_today = sum(1 for t in today_trades if t.get("pnl", 0) > 0)
                wr_today = (wins_today / len(today_trades) * 100.0) if today_trades else 0.0
                lifetime_pnl = sum(
                    t.get("pnl", 0) for t in self.tracker.trades
                    if t.get("strategy") == strat and t.get("type") == "sell"
                )
                stats[strat] = {
                    "n_open": len(open_pos),
                    "today_pnl": round(pnl_today, 2),
                    "today_trades": len(today_trades),
                    "today_wr_pct": round(wr_today, 1),
                    "lifetime_pnl": round(lifetime_pnl, 2),
                }
            return aiohttp.web.json_response(stats)
        except Exception as e:
            return aiohttp.web.json_response({"error": str(e)}, status=500)
```

- [ ] **Step 3: Register route**

In the `__init__` route registration block (around line 1602):

```python
        self.app.router.add_get("/api/strategy-stats", self._handle_strategy_stats)
```

- [ ] **Step 4: Smoke test**

Run: `python -c "import ast; ast.parse(open('dashboard/web_dashboard.py', encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add dashboard/web_dashboard.py
git commit -m "feat(dashboard): /api/strategy-stats — per-strategy A/B snapshot"
```

---

### Task 11: Dashboard HTML side-by-side blocks

**Files:**
- Modify: `dashboard/web_dashboard.py` (HTML rendering)

- [ ] **Step 1: Find the main stats card section in dashboard HTML**

Search for the existing "TOTAL P&L" or "OPEN POSITIONS" card block in the HTML returned by `_handle_index` (or wherever the dashboard HTML is built).

- [ ] **Step 2: Add side-by-side strategy blocks**

Insert a new section that fetches `/api/strategy-stats` and renders side-by-side cards:

```html
<!-- Strategy A/B comparison: dip_buy vs dip_runner -->
<div class="strategy-ab" style="display: flex; gap: 16px; margin: 16px 0;">
  <div class="strategy-block" style="flex: 1; padding: 12px; border: 1px solid #444; border-radius: 8px;">
    <h3 style="margin-top: 0;">dip_buy (production)</h3>
    <div id="dipbuy-stats">loading...</div>
  </div>
  <div class="strategy-block" style="flex: 1; padding: 12px; border: 1px solid #444; border-radius: 8px;">
    <h3 style="margin-top: 0;">dip_runner (claude ideas 2)</h3>
    <div id="diprunner-stats">loading...</div>
  </div>
</div>

<script>
async function loadStrategyStats() {
  try {
    const res = await fetch('/api/strategy-stats');
    const data = await res.json();
    function fmt(s) {
      if (!s) return '<i>no data</i>';
      return `
        Open: ${s.n_open}<br>
        Today: $${s.today_pnl} (${s.today_trades} trades, ${s.today_wr_pct}% WR)<br>
        Lifetime: $${s.lifetime_pnl}
      `;
    }
    document.getElementById('dipbuy-stats').innerHTML = fmt(data.dip_buy);
    document.getElementById('diprunner-stats').innerHTML = fmt(data.dip_runner);
  } catch (e) {
    console.error('strategy-stats fetch error', e);
  }
}
loadStrategyStats();
setInterval(loadStrategyStats, 10000);
</script>
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/web_dashboard.py
git commit -m "feat(dashboard): side-by-side dip_buy vs dip_runner blocks"
```

---

### Task 12: Phantom parity in live_forward_test.py

**Files:**
- Modify: `scripts/live_forward_test.py`

- [ ] **Step 1: Add regime gate mirrors**

Append to the predicate dict in `scripts/live_forward_test.py` (after existing entries):

```python
    # ── dip_runner regime gates (2026-05-17) ───────────────────────────
    # PASS when the gate would NOT block (so an "ALLOW" cohort matches).
    'DR_sol_tier_AB_pass': lambda c: (
        # dip_runner blocks Tier C only (sol_pc_h1<0 AND below 15m EMA).
        not (
            c.get('sol_pc_h1') is not None and c.get('sol_pc_h1') < 0
            and c.get('sol_above_ema15m_50') is False
        )
    ),
    'DR_time_of_day_pass': lambda c: (
        # hard_off if hour_ct is in [19, 7) — bot blocks this for dip_runner.
        # detected_at_iso is UTC; convert to CT (UTC-5).
        not (
            c.get('detected_at_iso') is not None
            and (
                (int(c['detected_at_iso'][11:13]) - 5) % 24 < 7
                or (int(c['detected_at_iso'][11:13]) - 5) % 24 >= 19
            )
        )
    ),
    'DR_combined_pass': lambda c: (
        # Both gates pass — what dip_runner actually trades.
        # Mirrors trader.buy(strategy="dip_runner") gate cascade.
        (
            not (
                c.get('sol_pc_h1') is not None and c.get('sol_pc_h1') < 0
                and c.get('sol_above_ema15m_50') is False
            )
        )
        and (
            c.get('detected_at_iso') is None
            or (
                ((int(c['detected_at_iso'][11:13]) - 5) % 24) >= 7
                and ((int(c['detected_at_iso'][11:13]) - 5) % 24) < 19
            )
        )
    ),
```

- [ ] **Step 2: Smoke test**

Run: `python -c "import ast; ast.parse(open('scripts/live_forward_test.py', encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/live_forward_test.py
git commit -m "feat(phantom): dip_runner regime-gate parity (SOL tier + TOD)"
```

---

### Task 13: Smoke test full integration locally + deploy

**Files:** none (verification only)

- [ ] **Step 1: Full syntax sweep**

Run:
```bash
python -c "import ast
for f in ['feeds/dip_scanner.py', 'core/trader.py', 'core/position_manager.py', 'core/regime_gates.py', 'dashboard/web_dashboard.py', 'main.py', 'scripts/live_forward_test.py']:
    ast.parse(open(f, encoding='utf-8').read())
    print(f, 'OK')"
```
Expected: each file reports `OK`.

- [ ] **Step 2: Run regime gate tests**

Run: `python -m pytest tests/test_regime_gates.py -v`
Expected: 14 passed.

- [ ] **Step 3: Verify no patient_bottom regression**

Run: `grep -n '_triggers_fired.append("patient_bottom")' feeds/dip_scanner.py`
Expected: only commented-out occurrence.

- [ ] **Step 4: Push + deploy**

Run:
```bash
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

- [ ] **Step 5: Verify deploy live**

Wait for bot to come back, then run:
```bash
MSYS_NO_PATHCONV=1 railway logs --tail 50 | grep -iE "(dip_runner|Cycle [0-9]+)"
```
Expected: at least one `dip_runner` log line (gate evaluation or buy attempt) and a cycle log.

- [ ] **Step 6: Verify dashboard endpoint**

Run:
```bash
curl -s https://gracious-inspiration-production.up.railway.app/api/strategy-stats | python -m json.tool
```
Expected: JSON with `dip_buy` and `dip_runner` keys, both with `n_open`, `today_pnl`, etc.

- [ ] **Step 7: No commit needed**

This task is verification — no code changes.

---

## Validation plan (post-deploy)

After 2 weeks (or 200+ trades per the doc):

1. Compare `dip_buy` vs `dip_runner` blocks on the dashboard daily.
2. If `dip_runner` lifetime_pnl beats `dip_buy` lifetime_pnl over 2 weeks → adopt as primary strategy. Demote dip_buy or run it as the new "shadow."
3. If `dip_buy` wins → kill dip_runner code path, archive the doc as a tested-and-rejected hypothesis.
4. If neither clearly wins → keep both running, refine each independently.

The doc's "Hard No's" (no manual interventions, no early TPs, no breakeven stops, no mid-test parameter changes) are **strategy conventions, not enforced in code**. They apply to user behavior, not code logic. User commits to following them.

---

## Self-review

**Spec coverage check:**
- ✓ Loose entry filters — Task 7 (`strategy == "dip_runner"` naturally skips filters guarded by `strategy == "dip_buy"`)
- ✓ SOL regime filter — Tasks 2, 4, 7
- ✓ Time-of-day filter — Tasks 1, 7
- ✓ Circuit breaker — Tasks 3, 8
- ✓ Exit ladder (SL -7%, TP1 +8/33, TP2 +20/33, runner trail) — Tasks 5, 6
- ✓ Position sizing $20 — Task 9 (uses `self.position_usd`)
- ✓ Logging schema — exists for `dip_buy` already; new entry_meta keys added in Task 7 (`dip_runner_sol_tier`, `dip_runner_tod`)
- ✓ Parallel dashboard visibility — Tasks 10, 11
- ✓ 10 concurrent cap — Task 9

**Gap**: doc's Step 1 (backtest existing log tagged with SOL regime + hour) is NOT a code task — it's research that can run separately after deploy using the new entry_meta fields. Not in this plan; can be a follow-up task.

**Placeholder scan:** no TBDs, no "implement later," every code block is complete.

**Type consistency:** `state.runner_tp1_fired`, `state.runner_tp2_fired`, `state.runner_peak_after_tp2` introduced in Task 6 — they're optional state fields via `getattr(state, ..., False/default)`, so no schema change needed in `PositionState`. ✓
