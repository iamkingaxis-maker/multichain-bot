# Real-Time Dip Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dip-buy fleet detect flushes in real-time (seconds, not ~5 min) by triggering on a fresh-derived dip + fresh demand-turn instead of the stale DexScreener snapshot, so it enters on the demand-turn (~$0.16) not the late recovery (~$0.20).

**Architecture:** Approach 1 — rewire in place. The existing fast-watch loop (`_fast_watch_tick` → `_eval_one_survivor` → `_evaluate_pair`) already holds a fresh per-survivor price; we (A) recompute the dip metrics against the slow high-reference using that fresh price and inject them into the bundle, (B) refresh arming each fast tick, (C) confirm the demand-turn with a fresh trade poll on the armed token, (D) keep the event loop from freezing so ticks happen, and (E) tag + A/B the two triggers on real fills.

**Tech Stack:** Python 3, asyncio, pytest. No new dependencies. Free data only (Jupiter lite-api, DexScreener internal). Runtime on Railway.

## Global Constraints

- **Free tools only** — no paid RPC / Jupiter key. (verbatim from spec)
- **Every new behavior behind an env flag** with `off`/`shadow`/`enforce` semantics, **per-bot resolvable** (bot-config override falls back to env default).
- **`BUY_REPRICE_MODE=enforce` stays on** as the live backstop.
- **Never flip `PAPER_MODE` without explicit AxiS approval.** The live A/B (Task 8) is the LAST step, gated on explicit go + the go-live runbook. Currently `PAPER_MODE=true`.
- **Fast fill = fidelity, not a P&L lever** — never slow a fill.
- **`pnl_pct` not feed `$`; state keyed by ADDRESS, never symbol.**
- Run `pytest tests/test_pre_live_invariants.py` green before any `PAPER_MODE=false`.
- Pure logic goes in `core/fast_watch.py` (already the home of the pure fast-watch helpers) so it is unit-testable without the scanner.

---

## File Structure

- `core/fast_watch.py` — **Modify.** Home of pure, testable helpers. Add: `reprice_change_pct()` (Task 2), `rt_mode()` per-bot/env flag resolver (Task 3), `demand_turn_ok()` (Task 5).
- `feeds/dip_scanner.py` — **Modify.** Wire the helpers into `_eval_one_survivor` (~`:4003`, inject `:4072-4076`, triggers read `:4440-4443`), `_fast_arm_subset` (`:3673`), the live-swap emit (`:2176`), and the sync-sweep yield (`:19095`).
- `core/live_swap_log.py` — **Modify.** Add `trigger_source` to `REQUIRED_FIELDS` (`:39-61`).
- `config/bots/badday_flush_nf15_live.json` — **Modify** → control bot (A, legacy trigger).
- `config/bots/badday_flush_nf15_rt_live.json` — **Create** → treatment bot (B, real-time trigger).
- `tests/test_realtime_dip_detection.py` — **Create.** All unit tests for the pure helpers.

---

### Task 1: Component D — Loop unblock verification + tightening

**Goal:** Guarantee the event loop never freezes long enough to starve the ~3s fast tick. Builds on the already-shipped `SCAN_YIELD_EVERY` (e17025b) and `to_thread`/`LEDGER_WRITE_OFFLOAD` (1d37398). This task makes the loop-block budget explicit and tightens it.

**Files:**
- Modify: `feeds/dip_scanner.py:19095` (the `_yield_every = int(os.environ.get("SCAN_YIELD_EVERY", "8"))` sweep yield)
- Test: `tests/test_realtime_dip_detection.py`

**Interfaces:**
- Produces: confidence (a measured loop-block ceiling) that Tasks 3–5's real-time ticks actually run. No code symbol consumed by later tasks.

- [ ] **Step 1: Capture the current loop-block baseline**

Run against the live Railway logs (read-only):
```bash
railway logs 2>/dev/null | grep -iE "loop-lag" | tail -20
```
Expected: lines like `[loop-lag] event loop blocked ~34.2s`. Record the max observed value as the baseline.

- [ ] **Step 2: Write a failing test for the yield cadence env default**

In `tests/test_realtime_dip_detection.py`:
```python
import os

def test_scan_yield_every_default_is_tight(monkeypatch):
    # The redesign tightens the cooperative-yield default from 8 to 4 so the
    # sync sweep cannot block the loop long enough to starve a ~3s fast tick.
    monkeypatch.delenv("SCAN_YIELD_EVERY", raising=False)
    from importlib import reload
    import feeds.dip_scanner as ds
    # The default is read inline; assert the literal default in source is 4.
    import inspect, re
    src = inspect.getsource(ds.DipScanner.run) if hasattr(ds.DipScanner, "run") else ""
    # Fallback: scan the module source for the default.
    msrc = inspect.getsource(ds)
    assert 'os.environ.get("SCAN_YIELD_EVERY", "4")' in msrc
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_realtime_dip_detection.py::test_scan_yield_every_default_is_tight -v`
Expected: FAIL (current default is `"8"`).

- [ ] **Step 4: Tighten the default**

In `feeds/dip_scanner.py:19095`, change:
```python
                _yield_every = int(os.environ.get("SCAN_YIELD_EVERY", "8"))
```
to:
```python
                _yield_every = int(os.environ.get("SCAN_YIELD_EVERY", "4"))
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_realtime_dip_detection.py::test_scan_yield_every_default_is_tight -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_realtime_dip_detection.py feeds/dip_scanner.py
git commit -m "perf(loop): tighten SCAN_YIELD_EVERY default 8->4 (un-starve fast tick)"
```

- [ ] **Step 7: Runtime verification note**

After deploy (paper), confirm via `railway logs | grep loop-lag` that the max loop-block dropped materially vs the Step 1 baseline (target < ~2s sustained). If it has not, this task is NOT done — escalate: the residual block is elsewhere (offload the per-pair sweep via `to_thread`, per the spec's Component D) before proceeding to live.

---

### Task 2: Component A core — `reprice_change_pct()` pure helper

**Goal:** A pure function that recomputes a price-change % (e.g. `pc_h1`) using a fresh price against the slow high-reference encoded in the snapshot %. This is the mathematical heart of the fix.

**Math:** The snapshot gives `pc = (P_snap/ref − 1)`. So `ref = P_snap / (1 + pc)`. With a fresh price `P_fresh`: `fresh_pc = (P_fresh/ref − 1) = (P_fresh/P_snap)·(1 + pc) − 1`. When `P_fresh == P_snap`, `fresh_pc == pc` (identity — the inversion fallback).

**Files:**
- Modify: `core/fast_watch.py` (add near `rolling_dip_pct`, ~`:365`)
- Test: `tests/test_realtime_dip_detection.py`

**Interfaces:**
- Produces: `reprice_change_pct(snapshot_pct: float, snapshot_price: float, fresh_price: float) -> float | None` — pct in **percent units** (e.g. -20.0 means -20%). Returns `None` if inputs are unusable (non-positive prices). Consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

In `tests/test_realtime_dip_detection.py`:
```python
import math
from core.fast_watch import reprice_change_pct

def test_reprice_identity_when_price_unchanged():
    # P_fresh == P_snap -> fresh_pc == snapshot_pc (inversion fallback property)
    assert reprice_change_pct(-20.0, 0.1521, 0.1521) == -20.0

def test_reprice_recovers_toward_high():
    # Snapshot: price 0.1521 is -20% off the 1h high => ref = 0.1521/0.8 = 0.190125
    # Fresh price 0.1998 => fresh_pc = (0.1998/0.190125 - 1)*100 = +5.09%
    out = reprice_change_pct(-20.0, 0.1521, 0.1998)
    assert math.isclose(out, 5.0855, abs_tol=0.01)

def test_reprice_deeper_dip_when_price_falls_further():
    # Fresh price BELOW snapshot => deeper negative pc
    out = reprice_change_pct(-20.0, 0.1521, 0.1300)
    assert out < -20.0

def test_reprice_none_on_bad_prices():
    assert reprice_change_pct(-20.0, 0.0, 0.1998) is None
    assert reprice_change_pct(-20.0, 0.1521, 0.0) is None
    assert reprice_change_pct(-20.0, 0.1521, -1.0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_realtime_dip_detection.py -k reprice -v`
Expected: FAIL with `ImportError: cannot import name 'reprice_change_pct'`.

- [ ] **Step 3: Implement the helper**

In `core/fast_watch.py`, after `rolling_dip_pct` (~`:374`):
```python
def reprice_change_pct(snapshot_pct, snapshot_price, fresh_price):
    """Recompute a price-change % (e.g. pc_h1) using a FRESH price against the
    slow high-reference encoded in the snapshot %.

    snapshot_pct: the DexScreener priceChange % at snapshot time (percent units,
        e.g. -20.0). snapshot_price: the priceUsd at snapshot time. fresh_price:
        the live price now. Returns the repriced % (percent units), or None if
        prices are unusable. When fresh_price == snapshot_price, returns
        snapshot_pct exactly (identity / inversion fallback). Pure; never raises.
    """
    try:
        sp = float(snapshot_price)
        fp = float(fresh_price)
        pc = float(snapshot_pct)
    except (TypeError, ValueError):
        return None
    if sp <= 0 or fp <= 0:
        return None
    fresh = ((fp / sp) * (1.0 + pc / 100.0) - 1.0) * 100.0
    return round(fresh, 6)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_realtime_dip_detection.py -k reprice -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add core/fast_watch.py tests/test_realtime_dip_detection.py
git commit -m "feat(rt-detect): reprice_change_pct pure helper (fresh price vs slow high-ref)"
```

---

### Task 3: Component A wiring + `rt_mode()` per-bot flag resolver

**Goal:** Define the per-bot/env flag resolver, then wire `reprice_change_pct` into `_eval_one_survivor` so the dip trigger gates on the fresh-derived `pc_h1`/`pc_m5` (and the decision price is fresh), under `RT_TRIGGER_MODE`.

**Files:**
- Modify: `core/fast_watch.py` (add `rt_mode`)
- Modify: `feeds/dip_scanner.py` `_eval_one_survivor` (the `_pair["priceUsd"]` injection block, `:4072-4076`)
- Test: `tests/test_realtime_dip_detection.py`

**Interfaces:**
- Consumes: `reprice_change_pct` (Task 2).
- Produces: `rt_mode(flag: str, bot_cfg=None, default: str = "off") -> str` returning one of `"off"|"shadow"|"enforce"`. `bot_cfg` is a bot config object/dict that may carry a per-bot override under the lowercased flag name. Consumed by Tasks 4, 5, 6.

- [ ] **Step 1: Write the failing tests for `rt_mode`**

In `tests/test_realtime_dip_detection.py`:
```python
from core.fast_watch import rt_mode

def test_rt_mode_env_default(monkeypatch):
    monkeypatch.delenv("RT_TRIGGER_MODE", raising=False)
    assert rt_mode("RT_TRIGGER_MODE") == "off"
    monkeypatch.setenv("RT_TRIGGER_MODE", "shadow")
    assert rt_mode("RT_TRIGGER_MODE") == "shadow"

def test_rt_mode_per_bot_override_wins(monkeypatch):
    monkeypatch.setenv("RT_TRIGGER_MODE", "off")
    # bot config override (dict form) beats the env default
    assert rt_mode("RT_TRIGGER_MODE", {"rt_trigger_mode": "enforce"}) == "enforce"

def test_rt_mode_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RT_TRIGGER_MODE", "garbage")
    assert rt_mode("RT_TRIGGER_MODE", default="off") == "off"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_realtime_dip_detection.py -k rt_mode -v`
Expected: FAIL with `ImportError: cannot import name 'rt_mode'`.

- [ ] **Step 3: Implement `rt_mode`**

In `core/fast_watch.py`:
```python
import os as _os

_RT_VALID = ("off", "shadow", "enforce")

def rt_mode(flag, bot_cfg=None, default="off"):
    """Resolve an off/shadow/enforce mode flag, per-bot override winning over env.

    flag: env var name (e.g. 'RT_TRIGGER_MODE'). bot_cfg: optional bot config —
    a dict or object that may carry the lowercased flag name as a per-bot
    override. default: returned when neither source has a valid value. Always
    returns one of off/shadow/enforce. Pure-ish (reads env); never raises.
    """
    key = flag.lower()
    val = None
    if bot_cfg is not None:
        if isinstance(bot_cfg, dict):
            val = bot_cfg.get(key)
        else:
            val = getattr(bot_cfg, key, None)
    if val is None:
        val = _os.environ.get(flag)
    val = (str(val).strip().lower() if val is not None else default)
    return val if val in _RT_VALID else default
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_realtime_dip_detection.py -k rt_mode -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Wire the fresh trigger into `_eval_one_survivor`**

In `feeds/dip_scanner.py`, inside `_eval_one_survivor`, replace the price-injection block at `:4072-4076`:
```python
                _pair = dict(_pair)
                if pinned is not None and pinned > 0:
                    _pair["priceUsd"] = str(pinned)
                else:
                    _pair["priceUsd"] = str(fresh)
```
with:
```python
                _pair = dict(_pair)
                _snap_price = None
                try:
                    _snap_price = float(pair.get("priceUsd") or 0) or None
                except (TypeError, ValueError):
                    _snap_price = None
                _fresh_price = pinned if (pinned is not None and pinned > 0) else fresh
                _pair["priceUsd"] = str(_fresh_price)
                # COMPONENT A: recompute the short-horizon dip metrics off the
                # FRESH price vs the slow high-reference encoded in the snapshot %,
                # so the dip trigger in _evaluate_pair gates on the LIVE move
                # instead of stale pair["priceChange"]. Per-bot/env RT_TRIGGER_MODE.
                from core.fast_watch import reprice_change_pct, rt_mode
                _rt_trig = rt_mode("RT_TRIGGER_MODE")
                if _rt_trig != "off" and _snap_price and _fresh_price and _fresh_price > 0:
                    _pch = dict(_pair.get("priceChange") or {})
                    _fresh_pc = {}
                    for _k in ("h1", "m5"):
                        _snap_pc = _pch.get(_k)
                        if _snap_pc is None:
                            continue
                        _rp = reprice_change_pct(_snap_pc, _snap_price, _fresh_price)
                        if _rp is not None:
                            _fresh_pc[_k] = _rp
                    if _fresh_pc:
                        if _rt_trig == "enforce":
                            _pch.update(_fresh_pc)
                            _pair["priceChange"] = _pch
                        logger.info(
                            "[rt-trigger] %s mode=%s snap_pc_h1=%s fresh_pc_h1=%s "
                            "snap_px=%.8f fresh_px=%.8f",
                            addr[:6], _rt_trig, _pch.get("h1"),
                            _fresh_pc.get("h1"), _snap_price, _fresh_price)
```

(The downstream trigger reads `pair.get("priceChange").get("h1")` at `:4440-4443`; in `enforce` it now sees the fresh value. `decision_mid_price`/`entry_price` already become fresh because `entry_price=b.price_usd` derives from the now-fresh `_pair["priceUsd"]`.)

- [ ] **Step 6: Run the full helper suite to confirm no regressions**

Run: `pytest tests/test_realtime_dip_detection.py -v`
Expected: PASS (all reprice + rt_mode tests).

- [ ] **Step 7: Smoke-check the scanner imports**

Run: `python -c "import feeds.dip_scanner"`
Expected: no exception.

- [ ] **Step 8: Commit**

```bash
git add core/fast_watch.py feeds/dip_scanner.py tests/test_realtime_dip_detection.py
git commit -m "feat(rt-detect): RT_TRIGGER_MODE — fast trigger gates on fresh-repriced pc_h1/m5"
```

---

### Task 4: Component B — Real-time arming refresh

**Goal:** Refresh the armed set every fast tick from the latest evaluated universe (not only once per ~30s main cycle), under `RT_ARM_MODE`, so a token entering the evaluated set is watched within ~3s.

**Background:** `_fast_arm_subset` (`:3673`) already arms the whole `_sticky_watchlist ∪ _cycle_pair_by_addr` set (`in_band = bool(pair)`), but it is invoked once per main cycle. The fast tick should re-arm from the freshest `_cycle_pair_by_addr` each tick when enabled.

**Files:**
- Modify: `feeds/dip_scanner.py` `_fast_watch_tick` (call `_fast_arm_subset` at tick start when `RT_ARM_MODE != off`)
- Test: `tests/test_realtime_dip_detection.py` (predicate test)

**Interfaces:**
- Consumes: `rt_mode` (Task 3).
- Produces: no new public symbol; behavior gated by `RT_ARM_MODE`.

- [ ] **Step 1: Write a failing test for the re-arm predicate**

Add a tiny pure predicate to `core/fast_watch.py` so the per-tick re-arm decision is testable in isolation. Test first:
```python
from core.fast_watch import should_rearm_this_tick

def test_should_rearm_off():
    assert should_rearm_this_tick("off") is False

def test_should_rearm_shadow_and_enforce():
    assert should_rearm_this_tick("shadow") is True
    assert should_rearm_this_tick("enforce") is True
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_realtime_dip_detection.py -k rearm -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the predicate**

In `core/fast_watch.py`:
```python
def should_rearm_this_tick(rt_arm_mode):
    """True when the fast tick should rebuild the armed set from the freshest
    evaluated universe (RT_ARM_MODE shadow or enforce). Pure."""
    return str(rt_arm_mode).strip().lower() in ("shadow", "enforce")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_realtime_dip_detection.py -k rearm -v`
Expected: PASS.

- [ ] **Step 5: Wire the per-tick re-arm**

In `feeds/dip_scanner.py` `_fast_watch_tick` (`:3894`), at the very start of the tick body (before building `survivors`), add:
```python
        # COMPONENT B: refresh the armed set from the freshest evaluated universe
        # each tick (not just once per ~30s main cycle), so a token entering the
        # evaluated set is watched within one fast tick. Gated by RT_ARM_MODE.
        from core.fast_watch import rt_mode as _rt_mode, should_rearm_this_tick
        if should_rearm_this_tick(_rt_mode("RT_ARM_MODE")):
            try:
                self._fast_arm_subset(cfg, now_ms)
            except Exception as _arm_e:
                logger.debug("[rt-arm] re-arm failed: %s", _arm_e)
```
(Use the existing `now_ms` computed in the tick; if it is computed later in the function, move this block just after `now_ms` is set. `_fast_arm_subset` is idempotent — it rebuilds `self._fast_armed` from current state.)

- [ ] **Step 6: Smoke-check + full suite**

Run: `python -c "import feeds.dip_scanner"` then `pytest tests/test_realtime_dip_detection.py -v`
Expected: import OK; all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add core/fast_watch.py feeds/dip_scanner.py tests/test_realtime_dip_detection.py
git commit -m "feat(rt-detect): RT_ARM_MODE — per-tick re-arm from freshest evaluated universe"
```

---

### Task 5: Component C — Fresh demand-turn confirm

**Goal:** When a candidate is hot, confirm the demand-turn with a FRESH `fetch_recent_trades` for that token (recompute `net_flow_15s`), requiring `>= 0`. On fetch failure, fall back to the cached value — **never fail-open to a buy**. Under `RT_DEMAND_TURN_MODE`.

**Files:**
- Modify: `core/fast_watch.py` (add `demand_turn_ok` pure helper)
- Modify: `feeds/dip_scanner.py` `_eval_one_survivor` (after the Task 3 block; fire the fresh trade fetch for the armed token and stash the fresh `net_flow_15s` into `_pair` so `_evaluate_pair`'s gate at `:1967`/`:5741-5757` reads fresh)
- Test: `tests/test_realtime_dip_detection.py`

**Interfaces:**
- Consumes: `rt_mode` (Task 3); `dexs_client.fetch_recent_trades` (existing, `:5724`).
- Produces: `demand_turn_ok(fresh_net_flow, cached_net_flow, fetch_ok) -> bool` — the gate decision. Consumed by the wiring step.

- [ ] **Step 1: Write the failing tests**

```python
from core.fast_watch import demand_turn_ok

def test_demand_turn_fresh_positive_passes():
    assert demand_turn_ok(fresh_net_flow=12.0, cached_net_flow=-5.0, fetch_ok=True) is True

def test_demand_turn_fresh_negative_blocks():
    assert demand_turn_ok(fresh_net_flow=-3.0, cached_net_flow=10.0, fetch_ok=True) is False

def test_demand_turn_fetch_fail_uses_cached_not_fail_open():
    # fetch failed -> use cached; cached negative -> BLOCK (never fail-open to buy)
    assert demand_turn_ok(fresh_net_flow=None, cached_net_flow=-1.0, fetch_ok=False) is False
    # fetch failed, cached positive -> allow (matches today's behavior)
    assert demand_turn_ok(fresh_net_flow=None, cached_net_flow=4.0, fetch_ok=False) is True

def test_demand_turn_fetch_fail_cached_none_blocks():
    # no fresh and no cached -> block (conservative, never fail-open)
    assert demand_turn_ok(fresh_net_flow=None, cached_net_flow=None, fetch_ok=False) is False
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_realtime_dip_detection.py -k demand_turn -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `demand_turn_ok`**

In `core/fast_watch.py`:
```python
def demand_turn_ok(fresh_net_flow, cached_net_flow, fetch_ok):
    """Demand-turn gate: require net_flow_15s >= 0. Prefer the FRESH value; on
    fetch failure fall back to the cached value (fail-toward-current-behavior).
    NEVER fail-open: if neither value is available, BLOCK. Pure; never raises."""
    val = fresh_net_flow if (fetch_ok and fresh_net_flow is not None) else cached_net_flow
    if val is None:
        return False
    try:
        return float(val) >= 0.0
    except (TypeError, ValueError):
        return False
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_realtime_dip_detection.py -k demand_turn -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire the fresh fetch into `_eval_one_survivor`**

In `feeds/dip_scanner.py` `_eval_one_survivor`, AFTER the Task 3 trigger block and BEFORE building `ctx`, add:
```python
                # COMPONENT C: fresh demand-turn confirm. For the armed token,
                # fetch recent trades NOW (bypassing the 60s cache), recompute
                # net_flow_15s, and stash it onto _pair so _evaluate_pair's gate
                # reads the fresh value. Bounded: only fires for survivors (small
                # set). Fail toward cached, never fail-open. Gated RT_DEMAND_TURN_MODE.
                from core.fast_watch import demand_turn_ok as _dturn_ok
                _rt_dt = rt_mode("RT_DEMAND_TURN_MODE")
                if _rt_dt != "off":
                    _fresh_nf = None
                    _fetch_ok = False
                    try:
                        _pair_addr_dt = _pair.get("pairAddress")
                        if _pair_addr_dt:
                            _rt_trades = await self.dexs_client.fetch_recent_trades(
                                _pair_addr_dt, limit=30)
                            if _rt_trades is not None:
                                _fetch_ok = True
                                _fresh_nf = _net_flow_15s_usd_from_trades(_rt_trades)
                    except Exception as _dt_e:
                        logger.debug("[rt-demand] fresh trade fetch failed %s: %s",
                                     addr[:6], _dt_e)
                    if _rt_dt == "enforce" and _fresh_nf is not None:
                        _pair["_rt_net_flow_15s_usd"] = _fresh_nf
                    logger.info("[rt-demand] %s mode=%s fresh_nf15=%s fetch_ok=%s",
                                addr[:6], _rt_dt, _fresh_nf, _fetch_ok)
```

- [ ] **Step 6: Extract the net-flow computation into a reusable function**

The `net_flow_15s` computation currently lives inline near `:5741-5757`. Extract it to a module-level helper in `feeds/dip_scanner.py` so both the inline path and the Component C path use ONE implementation (DRY). Add near the other module helpers:
```python
def _net_flow_15s_usd_from_trades(recent_trades):
    """Sum of buy USD minus sell USD over the last 15s of recent_trades.
    Mirrors the existing inline net_flow_15s derivation. Returns float or None
    when there are no trades. Pure; never raises."""
    try:
        if not recent_trades:
            return None
        # recent_trades newest-first; each has 'kind' (buy/sell) and 'amount_usd'.
        import time as _t
        now = _t.time()
        flow = 0.0
        seen = False
        for tr in recent_trades:
            ts = tr.get("ts") or tr.get("timestamp")
            if ts is not None:
                try:
                    if now - float(ts) > 15.0:
                        continue
                except (TypeError, ValueError):
                    pass
            usd = tr.get("amount_usd") or tr.get("usd") or 0.0
            try:
                usd = float(usd)
            except (TypeError, ValueError):
                continue
            seen = True
            if tr.get("kind") == "buy":
                flow += usd
            elif tr.get("kind") == "sell":
                flow -= usd
        return flow if seen else None
    except Exception:
        return None
```
Then refactor the inline `:5741-5757` block to call `_net_flow_15s_usd_from_trades(recent_trades)` for its `net_flow_15s_usd`, preserving existing behavior. **Verify the trade dict keys** (`kind`, `amount_usd`/`usd`, `ts`/`timestamp`) against the actual `fetch_recent_trades` output at `:5741-5757` and adjust the helper to match the real keys before finalizing.

- [ ] **Step 7: Make `_evaluate_pair` prefer the fresh net-flow when present**

At the `net_flow_15s_usd` read in `_evaluate_pair` (`:1967`, `gf("net_flow_15s_usd")`), prefer `_pair["_rt_net_flow_15s_usd"]` when set:
```python
        _rt_nf = (pair.get("_rt_net_flow_15s_usd") if isinstance(pair, dict) else None)
        nf15 = _rt_nf if _rt_nf is not None else gf("net_flow_15s_usd")
```
(Locate the exact `nf15 = gf("net_flow_15s_usd")` assignment near `:1967` and apply this override.)

- [ ] **Step 8: Run suite + smoke**

Run: `pytest tests/test_realtime_dip_detection.py -v` then `python -c "import feeds.dip_scanner"`
Expected: all PASS; import OK.

- [ ] **Step 9: Commit**

```bash
git add core/fast_watch.py feeds/dip_scanner.py tests/test_realtime_dip_detection.py
git commit -m "feat(rt-detect): RT_DEMAND_TURN_MODE — fresh net_flow_15s confirm (cached fallback, never fail-open)"
```

---

### Task 6: Component E (part 1) — `trigger_source` telemetry tag

**Goal:** Stamp `trigger_source` (`legacy` | `realtime`) on every live-swap record so the A/B is measurable.

**Files:**
- Modify: `core/live_swap_log.py:39-61` (add to `REQUIRED_FIELDS`)
- Modify: `feeds/dip_scanner.py:2176` (the `log_live_swap(...)` call in `_emit_buy_telemetry`)
- Test: `tests/test_realtime_dip_detection.py`

**Interfaces:**
- Consumes: `rt_mode` (Task 3) to decide the tag value.
- Produces: `trigger_source` key on the live-swap record.

- [ ] **Step 1: Write the failing test**

```python
from core import live_swap_log

def test_trigger_source_in_required_fields():
    assert "trigger_source" in live_swap_log.REQUIRED_FIELDS

def test_log_live_swap_writes_trigger_source(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    live_swap_log.log_live_swap(side="buy", token_address="X", trigger_source="realtime")
    import json
    line = (tmp_path / "live_swaps.jsonl").read_text().strip().splitlines()[-1]
    assert json.loads(line)["trigger_source"] == "realtime"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_realtime_dip_detection.py -k trigger_source -v`
Expected: FAIL (key not in REQUIRED_FIELDS; record value None).

- [ ] **Step 3: Add the field**

In `core/live_swap_log.py`, in `REQUIRED_FIELDS`, add `"trigger_source"` to the Identity/context line (`:41-42`):
```python
    "ts", "side", "bot_id", "token_address", "token_symbol", "pair_address",
    "trigger", "trigger_source", "size_usd", "size_sol", "lamports",
    "liquidity_usd", "mcap", "jupiter_api_base", "live_mode", "paper",
```

- [ ] **Step 4: Pass the tag from the emit site**

In `feeds/dip_scanner.py`, in `_emit_buy_telemetry`'s `log_live_swap(...)` call (`:2176`), add the argument (compute once near the call):
```python
                    trigger_source=("realtime" if rt_mode(
                        "RT_TRIGGER_MODE", getattr(pm, "config", None)) == "enforce"
                        else "legacy"),
```
(Import `rt_mode` at the top of the method or module: `from core.fast_watch import rt_mode`. `pm` is the position manager in scope at the emit site; if the bot config is reachable via a different local, use that — the goal is per-bot resolution so bot B reads `enforce`.)

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_realtime_dip_detection.py -k trigger_source -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/live_swap_log.py feeds/dip_scanner.py tests/test_realtime_dip_detection.py
git commit -m "feat(rt-detect): trigger_source tag (legacy|realtime) on live-swap telemetry"
```

---

### Task 7: Component E (part 2) — A/B bot configs

**Goal:** Two tiny capped live bots identical except the trigger: A = legacy (control), B = real-time (treatment).

**Files:**
- Modify: `config/bots/badday_flush_nf15_live.json` (control — pin legacy)
- Create: `config/bots/badday_flush_nf15_rt_live.json` (treatment — enforce real-time)
- Test: `tests/test_realtime_dip_detection.py`

**Interfaces:**
- Consumes: the per-bot `rt_*_mode` keys resolved by `rt_mode` (Task 3).

- [ ] **Step 1: Write the failing config-shape test**

```python
import json, os

def _load(name):
    p = os.path.join("config", "bots", name)
    with open(p) as f:
        return json.load(f)

def test_control_bot_is_legacy_trigger():
    cfg = _load("badday_flush_nf15_live.json")
    assert cfg.get("rt_trigger_mode", "off") == "off"
    assert cfg.get("live_probe") is True

def test_treatment_bot_is_realtime_and_capped():
    cfg = _load("badday_flush_nf15_rt_live.json")
    assert cfg["rt_trigger_mode"] == "enforce"
    assert cfg["rt_arm_mode"] == "enforce"
    assert cfg["rt_demand_turn_mode"] == "enforce"
    assert cfg["live_probe"] is True
    assert cfg["daily_loss_limit_usd"] <= 60
    # flat sizing, no conviction leverage
    assert cfg.get("conviction") in (None, False)
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_realtime_dip_detection.py -k bot -v`
Expected: FAIL (treatment file missing; control lacks `rt_trigger_mode`).

- [ ] **Step 3: Pin the control bot to legacy**

Add `"rt_trigger_mode": "off"`, `"rt_arm_mode": "off"`, `"rt_demand_turn_mode": "off"` to `config/bots/badday_flush_nf15_live.json` (so it is explicitly the control even if env defaults change).

- [ ] **Step 4: Create the treatment bot**

Copy `config/bots/badday_flush_nf15_live.json` to `config/bots/badday_flush_nf15_rt_live.json`, change `id`/`name` to `badday_flush_nf15_rt_live`, and set:
```json
  "rt_trigger_mode": "enforce",
  "rt_arm_mode": "enforce",
  "rt_demand_turn_mode": "enforce",
  "live_probe": true,
  "daily_loss_limit_usd": 60
```
Keep flat $100 sizing (no conviction), same entry gate as the control.

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_realtime_dip_detection.py -k bot -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add config/bots/badday_flush_nf15_live.json config/bots/badday_flush_nf15_rt_live.json tests/test_realtime_dip_detection.py
git commit -m "feat(rt-detect): A/B bot configs — nf15_live (legacy) vs nf15_rt_live (realtime)"
```

---

### Task 8: Shadow soak + capped live A/B (gated, manual)

**Goal:** Validate. NO automated code — this is the deploy + measurement gate. The live step requires explicit AxiS approval and the go-live runbook.

**Files:** none (operational).

- [ ] **Step 1: Full test suite green**

Run: `pytest tests/test_realtime_dip_detection.py tests/test_pre_live_invariants.py -v`
Expected: all PASS. Do NOT proceed otherwise.

- [ ] **Step 2: Deploy in SHADOW, paper mode**

Set env `RT_TRIGGER_MODE=shadow RT_ARM_MODE=shadow RT_DEMAND_TURN_MODE=shadow`, `PAPER_MODE=true`. Commit, push, deploy. Confirm via `railway logs | grep -E "rt-trigger|rt-arm|rt-demand"` that the shadow logs show fresh-vs-snapshot divergence (fresh `pc_h1` catching dips the stale path misses) and that loop-block is < ~2s (Task 1 Step 7). This is a sanity check, not the validation.

- [ ] **Step 3: Verify the A/B bots resolve different modes**

Confirm `badday_flush_nf15_live` logs `legacy` and `badday_flush_nf15_rt_live` would log `realtime` on the same deploy (per-bot resolution works).

- [ ] **Step 4: GATE — request explicit AxiS approval for the capped live A/B**

Present: shadow evidence, loop-block number, the cap config ($120 inflight / $50 daily-kill / $60 bot), and the go-live runbook order (flip `PAPER_MODE=false` → wait live cutover → AxiS clears daily P&L at cutover → confirm `BUY_REPRICE_MODE=enforce` → do NOT redeploy while holding a live position → watch first fills). **Do not flip `PAPER_MODE` without this approval.**

- [ ] **Step 5: Run the capped live A/B + report**

With approval: both bots live, tiny caps. Compare via `/api/live-swaps` + `trigger_source`: the real-time bot (B) should show **near-zero `reprice_runup_pct`** (decided on fresh price) and **entries below** the legacy bot (A) on the same tokens, at comparable/better realized `pnl_pct`. Report the A/B. If B wins, recommend promoting `RT_*_MODE=enforce` as the fleet default; else iterate from the data.

---

## Self-Review

**Spec coverage:**
- Component A (real-time trigger) → Tasks 2 + 3. ✓
- Component B (real-time arming) → Task 4. ✓
- Component C (fresh demand-turn) → Task 5. ✓
- Component D (loop unblock) → Task 1. ✓
- Component E (telemetry + A/B) → Tasks 6 + 7 + 8. ✓
- Off/shadow/enforce per-bot flags → `rt_mode` (Task 3), used by 3/4/5/6/7. ✓
- BUY_REPRICE_MODE=enforce stays + PAPER_MODE gated + pre-live invariants → Task 8. ✓
- Free tools only (reuses Jupiter samples + DexScreener `fetch_recent_trades`, no new deps). ✓
- Validation = capped live A/B on real fills → Task 8. ✓

**Type consistency:** `rt_mode(flag, bot_cfg, default)->str`, `reprice_change_pct(pct, snap_px, fresh_px)->float|None`, `should_rearm_this_tick(mode)->bool`, `demand_turn_ok(fresh, cached, fetch_ok)->bool`, `_net_flow_15s_usd_from_trades(trades)->float|None` — names/signatures consistent across tasks.

**Open verification flagged in-plan (must confirm against real code during implementation):** the exact `fetch_recent_trades` trade-dict keys (Task 5 Step 6) and the exact `nf15 = gf("net_flow_15s_usd")` line (Task 5 Step 7). These are localized reads, not design gaps.
