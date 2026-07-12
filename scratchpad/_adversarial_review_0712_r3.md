# Adversarial Code Review ROUND 3 — 2026-07-12
Scope: everything shipped after r2's baseline — d4923d7 (rug-gate prewarm + RH
cold-start), 523fe21 (5 factory racers + 4 lane gates + tracked facts),
7a808ce runtime followups (fastwatch now DEPLOYED, prod logs read-only),
ee8a899/f0f9be1 config residue re-sweep. Method: r1/r2 discipline — every
defect has a concrete failure scenario verified against actual code; fixes
<=15 lines + obviously safe applied in the working tree with regression tests.
No commits, no deploys, no Railway changes, no running processes touched.

IMPORTANT context: the working tree is SHARED with two other live agents
(fill-probe build + factory no-fire, both in scripts/rh_paper_lane.py). My
edits are scoped to feeds/dip_scanner.py, scripts/rh_chain_feed.py,
config/bots/badday_young_moonbag_ab.json + tests. See "Suite status".

## Confirmed defects

| # | Defect | Concrete failure scenario | Severity | Status |
|---|--------|---------------------------|----------|--------|
| R3-1 | **523fe21 shipped with a RED suite** — `tests/test_rh_paper_fleet.py::TestRoster::test_thirteen_racers_unique_ids` asserts `len(ROSTER) == 13` at HEAD while the commit ships ROSTER=18 (the commit added tests/test_rh_factory_racers.py but never updated the roster pin). | Anyone running the suite at HEAD gets a failure that masks NEW breakage in the same file — the exact R2-5 class (two LIVE-adjacent commits sat red for ~14h then; this is the SECOND occurrence within 24h, both times a `pytest \| pipe` / no-run-before-commit process failure). | process / suite-integrity (recurring class) | **DOCUMENTED** — the fix (13→18) already sits UNCOMMITTED in the tree from the concurrent session, and is ALREADY STALE again (their in-flight `rh_fill_probe` makes ROSTER 19). Their workstream owns the file; do not commit 523fe21-descendant work without a direct-exit-code suite run. |
| R3-2 | **Rug-gate prewarm 429 herd + cross-rebuild duplicate fetches** (d4923d7, `_fast_arm_subset` tail). The original tail fired up to 12 `create_task(self._holder_features_cached(...))` CONCURRENTLY per rebuild; `_holder_features_cached` has NO single-flight (r1-verified) and caches `feats={}` for **1800s** on ANY failure (`_fetch_rugcheck_full` returns None on non-200 — security/honeypot.py:343). Re-arm can run per fast tick (`RT_ARM_MODE` shadow/enforce → every ~8s) as well as per scan cycle. | Boot / armed-churn: 12 concurrent rugcheck hits, re-spawned each rebuild while fetches are in flight (up to ~36 concurrent) → elevated 429 odds → `{}` cached 30 min → the LIVE fleet rug gate (HOODLANA class) reads NEUTRAL at fire time for EXACTLY the most-fire-likely tokens (deepest dippers). The prewarm built to fix fill timing degrades the gate it feeds. Fire-and-forget tasks also held no reference (task-GC pitfall) and spammed "coroutine never awaited" in no-loop contexts. | live-money adjacent (gate-weakening, probabilistic) | **FIXED**: one background task fetches the batch SEQUENTIALLY; `self._pw_inflight` set dedupes across rebuilds (released in `finally`, even on cancel); `self._pw_task` holds a reference; `asyncio.get_running_loop()` guard keeps sync contexts silent; still fire-and-forget + fail-open (any error → outer `except: pass`, buy-time fetch unaffected). Test: `tests/test_fast_watch.py::test_rug_gate_prewarm_serial_and_deduped` (pins: fetched, no duplicates across back-to-back rebuilds, concurrency peak == 1, inflight drained). |
| R3-3 | **Aged-mode CAND_MAX prune deletes the ENTIRE aged-unknown cohort → the cold-start interleave (fix #4) is vacuous at default config** (d4923d7, `_refill_liq_queue`). The prune kept `rank_candidates(items)[:CAND_MAX]` = promotable + ALL young + aged; the interleaved `audition_order` ran only on the survivors. | Cold start at the DEFAULT `RH_FEED_CAND_MAX=5000` in aged mode: 72h backfill ≈ 49k candidates, young(≤24h) ≈ 16k+ → `ranked[:5000]` = promotable + youngest ~4.6k → **zero aged unknowns survive in `self.cand`** (and never come back — backfill runs once/session). The aged THESIS cohort is silently unauditable; the refill telemetry would print `aged_unknown=0`. Masked in production only because Railway sets `RH_FEED_CAND_MAX=60000` — but rh_coldstart/PROGRESS.md explicitly claims "No Railway env changes required (knobs all have defaults)", which was FALSE for this path. Same latent-env-config class as F4/R2-2. | mission-data (latent, config-dependent; RH paper only) | **FIXED**: prune now keeps the `audition_order(items)[:CAND_MAX]` prefix (young/aged keep their 1:1 share under prune pressure; promotable still lead, failed-aged still pruned first; byte-identical set+order whenever no prune triggers — Railway behavior unchanged). Test: `tests/test_rh_chain_feed.py::TestRefillLiqQueueAgedMode::test_prune_keeps_aged_unknowns_under_young_flood`. |
| R3-4 | **7a808ce corrupted `badday_young_moonbag_ab.json`** — both em-dashes in `display_name` became mojibake (`â€”` = UTF-8 "—" mis-decoded as cp1252) and the trailing newline was dropped when the hour lift was written. | Dashboard/display renders "â€”"; signals an encoding-unsafe config-rewrite path was used for a shipped config change. | cosmetic | **FIXED**: display_name restored (ensure_ascii=False round-trip, trailing newline back); `tests/test_moonbag_exit.py` 15/15 green (jersey twin parity intact — only display_name + newline changed, verified by diff). |

## Documented (no code change)

1. **Prewarm 1500s guard vs 1800s cache TTL — benign, not a double fetch**
   (review question 1a): for entries aged 1500-1800s the prewarm schedules a
   fetch but `_holder_features_cached` (dip_scanner.py:5634) returns the
   cache hit at <1800s — a no-op dict read. Net effect: refresh happens on
   the first rebuild AFTER expiry, not before it. Cosmetic task churn only
   (and now one serial task, not 12).
2. **Failure-caching `{}` for 1800s is still the posture** (r1 note 1, now
   more load-bearing): the prewarm moves fetches earlier, so a transient
   rugcheck outage AT ARM TIME blinds the fire-time gate for 30 min/token.
   Upstream fix = short negative TTL (~60s) for empty feats — but that
   changes live buy-path latency under a sustained outage (repeated 2.5s
   timeout waits), so it is a main-session posture decision, not a review
   patch.
3. **Factory-racer gate-data mismatches vs their mined cells** (523fe21):
   (a) `cum_vol` is volume SINCE LANE DISCOVERY; the mine's `vol_pre` for the
   aged cells is from-launch. `rh_f_reload24` ($16k floor) and
   `rh_f_reload_mid` will underfire vs their cells and bias their n>=30
   confirm samples toward hyper-active pools. Young bands (discovered at
   creation) are parity-true, as the code comment claims. (b) `pop_book` is
   in-memory: every ~5h lane restart blocks `rh_f_popret` for up to 30 min
   (`no_recent_pop`, fail-closed). (c) `first_px`/`cum_vol` persist only on
   buy/exit `save_state` calls and the state file lives under
   `scratchpad/robinhood_tapes/` (NOT the Railway data/ volume) → a REDEPLOY
   resets the arc basis (arc gate fails OPEN per its design) and the volume
   base (fails CLOSED). None of these lose money; all bias the A/B samples —
   note them when grading the factory five.
4. **`min_buys_30s` is a dead gate**: defined, wired into the gate loop, set
   by NO roster entry. Harmless; flag for the factory follow-ups.
5. **Recheck ladder degrades under throttle** (d4923d7): an r=None
   (throttled) recheck consumes no ladder try but re-enters `liq_queue` at
   the BACK (~45k deep when cold) — the +60/+180s rungs become hour-scale
   under sustained 429. Bounded, paper-only, self-heals when throttle lifts.
6. **AIMD backs off only on TOTAL-failure cycles**: a cycle with ≥1 result
   never halves, so partial throttling sustains full burst pressure on the
   shared public RPC (which also serves the watched-pool tape in the SAME
   batch). Telemetry now makes it visible; recovery verified additive +10 →
   no permanent starvation (the review question's scenario does NOT occur:
   floor 10 keeps probing, first success starts recovery, cap self-clears
   at the configured budget).
7. **Runtime followup, 7a808ce deployed** (prod logs, read-only): ONE
   anomaly — `fastwatch_eval_loop took 53.02s` (survivor LCM,
   `_evaluate_pair=53.02s`, 16:54:20 UTC). A single unbounded per-token eval
   freezes the ~2s fast loop — which now also carries post-TP1 fastwatch
   protection — for ~1 min. Pre-existing eval-path exposure surfaced by the
   new dependence; recommend a per-survivor eval time budget. NO
   "sell already in flight" shim lines (no double-fire attempts), no
   unretrieved-task noise, and the fleet rug gate observed ENFORCING live
   (BLUXEL hidden=72% holders=687, both branch prints). Caveat: `railway
   logs` window is ~40s and sells are rare — exit_cadence distribution not
   gradeable from logs; grade from /api/trades?full=1 at n>=50 as
   pre-registered.
8. **Config residue re-sweep (item 4) — clean**: exactly 3 enabled
   live_probe configs (absorb/rt/vsnap_ab), all $22.5 / hours 0-24 /
   regime_dial_exempt; all 151 configs load through BotConfig strictness;
   the ONLY config churn since ee8a899 is the moonbag twin (hour lift =
   sanctioned parity restore; mojibake = R3-4, fixed). The factory racers'
   new lane-level fields correctly NEVER leak into `bot_config()` (the
   f0f9be1 unknown-field class was checked — clean).

## Attack angles verified CLEAN (no defect)
- **Prewarm cache-key identity**: prewarm keys = `merged` addrs (original-case
  mint, FIX5) = `pair_by_addr` keys; gate keys = `decision.address` (same
  mint, same case, same source dicts). Entry shape `(ts, feats)` matches the
  guard's `hit[0]`. `pc_h1=None` filtered by isinstance; ascending sort =
  deepest dips first. `create_task` from the sync method is loop-guarded and
  outer-try'd (fail-open — prewarm can never block arming or a buy).
- **Liq seed staleness** (1b): order-only claim VERIFIED — promotion requires
  a fresh passing balanceOf through `pending_sym`; the loader stamps only
  `liq is None` candidates; missing/malformed seed = no-op; exporter
  strict-validates addresses. A stale seed can misdirect early audit budget
  only (bounded by seed size, 407 pools).
- **Burst budget arithmetic**: `liq_budget` = max(base, burst) inside the
  window (a burst below base can't shrink budget), default mode always base
  (test-pinned); `liq_dyn` floor 10, halve-on-zero, additive recovery,
  self-clears; budget applied to rechecks + queue with `taken` dedupe.
- **Recheck ladder bounds**: 3 tries/pool, young(≤1h)-only, tries increment
  only when a recheck is actually scheduled; over-budget dues re-queue at
  ts=0; entries for pruned/watched/pending pools drop at due time —
  `liq_recheck` cannot grow unboundedly.
- **Factory racers**: `reentry_cooldown_s=600` is keyed per (bot, pool) via
  each racer's own `st.last_exit` = per-cell mine parity (the exclusion
  group governs cross-sibling holds/loss-stops separately — correct);
  `dedupe_group_entries` winner logic correct for n siblings (fewest open
  positions, roster-order tie); r1-F5 (same token via two pools, one tick)
  applies to "factory" as to "aged" — known, tick-scoped, documented in r1.
  Gate cost: shared per-pool facts computed once/tick, per-racer gates O(1)
  — 18 racers x watch=180 adds one O(rows) pass (n_buys_30s) on a 2000-row
  cap, negligible. Depth band (`dip < cap` blocks) and pop detector
  (rolling 10-min window min catches up to a plateau → self-limiting
  re-stamps) both correct.
- **Tracker persistence correctness**: `first_px`/`cum_vol` restored
  UNCONDITIONALLY on load (lifetime facts, deliberate — not same-day-gated),
  guarded parses (v>0 / v>=0), atomic temp/replace write, `_note_px` only
  stamps first_px when absent (restored value wins over recomputed — the
  stated design). pop_book non-persistence documented above (note 3b).

## Suite status (exit codes checked DIRECTLY, never a pipe)
- Full suite: **2752 passed, 5 failed, 2 skipped** (exit 1). ALL FIVE
  failures are the CONCURRENT agents' in-flight, uncommitted work in
  scripts/rh_paper_lane.py, not the reviewed commits and not my fixes:
  * 2x static dormancy guards (`test_paper_lane_never_calls_swap_methods_static`,
    `test_lane_never_swaps`) trip on their new `_live_buy_leg` /
    `._live_executor().live_buy(...)` wiring (+222 uncommitted lines).
    At HEAD the lane has ZERO such references (verified via
    `git show HEAD:scripts/rh_paper_lane.py`) — the guards pass at HEAD.
  * 3x fleet tests (roster count, routing, ledger rows) trip on their new
    19th racer `rh_fill_probe` (their own uncommitted 18-pin is stale again).
  Note HEAD itself is red per R3-1 (roster pin 13 vs 18) — the tree's
  uncommitted 18-pin was masking that in my first full run.
- Every suite my fixes touch: **223 passed, 0 failed** (test_fast_watch,
  test_rh_chain_feed, test_rug_gate, test_moonbag_exit,
  test_sell_serialization, test_ledger_rotation, test_pre_live_invariants).
- My edit scope (verified vs the shared tree): feeds/dip_scanner.py
  (prewarm rewrite only), scripts/rh_chain_feed.py (prune hunk only),
  config/bots/badday_young_moonbag_ab.json (display_name + newline only),
  tests/test_fast_watch.py (+1 test), tests/test_rh_chain_feed.py (+1 test).
  I did not touch scripts/rh_paper_lane.py, tests/test_rh_paper_fleet.py,
  .gitignore, or dashboard/web_dashboard.py (other agents' in-flight files).

## Fixes applied (working tree only, no commits)
- feeds/dip_scanner.py — R3-2: prewarm serialized into one deduped
  background task (`_pw_inflight` + `_pw_task` + running-loop guard).
- scripts/rh_chain_feed.py — R3-3: aged-mode CAND_MAX prune keeps the
  interleaved audition prefix.
- config/bots/badday_young_moonbag_ab.json — R3-4: mojibake repaired.
- tests: test_fast_watch.py::test_rug_gate_prewarm_serial_and_deduped;
  test_rh_chain_feed.py::test_prune_keeps_aged_unknowns_under_young_flood.
