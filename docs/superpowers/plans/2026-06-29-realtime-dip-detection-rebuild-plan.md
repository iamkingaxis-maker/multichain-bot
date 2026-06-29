# Real-Time Dip Detection Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute the dip signal (`pc_m5/h1/h6/h24`) against a real-time rolling reference (in-memory Jupiter price buffer + io.dexscreener bars) instead of the ~2-min-stale snapshot anchor, behind a shadow-first `RT_DIP_MODE` flag.

**Architecture:** A new pure module `core/realtime_dip.py` holds a per-token rolling price buffer and a `compute_rt_price_change` function that derives real-time percent-change off the window high. A bar-window helper in `feeds/dexscreener_chart_format.py` gives historical depth from io.dexscreener bars. `feeds/dip_scanner.py` wires these into the fast-watch reprice site, branching on `RT_DIP_MODE` (off = byte-identical, shadow = log divergence, enforce = overwrite `priceChange`), falling back to today's `reprice_all` whenever the real-time reference is unusable.

**Tech Stack:** Python 3, asyncio, `curl_cffi` (keyless `impersonate=chrome`), pytest. Reuses `core/fast_watch.py` (`rolling_dip_pct`, `reprice_all`, `rt_mode`), `feeds/dexscreener_chart_format.py` (`parse_chart_bars`), and `feeds/dip_scanner.py` (`run_ds_fetch`, the io.dexscreener 1S fetch pattern at ~16864, the reprice site at ~6524).

## Global Constraints

- **Free tools only** — io.dexscreener via keyless `curl_cffi` `impersonate="chrome"` with `Origin: https://dexscreener.com` / `Referer: https://dexscreener.com/` headers; Jupiter keyless. No paid RPC/keys.
- **Every new behavior behind `RT_DIP_MODE` off/shadow/enforce**, per-bot resolvable via `core.fast_watch.rt_mode("RT_DIP_MODE", bot_cfg)`; **default `off` = byte-identical** to current behavior.
- **All external fetches off the event loop** (`run_ds_fetch` / `asyncio.to_thread`) — no new loop-block.
- **Never fail-open into a buy** — when the real-time reference is unusable (`coverage == "NONE"`), fall back to the existing `reprice_all` path; never fabricate a dip.
- **Pure logic never raises** — `compute_rt_price_change` / `rolling_high_from_bars` / `RollingPriceWindow` methods return sentinel values, never throw.
- **Horizon→window seconds:** `m5=300, h1=3600, h6=21600, h24=86400`. Dip measured off the window **high**: `(fresh_price / window_high - 1) * 100`.
- **Staleness guard:** if the freshest contributing sample is older than `RT_DIP_MAX_AGE_SECS` (default 90s), treat the reference as unusable for that horizon.
- **Paper shadow → paper enforce before any live**; never flip `PAPER_MODE` without approval.
- **`python -m pytest tests/test_pre_live_invariants.py -q` green before any deploy.**

---

### Task 1: `RollingPriceWindow` — in-memory rolling price buffer

**Files:**
- Create: `core/realtime_dip.py`
- Test: `tests/test_realtime_dip.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `class RollingPriceWindow(max_age_secs: float = 86400.0, max_samples: int = 4000)`
  - `.append(ts: float, price: float) -> None` — drops samples with `price <= 0`; evicts by age (`ts < now - max_age_secs`, where `now` = the just-appended ts) and by count (keep newest `max_samples`).
  - `.window_high(secs: float, now: float) -> float | None` — max price over samples with `ts >= now - secs`; `None` if none.
  - `.window_low(secs: float, now: float) -> float | None` — min over the same window; `None` if none.
  - `.newest_ts() -> float | None` — ts of most recent sample, or `None`.
  - `.__len__() -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_realtime_dip.py
from core.realtime_dip import RollingPriceWindow


def test_append_and_window_high_low():
    w = RollingPriceWindow()
    w.append(1000.0, 1.0)
    w.append(1001.0, 2.0)
    w.append(1002.0, 1.5)
    assert w.window_high(10.0, 1002.0) == 2.0
    assert w.window_low(10.0, 1002.0) == 1.0
    assert len(w) == 3
    assert w.newest_ts() == 1002.0


def test_window_excludes_out_of_window_samples():
    w = RollingPriceWindow()
    w.append(1000.0, 5.0)   # old high
    w.append(1100.0, 2.0)
    # window of 50s ending at 1100 excludes the 1000 sample
    assert w.window_high(50.0, 1100.0) == 2.0
    # wide window includes it
    assert w.window_high(500.0, 1100.0) == 5.0


def test_evicts_by_age_on_append():
    w = RollingPriceWindow(max_age_secs=100.0)
    w.append(1000.0, 1.0)
    w.append(1200.0, 2.0)   # now=1200, max_age=100 -> 1000 evicted
    assert len(w) == 1
    assert w.window_high(10_000.0, 1200.0) == 2.0


def test_evicts_by_count():
    w = RollingPriceWindow(max_age_secs=1e9, max_samples=3)
    for i in range(5):
        w.append(1000.0 + i, float(i + 1))
    assert len(w) == 3
    # newest three are prices 3,4,5
    assert w.window_high(10_000.0, 1004.0) == 5.0
    assert w.window_low(10_000.0, 1004.0) == 3.0


def test_ignores_nonpositive_prices():
    w = RollingPriceWindow()
    w.append(1000.0, 0.0)
    w.append(1001.0, -1.0)
    assert len(w) == 0
    assert w.window_high(10.0, 1001.0) is None
    assert w.newest_ts() is None


def test_empty_window_returns_none():
    w = RollingPriceWindow()
    assert w.window_high(10.0, 1000.0) is None
    assert w.window_low(10.0, 1000.0) is None
    assert len(w) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_realtime_dip.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.realtime_dip'`

- [ ] **Step 3: Write the minimal implementation**

```python
# core/realtime_dip.py
"""Real-time dip reference (RT_DIP_MODE).

Pure logic for computing the dip signal off a LIVE rolling reference instead of
the ~2-min-stale DexScreener snapshot anchor. Two sources feed the reference:
an in-memory per-token price buffer (built from the fresh Jupiter prices the
fast-watch already polls) and io.dexscreener bars (historical depth). Nothing
here touches the network or raises.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Tuple


class RollingPriceWindow:
    """Per-token ring buffer of (ts, price), evicted by age and count.

    Only positive prices are stored. window_high/window_low scan the samples
    whose ts is within `secs` of the supplied `now`. All methods are pure and
    never raise.
    """

    def __init__(self, max_age_secs: float = 86400.0, max_samples: int = 4000) -> None:
        self._samples: Deque[Tuple[float, float]] = deque()
        self._max_age = float(max_age_secs)
        self._max_samples = int(max_samples)

    def append(self, ts: float, price: float) -> None:
        try:
            ts = float(ts)
            price = float(price)
        except (TypeError, ValueError):
            return
        if not (price > 0):
            return
        self._samples.append((ts, price))
        cutoff = ts - self._max_age
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        while len(self._samples) > self._max_samples:
            self._samples.popleft()

    def window_high(self, secs: float, now: float) -> Optional[float]:
        lo = float(now) - float(secs)
        vals = [p for (t, p) in self._samples if t >= lo]
        return max(vals) if vals else None

    def window_low(self, secs: float, now: float) -> Optional[float]:
        lo = float(now) - float(secs)
        vals = [p for (t, p) in self._samples if t >= lo]
        return min(vals) if vals else None

    def newest_ts(self) -> Optional[float]:
        return self._samples[-1][0] if self._samples else None

    def __len__(self) -> int:
        return len(self._samples)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_realtime_dip.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add core/realtime_dip.py tests/test_realtime_dip.py
git commit -m "feat(realtime-dip): RollingPriceWindow in-memory price buffer (pure)"
```

---

### Task 2: `rolling_high_from_bars` — historical depth from io.dexscreener bars

**Files:**
- Modify: `feeds/dexscreener_chart_format.py` (append a new function after `parse_chart_bars`, ~line 116)
- Test: `tests/test_rolling_high_from_bars.py`

**Interfaces:**
- Consumes: bar dicts as produced by `parse_chart_bars` — each `{"ts_ms": int, "high": float, "low": float, ...}`, oldest-first.
- Produces:
  - `rolling_high_from_bars(bars: list, window_secs: float, now_ms: float) -> float | None` — max `bar["high"]` (with `high > 0`) over bars whose `ts_ms >= now_ms - window_secs*1000`; `None` if no qualifying bar.
  - `rolling_low_from_bars(bars: list, window_secs: float, now_ms: float) -> float | None` — same with `min` of `bar["low"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rolling_high_from_bars.py
from feeds.dexscreener_chart_format import (
    rolling_high_from_bars,
    rolling_low_from_bars,
)


def _bar(ts_ms, high, low):
    return {"ts_ms": ts_ms, "open": high, "close": low, "high": high, "low": low,
            "volume_usd": 0.0, "block_first": 0, "block_last": 0}


def test_high_low_over_window():
    now_ms = 2_000_000_000_000
    bars = [
        _bar(now_ms - 100_000, 5.0, 4.0),
        _bar(now_ms - 50_000, 3.0, 2.0),
        _bar(now_ms - 10_000, 4.0, 3.5),
    ]
    # 200s window includes all -> high 5.0, low 2.0
    assert rolling_high_from_bars(bars, 200.0, now_ms) == 5.0
    assert rolling_low_from_bars(bars, 200.0, now_ms) == 2.0


def test_window_excludes_old_bars():
    now_ms = 2_000_000_000_000
    bars = [
        _bar(now_ms - 100_000, 9.0, 8.0),  # 100s old
        _bar(now_ms - 10_000, 3.0, 2.0),
    ]
    # 30s window excludes the 100s-old bar
    assert rolling_high_from_bars(bars, 30.0, now_ms) == 3.0
    assert rolling_low_from_bars(bars, 30.0, now_ms) == 2.0


def test_empty_bars_returns_none():
    assert rolling_high_from_bars([], 100.0, 2_000_000_000_000) is None
    assert rolling_low_from_bars([], 100.0, 2_000_000_000_000) is None


def test_malformed_bars_skipped():
    now_ms = 2_000_000_000_000
    bars = [
        {"ts_ms": now_ms - 5_000},                 # missing high/low
        {"high": "x", "low": "y", "ts_ms": now_ms},  # non-numeric
        _bar(now_ms - 1_000, 7.0, 6.0),
    ]
    assert rolling_high_from_bars(bars, 100.0, now_ms) == 7.0
    assert rolling_low_from_bars(bars, 100.0, now_ms) == 6.0


def test_nonpositive_high_ignored():
    now_ms = 2_000_000_000_000
    bars = [_bar(now_ms - 1_000, 0.0, 0.0), _bar(now_ms, 2.0, 1.0)]
    assert rolling_high_from_bars(bars, 100.0, now_ms) == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rolling_high_from_bars.py -q`
Expected: FAIL with `ImportError: cannot import name 'rolling_high_from_bars'`

- [ ] **Step 3: Write the minimal implementation**

Append to `feeds/dexscreener_chart_format.py`:

```python
def rolling_high_from_bars(bars, window_secs, now_ms):
    """Max bar high over bars whose ts_ms is within window_secs of now_ms.

    Returns None if no qualifying bar with a positive high. Pure; never raises;
    skips malformed/non-numeric bars."""
    lo_ms = float(now_ms) - float(window_secs) * 1000.0
    best = None
    for b in bars or []:
        try:
            if float(b["ts_ms"]) < lo_ms:
                continue
            h = float(b["high"])
        except (KeyError, TypeError, ValueError):
            continue
        if h > 0 and (best is None or h > best):
            best = h
    return best


def rolling_low_from_bars(bars, window_secs, now_ms):
    """Min bar low over bars whose ts_ms is within window_secs of now_ms.

    Returns None if no qualifying bar with a positive low. Pure; never raises."""
    lo_ms = float(now_ms) - float(window_secs) * 1000.0
    best = None
    for b in bars or []:
        try:
            if float(b["ts_ms"]) < lo_ms:
                continue
            lw = float(b["low"])
        except (KeyError, TypeError, ValueError):
            continue
        if lw > 0 and (best is None or lw < best):
            best = lw
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rolling_high_from_bars.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add feeds/dexscreener_chart_format.py tests/test_rolling_high_from_bars.py
git commit -m "feat(realtime-dip): rolling_high/low_from_bars window helpers (pure)"
```

---

### Task 3: `compute_rt_price_change` — real-time priceChange + coverage stamp

**Files:**
- Modify: `core/realtime_dip.py` (add function + constants)
- Test: `tests/test_realtime_dip.py` (add cases)

**Interfaces:**
- Consumes: `RollingPriceWindow` (Task 1), `rolling_high_from_bars` (Task 2).
- Produces:
  - `HORIZON_SECS = {"m5": 300.0, "h1": 3600.0, "h6": 21600.0, "h24": 86400.0}`
  - `compute_rt_price_change(buffer, bars, fresh_price, now, horizons=("m5","h1","h6","h24"), max_age_secs=90.0) -> tuple[dict, str]`
    - Returns `(price_change_dict, coverage)`.
    - `price_change_dict`: `{horizon: pct}` for each horizon with a usable window high, `pct = round((fresh_price/window_high - 1)*100, 6)`.
    - `coverage`: `"NONE"` if nothing usable (then dict is `{}`); `"BARS+BUFFER"` if any bar high contributed; else `"BUFFER_ONLY"`.
    - Staleness: if `buffer.newest_ts()` is older than `now - max_age_secs` AND `bars` is empty, the buffer is too stale to trust alone → `coverage="NONE"`, `{}`.
    - `fresh_price <= 0` → `({}, "NONE")`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_realtime_dip.py
from core.realtime_dip import compute_rt_price_change, HORIZON_SECS, RollingPriceWindow


def _bar(ts_ms, high, low):
    return {"ts_ms": ts_ms, "open": high, "close": low, "high": high, "low": low,
            "volume_usd": 0.0, "block_first": 0, "block_last": 0}


def test_compute_dip_off_buffer_only():
    now = 2_000_000_000.0  # seconds
    w = RollingPriceWindow()
    w.append(now - 60, 2.0)   # recent high
    w.append(now - 1, 1.0)
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now)
    assert cov == "BUFFER_ONLY"
    # m5/h1 windows both see high 2.0 -> -50%
    assert pc["m5"] == -50.0
    assert pc["h1"] == -50.0


def test_compute_combines_bars_and_buffer():
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 120, 1.0)                      # >=2 samples so the buffer is usable
    w.append(now - 1, 1.0)
    bars = [_bar(now_ms - 1800_000, 4.0, 3.0)]   # 30min-old bar high 4.0 (in h1 window)
    pc, cov = compute_rt_price_change(w, bars, fresh_price=1.0, now=now)
    assert cov == "BARS+BUFFER"
    # h1 sees the bar high 4.0 -> -75%; m5 sees only the buffer 1.0 -> 0%
    assert pc["h1"] == -75.0
    assert pc["m5"] == 0.0


def test_compute_none_when_empty():
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now)
    assert cov == "NONE"
    assert pc == {}


def test_compute_none_on_nonpositive_fresh():
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    w.append(now - 1, 2.0)
    pc, cov = compute_rt_price_change(w, [], fresh_price=0.0, now=now)
    assert cov == "NONE"
    assert pc == {}


def test_compute_none_when_buffer_stale_and_no_bars():
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    w.append(now - 600, 2.0)   # newest sample 600s old > max_age 90s
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now, max_age_secs=90.0)
    assert cov == "NONE"
    assert pc == {}


def test_compute_uses_bars_when_buffer_stale():
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 600, 2.0)   # stale buffer
    bars = [_bar(now_ms - 30_000, 4.0, 3.0)]  # fresh bar 30s old
    pc, cov = compute_rt_price_change(w, bars, fresh_price=1.0, now=now, max_age_secs=90.0)
    assert cov == "BARS+BUFFER"
    assert pc["m5"] == -75.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_realtime_dip.py -q`
Expected: FAIL with `ImportError: cannot import name 'compute_rt_price_change'`

- [ ] **Step 3: Write the minimal implementation**

Append to `core/realtime_dip.py` (add `from feeds.dexscreener_chart_format import rolling_high_from_bars` at top):

```python
HORIZON_SECS = {"m5": 300.0, "h1": 3600.0, "h6": 21600.0, "h24": 86400.0}


def compute_rt_price_change(buffer, bars, fresh_price, now,
                            horizons=("m5", "h1", "h6", "h24"),
                            max_age_secs=90.0):
    """Real-time priceChange dict + coverage stamp off the window high.

    Returns ({horizon: pct}, coverage) where coverage is one of
    "BARS+BUFFER" / "BUFFER_ONLY" / "NONE". pct = (fresh/window_high - 1)*100.
    Falls to ({}, "NONE") when nothing is usable: fresh<=0, or no source
    yields a window high, or the buffer's newest sample is staler than
    max_age_secs AND there are no bars. Pure; never raises."""
    try:
        fp = float(fresh_price)
    except (TypeError, ValueError):
        return {}, "NONE"
    if not (fp > 0):
        return {}, "NONE"

    has_bars = bool(bars)
    newest = buffer.newest_ts() if buffer is not None else None
    buffer_stale = (newest is None) or (float(now) - float(newest) > float(max_age_secs))
    if buffer_stale and not has_bars:
        return {}, "NONE"

    now_ms = float(now) * 1000.0
    # Buffer contributes a reference only with >=2 samples — a single sample is
    # just the current price, giving a degenerate 0% dip (mirrors rolling_dip_pct).
    buf_usable = (buffer is not None and not buffer_stale and len(buffer) >= 2)
    out = {}
    bars_contributed = False
    buffer_contributed = False
    for h in horizons:
        secs = HORIZON_SECS.get(h)
        if secs is None:
            continue
        bar_hi = rolling_high_from_bars(bars, secs, now_ms) if has_bars else None
        buf_hi = buffer.window_high(secs, now) if buf_usable else None
        highs = [x for x in (bar_hi, buf_hi) if x is not None and x > 0]
        if not highs:
            continue
        window_high = max(highs)
        out[h] = round((fp / window_high - 1.0) * 100.0, 6)
        if bar_hi is not None and bar_hi > 0:
            bars_contributed = True
        if buf_hi is not None and buf_hi > 0:
            buffer_contributed = True

    if not out:
        return {}, "NONE"
    if bars_contributed:
        return out, "BARS+BUFFER"
    if buffer_contributed:
        return out, "BUFFER_ONLY"
    return {}, "NONE"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_realtime_dip.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add core/realtime_dip.py tests/test_realtime_dip.py
git commit -m "feat(realtime-dip): compute_rt_price_change + coverage stamp (pure)"
```

---

### Task 4: Per-token bar cache + off-loop io.dexscreener 1m fetch on `DipScanner`

**Files:**
- Modify: `feeds/dip_scanner.py` (add a method + cache dict on `DipScanner`; reuse the 1S fetch pattern at ~16864 and `run_ds_fetch`)
- Test: `tests/test_rt_dip_bar_cache.py`

**Interfaces:**
- Consumes: `parse_chart_bars` (existing), `run_ds_fetch` (existing), the io.dexscreener URL pattern.
- Produces (on `DipScanner`):
  - `self._rt_dip_bar_cache: dict[str, tuple[list, float]]` — `addr -> (bars, fetched_ts)`, initialized in `__init__`.
  - `async def _get_rt_dip_bars(self, addr, dex_slug, pair_addr, *, res="1m", ttl_secs=60.0, now=None) -> list` — returns cached bars if `now - fetched_ts < ttl_secs`, else fetches off-loop via `run_ds_fetch`, parses with `parse_chart_bars`, caches, and returns. On fetch failure returns the last cached bars (or `[]`). Never raises.

**Note for implementer:** the fetch closure mirrors `_fetch_1s_sync` at dip_scanner.py:16866 — same `curl_cffi impersonate="chrome"`, same `Origin`/`Referer` headers, same URL shape `https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}?res={res}&cb=999&q={SOL_QUOTE}`, with `_SOL_QUOTE = "So11111111111111111111111111111111111111112"`. Use `res="1m"` for the 24h reference. Test with a monkeypatched fetch — do NOT hit the network in tests.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rt_dip_bar_cache.py
import asyncio
import pytest
from feeds.dip_scanner import DipScanner


def _mk_scanner():
    # DipScanner has heavy deps; construct without __init__ for a unit cache test.
    s = DipScanner.__new__(DipScanner)
    s._rt_dip_bar_cache = {}
    return s


def test_cache_hit_skips_fetch(monkeypatch):
    s = _mk_scanner()
    calls = {"n": 0}

    async def fake_fetch(self, addr, dex_slug, pair_addr, *, res="1m", ttl_secs=60.0, now=None):
        # call the REAL method but stub the network layer it uses
        raise AssertionError("should not be called")

    s._rt_dip_bar_cache["AAA"] = ([{"ts_ms": 1, "high": 2.0, "low": 1.0}], 1000.0)

    async def run():
        bars = await s._get_rt_dip_bars("AAA", "ray", "pair", ttl_secs=60.0, now=1030.0)
        return bars

    bars = asyncio.run(run())
    assert bars == [{"ts_ms": 1, "high": 2.0, "low": 1.0}]


def test_cache_miss_fetches_and_caches(monkeypatch):
    s = _mk_scanner()
    parsed = [{"ts_ms": 5, "high": 9.0, "low": 8.0}]

    async def fake_run_ds_fetch(fn, arg):
        return b"rawbytes"

    monkeypatch.setattr("feeds.dip_scanner.run_ds_fetch", fake_run_ds_fetch, raising=False)
    monkeypatch.setattr("feeds.dip_scanner.parse_chart_bars", lambda raw: parsed, raising=False)

    async def run():
        return await s._get_rt_dip_bars("BBB", "ray", "pair", ttl_secs=60.0, now=2000.0)

    bars = asyncio.run(run())
    assert bars == parsed
    assert s._rt_dip_bar_cache["BBB"][0] == parsed


def test_fetch_failure_returns_stale_cache(monkeypatch):
    s = _mk_scanner()
    s._rt_dip_bar_cache["CCC"] = ([{"ts_ms": 1, "high": 2.0, "low": 1.0}], 100.0)

    async def boom(fn, arg):
        raise RuntimeError("io.dx down")

    monkeypatch.setattr("feeds.dip_scanner.run_ds_fetch", boom, raising=False)

    async def run():
        return await s._get_rt_dip_bars("CCC", "ray", "pair", ttl_secs=1.0, now=10_000.0)

    bars = asyncio.run(run())
    assert bars == [{"ts_ms": 1, "high": 2.0, "low": 1.0}]  # stale cache, no raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rt_dip_bar_cache.py -q`
Expected: FAIL with `AttributeError: ... has no attribute '_get_rt_dip_bars'`

- [ ] **Step 3: Write the minimal implementation**

In `DipScanner.__init__`, add the cache dict (place near other per-token caches):

```python
        self._rt_dip_bar_cache = {}  # addr -> (bars, fetched_ts); RT_DIP bar reference
```

Add the method to `DipScanner` (near the 1S fetch helpers, ~line 16860). Ensure `parse_chart_bars` and `run_ds_fetch` are importable at module scope (the file already imports them in the 1S path — hoist to a module-level import if not already):

```python
    async def _get_rt_dip_bars(self, addr, dex_slug, pair_addr, *, res="1m",
                               ttl_secs=60.0, now=None):
        """Cached io.dexscreener bars for the RT dip reference. Returns the
        cached bars within ttl, else fetches off-loop, parses, caches. On any
        fetch/parse failure returns the last cached bars (or []). Never raises."""
        import time as _t
        from feeds.dexscreener_chart_format import parse_chart_bars
        now = _t.time() if now is None else now
        cached = self._rt_dip_bar_cache.get(addr)
        if cached is not None and (now - cached[1]) < ttl_secs:
            return cached[0]
        _SOL_QUOTE = "So11111111111111111111111111111111111111112"

        def _fetch_sync(slug):
            try:
                from curl_cffi import requests as _cf
                _url = (
                    f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}"
                    f"/bars/solana/{pair_addr}?res={res}&cb=999&q={_SOL_QUOTE}"
                )
                _r = _cf.get(_url, impersonate="chrome", timeout=8,
                             headers={"Origin": "https://dexscreener.com",
                                      "Referer": "https://dexscreener.com/"})
                if _r.status_code == 200 and _r.content:
                    return _r.content
            except Exception:
                return None
            return None

        try:
            raw = await run_ds_fetch(_fetch_sync, dex_slug)
            bars = parse_chart_bars(raw) if raw else None
            if bars:
                self._rt_dip_bar_cache[addr] = (bars, now)
                return bars
        except Exception:
            pass
        return cached[0] if cached is not None else []
```

If `run_ds_fetch` is not already a module-level name in `feeds/dip_scanner.py`, add near the top imports:

```python
from feeds.dexscreener_client import run_ds_fetch
from feeds.dexscreener_chart_format import parse_chart_bars
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rt_dip_bar_cache.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_rt_dip_bar_cache.py
git commit -m "feat(realtime-dip): per-token bar cache + off-loop io.dx 1m fetch"
```

---

### Task 5: Wire `RT_DIP_MODE` into the fast-watch reprice site

**Files:**
- Modify: `feeds/dip_scanner.py` (the reprice block at ~6524–6553, where `reprice_all` runs under `RT_TRIGGER_MODE`)
- Test: `tests/test_rt_dip_mode.py`

**Interfaces:**
- Consumes: `compute_rt_price_change`, `HORIZON_SECS` (Task 3), `RollingPriceWindow` (Task 1), `_get_rt_dip_bars` (Task 4), `core.fast_watch.rt_mode` (existing).
- Produces (behavior, on `DipScanner`):
  - `self._rt_dip_windows: dict[str, RollingPriceWindow]` — `addr -> window`, initialized in `__init__`.
  - At the reprice site, after the fresh price is known and BEFORE the existing `reprice_all` enforce-overwrite, branch on `rt_mode("RT_DIP_MODE", bot_cfg=None)`:
    - `"off"`: do nothing new (byte-identical — existing `RT_TRIGGER` path runs unchanged).
    - `"shadow"`: append fresh price to the window, fetch bars, compute rt pc; log `[rt-dip] addr cov=<coverage> rt_h1=<..> stale_h1=<snap> reprice_h1=<..>`; **do not** modify `_pair["priceChange"]`.
    - `"enforce"`: same compute; if `coverage != "NONE"`, overwrite the computed horizons in `_pair["priceChange"]`; if `coverage == "NONE"`, leave the existing `reprice_all` result in place (fall back — never fail-open).

**Note for implementer:** the existing block at ~6526 computes `_fresh_pc = reprice_all(_pch, _snap_price, _fresh_price)` and, when `_rt_trig == "enforce"`, does `_pch.update(_fresh_pc); _pair["priceChange"] = _pch`. The RT_DIP block goes immediately AFTER that, so RT_DIP's enforce overwrite (when usable) supersedes the stale-anchor reprice, and RT_DIP's NONE-fallback leaves the reprice result intact. `bot_cfg` is `None` at this site (universe-level reprice); per-bot resolution is a later enhancement and out of scope here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rt_dip_mode.py
import os
import pytest
from core.realtime_dip import RollingPriceWindow
from feeds.dip_scanner import DipScanner


def _scanner():
    s = DipScanner.__new__(DipScanner)
    s._rt_dip_bar_cache = {}
    s._rt_dip_windows = {}
    return s


def _apply(s, pair, snap_price, fresh_price, mode, bars=None, now=2_000_000_000.0):
    """Drive the extracted RT_DIP helper directly (pure dispatch)."""
    return s._apply_rt_dip(pair, snap_price, fresh_price, mode, bars=bars or [], now=now)


def test_off_is_byte_identical():
    s = _scanner()
    pair = {"priceChange": {"h1": -10.0, "m5": -2.0}}
    before = dict(pair["priceChange"])
    _apply(s, pair, snap_price=1.0, fresh_price=1.0, mode="off")
    assert pair["priceChange"] == before


def test_shadow_does_not_mutate_pricechange():
    s = _scanner()
    pair = {"priceChange": {"h1": -10.0, "m5": -2.0}}
    before = dict(pair["priceChange"])
    # seed a window high so a real-time pc exists
    w = RollingPriceWindow(); w.append(2_000_000_000.0 - 60, 2.0)
    s._rt_dip_windows["AAA"] = w
    _apply(s, {"address": "AAA", **pair}, snap_price=1.0, fresh_price=1.0, mode="shadow")
    assert pair["priceChange"] == before


def test_enforce_overwrites_when_usable():
    s = _scanner()
    pair = {"address": "AAA", "priceChange": {"h1": -10.0, "m5": -2.0}}
    bars = [{"ts_ms": (2_000_000_000.0 - 1800) * 1000.0, "high": 4.0, "low": 3.0}]
    s._rt_dip_windows["AAA"] = RollingPriceWindow()
    s._rt_dip_windows["AAA"].append(2_000_000_000.0 - 1, 1.0)
    _apply(s, pair, snap_price=1.0, fresh_price=1.0, mode="enforce", bars=bars)
    # h1 sees bar high 4.0 -> -75% (overwrote -10.0)
    assert pair["priceChange"]["h1"] == -75.0


def test_enforce_none_leaves_pricechange_untouched():
    s = _scanner()
    pair = {"address": "AAA", "priceChange": {"h1": -10.0}}
    s._rt_dip_windows["AAA"] = RollingPriceWindow()  # empty -> coverage NONE
    _apply(s, pair, snap_price=1.0, fresh_price=1.0, mode="enforce", bars=[])
    assert pair["priceChange"]["h1"] == -10.0  # fell back, not fabricated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rt_dip_mode.py -q`
Expected: FAIL with `AttributeError: ... has no attribute '_apply_rt_dip'`

- [ ] **Step 3: Write the minimal implementation**

Add to `DipScanner.__init__`:

```python
        self._rt_dip_windows = {}  # addr -> RollingPriceWindow; RT_DIP reference
```

Add the extracted, testable helper to `DipScanner` (keeps the wiring site thin and unit-testable):

```python
    def _apply_rt_dip(self, pair, snap_price, fresh_price, mode, *, bars, now):
        """Apply the RT_DIP reference to pair['priceChange'] per mode.

        off: no-op. shadow: log divergence, no mutation. enforce: overwrite the
        computed horizons when coverage != NONE, else leave priceChange as-is
        (fall back to the existing reprice — never fabricate a dip). Returns the
        (price_change_dict, coverage) it computed (for logging/tests). Never raises."""
        from core.realtime_dip import compute_rt_price_change
        if mode == "off":
            return {}, "off"
        addr = (pair.get("address") or "") if isinstance(pair, dict) else ""
        win = self._rt_dip_windows.get(addr)
        if win is not None:
            try:
                win.append(now, float(fresh_price))
            except (TypeError, ValueError):
                pass
        rt_pc, coverage = compute_rt_price_change(win, bars, fresh_price, now)
        if mode == "shadow":
            try:
                _pch = pair.get("priceChange") or {}
                logger.info("[rt-dip] %s cov=%s rt_h1=%s stale_h1=%s",
                            addr[:6], coverage, rt_pc.get("h1"), _pch.get("h1"))
            except Exception:
                pass
            return rt_pc, coverage
        if mode == "enforce" and coverage != "NONE" and rt_pc:
            _pch = dict(pair.get("priceChange") or {})
            _pch.update(rt_pc)
            pair["priceChange"] = _pch
        return rt_pc, coverage
```

Then at the reprice site (~6536, immediately after the existing `if _rt_trig == "enforce": _pch.update(_fresh_pc); _pair["priceChange"] = _pch` block), add the wiring that builds/uses the window and calls the helper:

```python
                # RT_DIP (2026-06-29): real-time dip reference off io.dexscreener
                # bars + the in-memory rolling buffer, superseding the stale-anchor
                # reprice_all above when usable. RT_DIP_MODE off=byte-identical.
                from core.fast_watch import rt_mode as _rt_mode
                _rt_dip = _rt_mode("RT_DIP_MODE")
                if _rt_dip != "off" and _snap_price and _fresh_price and _fresh_price > 0:
                    if addr not in self._rt_dip_windows:
                        from core.realtime_dip import RollingPriceWindow as _RPW
                        self._rt_dip_windows[addr] = _RPW()
                    _slug = locals().get("_1s_slug_primary") or ""
                    _rt_bars = []
                    try:
                        _rt_bars = await self._get_rt_dip_bars(
                            addr, _slug, _1s_pair, res="1m") if _slug else []
                    except Exception:
                        _rt_bars = []
                    self._apply_rt_dip(_pair, _snap_price, _fresh_price, _rt_dip,
                                       bars=_rt_bars, now=now)
```

**Implementer note:** confirm `addr`, `_1s_pair`, `now`, `_snap_price`, `_fresh_price`, `_pair` are all in scope at this site (they are in the surrounding fast-watch eval block per dip_scanner.py:6512–6559); if `_1s_slug_primary`/`_1s_pair` are computed later in the function, hoist the slug/pair resolution above this block or pass the already-known `pair` address fields. Keep the io.dx fetch inside the existing off-loop discipline.

- [ ] **Step 4: Run unit tests to verify they pass**

Run: `python -m pytest tests/test_rt_dip_mode.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full RT-dip suite + pre-live invariants**

Run: `python -m pytest tests/test_realtime_dip.py tests/test_rolling_high_from_bars.py tests/test_rt_dip_bar_cache.py tests/test_rt_dip_mode.py tests/test_pre_live_invariants.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add feeds/dip_scanner.py tests/test_rt_dip_mode.py
git commit -m "feat(realtime-dip): wire RT_DIP_MODE into fast-watch reprice site (shadow-first)"
```

---

## Verification (whole feature)

- [ ] `python -m pytest tests/test_realtime_dip.py tests/test_rolling_high_from_bars.py tests/test_rt_dip_bar_cache.py tests/test_rt_dip_mode.py -q` → all pass.
- [ ] `python -m pytest tests/test_pre_live_invariants.py tests/test_reprice_all.py tests/test_fast_watch.py -q` → all pass (no regression to the existing reprice/fast-watch paths).
- [ ] `RT_DIP_MODE` unset → grep confirms no behavior change (the `off` branch is a no-op; `_apply_rt_dip` returns early).
- [ ] Deploy plan (separate, gated on AxiS): ship with `RT_DIP_MODE=shadow`, accrue the `[rt-dip]` divergence log, compare rt-vs-stale catastrophic-miss-rate + coverage distribution, then promote to paper `enforce`. Live enable only via the go-live runbook.
