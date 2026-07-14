# FULL-SYSTEM VERIFICATION SWEEP — 2026-07-05 (go-live decision day)

Directive: AxiS — "if you are finding failures and bugs, the entire system needs
to be checked." Scope: full test suite + money-path focus + env-leak hunt +
static scan of production for the NameError/dead-local class. Production code
NOT touched; test files and harnesses fixed and committed.

## Headline

- BEFORE: `python -m pytest tests/ -q` -> **38 failed, 1969 passed**
- AFTER:  `python -m pytest tests/ -q` -> **2009 passed, 0 failed** (35s)
- Money-path suites: green in isolation AND together, forward and reversed
  order (no order-dependence remains on the live path).
- Production bugs found (class C): **1 family (large)** + several benign
  dead-locals. NONE on the live execute/money path; none can crash the buy
  or sell path (all are swallowed fail-open). Details below.

## Triage of the 38 failures

### Class A — stale test harness (production correct, test outdated): 22 tests, all FIXED

1. **`_addr_by_token` plain dict vs production LRU `OrderedDict`** — 16 tests
   across test_no_fast_price_gate (5), test_paper_fidelity_wire_integration (5),
   test_paper_token_cap (5), test_shared_token_registry (1).
   Production: `feeds/dip_scanner.py:598` initializes `OrderedDict`; buy path
   line 2930 calls `.move_to_end()` (LRU eviction, `_addr_by_token_max=20000`
   cap at ~L23915). Tests built DipScanner via `__new__` and stubbed `{}`.
   FIX: all 11 test files that stub `_addr_by_token` now use `OrderedDict`
   (incl. 4 files that were passing only because their path never reached
   L2930 — future-proofed).
2. **`_exit_price_guard_ts` missing from harness** — surfaced behind #1 in
   test_paper_fidelity_wire_integration + test_symbol_collision_pricing.
   Production inits it in `__init__` (dip_scanner.py:613); buy path stamps it
   (L3073/L3343). FIX: harnesses now stub it alongside `_exit_price_guard`.
3. **FILL_CALIBRATION changed the booked paper fill** — 2 tests in
   test_paper_fidelity_wire_integration expected the fixed placeholder slip
   (`measured_live_slip_pct`), but production (2026-06-22, dip_scanner
   ~L14120ff of buy path) books calibrated slip learned from live_swaps.jsonl
   (repo data => data-dependent test). Documented off-switch exists
   ("off => byte-identical placeholder"). FIX: test env pins
   `FILL_CALIBRATION_ENABLED=off` + `ULTRA_FEE_MODEL=off`.
4. **test_live_per_token_cap (2)** — NOT a regression. Production semantics
   changed 2026-06-21 (documented in `_live_token_exposure` docstring,
   dip_scanner.py:1332): the LIVE cap counts ONLY live-routing bots
   (config.live_probe AND USE_JUPITER_ULTRA AND private key), because paper
   bots piling a mint used to phantom-block the live bot (OGFLOKI). The old
   harness had no config/trader so the counter correctly returned (0,0).
   **The DEGEN-x198 guard is intact.** FIX: harness builds a live-routing
   fleet (live_probe + key + Ultra pinned via monkeypatch) and I ADDED two
   regression tests: paper bots not counted; no-key counts nothing.
5. **test_bot_catalog::test_champion_proposal_is_enabled_synthesis** —
   champion_proposal disabled by the paper-fleet cost trim (commit 6a3c955,
   65 bots disabled, configs preserved). FIX: assert `enabled is False`,
   knob assertions kept.
6. **test_bot_catalog::test_all_base_position_20** — patient_sleeve created
   at $100 base (commit 62dcae6, the $100-bet era). FIX: added to EXEMPT.
7. **test_patient_slot_ab_config** — badday_flush_patient_slot_ab RETIRED
   (commit a603628). FIX: assert `enabled is False`, lever assertions kept.
8. **test_fw_stats** — production `_fw_record_tick` (dip_scanner.py:6475) now
   stamps tiered-poll `hot`/`full` counters into last_tick. FIX: expected
   dict updated.

### Class B — cross-test contamination (order-dependent): 16 tests, all FIXED

1. **test_dashboard_auth (12)** — `_run` used `asyncio.get_event_loop()`;
   on Python 3.12 ANY earlier test that called `asyncio.run()` leaves the
   main-thread loop unset -> RuntimeError mid-suite, passes alone.
   FIX: `_run` now uses `asyncio.run()`.
2. **test_onchain_reconcile (4 supervisor tests)** — leaker found by bisection:
   **tests/test_exhaustion_realtime.py:74** did
   `pm_mod.asyncio.ensure_future = _fake_ef` — `pm_mod.asyncio` IS the global
   asyncio module, so the fake (closes the coroutine, returns None) replaced
   `asyncio.ensure_future` PROCESS-WIDE for every later test. The reconcile
   supervisor's tasks never started. FIX: autouse fixture in
   test_exhaustion_realtime restores the real ensure_future after each test.
   (Audited for siblings: test_bot_manager patches `bm.asyncio.sleep` but
   restores in a finally — clean.)
3. **Global env-leak guard (task 5)** — beyond the already-committed
   test_fastwatch_live_invariants fix, raw `os.environ[...] =` writes without
   cleanup exist in test_egress_throttle (EGRESS_*), test_follow_capital
   (DATA_DIR->deleted tempdir!, SMART_FOLLOW_POOL_USD), test_slug_cache_persist
   (DATA_DIR, SLUG_CACHE_PERSIST), test_smart_follow_tiers, test_pre_live_
   invariants (DATA_DIR), and pop-at-end-not-in-finally patterns in
   test_probe_bridge / test_regime_buy_gate. Rather than whack each, NEW
   **tests/conftest.py** adds an autouse fixture that snapshots the ENTIRE
   os.environ before each test and restores it after — the whole leak class
   is dead. (Verified safe: the only module-scoped fixtures in the suite —
   test_bot_catalog's — don't write env.) Full suite green with it.

### Class C — REAL production bugs (NOT fixed, report only)

**C1. Dead mined triggers + a dead protective filter in `_evaluate_pair`
(feeds/dip_scanner.py, function spans L7961-23260) — ~83 always-NameError
sites, swallowed by per-block `try/except`.**

Mechanism: mined predicates were shipped referencing dataframe column names
that are NOT locals in the scanner — `bs_h6`, `bs_h1`, `_1s_features`,
`volume_velocity_features`, `entry_meta_dict`, `entry_meta_local`,
`chart_features`, `tier3_features`, `ones_features` are never bound anywhere
in the function or module scope (verified: the real locals are `ratio_h6`/
`ratio_h1`; tier-3 carries `bs_h6` only as a dict key). Pyflakes confirms
113 undefined-name sites; my classifier: **0 naked** (crash-capable),
83 swallowed by try/except, 28 guarded by `'name' in dir()` (always-None).

Consequences (silent, fail-open — no crash risk):
- The mined trigger families around L14735-15467 (midcap_quality_accumulation,
  the overnight `_ovn`/`_3d_*`/`_fd_*` cohorts), the `_1s_features` trigger
  cluster (L15759-17509), and sweep_holder_liq (L16838) raise NameError on
  their first line and NEVER FIRE. Any WR/edge numbers in their comments were
  never realized in production.
- **filter_microcap_trap (F5), dip_scanner.py:19993** — a protective BLOCK
  filter (bs_h1 1.0-1.4 + mcap $0.5-5M + thin liq trap; "100% precision,
  0% winner-block") NameErrors on `bs_h1` -> verdict is permanently PASS.
  A blocker the rulebook thinks is live is dead.
- filter_sat_eve_midliq (L19948 region) and the L19993 block share the
  pattern via `liquidity_usd` (dir()-guarded fallback to `pair` partially
  rescues liq but `bs_h1` still kills F5).
- The trigger-level loss-cohort suppression at L17545/17579 references
  `entry_meta_dict` (never built) — suppression dead (one site even
  hard-codes `if False`).
- Cosmetic: `hour_ct`/`mcap` in chart-CNN context (L11108) and btc_features
  in tier10 (L18816) are dir()-guarded -> always None.

LIVE money path impact: **none direct.** All sites are inside entry
SELECTION (`_evaluate_pair`); zero naked NameErrors; the execute/buy/sell
paths are clean. Indirect impact: live bots select from a stream where some
mined protections/triggers silently don't exist — the fleet's MEASURED paper
performance already includes this, so go-live economics are unaffected;
but the wallet-decode->trigger pipeline has a systematic transcription gap
(mined feature names vs scanner locals) worth a dedicated fix pass with
tests. Recommend: a harness that asserts every `_trigger_*`/`_filter_*`
block can bind its names (would have caught all 83).

**C2. Benign dead-locals (not bugs, noted for completeness):**
- core/position_manager.py:782 `closed_state` unused (peak-recorder finalize
  doesn't take it) — cosmetic.
- core/position_manager.py:1915 `_dud_peak` computed for the RETIRED fast_dud
  exit (its consumer is commented out 2026-05-17) — cosmetic.
- core/meta_chameleon.py:330/846 `Dict` unimported — harmless because
  `from __future__ import annotations` (annotations never evaluated).
- multi_source_scanner.py:3459 `now` unused; regime_dial.py:82 `y_sells`
  unused; several unused locals in scanner/pool_price_feed — cosmetic.

### Class D — environment/platform: none

No cp1252/unicode failures, no missing deps (pyflakes installed for the scan
only). The 4 remaining warnings are upstream deprecations (aiohttp bare
functions; pytest-asyncio loop-scope notice).

## Money-path focus (task 4)

Isolation runs (each `-p no:cacheprovider`): test_pre_live_invariants (8),
test_probe_bridge (17), test_fastwatch_live_invariants (9),
test_exit_slip_escalation (6), test_buy_reprice (10), test_fill_calibration
(24), test_live_swap_log (12), test_live_swap_wiring (5),
test_live_funding_shadow (14), test_live_exec_latency_optA (5),
test_profit_sweeper (41) — ALL PASS.
Together forward order: 151 passed. Together REVERSED order: 151 passed.
Adjacent live/swap/probe suites (exit_reprice, exit_slip_liq, fill_probe,
fill_speed_*, jupiter_ultra, live_faithful_pnl, live_per_token_cap, live_pnl,
low_mcap_probe, paper_live_reconcile, paper_slippage_reconciled, probe_config,
probe_instrument, profit_sweep_sim, reprice_all, slippage_model, tp1_fastfill,
trail_reprice, young_token_probe): 191 passed together.
**No order-dependence in money-path tests.**

## What was fixed (all test-side, committed; no production code touched)

- 11 harness files: `_addr_by_token` -> OrderedDict (+2 files: `_exit_price_guard_ts`)
- test_paper_fidelity_wire_integration: pin FILL_CALIBRATION/ULTRA_FEE off
- test_live_per_token_cap: rebuilt for 2026-06-21 live-only semantics + 2 NEW
  regression tests (paper exclusion, no-key)
- test_bot_catalog (2), test_patient_slot_ab_config, test_fw_stats: updated
  to current intentional production state (commits 6a3c955/62dcae6/a603628
  verified as the source of each change)
- test_dashboard_auth: asyncio.run (py3.12 loop semantics)
- test_exhaustion_realtime: autouse restore of asyncio.ensure_future
  (the process-wide monkeypatch leak)
- NEW tests/conftest.py: global per-test os.environ snapshot/restore

## GO/NO-GO input (test-health perspective)

**GO — with one caveat.** 2009/2009 green, live-path suites green in every
order tried, the live per-token exposure cap verified intact under its
current (correct) semantics with new regression coverage, and the static
sweep found ZERO crash-capable (naked) undefined names anywhere in
core/feeds/dashboard — the NameError-on-`now` class appears fully flushed
from the execute path. The caveat: the C1 dead-trigger/dead-filter family
means some mined entry protections (notably filter_microcap_trap) are
silently inactive; live probe economics already reflect this (paper measured
the same dead state), so it does not block tonight's $25 probe, but it
should be scheduled as its own fix+test pass.
