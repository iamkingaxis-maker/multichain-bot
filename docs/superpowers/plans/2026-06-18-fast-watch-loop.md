# Fast-Watch Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cheap background loop that re-checks the already-watched token cohort every ~3s and triggers the *existing* entry evaluation within seconds of a dip, instead of waiting up to ~160s for the compute-bound main sweep.

**Architecture:** A new `core/fast_watch.py` holds pure, unit-testable trigger/dedup logic. `core/bot_manager.py` gains an optional bot-allowlist on `evaluate_all`. `feeds/dip_scanner.py` threads a fast-path allowlist + shadow flag through `_evaluate_pair` (None ⇒ byte-identical today), and runs a new `_fast_watch_loop` coroutine that reads in-memory Axiom tick trends over the sticky cohort and escalates the 0–3 fresh-dip survivors into `_evaluate_pair`. All buy fires keep going through the existing process-lifetime `_buy_fire_lock` + the durable in-`_execute_bot_buy` guards, so no double-buy.

**Tech Stack:** Python 3, asyncio, pytest (+ pytest-asyncio, already used by `tests/test_parallel_scan_decision.py`). Spec: `docs/superpowers/specs/2026-06-18-fast-watch-loop-design.md`.

---

## File Structure

- **Create `core/fast_watch.py`** — pure logic with no scanner imports: `FastWatchConfig` (env parse), `dip_trigger()`, `FastWatchDedup`, `shortlist()`. One responsibility: decide *which* watched tokens are worth a full evaluation right now.
- **Modify `core/bot_manager.py`** — add `bot_allowlist` param to `evaluate_all` so a caller can restrict the fan-out to specific bots.
- **Modify `feeds/dip_scanner.py`** — (a) read `_fast_path_allowlist` / `_fast_path_shadow` out of `_eval_ctx` inside `_evaluate_pair` and honor them in the fan-out + legacy-fire blocks; (b) stash the per-cycle regime ctx on `self` for the fast loop; (c) add `_fast_held_or_blocked`, `_fast_watch_tick`, `_fast_watch_loop`; (d) spawn the loop in `run()`.
- **Create `tests/test_fast_watch.py`** — unit tests for the pure module + the allowlist/shadow threading + the double-fire guard.

Default allowlist (the live pool + dip-entry heavy hitters), used when `FAST_WATCH_BOT_ALLOWLIST` is unset:
`badday_flush, badday_flush_conviction, deepflush_timebox, timebox_probe_5mgreen` and their `_live` twins.

---

## Task 1: Pure fast-watch logic module

**Files:**
- Create: `core/fast_watch.py`
- Test: `tests/test_fast_watch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fast_watch.py
import os
import importlib
from core import fast_watch as fw


def test_dip_trigger_fires_at_or_below_threshold():
    assert fw.dip_trigger(-3.0, 3.0) is True      # exactly at threshold
    assert fw.dip_trigger(-5.5, 3.0) is True       # below
    assert fw.dip_trigger(-2.9, 3.0) is False      # not deep enough
    assert fw.dip_trigger(0.0, 3.0) is False
    assert fw.dip_trigger(4.0, 3.0) is False       # up move
    assert fw.dip_trigger(None, 3.0) is False      # no ticks -> never trigger
    assert fw.dip_trigger(-3.0, -3.0) is True      # threshold sign-insensitive


def test_dedup_suppresses_within_ttl_then_allows():
    d = fw.FastWatchDedup(ttl_secs=60)
    assert d.should_eval("A", now=1000.0) is True
    d.mark("A", now=1000.0)
    assert d.should_eval("A", now=1030.0) is False   # within TTL
    assert d.should_eval("A", now=1060.0) is True    # TTL elapsed (>=)
    assert d.should_eval("B", now=1030.0) is True     # different token


def test_shortlist_filters_held_blocked_and_recent():
    cfg = fw.FastWatchConfig(mode="shadow", interval_secs=3.0, trend_secs=90,
                             dip_pct=3.0, eval_cooldown_secs=60.0,
                             bot_allowlist=frozenset({"x"}))
    trends = {"DIP": -4.0, "FLAT": -0.5, "HELD": -9.0, "RECENT": -4.0}
    dedup = fw.FastWatchDedup(60)
    dedup.mark("RECENT", now=1000.0)
    snapshot = [("DIP", {"pair": {}}), ("FLAT", {"pair": {}}),
                ("HELD", {"pair": {}}), ("RECENT", {"pair": {}})]
    out = fw.shortlist(
        snapshot,
        get_trend=lambda addr, secs: trends.get(addr),
        dedup=dedup,
        is_held_or_blocked=lambda addr: addr == "HELD",
        cfg=cfg,
        now=1001.0,
    )
    assert [a for a, _e, _t in out] == ["DIP"]        # FLAT no-dip, HELD blocked, RECENT deduped


def test_config_from_env_defaults_and_overrides(monkeypatch):
    for k in ("FAST_WATCH_MODE", "FAST_WATCH_INTERVAL_SECS", "FAST_WATCH_TREND_SECS",
              "FAST_WATCH_DIP_PCT", "FAST_WATCH_EVAL_COOLDOWN_SECS", "FAST_WATCH_BOT_ALLOWLIST"):
        monkeypatch.delenv(k, raising=False)
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.mode == "off"                          # safe default
    assert cfg.interval_secs == 3.0
    assert cfg.trend_secs == 90
    assert cfg.dip_pct == 3.0
    assert cfg.eval_cooldown_secs == 60.0
    assert "badday_flush_conviction" in cfg.bot_allowlist
    assert "timebox_probe_5mgreen_live" in cfg.bot_allowlist

    monkeypatch.setenv("FAST_WATCH_MODE", "ShAdOw")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "5")
    monkeypatch.setenv("FAST_WATCH_BOT_ALLOWLIST", "a, b ,c")
    cfg2 = fw.FastWatchConfig.from_env()
    assert cfg2.mode == "shadow"                       # normalized lowercase
    assert cfg2.dip_pct == 5.0
    assert cfg2.bot_allowlist == frozenset({"a", "b", "c"})


def test_config_bad_numbers_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_INTERVAL_SECS", "not-a-number")
    monkeypatch.setenv("FAST_WATCH_TREND_SECS", "")
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.interval_secs == 3.0
    assert cfg.trend_secs == 90
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fast_watch.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.fast_watch'`.

- [ ] **Step 3: Write the module**

```python
# core/fast_watch.py
"""Pure logic for the fast-watch loop (no scanner/asyncio imports).

The fast-watch loop re-checks the already-watched token cohort every few
seconds and escalates fresh dips into the existing scanner evaluation, instead
of waiting for the compute-bound ~150-165s main sweep. This module holds only
the cheap, deterministic decision logic so it is trivially unit-testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

# Live pool + dip-entry heavy hitters; used when FAST_WATCH_BOT_ALLOWLIST unset.
_DEFAULT_ALLOWLIST = frozenset({
    "badday_flush", "badday_flush_conviction", "deepflush_timebox", "timebox_probe_5mgreen",
    "badday_flush_live", "badday_flush_conviction_live", "deepflush_timebox_live",
    "timebox_probe_5mgreen_live",
})


def _f(env_key: str, default: float) -> float:
    try:
        return float(os.environ.get(env_key, "").strip())
    except (TypeError, ValueError):
        return default


def _i(env_key: str, default: int) -> int:
    try:
        return int(os.environ.get(env_key, "").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class FastWatchConfig:
    mode: str                 # "off" | "shadow" | "enforce"
    interval_secs: float
    trend_secs: int
    dip_pct: float
    eval_cooldown_secs: float
    bot_allowlist: frozenset

    @classmethod
    def from_env(cls) -> "FastWatchConfig":
        mode = os.environ.get("FAST_WATCH_MODE", "off").strip().lower()
        if mode not in ("off", "shadow", "enforce"):
            mode = "off"
        raw = os.environ.get("FAST_WATCH_BOT_ALLOWLIST", "").strip()
        if raw:
            allow = frozenset(b.strip() for b in raw.split(",") if b.strip())
        else:
            allow = _DEFAULT_ALLOWLIST
        return cls(
            mode=mode,
            interval_secs=_f("FAST_WATCH_INTERVAL_SECS", 3.0),
            trend_secs=_i("FAST_WATCH_TREND_SECS", 90),
            dip_pct=_f("FAST_WATCH_DIP_PCT", 3.0),
            eval_cooldown_secs=_f("FAST_WATCH_EVAL_COOLDOWN_SECS", 60.0),
            bot_allowlist=allow,
        )


def dip_trigger(trend_pct: Optional[float], threshold_pct: float) -> bool:
    """True when the token dipped at least `threshold_pct` over the trend window.

    Deliberately a LOOSE superset signal — the real entry gates inside
    `_evaluate_pair` make the actual buy decision. None (no buffered ticks) never
    triggers, so the fast loop is best-effort and the main sweep stays the net.
    """
    if trend_pct is None:
        return False
    return trend_pct <= -abs(threshold_pct)


class FastWatchDedup:
    """Per-token TTL guard so the fast loop doesn't re-evaluate the same token
    every tick. `now` is injected (seconds) for testability."""

    def __init__(self, ttl_secs: float):
        self.ttl = ttl_secs
        self._last: dict[str, float] = {}

    def should_eval(self, addr: str, now: float) -> bool:
        t = self._last.get(addr)
        return t is None or (now - t) >= self.ttl

    def mark(self, addr: str, now: float) -> None:
        self._last[addr] = now


def shortlist(snapshot, get_trend: Callable, dedup: FastWatchDedup,
              is_held_or_blocked: Callable, cfg: FastWatchConfig, now: float):
    """Return [(addr, entry, trend)] for cohort tokens worth a full evaluation.

    `snapshot` is a list of (addr, entry) pairs (a copy of the sticky watchlist).
    `get_trend(addr, secs)` and `is_held_or_blocked(addr)` are injected so this
    stays pure and testable.
    """
    out = []
    for addr, entry in snapshot:
        trend = get_trend(addr, cfg.trend_secs)
        if not dip_trigger(trend, cfg.dip_pct):
            continue
        if not dedup.should_eval(addr, now):
            continue
        if is_held_or_blocked(addr):
            continue
        out.append((addr, entry, trend))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fast_watch.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add core/fast_watch.py tests/test_fast_watch.py
git commit -m "feat(fast-watch): pure trigger/dedup/config logic"
```

---

## Task 2: Optional bot-allowlist on `evaluate_all`

**Files:**
- Modify: `core/bot_manager.py:21-48`
- Test: `tests/test_fast_watch.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_fast_watch.py
class _FakeCfg:
    def __init__(self, bot_id, enabled=True):
        self.bot_id = bot_id
        self.enabled = enabled


class _FakeEvaluator:
    def __init__(self, bot_id, enabled=True):
        self.config = _FakeCfg(bot_id, enabled)
    def evaluate(self, bundle, realized_pnl_usd=0.0):
        return f"BUY:{self.config.bot_id}"


def test_evaluate_all_respects_bot_allowlist():
    from core.bot_manager import BotManager
    mgr = BotManager.__new__(BotManager)            # bypass real __init__
    mgr.evaluators = [_FakeEvaluator("a"), _FakeEvaluator("b"), _FakeEvaluator("c")]
    # No allowlist -> all enabled bots evaluated (unchanged behavior).
    assert set(mgr.evaluate_all(bundle=object())) == {"BUY:a", "BUY:b", "BUY:c"}
    # Allowlist -> only listed bots.
    assert set(mgr.evaluate_all(bundle=object(), bot_allowlist={"a", "c"})) == {"BUY:a", "BUY:c"}
    # Empty allowlist -> nothing.
    assert mgr.evaluate_all(bundle=object(), bot_allowlist=set()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fast_watch.py::test_evaluate_all_respects_bot_allowlist -v`
Expected: FAIL with `TypeError: evaluate_all() got an unexpected keyword argument 'bot_allowlist'`.

- [ ] **Step 3: Implement**

In `core/bot_manager.py`, change the signature and add the skip. Replace lines 21-22:

```python
    def evaluate_all(self, bundle: FeatureBundle,
                     realized_pnl_by_bot: dict[str, float] | None = None,
                     bot_allowlist: set[str] | frozenset[str] | None = None) -> list[BuyDecision]:
```

Inside the loop, immediately after the `if not ev.config.enabled: continue` block (after line 36), add:

```python
            if bot_allowlist is not None and ev.config.bot_id not in bot_allowlist:
                continue
```

Add one line to the docstring after the existing paragraph:

```python
        ``bot_allowlist`` (optional): when not None, only bots whose bot_id is in
        the set are evaluated — used by the fast-watch loop to scope its fires.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_fast_watch.py::test_evaluate_all_respects_bot_allowlist -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/bot_manager.py tests/test_fast_watch.py
git commit -m "feat(bot-manager): optional bot_allowlist on evaluate_all"
```

---

## Task 3: Thread fast-path allowlist + shadow flag through `_evaluate_pair`

This is the highest-risk task: it touches the buy-firing method. The invariant is **when `_eval_ctx` has no `_fast_path_allowlist` key, behavior is byte-identical to today.**

**Files:**
- Modify: `feeds/dip_scanner.py:16730-16767` (the fan-out fire + legacy single-bot fire blocks)
- Test: `tests/test_fast_watch.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_fast_watch.py
import asyncio
import types


def _make_scanner_with_fire_blocks():
    """Build a minimal object exposing the fan-out + legacy fire logic under test.
    We exercise the real decision-routing helper extracted in Step 3."""
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s.fired = []          # (path, bot_id)
    return s


def test_fanout_fires_all_when_no_allowlist():
    s = _make_scanner_with_fire_blocks()
    decisions = [types.SimpleNamespace(bot_id="a", token="T"),
                 types.SimpleNamespace(bot_id="b", token="T")]
    async def fake_exec(d, bundle): s.fired.append(("fire", d.bot_id))
    s._execute_bot_buy = fake_exec
    asyncio.run(s._fast_route_decisions(decisions, bundle=None, allowlist=None,
                                        shadow=False, token_symbol="T"))
    assert s.fired == [("fire", "a"), ("fire", "b")]


def test_fanout_shadow_logs_never_fires():
    s = _make_scanner_with_fire_blocks()
    decisions = [types.SimpleNamespace(bot_id="a", token="T")]
    async def fake_exec(d, bundle): s.fired.append(("fire", d.bot_id))
    s._execute_bot_buy = fake_exec
    asyncio.run(s._fast_route_decisions(decisions, bundle=None,
                                        allowlist={"a"}, shadow=True, token_symbol="T"))
    assert s.fired == []          # shadow: no fire at all


def test_fanout_enforce_fires_only_allowlisted():
    s = _make_scanner_with_fire_blocks()
    decisions = [types.SimpleNamespace(bot_id="a", token="T"),
                 types.SimpleNamespace(bot_id="z", token="T")]
    async def fake_exec(d, bundle): s.fired.append(("fire", d.bot_id))
    s._execute_bot_buy = fake_exec
    # evaluate_all already filtered; route just fires what it's given in enforce.
    asyncio.run(s._fast_route_decisions(decisions, bundle=None,
                                        allowlist={"a"}, shadow=False, token_symbol="T"))
    assert s.fired == [("fire", "a"), ("fire", "z")]   # routing fires given decisions under the lock
```

Note: `_fast_route_decisions` is a small helper we extract from the inline fan-out so the fire-routing is testable in isolation. The shadow/allowlist branch logic lives here; `_evaluate_pair` calls it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fast_watch.py -k fanout -v`
Expected: FAIL with `AttributeError: 'DipScanner' object has no attribute '_fast_route_decisions'`.

- [ ] **Step 3: Implement**

3a. Add the helper method to `DipScanner` (place it just above `_evaluate_pair`, before line 2717 in `feeds/dip_scanner.py`):

```python
    async def _fast_route_decisions(self, decisions, bundle, allowlist, shadow, token_symbol):
        """Fire (or shadow-log) the fan-out decisions under the buy-fire lock.

        Used by both the normal scan fan-out and the fast-watch path. `allowlist`
        is informational here (evaluate_all already filtered); `shadow=True`
        replaces the real fire with a log line and moves no money.
        """
        for d in decisions:
            async with self._buy_fire_lock:
                if shadow:
                    logger.info(
                        "[fast-watch] would-fire bot=%s token=%s (shadow)",
                        getattr(d, "bot_id", "?"), token_symbol,
                    )
                else:
                    await self._execute_bot_buy(d, bundle)
```

3b. In `_evaluate_pair`, read the two fast-path fields near where `_eval_ctx` is unpacked (the method already receives `_eval_ctx`). Add at the top of the per-pair body (just after the method reads `now_ms` from `_eval_ctx`):

```python
        _fp_allow = _eval_ctx.get("_fast_path_allowlist")   # set/frozenset or None
        _fp_shadow = bool(_eval_ctx.get("_fast_path_shadow"))
```

3c. Replace the existing fan-out fire block (lines 16730-16742) — the `decisions = self.bot_manager.evaluate_all(...)` call plus the `for d in decisions: async with self._buy_fire_lock: await self._execute_bot_buy(d, bundle)` loop — with:

```python
                    decisions = self.bot_manager.evaluate_all(
                        bundle, realized_pnl_by_bot=realized_by_bot,
                        bot_allowlist=_fp_allow,
                    )
                    await self._fast_route_decisions(
                        decisions, bundle, _fp_allow, _fp_shadow, token_symbol,
                    )
```

3d. Gate the legacy single-bot dip fire so the fast path doesn't also fire it. Immediately before the legacy fire block's `if _filters_block: continue` (line ~16746), add:

```python
            # Fast-watch path: only the scoped fan-out fires; skip the legacy
            # single-bot dip fire entirely (it is not in the bot allowlist model).
            if _fp_allow is not None:
                continue
```

- [ ] **Step 4: Run tests + byte-identical regression**

Run: `python -m pytest tests/test_fast_watch.py -k fanout -v`
Expected: PASS (3 tests).

Run the existing scan regression to confirm no main-path change:
`python -m pytest tests/test_parallel_scan.py tests/test_parallel_scan_decision.py -q`
Expected: PASS (no regressions — `_fast_path_*` keys are absent in the main loop's `_eval_ctx`, so `_fp_allow` is None and `_fp_shadow` is False ⇒ unchanged).

`python -c "import feeds.dip_scanner"` → no error.

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_fast_watch.py
git commit -m "feat(scan): fast-path allowlist+shadow routing in _evaluate_pair (None=identical)"
```

---

## Task 4: The fast-watch loop on `DipScanner`

**Files:**
- Modify: `feeds/dip_scanner.py` — add `self._fast_watch_regime` stash in `_scan_cycle`; add `_fast_held_or_blocked`, `_fast_watch_tick`, `_fast_watch_loop`.
- Test: `tests/test_fast_watch.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_fast_watch.py
def _scanner_for_tick(mode="shadow"):
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s._sticky_watchlist = {
        "DIPADDR": {"pair": {"pairAddress": "P", "priceUsd": "1"}},
        "FLATADDR": {"pair": {"pairAddress": "P2"}},
    }
    s._token_registry = None
    s._fast_watch_regime = {"_regime_n": 0, "_regime_dip_breadth_pct": None,
                            "_regime_h1_neg_pct": None}

    class _Feed:
        def __init__(self): self.subscribed = []
        def subscribe_token(self, a): self.subscribed.append(a)
        def get_tick_trend(self, a, secs): return -5.0 if a == "DIPADDR" else -0.1
    s.axiom_price_feed = _Feed()

    s.evaluated = []
    async def fake_eval(pair, ctx):
        s.evaluated.append((pair.get("pairAddress"), ctx.get("_fast_path_shadow"),
                            ctx.get("_fast_path_allowlist")))
        return (None, 0, False)
    s._evaluate_pair = fake_eval
    return s


def test_fast_watch_tick_escalates_only_the_dip(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_BOT_ALLOWLIST", "x,y")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick()
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    # Only the dipping token was evaluated; ctx carried shadow + allowlist.
    assert s.evaluated == [("P", True, frozenset({"x", "y"}))]
    # The whole cohort was subscribed (Tier 0).
    assert set(s.axiom_price_feed.subscribed) == {"DIPADDR", "FLATADDR"}


def test_fast_watch_tick_dedups_second_call(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick()
    dedup = FastWatchDedup(cfg.eval_cooldown_secs)
    asyncio.run(s._fast_watch_tick(cfg, dedup))
    asyncio.run(s._fast_watch_tick(cfg, dedup))      # immediate re-tick
    assert len(s.evaluated) == 1                     # deduped within TTL


def test_fast_watch_tick_survives_eval_exception(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick()
    async def boom(pair, ctx): raise RuntimeError("eval blew up")
    s._evaluate_pair = boom
    # Must not raise out of the tick.
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_fast_watch.py -k fast_watch_tick -v`
Expected: FAIL with `AttributeError: 'DipScanner' object has no attribute '_fast_watch_tick'`.

- [ ] **Step 3: Implement**

3a. Stash the regime ctx for the fast loop. In `_scan_cycle`, immediately after the `_eval_ctx = {...}` dict is built (right after line 16962), add:

```python
        # Snapshot the regime ctx so the fast-watch loop can reuse the latest
        # values between full cycles (it has no cycle of its own).
        self._fast_watch_regime = {
            "_regime_n": _regime_n,
            "_regime_dip_breadth_pct": _regime_dip_breadth_pct,
            "_regime_h1_neg_pct": _regime_h1_neg_pct,
        }
```

3b. Add the three methods (place them next to `_fast_route_decisions`, before `_evaluate_pair`). Ensure `import time` exists at the top of the file (it does; if not, add it):

```python
    def _fast_held_or_blocked(self, addr, allowlist):
        """Best-effort pre-check to skip tokens already held/blocked for the
        scoped bots. Authoritative dedup still happens inside _execute_bot_buy
        under the buy-fire lock (exclusion pool, capital, open positions)."""
        reg = getattr(self, "_token_registry", None)
        if reg is None:
            return False
        for bid in allowlist:
            try:
                if reg.is_blocked(bid, addr):
                    return True
            except Exception:
                pass
        return False

    async def _fast_watch_tick(self, cfg, dedup):
        from core.fast_watch import shortlist
        feed = getattr(self, "axiom_price_feed", None)
        if feed is None:
            return
        snapshot = list(self._sticky_watchlist.items())
        # Tier 0: subscribe the cohort (idempotent; safe before WS connects).
        for addr, _entry in snapshot:
            try:
                feed.subscribe_token(addr)
            except Exception:
                pass
        now = time.time()
        now_ms = int(now * 1000)
        # Tier 1: cheap in-memory shortlist.
        survivors = shortlist(
            snapshot,
            get_trend=lambda a, secs: self._fast_trend(feed, a, secs),
            dedup=dedup,
            is_held_or_blocked=lambda a: self._fast_held_or_blocked(a, cfg.bot_allowlist),
            cfg=cfg,
            now=now,
        )
        # Coverage health (how much of the cohort actually has live ticks).
        have = sum(1 for a, _e in snapshot
                   if self._fast_trend(feed, a, cfg.trend_secs) is not None)
        logger.info("[fast-watch] tick cohort=%d live_ticks=%d shortlisted=%d mode=%s",
                    len(snapshot), have, len(survivors), cfg.mode)
        regime = getattr(self, "_fast_watch_regime", {}) or {}
        # Tier 2: escalate survivors into the existing evaluation.
        for addr, entry, _trend in survivors:
            dedup.mark(addr, now)
            pair = (entry or {}).get("pair")
            if not pair:
                continue
            ctx = {
                "now_ms": now_ms,
                "_regime_n": regime.get("_regime_n", 0),
                "_regime_dip_breadth_pct": regime.get("_regime_dip_breadth_pct"),
                "_regime_h1_neg_pct": regime.get("_regime_h1_neg_pct"),
                "_fast_path_allowlist": cfg.bot_allowlist,
                "_fast_path_shadow": (cfg.mode == "shadow"),
            }
            try:
                await self._evaluate_pair(pair, ctx)
            except Exception as e:
                logger.error("[fast-watch] eval failed token=%s: %s", addr, e, exc_info=True)

    @staticmethod
    def _fast_trend(feed, addr, secs):
        try:
            return feed.get_tick_trend(addr, secs)
        except Exception:
            return None

    async def _fast_watch_loop(self):
        from core.fast_watch import FastWatchConfig, FastWatchDedup
        cfg = FastWatchConfig.from_env()
        if cfg.mode == "off":
            logger.info("[fast-watch] disabled (FAST_WATCH_MODE=off)")
            return
        logger.info("[fast-watch] starting mode=%s interval=%.1fs dip<=-%.1f%% "
                    "trend=%ds allowlist=%d bots",
                    cfg.mode, cfg.interval_secs, cfg.dip_pct, cfg.trend_secs,
                    len(cfg.bot_allowlist))
        if getattr(self, "_buy_fire_lock", None) is None:
            self._buy_fire_lock = asyncio.Lock()
        dedup = FastWatchDedup(cfg.eval_cooldown_secs)
        while True:
            try:
                await self._fast_watch_tick(cfg, dedup)
            except Exception as e:
                logger.error("[fast-watch] tick error: %s", e, exc_info=True)
            await asyncio.sleep(cfg.interval_secs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fast_watch.py -k fast_watch_tick -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_fast_watch.py
git commit -m "feat(fast-watch): cohort subscribe + cheap shortlist + escalation tick"
```

---

## Task 5: Spawn the loop + full integration verification

**Files:**
- Modify: `feeds/dip_scanner.py:611-625` (`run()`)
- Test: `tests/test_fast_watch.py`, `tests/test_pre_live_invariants.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_fast_watch.py
def test_run_spawns_fast_watch_task(monkeypatch):
    """run() must create the fast-watch task exactly once before the sweep loop."""
    import feeds.dip_scanner as ds
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    created = []
    monkeypatch.setattr(ds.asyncio, "create_task",
                        lambda coro, *a, **k: created.append(coro) or coro.close())

    # Stop run() after one iteration by raising out of _scan_cycle.
    async def stop_cycle():
        raise KeyboardInterrupt
    s._scan_cycle = stop_cycle
    s.bot_manager = None
    try:
        asyncio.run(s.run())
    except KeyboardInterrupt:
        pass
    assert len(created) == 1            # the fast-watch loop was scheduled once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_fast_watch.py::test_run_spawns_fast_watch_task -v`
Expected: FAIL (0 tasks created — `run()` doesn't spawn the loop yet).

- [ ] **Step 3: Implement**

In `feeds/dip_scanner.py`, `run()` (line 611). Insert the spawn right after the opening log line and before `while True:`:

```python
    async def run(self):
        logger.info("[DipScanner] Starting — targeting $1M+ mcap dip entries")
        # Fast-watch loop: re-checks the watched cohort every few seconds and
        # escalates fresh dips into _evaluate_pair sooner than the slow sweep.
        # No-op when FAST_WATCH_MODE=off (the loop returns immediately).
        try:
            asyncio.create_task(self._fast_watch_loop())
        except Exception as e:
            logger.error("[fast-watch] failed to spawn: %s", e)
        while True:
            try:
                await self._scan_cycle()
```

(Leave the rest of the `while True` body unchanged.)

- [ ] **Step 4: Run the full verification suite**

```bash
python -c "import feeds.dip_scanner" && echo IMPORT_OK
python -m pytest tests/test_fast_watch.py -q
python -m pytest tests/test_parallel_scan.py tests/test_parallel_scan_decision.py tests/test_parallel_tick.py -q
python tests/test_pre_live_invariants.py
```

Expected: import OK; `test_fast_watch.py` all PASS; the parallel-scan regression suites PASS (no main-path change); pre-live invariants print `Pre-live invariants OK` and exit 0.

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_fast_watch.py
git commit -m "feat(fast-watch): spawn loop in run() (no-op when mode=off)"
```

---

## Task 6: Deploy in shadow + validate (no code; runtime gate)

**Files:** none (env + observation only). This task is the spec's Phase-1 gate; do NOT skip to enforce.

- [ ] **Step 1: Deploy with the loop present but inert, then enable shadow**

```bash
git push
railway up --detach
# After the deploy is WARM (~25min, not cold — cold-cache lesson from the latency mission):
railway variables --set "FAST_WATCH_MODE=shadow"
railway up --detach
```

Confirm guardrails unchanged: `railway variables` shows `PAPER_MODE=true`, `PROFIT_SWEEP_DRY_RUN=1`.

- [ ] **Step 2: Validate over real cycles (warm)**

Capture ~5 min of logs and check:
- `[fast-watch] tick cohort=N live_ticks=M shortlisted=K mode=shadow` lines appear every ~3s, with `live_ticks` a healthy fraction of `cohort` (this is the Axiom-coverage health metric).
- `[fast-watch] would-fire bot=X token=Y (shadow)` lines appear on real dips.
- Cross-check: a `would-fire` token should later show a normal main-loop buy for the same bot/token — and the fast-watch log timestamp should PRECEDE it (the timing advantage). Record the median lead time.
- **Zero double-decisions:** no token shows two real buys for the same bot (shadow fires nothing, so this validates the routing; the durable lock guards protect enforce).

- [ ] **Step 3: Decision gate (AxiS)**

Only after shadow shows (a) a real timing advantage and (b) zero anomalies: flip to enforce **in paper** (`FAST_WATCH_MODE=enforce`, `PAPER_MODE` stays `true`). Live is a separate explicit AxiS decision. Document the shadow findings before flipping.

---

## Self-Review (completed by plan author)

- **Spec coverage:** Tier 0 subscribe → Task 4 `_fast_watch_tick`. Tier 1 cheap shortlist → Task 1 `shortlist` + Task 4. Tier 2 scoped eval → Tasks 2+3+4. Reuse-existing-gates → Task 3 routes through `_evaluate_pair`. Scope to allowlist → Tasks 1 (default) + 2 + 3. Shadow-first → Task 3 (`_fp_shadow`) + Task 6. Buy-safety (`_buy_fire_lock` + durable guards) → Task 3 helper + `_execute_bot_buy` (unchanged). Best-effort/degradation → `dip_trigger(None)=False`, `_fast_trend` try/except, coverage log, tick try/except (Tasks 1, 4). Byte-identical-when-off → Tasks 3 (`_fp_allow None`) + 5 (mode=off returns). Env flags → Task 1 `from_env`. Tests/acceptance → every task + Task 6.
- **Placeholder scan:** none — every code step is complete and runnable.
- **Type consistency:** `FastWatchConfig` fields, `dip_trigger(trend, threshold)`, `FastWatchDedup(ttl).should_eval/mark(addr, now)`, `shortlist(snapshot, get_trend, dedup, is_held_or_blocked, cfg, now)`, `evaluate_all(..., bot_allowlist=...)`, and the `_eval_ctx` keys `_fast_path_allowlist`/`_fast_path_shadow` are used identically across Tasks 1–5.
