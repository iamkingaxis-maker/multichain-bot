# Adversarial Code Review ROUND 2 — 2026-07-12
Scope: everything after aa2ffc9 (round 1 baseline): ae724f8 (LIVE, ledger rotation),
81fb233 (RH live exec + lane supervise), 967726b (RH rug stamps), 7708e20 (RH regime v1),
ff840aa + 165232a + f0f9be1 (LIVE rug-gate branch 2 / promotions / allowlist),
plus late additions 70a870e (young 03-08 lift) and ee8a899 ($22.5 base — LIVE MONEY check).
Method: round-1 discipline — diff read + surrounding code; every defect has a concrete,
code-verified failure scenario. No commits, no deploys, no Railway changes.

## LIVE-MONEY sizing verification (ee8a899) — VERIFIED CORRECT
The next live buy for all 3 bots WILL fire ~$22.50. Full chain audited:
- `base_position_usd` IS the field the sizing path reads: `BotEvaluator._size_for`
  (core/bot_evaluator.py:1494-1563) -> `decision.size_usd` (line 1120/1128) ->
  `_execute_bot_buy` `_used_size = decision.size_usd` (dip_scanner.py:3435) ->
  `_execute_bot_buy_live(size_usd)` -> `_usd_to_sol(size_usd)` -> lamports. No other
  sizing source on the live path.
- Every lift-back path is neutralized IN THE SHIPPED CONFIGS (all three):
  * `alpha_multiplier: 1.0`, `premium_runner_multiplier: 1.0`, `marginal_multiplier: 1.0`
  * `compound_mode` / `macro_conditional_mode` / `conviction_sizing_mode` ABSENT (None -> skipped)
  * the P7 regime dial (incl. the 1.5x live-set OFFENSE upsize) is skipped outright:
    all three set `regime_dial_exempt: true` (bot_evaluator.py:1541 gate)
- Remaining modifiers are DOWNWARD-ONLY: `adaptive_swing_size` (rt only; multiplier
  applied only when < 1.0, dip_scanner.py:3451), pool derates (flag off on all three),
  LIVE_PER_TOKEN_MAX_USD=60 fleet cap, canary/daily-loss halts.
- Env name check: code reads exactly `LIVE_PER_TOKEN_MAX_POSITIONS` (dip_scanner.py:3540)
  and `LIVE_PER_TOKEN_MAX_USD` (3541) — the Railway names match.
- Whole-fleet sweep: exactly 3 configs are enabled+live_probe (absorb/rt/vsnap_ab), all at
  22.5 and hours 0/24; every other live_probe config is `enabled: false`. All 151
  config/bots/*.json load clean through BotConfig.from_json strictness (no other
  unknown-field landmines).

## 70a870e (young 03-08 lift) — hour semantics clean, ONE regression found
Exactly two fields per config changed: `trading_hour_utc_start/end` 8/3 -> 0/24 in the 5
young configs (+ scratchpad artifacts only). (0, 24) is the special-cased 24/7 branch in
`_is_off_hours` (bot_evaluator.py:1186) — no empty-window/mod-24 trap. Old (8,3) wrap
semantics confirmed = block 03-08. swing_latch/wickride keep (8,3) by design.
**R2-5 (regression, shipped red at HEAD)**: 70a870e lifted `badday_young_rt_paper` but
NOT its pinned byte-twin `badday_young_moonbag_ab` — `tests/test_moonbag_exit.py::
test_jersey_integrity` FAILED at 70a870e..ee8a899 ("unexpected twin drift:
{trading_hour_utc_start, trading_hour_utc_end}"), i.e. those commits shipped with a red
suite (the same `pytest | pipe` lesson f0f9be1 wrote down). RESOLVED at HEAD: 7a808ce
lifts moonbag_ab to 0/24 and restores twin parity (semantically right — the twin's only
sanctioned diffs are the moonbag fields). No action left; recorded because two LIVE
commits sat on a red suite for ~14 hours.

## 7a808ce (post-TP1 fast-watch, LIVE exit path, held for this review) — CLEARED with one fix applied
Attack results on the five named angles:
1. **Clone-tick mutating real state**: NO. The confirm ticks run on a `copy.deepcopy` of
   the position inside a FRESH `PerBotPositionManager` (init verified pure in-memory,
   no registration/disk); `_ohlcv_capture` forced off on the clone; tier flags/shadow
   stamps burn only on the throwaway snap. The ONE deliberate real-state write is the
   `confirmed_peak_ratchet` (raises `p.peak_pnl_pct` only when ALL newest confirm
   samples exceed it, takes the MIN — monotone, glitch-guarded; the intended eff_peak
   feature; it also tightens the MAIN sweep's trail line, which is the point).
2. **Double-fire race fastwatch vs main sweep**: REAL residual window, and 7a808ce makes
   it reachable BY DEFAULT for the first time (exit_reprice/trail_reprice default off;
   POST_TP1_FASTWATCH defaults ON). TP1/TP2 are safe (tick sets the tier flag at
   emission), but trail/stop/moonbag carry no once-only flag: fastwatch fires
   POST_TP1_TRAIL -> awaits the live swap (~2s) -> the slow sweep (separate coroutine)
   ticks the same open position -> re-emits the same kind -> concurrent duplicate
   `_execute_bot_sell`. Existing DONALT guards bound the damage (stale-balance 1.5x,
   pre-settlement, exact-in second swap reverts on-chain), leaving a reverted dust tx
   or a rare booking-basis error (phantom "empty" paper close winning the race over the
   real fill's booking). **FIXED (applied)**: `_execute_bot_sell` is now a per-
   (bot_id, token) serialization shim over `_execute_bot_sell_inner` — a concurrent
   duplicate is SKIPPED (dedupe, never a gate: the position retries next tick);
   try/finally releases unconditionally so a wedged key can never block a position's
   exits (07-10 class). Covers ALL callers (main sweep + all four fast paths).
   Tests: tests/test_sell_serialization.py (duplicate skipped; other bot/token never
   blocked; sequential re-sell runs; key released even when the inner raises).
3. **FIFO cap starving the highest-value position**: the cap keeps the OLDEST
   enrollments — under overflow (>10 post-TP1 positions) the NEWEST post-TP1 positions
   (largest immediate giveback exposure) are the ones deferred to the slow sweep.
   Fleet holds ~3 post-TP1 concurrently vs cap 10, and overflow positions keep full
   slow-sweep protection, so documented-only. Sub-edge: a position whose `state_blob`
   is None re-stamps `now` as its enrollment each tick -> always sorts newest -> is
   starved first under overflow (cosmetic).
4. **Enrollment vs positions closed mid-tick**: safe — the real `pm.tick(token)`
   re-fetches from `_positions` (returns [] if closed during the step-3 await), the
   stale snapshot only feeds throwaway clones, and `close_position` clamps to
   remaining + raises KeyError on a gone position (caught per-position). Corner case
   documented: close-and-reopen of the same token within one ~2s fast tick would run
   the real tick on the NEW position while the confirm validated the OLD one — the
   fired rules are still the new position's own, and re-entry cooldowns make the
   window practically unreachable.
5. **kwarg widening**: `exit_cadence` added LAST with default "main"; every legacy call
   site passes exactly 5 positionals (grep-verified: dip_scanner 4877/6427 + tests);
   the fast paths pass it explicitly; test stubs updated in the commit; suite green.
Also verified: prices dict lowercase-keyed vs `_fast_samples` original-case keyed —
matches the existing reprice-path convention (donalt test pins it); hook only runs
inside `_fast_watch_tick` (inert when FAST_WATCH_MODE=off, as claimed); consecutive
fast ticks are awaited serially (no self-overlap) and `_ptfw_inflight` guards the
fastwatch against its own mid-await re-fire.
**Deploy note**: with the serialization shim in the tree this commit is CLEAR to
deploy; without the shim, deploy only with POST_TP1_FASTWATCH=off.

## Confirmed defects

| # | Defect | Failure scenario | Severity | Status |
|---|--------|------------------|----------|--------|
| R2-1 | **Ledger rotation folds PRE-reset P&L back into /api/leaderboard** (ae724f8, LIVE). `_rotate_ledger._count` had NO `reset_after_iso` filter, while the dashboard drops a reset bot's pre-reset rows per-row (web_dashboard.py:4580-4582). `ledger_stats.sell_stats`'s fold-skip only handles a reset NEWER than the whole archive; its docstring claim "(Rotation itself already excludes rows predating a reset...)" was FALSE, and test_sell_stats_reset_after_iso_skips_stale_archive pinned the false premise. | Bot reset at R with history both sides of R; once rows >21d span R, rotation aggregates BOTH sides; latest archived > R -> fold applied -> the pre-reset P&L re-enters the authoritative leaderboard total. Silent, permanent until fixed (stats re-derive each rotation, but always unfiltered). | data-corruption of the authoritative P&L (activates for any bot using dashboard reset) | **FIXED**: rotation reads `reset_after_iso` from the bot_state files it already globs; `_count` skips pre-reset sells (latest_time semantics unchanged, so the sell_stats skip rule still works). Stats self-correct at the next real rotation (re-derived from the archive with the filter). Test: `test_rotation_respects_bot_reset_after_iso` (end-to-end identity vs the dashboard's math). |
| R2-2 | **Go-live allowlist invariant could pass VACUOUSLY** (f0f9be1's own guard). `test_no_enabled_live_probe_bot` globbed `config/bots/*.json` CWD-relative with no non-empty check. | Run pytest / the standalone runner from any directory other than repo root -> glob matches ZERO files -> zero offenders -> the deploy gate is green without scanning anything. This is THE pre-deploy invariant that catches accidental live bots. | live-money guard (latent, invocation-dependent) | **FIXED**: glob anchored to repo root via `__file__` + `assert cfg_files` (an empty scan now fails loudly). |
| R2-3 | **`_trade_sig` collides two REAL same-second ladder legs** (ae724f8). Signature omitted `reason`/`exit_price`; two sell slices of one position filled in the same second with equal pnl/size produce identical sigs. | Both legs enter the archive; the next boot's per-line dedup counts ONE -> stats drop a leg's pnl, and totals differ between the first boot (counted at append time: both) and every later boot (deduped: one). If one copy is in the archive and its twin still in base, the base twin is silently DELETED as a "crash leftover". | data-corruption (rare-trigger, small $) | **FIXED**: sig now includes `reason` + `exit_price`. Safe by construction: a true crash duplicate is a byte-identical copy, so added fields only reduce false dedup; existing on-volume archives still dedup correctly (sig derived at read time). Test: `test_trade_sig_distinguishes_same_second_ladder_legs`. |
| R2-4 | **Silent session-long leaderboard double-count when the post-rotation base rewrite fails** (ae724f8). `_ensure_trades_loaded`'s rewrite was `except Exception: pass`. | Rotation appends+fsyncs archive and WRITES STATS, then `_atomic_write_stream` fails (disk full/transient): archived rows remain in the base AND in the stats fold -> every leaderboard read double-counts them for the whole session, zero log evidence. Self-heals at next boot (sig dedup). | data-corruption (transient, self-healing, previously invisible) | **FIXED** (visibility): failure now logs ERROR naming the double-count consequence. Behavior unchanged (still fail-open). |

## Attack angles verified CLEAN (no defect)

### ae724f8 — rotation / trim / compaction
- **Concurrent writers during boot rotation**: exactly ONE MultiBotTradeStore in prod
  (main.py:485; the dashboard shares it, main.py:602). `record_trade` calls
  `_ensure_trades_loaded()` first, and rotation runs inside `self._lock` before the
  sidecar append takes the same lock — a fill during boot rotation blocks, then lands in
  the (post-truncation) sidecar. The two-instance sidecar-truncation race exists only in
  tests, not prod wiring.
- **Symbol-vs-address keying**: rotation groups by `(bot_id, token)` = SYMBOL. Two mints
  sharing a symbol merge groups — but strictly CONSERVATIVE for no-straddle (more rows
  kept), and the stats key `(bot, token, entry_price)` mirrors the dashboard's own
  `(token, entry_price)` position key exactly, so fold == active math even under symbol
  collision. Not a defect; the same-symbol class does not bite here.
- **Crash between archive-append and stats-write**: archive is fsynced first; next boot
  re-derives stats from the archive and drops base twins by signature — counted exactly
  once. Pinned by the existing crash-leftover test.
- **LEDGER_APPEND_MODE interaction**: rotation runs ONLY in `_ensure_trades_loaded`
  (append-mode path). Railway runs append-mode=on, so it fires; with append mode OFF,
  rotation is silently dormant (no memory cut) but stats stay consistent because a
  previously-rotated base + stats fold still add up. Documented, not a bug.
- **`_trim_entry_meta`**: cache-only, disk lossless verified — non-append `record_trade`
  re-reads the FILE before rewriting (never the trimmed cache); keep=6000 covers the
  /api/trades?full=1 5000-row cap; live_faithful's two booleans whitelisted.
- **Trailing-21d consumers**: live_faithful "full history" now means trailing-21d for
  rows >21d old (they leave the base entirely — the meta whitelist preserves only what
  is still loaded). Boot daily-pnl re-derive (today only), restore_positions (bot_state),
  follow_capital reconcile (recent), /api/race (7d) are all inside the window.
  Research scripts reading trades_multi.json directly are trailing-21d views (as the
  commit declares); the archive stays on disk for full-history pulls.
- `/api/leaderboard` -> `_build_bot_rows` -> sell_stats fold: single aggregation path,
  fold wired where it matters.

### 81fb233 — RH live execution (dormant) + lane supervise
- **Triple-gate bypass hunt**: every money path (`live_buy`/`live_sell`) goes through
  `_require_live()`; an injected paper-only executor is rejected; self-built executor
  needs the env key the gate already requires; `rh_wallet_rebase` is manual-only and
  read-only wrt funds. No bypass found.
- **Gas cap actually intercepts every send**: `RhExecutor._sign_and_send` has exactly two
  callers — `_ensure_allowance` (approve) and `_execute_and_record` — and BOTH build via
  `_build_tx`, which `GasCappedExecutor` overrides (cap enforced pre-sign, incl. the
  1inch-routed call, whose calldata still flows through `_build_tx`). Cap=0/garbage env
  fails CLOSED (refuses every tx).
- **Canary flag races**: single writer (the lane) + atomic temp/replace writes; readers
  re-evaluate `healthy(now)` from state so a stale writer can't pin healthy; missing
  flag file fails CLOSED after the 180s module grace; wedged-loop staleness (4x interval)
  fails CLOSED. Two-process flapping would only intermittently HALT buys (fail-closed
  direction).
- **Daily-pnl fail-closed claim**: verified — `today_usd` returns None ONLY when a state
  file exists but is unreadable and no in-memory copy exists; `buys_halted` halts on
  None. Fresh deploy (no file) correctly reads 0.0. Minor TOCTOU (file deleted between
  read and exists-check reads as fresh) noted, not material.
- **Wallet-truth baseline arming**: arms only while the triple gate is open; balance-read
  errors never touch the baseline and never emit a stale/zero number. Two edges noted
  below (notes 3-4).
- **Supervise loop (main.py:40-57)**: uploader Popen is BEFORE the while loop — exactly
  one uploader per container boot, no accumulation across lane restarts; `_sp.run` waits,
  so lanes never overlap. 10s pause bounds a crashloop to ~8.6k spawns/day (acceptable
  for paper); an import-time crashloop never exits the parent, so Railway won't flag the
  service — see note 5.

### 967726b — rug-defense stamps
- **Thread safety vs the lane main loop**: the stamper uses a DEDICATED `Rpc` object
  (never the feed's), all RPC behind `_rug_lock` single-flight; shared-state writes
  (`_rug_cache`) are GIL-atomic dict ops. `_ledger_ts`'s unlocked `+= 1` is cross-thread
  reachable, but the dashboard dedup key is (ts, ev, pool) and the stamper's ev differs
  from trade rows — a seq race cannot merge rows. `_append` is one write() per line on
  O_APPEND handles.
- **Caps bound**: 90s wall budget checked per chunk (can overshoot by ONE in-flight call),
  60k-log cap checked per chunk (can overshoot by one <=10k chunk) — bounded, on a daemon
  thread, zero entry latency. Missing lock-holder cache re-check means two same-pool
  threads can compute twice (wasted RPC only).
- **Ledger-row format**: `ev="rug_signals"` rows carry ts+ev+pool -> ingest dedup key
  works; `merge_rh_paper_rows` accepts any ev; summary counts only buy/sell so stats are
  untouched. Rows do surface in the dashboard's raw last-N table (cosmetic).

### 7708e20 — regime v1
- **CompositionTracker bounds**: deque pruned on every ingest to the 30-min window;
  `_pool_n` decremented to eviction. Bounded by window flow. Float drift cosmetic.
- **Aged 19-21 gate scoped BY TEST**: `test_scalp_fleet_unchanged` asserts
  `regime_hours is False` for all 9 scalp racers + launch_scalp;
  `test_regime_v1_blocks_aged_band_in_19_21` / `..._mid_band_passes...` pin the
  behavior end-to-end; the gate itself blocks only `age_band == "aged"` so even a
  misflagged scalp racer couldn't lose young entries. Verified in tests, not comments.
- **Stamp cost**: pure dict shaping + O(1) snapshot per entry. No RPC.

### ff840aa + 165232a + f0f9be1 — rug gate branch 2 / promotions
- **Branch precedence/reasons**: independent signals; reasons append in stable order
  (LP-unlock, branch 1, branch 2); both hidden branches can co-fire (both reasons
  recorded); BLOCK on any. bebu/ANSUM arithmetic re-checked (88.4/2057 and 82.0/1194
  pass branch 1, caught by branch 2). NEUTRAL only when both signals' inputs absent —
  unchanged posture.
- Env-misconfig hazard (not a bug): `RUG_GATE_HIDDEN2_MIN=0` would block nearly every
  young token (same class as round-1 note 2 — a startup log of parsed gate config is
  still the cheap fix).

## Notes for the main session
1. **Existing deployed rotation stats**: R2-1's fix re-derives the archived aggregates at
   the NEXT rotation event (any boot where rows age past 21d, or any crash-leftover dedup).
   Until then a stats file built for a reset bot (if any bot currently has
   `reset_after_iso` set with >21d pre-reset history) may still carry the unfiltered sum.
   One redeploy/restart after rows age is enough; no manual surgery needed.
2. **Leaderboard live-truth override is now inert**: the 2026-07-07 on-chain wallet-delta
   correction applies only when EXACTLY ONE enabled live_probe exists
   (web_dashboard.py:5113). With 3 live bots it no-ops — live rows on /api/leaderboard
   are back to simulated ledger P&L. Wallet-truth delta remains the only honest live
   number (standing memory rule).
3. **RH wallet-truth arming while unfunded**: if the triple gate is opened before funding,
   the baseline arms at ~0 and the later deposit reads as +delta "trading" P&L. The
   runbook already orders fund-first; keep it that way (or rebase after funding, as done
   on Solana 165232a).
4. **rh_wallet_truth ok+error combo**: a malformed baseline file (missing `total_eth`)
   raises AFTER `ok: True` is set — the status JSON then carries ok=True AND error, no
   delta. Consumers should treat `error` as authoritative. Cosmetic; left unfixed.
5. **Supervise loop can mask a permanent lane crashloop**: the parent never exits, so
   Railway sees a healthy service while the lane crash-restarts every 10s (uploader keeps
   pushing stale ledger). If the lane ever ships a boot-time crash, the only tell is the
   log. A cheap improvement: exponential backoff + a stderr banner every N restarts.
6. **Standalone pre-live runner currently FAILS locally on the decimals live-RPC check**
   (`So111...`: expected 9, got 6) amid visible 429 throttling — environmental (throttled
   RPC fallback), not caused by any reviewed commit; the pytest-collected subset passes.
   Re-run from a clean network before any live deploy (the runner's exit-1 is doing its
   job).
7. `_rug_cache` in the lane is unbounded per process (bounded in practice by the 300-min
   lane lifetime); `_get_logs_budgeted` can overshoot its budgets by one chunk. Both
   cosmetic, documented here.
8. Working tree contains ANOTHER workstream's in-flight, uncommitted change I did not
   review or touch: scripts/rh_chain_feed.py cold-start audition fixes + its tests
   (tests/test_rh_chain_feed.py, "2026-07-12 seed/burst/recheck ladder"). Its tests pass
   in the full-suite runs reported here.

## Fixes applied (working tree only, no commits)
- core/multi_bot_persistence.py — R2-1 reset filter in `_rotate_ledger._count` (+resets
  map from the bot_state glob it already does); R2-3 `_trade_sig` +reason/+exit_price;
  R2-4 loud ERROR log on post-rotation base-rewrite failure.
- tests/test_pre_live_invariants.py — R2-2 repo-root-anchored glob + non-empty assert.
- tests/test_ledger_rotation.py — `test_rotation_respects_bot_reset_after_iso` (end-to-end
  identity for a reset bot), `test_trade_sig_distinguishes_same_second_ladder_legs`.
- feeds/dip_scanner.py — 7a808ce follow-up: per-(bot_id, token) sell serialization shim
  (`_execute_bot_sell` -> guard -> `_execute_bot_sell_inner`); duplicate concurrent
  exits deduped, never gated; unconditional release.
- tests/test_sell_serialization.py — new (2 tests, 4 properties pinned).
- tests/test_post_exit_tracker.py — source-guard updated for the shim (now checks the
  sell body `_execute_bot_sell_inner` AND that the shim delegates to it).

Full suite after ALL fixes: **2721 passed, 2 skipped, EXIT CODE 0 checked DIRECTLY**
(never a pipe), with ONE deselect that is NOT mine: the concurrent rh_chain_feed
cold-start workstream added `TestLiqSeed::test_shipped_seed_file_is_valid` to the
working tree MID-REVIEW and its artifact (data/rh_liq_seed.json) does not exist yet
(their test also joins "config/rh_liq_seed.json" but the error shows "data/…" — their
in-flight inconsistency to resolve). Everything the reviewed commits + my fixes touch
is green. Exit-path battery (donalt/booking-fidelity/slip/partial-burn/corpse/exit-arm/
reprice/moonbag/post-tp1-fastwatch + new serialization): 158 passed.
(One source-guard, test_post_exit_tracker::test_full_close_queues_pending, was updated
for the shim — it now checks the sell BODY and the shim's delegation, so its intent is
strictly stronger.)
