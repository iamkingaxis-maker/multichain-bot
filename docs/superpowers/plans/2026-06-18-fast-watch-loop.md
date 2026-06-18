# Fast-Watch Loop Implementation Plan (Rev 2: armed-subset + DexScreener batch)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Catch dips on already-watched tokens within ~3–5s by arming a small near-miss subset each scan cycle and batch-polling only that subset via DexScreener, instead of the dead Axiom WS or the compute-bound ~150–165s sweep.

**Architecture:** Migration from the merged Rev-1 (Axiom-based) loop. Rev-1's allowlist+shadow threading (`evaluate_all` allowlist, `_evaluate_pair` `_fast_path_*`, `_fast_route_decisions`), the `run()` spawn, and the `__init__` cycle-attr init **stay as-merged**. We swap the loop's data source: arm the ≤30 closest-to-firing watchlist tokens (proxy distance from cached `pc_h1`), batch-poll them via `api.dexscreener.com/latest/dex/tokens/{≤30}`, detect a dip off rolling price samples, and escalate into the existing `_evaluate_pair`. Add an armed-hit-rate log so shadow proves arming is correct before enforce.

**Tech Stack:** Python 3, asyncio, aiohttp, pytest (+ pytest-asyncio). Spec: `docs/superpowers/specs/2026-06-18-fast-watch-loop-design.md`. Rev-1 commits already on master: `7c67b56`, `a2eec56`, `0b68276`, `7f51b09`, `cbc1fcc`, `8228be3`.

---

## What stays from Rev-1 (do NOT touch)
- `core/bot_manager.evaluate_all(bot_allowlist=...)` — unchanged.
- `_evaluate_pair` `_fast_path_allowlist`/`_fast_path_shadow` threading + `_fast_route_decisions` + the legacy-fire `if _fp_allow is not None: continue` guard — unchanged (reviewed SHIP).
- `DipScanner.run()` spawn of `_fast_watch_loop` — unchanged.
- `__init__` init of `_cycle_bought_addrs`/`_cycle_trend_reversal_blocked`/`_fp_shadow_culled` — unchanged.
- The Tasks 1–3 tests in `tests/test_fast_watch.py` for allowlist/shadow/route — unchanged.

## What changes (this plan)
- **R1** `core/fast_watch.py`: Rev-2 `FastWatchConfig` fields; new pure `arm_subset()` + `rolling_dip_pct()`; `shortlist` `get_trend` loses the `secs` arg.
- **R2** `feeds/dip_scanner.py`: `_fast_arm_subset()` (Tier 0), `_fast_batch_prices()` (Tier 1 fetch), rewritten `_fast_watch_tick` (no Axiom), `__init__` adds `_fast_armed`/`_fast_samples`, `_scan_cycle` calls `_fast_arm_subset`, `_fast_watch_loop` log line drops `trend_secs`. Remove `_fast_trend`.
- **R3** `feeds/dip_scanner.py`: armed-hit-rate log at the buy-fire sites.
- **R4** Full verification.
- **R5** Deploy shadow + validate the armed-hit-rate gate (runtime; AxiS-gated for enforce).

---

## Task R1: Rev-2 config + pure arm_subset + rolling_dip_pct

**Files:**
- Modify: `core/fast_watch.py`
- Test: `tests/test_fast_watch.py`

- [ ] **Step 1: Update the failing tests**

In `tests/test_fast_watch.py`, REPLACE `test_config_from_env_defaults_and_overrides` and
`test_config_bad_numbers_fall_back_to_defaults` with the Rev-2 versions below, UPDATE
`test_shortlist_filters_held_blocked_and_recent` (the `get_trend` lambda loses `secs`, and the config is
built with Rev-2 fields), and ADD the new `arm_subset`/`rolling_dip_pct` tests:

```python
def test_config_from_env_defaults_and_overrides(monkeypatch):
    for k in ("FAST_WATCH_MODE", "FAST_WATCH_INTERVAL_SECS", "FAST_WATCH_DIP_PCT",
              "FAST_WATCH_EVAL_COOLDOWN_SECS", "FAST_WATCH_BOT_ALLOWLIST", "FAST_WATCH_ARMED_MAX",
              "FAST_WATCH_SAMPLE_WINDOW", "FAST_WATCH_VOLATILITY_RESERVE",
              "FAST_WATCH_DIP_ZONE_PCT", "FAST_WATCH_ARM_BAND_PP"):
        monkeypatch.delenv(k, raising=False)
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.mode == "off"
    assert cfg.interval_secs == 3.0
    assert cfg.dip_pct == 3.0
    assert cfg.eval_cooldown_secs == 60.0
    assert cfg.armed_max == 30
    assert cfg.sample_window == 40
    assert cfg.volatility_reserve == 0.2
    assert cfg.dip_zone_pct == -12.0
    assert cfg.arm_band_pp == 12.0
    assert "badday_flush_conviction" in cfg.bot_allowlist
    assert not hasattr(cfg, "trend_secs")
    monkeypatch.setenv("FAST_WATCH_MODE", "ShAdOw")
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "10")
    cfg2 = fw.FastWatchConfig.from_env()
    assert cfg2.mode == "shadow"
    assert cfg2.armed_max == 10


def test_config_bad_numbers_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_INTERVAL_SECS", "not-a-number")
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "")
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.interval_secs == 3.0
    assert cfg.armed_max == 30


def _cfg(**kw):
    base = dict(mode="shadow", interval_secs=3.0, dip_pct=3.0, eval_cooldown_secs=60.0,
                bot_allowlist=frozenset({"x"}), armed_max=30, sample_window=40,
                volatility_reserve=0.2, dip_zone_pct=-12.0, arm_band_pp=12.0)
    base.update(kw)
    return fw.FastWatchConfig(**base)


def test_arm_subset_picks_cusp_excludes_far_and_past():
    cfg = _cfg(armed_max=3, volatility_reserve=0.0)
    cands = [
        {"addr": "NEAR", "pc_h1": -8.0, "vol_h1": 1.0, "in_band": True},   # dist 4 -> cusp
        {"addr": "FLAT", "pc_h1": -2.0, "vol_h1": 1.0, "in_band": True},   # dist 10 -> cusp (farther)
        {"addr": "FAR",  "pc_h1": +5.0, "vol_h1": 1.0, "in_band": True},   # dist 17 > band -> out
        {"addr": "PAST", "pc_h1": -20.0,"vol_h1": 1.0, "in_band": True},   # dist -8 <=0 -> out (already in zone)
        {"addr": "OOB",  "pc_h1": -8.0, "vol_h1": 9.0, "in_band": False},  # out of band -> out
    ]
    armed = fw.arm_subset(cands, cfg)
    assert armed == ["NEAR", "FLAT"]   # smallest-distance first; FAR/PAST/OOB excluded


def test_arm_subset_volatility_reserve_fills_remaining():
    cfg = _cfg(armed_max=2, volatility_reserve=0.5)   # 1 cusp slot, 1 reserve slot
    cands = [
        {"addr": "CUSP", "pc_h1": -8.0, "vol_h1": 1.0, "in_band": True},   # cusp
        {"addr": "VOLA", "pc_h1": +50.0, "vol_h1": 99.0, "in_band": True}, # far (no cusp) but high vol
        {"addr": "VOLB", "pc_h1": +40.0, "vol_h1": 50.0, "in_band": True},
    ]
    armed = fw.arm_subset(cands, cfg)
    assert armed[0] == "CUSP"
    assert "VOLA" in armed and len(armed) == 2   # reserve filled by highest vol_h1


def test_arm_subset_caps_at_armed_max():
    cfg = _cfg(armed_max=2, volatility_reserve=0.0)
    cands = [{"addr": f"T{i}", "pc_h1": -float(i), "vol_h1": 1.0, "in_band": True} for i in range(1, 6)]
    assert len(fw.arm_subset(cands, cfg)) == 2


def test_rolling_dip_pct():
    assert fw.rolling_dip_pct([]) is None
    assert fw.rolling_dip_pct([100.0]) is None            # <2 samples
    assert fw.rolling_dip_pct([100.0, 90.0]) == -10.0      # 10% off the high
    assert fw.rolling_dip_pct([100.0, 120.0, 114.0]) == -5.0  # off the window MAX (120), not first
    assert fw.rolling_dip_pct([0.0, 0.0]) is None          # bad data
```

Also change the existing `test_shortlist_filters_held_blocked_and_recent`: build `cfg` via the new
field set (use the `_cfg(...)` helper) and change the lambda to `get_trend=lambda addr: trends.get(addr)`
(no `secs`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_fast_watch.py -k "arm_subset or rolling_dip or config or shortlist" -q`
Expected: FAIL (`AttributeError: module 'core.fast_watch' has no attribute 'arm_subset'`, plus config field errors).

- [ ] **Step 3: Implement in `core/fast_watch.py`**

Replace the `FastWatchConfig` dataclass + `from_env` with:

```python
@dataclass(frozen=True)
class FastWatchConfig:
    mode: str                 # "off" | "shadow" | "enforce"
    interval_secs: float
    dip_pct: float
    eval_cooldown_secs: float
    bot_allowlist: frozenset
    armed_max: int
    sample_window: int
    volatility_reserve: float
    dip_zone_pct: float
    arm_band_pp: float

    @classmethod
    def from_env(cls) -> "FastWatchConfig":
        mode = os.environ.get("FAST_WATCH_MODE", "off").strip().lower()
        if mode not in ("off", "shadow", "enforce"):
            mode = "off"
        raw = os.environ.get("FAST_WATCH_BOT_ALLOWLIST", "").strip()
        allow = (frozenset(b.strip() for b in raw.split(",") if b.strip())
                 if raw else _DEFAULT_ALLOWLIST)
        return cls(
            mode=mode,
            interval_secs=_f("FAST_WATCH_INTERVAL_SECS", 3.0),
            dip_pct=_f("FAST_WATCH_DIP_PCT", 3.0),
            eval_cooldown_secs=_f("FAST_WATCH_EVAL_COOLDOWN_SECS", 60.0),
            bot_allowlist=allow,
            armed_max=_i("FAST_WATCH_ARMED_MAX", 30),
            sample_window=_i("FAST_WATCH_SAMPLE_WINDOW", 40),
            volatility_reserve=_f("FAST_WATCH_VOLATILITY_RESERVE", 0.2),
            dip_zone_pct=_f("FAST_WATCH_DIP_ZONE_PCT", -12.0),
            arm_band_pp=_f("FAST_WATCH_ARM_BAND_PP", 12.0),
        )
```

Change `shortlist` to call `get_trend(addr)` (drop the `secs` arg):

```python
def shortlist(snapshot, get_trend: Callable, dedup: FastWatchDedup,
              is_held_or_blocked: Callable, cfg: FastWatchConfig, now: float):
    """Return [(addr, entry, trend)] for armed tokens worth a full evaluation.
    `get_trend(addr)` and `is_held_or_blocked(addr)` are injected for testability."""
    out = []
    for addr, entry in snapshot:
        trend = get_trend(addr)
        if not dip_trigger(trend, cfg.dip_pct):
            continue
        if not dedup.should_eval(addr, now):
            continue
        if is_held_or_blocked(addr):
            continue
        out.append((addr, entry, trend))
    return out
```

Append the two new pure functions:

```python
def arm_subset(candidates, cfg: FastWatchConfig):
    """Select the armed token addresses for the fast loop.

    `candidates`: list of dicts {addr, pc_h1 (float|None), vol_h1 (float|None), in_band (bool)}.
    distance = pc_h1 − cfg.dip_zone_pct  (pp ABOVE the dip-zone edge; e.g. pc_h1=-8, edge=-12 → 4pp).
    Arm tokens approaching the zone (0 < distance ≤ arm_band_pp), smallest distance first (closest to
    firing), filling cfg.armed_max; reserve a fraction for highest-volatility in-band tokens so a sudden
    crash on a non-near-miss can still be caught. Returns an ordered list of addresses (≤ armed_max).
    """
    in_band = [c for c in candidates if c.get("in_band")]
    cusp = []
    for c in in_band:
        pc = c.get("pc_h1")
        if pc is None:
            continue
        dist = pc - cfg.dip_zone_pct
        if 0 < dist <= cfg.arm_band_pp:
            cusp.append((dist, c["addr"]))
    cusp.sort(key=lambda t: t[0])
    n_cusp = max(0, int(round(cfg.armed_max * (1.0 - cfg.volatility_reserve))))
    armed = [a for _d, a in cusp[:n_cusp]]
    chosen = set(armed)
    n_reserve = cfg.armed_max - len(armed)
    if n_reserve > 0:
        vol = sorted(
            (c for c in in_band if c["addr"] not in chosen and c.get("vol_h1") is not None),
            key=lambda c: c["vol_h1"], reverse=True,
        )
        armed.extend(c["addr"] for c in vol[:n_reserve])
    return armed[:cfg.armed_max]


def rolling_dip_pct(samples):
    """% drop of the latest sample off the window max. None if <2 valid (>0) samples.
    `samples`: iterable of prices (oldest→newest)."""
    vals = [p for p in samples if isinstance(p, (int, float)) and p > 0]
    if len(vals) < 2:
        return None
    hi = max(vals)
    if hi <= 0:
        return None
    return (vals[-1] / hi - 1.0) * 100.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_fast_watch.py -q`
Expected: PASS (the allowlist/shadow/route tests from Rev-1 still pass; the new + updated ones pass).

- [ ] **Step 5: Commit**

```bash
git add core/fast_watch.py tests/test_fast_watch.py
git commit -m "feat(fast-watch): Rev2 config + pure arm_subset + rolling_dip_pct"
```

---

## Task R2: Rewrite the loop for armed-subset + DexScreener batch

**Files:**
- Modify: `feeds/dip_scanner.py` (`__init__`, `_scan_cycle` end, `_fast_watch_tick`, `_fast_watch_loop` log, remove `_fast_trend`; add `_fast_arm_subset`, `_fast_batch_prices`)
- Test: `tests/test_fast_watch.py`

- [ ] **Step 1: Replace the failing tick tests**

In `tests/test_fast_watch.py`, REMOVE the Rev-1 Axiom-based helper `_scanner_for_tick` and its three
tests (`test_fast_watch_tick_escalates_only_the_dip`, `_dedups_second_call`, `_survives_eval_exception`)
and REPLACE with the armed-subset versions:

```python
def _scanner_for_tick_v2():
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s._token_registry = None
    s._fast_watch_regime = {"_regime_n": 0, "_regime_dip_breadth_pct": None, "_regime_h1_neg_pct": None}
    # armed set: DIP token will be made to dip via injected batch prices; FLAT will not.
    s._fast_armed = {
        "DIPADDR": {"pairAddress": "P", "priceUsd": "1"},
        "FLATADDR": {"pairAddress": "P2", "priceUsd": "1"},
    }
    from collections import deque
    s._fast_samples = {}
    # Pre-seed a high sample so a single fresh low price registers as a dip.
    s._fast_samples["DIPADDR"] = deque([1.00], maxlen=40)
    s._fast_samples["FLATADDR"] = deque([1.00], maxlen=40)

    async def fake_batch(addrs):
        return {"dipaddr": 0.90, "flataddr": 1.00}   # DIP drops 10%, FLAT flat
    s._fast_batch_prices = fake_batch

    s.evaluated = []
    async def fake_eval(pair, ctx):
        s.evaluated.append((pair.get("pairAddress"), ctx.get("_fast_path_shadow"),
                            ctx.get("_fast_path_allowlist")))
        return (None, 0, False)
    s._evaluate_pair = fake_eval
    return s


def test_fast_tick_v2_escalates_only_the_dip(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_BOT_ALLOWLIST", "x,y")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == [("P", True, frozenset({"x", "y"}))]   # only DIP, shadow + allowlist


def test_fast_tick_v2_dedups(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    d = FastWatchDedup(cfg.eval_cooldown_secs)
    asyncio.run(s._fast_watch_tick(cfg, d))
    asyncio.run(s._fast_watch_tick(cfg, d))
    assert len(s.evaluated) == 1


def test_fast_tick_v2_survives_eval_exception(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    async def boom(pair, ctx): raise RuntimeError("x")
    s._evaluate_pair = boom
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))   # must not raise


def test_fast_tick_v2_empty_armed_is_noop(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    s._fast_armed = {}
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_fast_watch.py -k fast_tick_v2 -v`
Expected: FAIL (`_fast_batch_prices` is set by the test, but `_fast_watch_tick` still calls the old
Axiom path / `self.axiom_price_feed` → AttributeError or wrong behavior).

- [ ] **Step 3: Implement in `feeds/dip_scanner.py`**

3a. Ensure `from collections import deque` is imported at the top of the file (add it if absent — check
the existing imports first).

3b. In `__init__`, next to the existing `self._sticky_watchlist` initialization, add:

```python
        self._fast_armed: Dict[str, dict] = {}      # addr -> pair (armed subset, rebuilt each cycle)
        self._fast_samples: Dict[str, deque] = {}   # addr -> rolling price deque (fast-watch batch poll)
```

3c. Add `_fast_arm_subset` and `_fast_batch_prices` next to the other `_fast_*` methods (above
`_evaluate_pair`). Use `self.min_mcap`, `self.max_mcap`, `self.min_age_ms` (all existing):

```python
    def _fast_arm_subset(self, cfg, now_ms):
        """Tier 0: build self._fast_armed from the watchlist via proxy distance-to-dip-zone.
        Pure in-memory selection over cached pair data; no network, no _evaluate_pair change."""
        from core.fast_watch import arm_subset
        cands = []
        for addr, entry in list(self._sticky_watchlist.items()):
            pair = (entry or {}).get("pair") or {}
            try:
                mcap = float(pair.get("marketCap") or 0)
                liq = float((pair.get("liquidity") or {}).get("usd") or 0)
                created = pair.get("pairCreatedAt") or 0
                pch = pair.get("priceChange") or {}
                _h1 = pch.get("h1")
                pc_h1 = float(_h1) if _h1 is not None else None
                vol_h1 = float((pair.get("volume") or {}).get("h1") or 0)
            except (TypeError, ValueError):
                continue
            age_ok = created and (now_ms - created) >= self.min_age_ms
            in_band = bool(self.min_mcap <= mcap <= self.max_mcap and liq > 0 and age_ok)
            cands.append({"addr": addr, "pc_h1": pc_h1, "vol_h1": vol_h1, "in_band": in_band})
        armed_addrs = arm_subset(cands, cfg)
        new_armed = {}
        for addr in armed_addrs:
            entry = self._sticky_watchlist.get(addr) or {}
            pair = entry.get("pair")
            if pair:
                new_armed[addr] = pair
        self._fast_armed = new_armed
        # Drop sample buffers for tokens no longer armed (bound memory).
        for addr in list(self._fast_samples.keys()):
            if addr not in self._fast_armed:
                self._fast_samples.pop(addr, None)

    async def _fast_batch_prices(self, addrs):
        """Tier 1 fetch: fresh priceUsd for ≤30 addrs per DexScreener call.
        Returns {addr_lower: price}. Best-effort — returns partial/{} on failure."""
        out = {}
        if not addrs:
            return out
        import aiohttp
        for i in range(0, len(addrs), 30):
            chunk = addrs[i:i + 30]
            url = "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(chunk)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        data = await resp.json(content_type=None)
                for p in (data or {}).get("pairs") or []:
                    base = ((p.get("baseToken") or {}).get("address") or "").lower()
                    pr = p.get("priceUsd")
                    if base and pr:
                        try:
                            out[base] = float(pr)
                        except (TypeError, ValueError):
                            pass
            except Exception as e:
                logger.debug("[fast-watch] batch price fetch failed: %s", e)
        return out
```

3d. REPLACE the entire Rev-1 `_fast_watch_tick` body (the `from core.fast_watch import shortlist` …
through the escalation loop) with the armed-subset version, and DELETE the `_fast_trend` staticmethod:

```python
    async def _fast_watch_tick(self, cfg, dedup):
        from core.fast_watch import shortlist, rolling_dip_pct
        armed = dict(self._fast_armed)   # snapshot
        if not armed:
            logger.info("[fast-watch] tick armed=0 polled=0 dipped=0 mode=%s", cfg.mode)
            return
        addrs = list(armed.keys())
        prices = await self._fast_batch_prices(addrs)
        now = time.time()
        now_ms = int(now * 1000)
        polled = 0
        for addr in addrs:
            pr = prices.get(addr.lower())
            if pr is None:
                continue
            polled += 1
            buf = self._fast_samples.setdefault(addr, deque(maxlen=cfg.sample_window))
            buf.append(pr)
        snapshot = [(addr, armed[addr]) for addr in addrs]
        survivors = shortlist(
            snapshot,
            get_trend=lambda a: rolling_dip_pct(self._fast_samples.get(a) or ()),
            dedup=dedup,
            is_held_or_blocked=lambda a: self._fast_held_or_blocked(a, cfg.bot_allowlist),
            cfg=cfg, now=now,
        )
        logger.info("[fast-watch] tick armed=%d polled=%d dipped=%d mode=%s",
                    len(addrs), polled, len(survivors), cfg.mode)
        regime = getattr(self, "_fast_watch_regime", {}) or {}
        for addr, pair, _trend in survivors:
            dedup.mark(addr, now)
            if not pair:
                continue
            fresh = prices.get(addr.lower())
            if fresh:
                pair = dict(pair)
                pair["priceUsd"] = str(fresh)
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
```

3e. In `_fast_watch_loop`, change the startup log (it references the removed `trend_secs`):

```python
        logger.info("[fast-watch] starting mode=%s interval=%.1fs dip<=-%.1f%% "
                    "armed_max=%d allowlist=%d bots",
                    cfg.mode, cfg.interval_secs, cfg.dip_pct, cfg.armed_max,
                    len(cfg.bot_allowlist))
```

3f. Call `_fast_arm_subset` at the END of `_scan_cycle`. Right after the existing
`self._fast_watch_regime = {...}` stash (added in Rev-1), add:

```python
        # Tier 0: re-arm the fast-watch subset from this cycle's fresh watchlist data.
        try:
            from core.fast_watch import FastWatchConfig as _FWC
            _fw_cfg = _FWC.from_env()
            if _fw_cfg.mode != "off":
                self._fast_arm_subset(_fw_cfg, now_ms)
        except Exception as _arm_e:
            logger.error("[fast-watch] arm subset error: %s", _arm_e)
```

(`now_ms` is in scope where `_fast_watch_regime` is built.)

- [ ] **Step 4: Run tests + regression**

```
PYTHONIOENCODING=utf-8 python -m pytest tests/test_fast_watch.py -q
PYTHONIOENCODING=utf-8 python -m pytest tests/test_parallel_scan.py tests/test_parallel_scan_decision.py tests/test_parallel_tick.py -q
python -c "import feeds.dip_scanner"
```
Expected: fast_watch all pass; parallel-scan regressions pass (the `_scan_cycle` arm call is wrapped + mode-gated, so main-path unaffected); import OK.

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_fast_watch.py
git commit -m "feat(fast-watch): Rev2 loop — arm subset + DexScreener batch (drop Axiom)"
```

---

## Task R3: Armed-hit-rate log at the buy-fire sites

**Files:**
- Modify: `feeds/dip_scanner.py` (`_fast_route_decisions` real-fire branch + the legacy single-bot fire)
- Test: `tests/test_fast_watch.py`

- [ ] **Step 1: Write the failing test**

```python
def test_hitrate_log_marks_armed(monkeypatch, caplog):
    import logging, types, asyncio as aio
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = aio.Lock()
    s._fast_armed = {"TOKADDR": {"pairAddress": "P"}}
    fired = []
    async def fake_exec(d, bundle): fired.append(d.bot_id)
    s._execute_bot_buy = fake_exec
    d = types.SimpleNamespace(bot_id="a", token="TOKADDR")
    with caplog.at_level(logging.INFO):
        aio.run(s._fast_route_decisions([d], bundle=None, allowlist=None, shadow=False,
                                        token_symbol="TOK"))
    assert fired == ["a"]
    assert any("hit-rate" in r.message and "armed=True" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_fast_watch.py::test_hitrate_log_marks_armed -v`
Expected: FAIL (no `hit-rate` log emitted yet).

- [ ] **Step 3: Implement**

In `_fast_route_decisions`, in the **real-fire branch** (the `else:` that calls `_execute_bot_buy`), add
the hit-rate log immediately before the fire:

```python
                else:
                    _armed = getattr(self, "_fast_armed", {}) or {}
                    _tok = getattr(d, "token", "") or ""
                    logger.info(
                        "[fast-watch] hit-rate buy bot=%s token=%s armed=%s",
                        getattr(d, "bot_id", "?"), token_symbol,
                        (_tok in _armed),
                    )
                    await self._execute_bot_buy(d, bundle)
```

In the legacy single-bot fire block (where `await self.trader.buy(...)` runs under the lock), add the same
log immediately before `self.trader.buy(`:

```python
                _armed = getattr(self, "_fast_armed", {}) or {}
                logger.info(
                    "[fast-watch] hit-rate buy bot=legacy_dip token=%s armed=%s",
                    token_symbol, (token_address in _armed),
                )
```

(`token_address`/`token_symbol` are in scope in the legacy block.)

- [ ] **Step 4: Run tests**

```
PYTHONIOENCODING=utf-8 python -m pytest tests/test_fast_watch.py -q
PYTHONIOENCODING=utf-8 python -m pytest tests/test_parallel_scan.py tests/test_parallel_scan_decision.py -q
```
Expected: all PASS (the hit-rate log is additive, decision-neutral — regressions unaffected).

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_fast_watch.py
git commit -m "feat(fast-watch): armed-hit-rate log at buy-fire sites (shadow validation)"
```

---

## Task R4: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full suite**

```bash
python -c "import feeds.dip_scanner" && echo IMPORT_OK
PYTHONIOENCODING=utf-8 python -m pytest tests/test_fast_watch.py -q
PYTHONIOENCODING=utf-8 python -m pytest tests/test_parallel_scan.py tests/test_parallel_scan_decision.py tests/test_parallel_tick.py tests/test_exit_price_guard.py -q
python tests/test_pre_live_invariants.py
```
Expected: import OK; `test_fast_watch.py` all pass; all regression suites pass; pre-live invariants print
`Pre-live invariants OK` and exit 0.

- [ ] **Step 2: Confirm no stray Axiom usage in the loop**

Run: `grep -nE "get_tick_trend|axiom_price_feed|_fast_trend|trend_secs" feeds/dip_scanner.py core/fast_watch.py`
Expected: no matches inside the fast-watch loop/module (any remaining `axiom_price_feed` references must be
outside the fast-watch methods — position pricing etc.).

- [ ] **Step 3: Commit (if any cleanup needed; otherwise skip)**

---

## Task R5: Deploy shadow + validate armed-hit-rate (runtime gate; no code)

**Files:** none (env + observation). Do NOT skip to enforce. This is the spec's Phase-1 gate.

- [ ] **Step 1: Deploy + enable shadow**

```bash
git push
railway up --detach
# After the deploy is WARM (give it a few cycles so the watchlist + armed set populate):
railway variables --set "FAST_WATCH_MODE=shadow"
railway up --detach
```
Confirm `railway variables` shows `PAPER_MODE=true`, `PROFIT_SWEEP_DRY_RUN=1` (unchanged).

- [ ] **Step 2: Validate over real cycles**

Capture logs and check:
- `[fast-watch] tick armed=N polled=M dipped=K mode=shadow` with **`M ≈ N`** (DexScreener coverage healthy
  — the fix for Axiom's `live_ticks=0`), `N` ≤ `FAST_WATCH_ARMED_MAX`.
- `[fast-watch] would-fire bot=X token=Y (shadow)` on real dips, with no money moved.
- **Armed-hit-rate:** collect `[fast-watch] hit-rate buy … armed=<bool>` lines over a window; compute the
  fraction `armed=True`. This is the go/no-go on whether we're arming the *correct* tokens.
- DexScreener call rate stays tiny (≤ a couple calls / tick).

- [ ] **Step 3: Decision gate (AxiS)**

Only after shadow shows healthy coverage AND a satisfactory armed-hit-rate (with would-fire leading the
main loop): flip to `FAST_WATCH_MODE=enforce` **in paper** (`PAPER_MODE` stays `true`). If hit-rate is
poor, tune `FAST_WATCH_DIP_ZONE_PCT` / `FAST_WATCH_ARM_BAND_PP` / `FAST_WATCH_VOLATILITY_RESERVE` /
`FAST_WATCH_ARMED_MAX` and re-measure first. Live is a separate explicit AxiS decision.

---

## Self-Review (completed by plan author)

- **Spec coverage:** Tier 0 arm-by-proxy-distance → R1 `arm_subset` + R2 `_fast_arm_subset`/`_scan_cycle`
  call. Tier 1 batch-poll → R2 `_fast_batch_prices`. Dip-from-samples → R1 `rolling_dip_pct` + R2 tick.
  Tier 2 escalate (reuse `_evaluate_pair` threading) → R2 tick ctx (unchanged threading). Volatility
  reserve → R1 `arm_subset`. Armed-hit-rate gate → R3 + R5. Config (all Rev-2 flags) → R1 `from_env`.
  Drop Axiom → R2 (remove `_fast_trend`/subscribe/get_tick_trend) + R4 grep. Default-off inert / shadow
  no-money / byte-identical-off → preserved from Rev-1 (run() spawn + threading untouched) + R2 tick
  empty-armed/mode gating + R5.
- **Placeholder scan:** none — every code step has complete code + exact commands.
- **Type consistency:** `FastWatchConfig` Rev-2 fields, `arm_subset(candidates, cfg)→[addr]`,
  `rolling_dip_pct(samples)→float|None`, `shortlist(..., get_trend(addr), ...)`, `_fast_arm_subset(cfg,
  now_ms)`, `_fast_batch_prices(addrs)→{addr_lower:price}`, `self._fast_armed`/`self._fast_samples` are
  used identically across R1–R3. The Rev-1 `_fast_route_decisions`/`_fast_path_*` signatures are reused
  unchanged.
