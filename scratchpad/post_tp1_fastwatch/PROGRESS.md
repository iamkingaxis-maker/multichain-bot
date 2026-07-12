# POST-TP1 FAST-WATCH — build progress

Task: enroll post-TP1 remainders in a high-frequency exit watcher (fresh reads ->
SAME ExitDecision logic), env kill switch, cap, TTL, fail-open, cadence stamp.
Working tree only — NO commits, NO deploys. Main session reviews and ships.

## Provenance (the measured estimate)
Source artifact: `scratchpad/_family_spec.md` (2026-07-01 family re-mine), item #2:

> **#2 — Fast-watch cadence on HELD post-TP1 winners (BUILD + ENFORCE — fidelity,
> not a strategy bet).** Evidence: 393 trail closers book peak-7.29 avg vs peak-2.0
> config; decomposed 2.0 config + 3.42pp fired-below-line (scan-cadence, median
> 2.21 = systematic) + 1.87pp decision→fill. Realistic recovery ~+300-450
> token-pp/8.5d = 15-20% of the entire book loss, zero volume cost, zero selection
> risk. Change: route open post-TP1 positions (max ~3 at a time — cheap) onto the
> ~2s fast-watch loop for trail checks.

Haircut note (`scratchpad/_giveback_lever_report.md` §Q4): the +300-450 headline is a
POOL; measured reprice slip/gap-throughs shrink the defensible net to ~15-150 pp/wk.
Forward grade (below) measures the real number.

## What already exists (found in research)
- `core/fast_watch.py:trail_reprice_would_fire` (TRAIL_REPRICE, 2026-07-01) — post-TP1
  trail on fresh samples, confirm_ticks wick guard. Gated by TRAIL_REPRICE_MODE —
  **unset on Railway => OFF (dormant)**, badday_*-scoped, trail-only (no TP2/stop).
- `feeds/dip_scanner.py:_reprice_trail_exits` (~7846) — the scanner hook, incl. the
  "opens-union" fresh-price batch for held winners not in the armed set.
- `_reprice_exit_floors` (EXIT_REPRICE, enforce in prod — pre-TP1 only) and
  `_reprice_tp1_fastfill` (off in prod) — same pattern, same fail-open discipline.
- Clone-tick pattern (deepcopy position -> throwaway PerBotPositionManager ->
  clone.tick) at dip_scanner ~5171 and ~7658 — re-runs the EXACT exit rules on a
  fresh price without mutating live state.
- pm.tick post-TP1 decision set: TP2 (tp2_hit set PRE-sell — burn-sensitive),
  POST_TP1_TRAIL (incl. peel/scaled/run-winners variants), HARD_STOP,
  MOONBAG_FLOOR/TRAIL. vol_m5_usd only gates PRE-TP1 exits (pre_stop_bail) —
  passing None post-TP1 is behavior-identical.
- Sell path: `_execute_bot_sell` (~6452) is the SINGLE shared paper/live sell entry
  (live routes via should_route_live inside). Sell record dict ~6856 carries shadow
  stamp surface (trail_reprice_shadow_* etc. at ~6900).
- Prod env (scratchpad/_rw_env.txt): FAST_WATCH_MODE=enforce, interval 2s,
  EXIT_ARM_MODE=shadow, TRAIL_REPRICE/TP1_FASTFILL unset (off).

## Design (decided)
NEW hook `_post_tp1_fastwatch(cfg, prices, now)` in the fast tick (runs ~2s), after
the existing reprice hooks. NO new exit rules: fires by calling the REAL
`pm.tick(token, fresh_price, now)` and executing its own ExitDecisions through the
SAME `_execute_bot_sell`. Details:
- Enrollment: derived per tick = ALL bots' open positions with tp1_hit (any family,
  paper or live; no badday_* scope). First sighting stamps state_blob
  `ptfw_enrolled_ts`/`ptfw_enrolled` (persists via bot_state round-trip; restart-safe).
  Eviction on close is automatic (position gone).
- Cap: POST_TP1_FASTWATCH_MAX (default 10), FIFO by enrolled_ts (pure helper
  `select_post_tp1_watches` in core/fast_watch.py). Overflow positions stay on the
  main-scan cadence (natural controls for the grade).
- TTL: POST_TP1_FASTWATCH_TTL_SECS (default 0 = position lifetime). >0 => watch
  expires, falls back to main cadence.
- Fresh reads: reuse the opens-union pattern (<=50-id `_fast_batch_prices` for
  enrolled addrs missing from this tick's poll; append to `_fast_samples`).
- Wick guard (house pattern, confirm_ticks default 2 via
  POST_TP1_FASTWATCH_CONFIRM_TICKS): clone-tick the newest N fresh samples from
  fresh deepcopies; fire ONLY when all N clone runs emit the IDENTICAL non-empty
  decision-kind set. Real pm.tick is called ONLY then => a TP2 tier flag can never
  be burned on an unconfirmed print (DONALT class).
- Peak freshness: ratchet the real position's peak from fresh samples using
  pm.tick's own formula, but conservatively (min of the newest 2 samples, both must
  exceed current peak) so a single glitch print can't inflate the trail line.
- Kill switch: POST_TP1_FASTWATCH (default ON). Paper and live are IDENTICAL wiring
  (same _execute_bot_sell -> should_route_live), so default ON for both.
- Cadence stamp: `_execute_bot_sell` gains kwarg `exit_cadence="main"`; sell record
  gains `exit_cadence` + `post_tp1_fw_enrolled`. New hook passes "fastwatch"; the
  three existing fast-cadence enforce call sites (exit-reprice / trail-reprice /
  tp1-fastfill) stamped "fastwatch" too.
- Fail-open: whole hook + per-position try/except; any error => position falls back
  to the main-scan cadence (which never stopped). In-flight set `_ptfw_inflight`
  guards re-entrant fires across awaits.
- Interaction note: POST_TP1_FASTWATCH supersedes TRAIL_REPRICE_MODE for post-TP1;
  both-on is safe (idempotent via close + cost_usd<=0 guard) but redundant — keep
  TRAIL_REPRICE_MODE off.

## Pre-registered forward grade
Compare post-TP1 REMAINDER exit legs (sells where a TP1 leg was already banked, i.e.
records joining to a position with tp1_hit) `exit_cadence="fastwatch"` vs `"main"`:
realized pnl_pct of the remainder leg, peak-minus-exit giveback pp, and
fired-below-line pp (peak_pnl_pct - trail_pp - realized). Bar: n>=50 each arm.
KPI from the mine: fired-below-line median <1pp; TP-leg booked-vs-config >= config.

## Status
- [x] Research: mine artifact found + quoted; existing machinery mapped
- [x] Design locked (above)
- [x] core/fast_watch.py pure helpers (post_tp1_fastwatch_enabled/max/ttl_secs/
      confirm_ticks, select_post_tp1_watches, confirmed_peak_ratchet)
- [x] feeds/dip_scanner.py: `_post_tp1_fastwatch` method (after
      _reprice_tp1_fastfill), hook call in _fast_watch_tick (after the existing
      reprice hooks), `_execute_bot_sell(..., exit_cadence="main")` + sell-record
      fields exit_cadence/post_tp1_fw_enrolled/post_tp1_fw_fire_pnl, and
      exit_cadence="fastwatch" at the 3 existing fast-cadence enforce call sites
      (exit-reprice / trail-reprice / tp1-fastfill). All legacy callers verified
      positional (web_dashboard force-sell, corpse exit, slow loop) — default
      "main" covers them.
- [x] dashboard/web_dashboard.py: POST_TP1_FASTWATCH surfaced in the fast-watch
      flags payload (observability).
- [x] tests/test_post_tp1_fastwatch.py — 35 tests, all passing (enrollment on
      TP1, pre-TP1 not enrolled, kill switch, eviction on close, cap FIFO, TTL
      evict, confirmed trail fire + cadence stamp, single-wick no-fire,
      confirmed TP2 partial + tier flag, UNCONFIRMED TP2 does NOT burn tp2_hit,
      confirmed peak ratchet, too-few-samples wait, batch-crash fail-open,
      sell-crash fail-open + inflight freed, opens-union fetch, pure helpers)
- [x] Full suite run #1: 4 failed / 2689 passed / 2 skipped.
      Triage: 2 failures MINE (test_donalt_double_sell, test_exit_reprice —
      their `_execute_bot_sell` stubs took exactly 5 positional args; the new
      exit_cadence kwarg raised TypeError which the fail-open except swallowed
      => 0 sells recorded). FIXED: widened both stub signatures with
      exit_cadence="main".
      2 failures PRE-EXISTING / other sessions' work, NOT mine:
      * test_moonbag_exit::test_jersey_integrity — twin drift
        trading_hour_utc_start/end, introduced by commit 70a870e ("lift 03-08
        UTC block for the YOUNG band"); config files not locally modified.
      * test_rh_chain_feed::TestRefillLiqQueueAgedMode — tests/test_rh_chain_feed.py
        + scripts/rh_chain_feed.py are modified in the working tree by ANOTHER
        session (not part of this build; I did not touch RH files).
- [x] Full suite run #2 after stub fixes: **1 failed, 2716 passed, 2 skipped**
      (python -m pytest tests/ -q, exit code checked — no tail piping).
      The single failure = test_moonbag_exit::test_jersey_integrity, PRE-EXISTING
      at HEAD (commit 70a870e lifted trading_hour_utc_start/end on
      badday_young_rt_paper but not its moonbag twin -> jersey-parity assertion
      drift; neither config nor test locally modified). NOT this build's code —
      flagged for the main session to reconcile (lift the twin's hours or extend
      allowed_diffs). test_rh_chain_feed passed on rerun (another session's
      in-flight working-tree edits, not this build).

## DONE — ready for main-session review (NO commits, NO deploys made)
Files changed by this build:
- core/fast_watch.py                 (pure helpers, +~110 lines)
- feeds/dip_scanner.py               (_post_tp1_fastwatch + hook + exit_cadence)
- dashboard/web_dashboard.py         (POST_TP1_FASTWATCH in flags payload)
- tests/test_post_tp1_fastwatch.py   (new, 35 tests)
- tests/test_donalt_double_sell.py   (stub signature widened)
- tests/test_exit_reprice.py         (stub signature widened)
Env surface (all optional, safe defaults): POST_TP1_FASTWATCH=on|off (default on),
POST_TP1_FASTWATCH_MAX=10, POST_TP1_FASTWATCH_TTL_SECS=0 (=lifetime),
POST_TP1_FASTWATCH_CONFIRM_TICKS=2. Rides the fast-watch loop => inert when
FAST_WATCH_MODE=off (prod: enforce, 2s interval). Recommend keeping
TRAIL_REPRICE_MODE off (superseded for post-TP1; both-on is safe but redundant).
