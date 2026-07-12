# RH cloud-lane cold-start starvation — investigation progress

## Status: COMPLETE. Fix in working tree (no commits), tests 395 passed /
## 2 skipped across all 13 RH suites, cold repro proven before+after.
## coldrun3 FINAL (8 min, worst-case RPC contention): watch=24 taped=30
## quotes=268 evals=671, 24 promotions (aged band 24-66h + fresh 0.0-0.5h),
## all 13 racers evaluating with full block histograms.
## SHIP CHECKLIST for main session: git add scripts/rh_liq_seed_export.py
## config/rh_liq_seed.json (config/, NOT data/ — data/ is gitignored volume)
## + the modified scripts/rh_chain_feed.py tests/test_rh_chain_feed.py.
## No Railway env changes required (knobs all have defaults).

## SECOND root-cause layer found during after-repro (2026-07-12 ~09:30 UTC)
- The public RPC answers throttled eth_call batches with {} / HTTP 429;
  Rpc.batch gives up after 3 tries per chunk and the audition loop treated
  missing results as "skip silently" — a fully-throttled lane looks IDLE
  (watch=0, no errors). Probe measured 0/10 batch results under load.
- Local machine had TWO lanes sharing the RPC IP (main session's repo-root
  `rh_paper_lane.py 300` started 09:14 UTC + my temp-cwd repros) — my
  coldrun2 after-repro was 429-starved by the overlap; treat its watch=0 as
  CONTENDED, not a fix failure.
- Added: audition telemetry line every 40 cycles (checked/ok/promoted/queue/
  recheck/pending_sym), "audition throttled" print, AIMD budget backoff
  (halve to floor 10 on zero-result cycles, +10 additive recovery), and
  requeue-on-missing so throttled audits aren't orphaned for the sweep hour.

## AFTER evidence (fixed code)
- Repo-root lane (PID 23752, started 09:14:11 UTC COLD with the fixed
  working tree, aged env): 94 promotions by ~09:23 (8 min cold, WHILE
  contended by my overlapping repro backfill+burst), including the aged
  thesis band: DEBTDOG 28.0h/$13.2k, MEOW 29.3h/$9.7k, MEANINGFUL 29.4h,
  DALV 24.3h, few 24.4h. Tapes updating. Before-fix Railway: 0 in 31+ min.
- coldrun3 (FINAL code incl. AIMD backoff + front-requeue, 8 min, temp cwd,
  WORST CASE: sharing the RPC IP with the still-running repo-root lane, so
  most burst batches 429'd): first promotions ~40s in (SMILE 21.3h $50k,
  S&P500 11.9h $42k); watch=2 @1.1min, watch=12 taped=2 quotes=27 evals=27
  @2.1min; 15+ promotions by ~3min incl. the aged band (FLOWR 41h, HD 59.7h,
  UNIPCS 66.1h, CHUD 58.9h, HET 62.1h, 0DTE 43.5h); ALL 13 racers show real
  block histograms (no_dip/no_demand_turn/age_ceiling/liq_floor/hour_window/
  weak_inflow) instead of "blocks: -". "audition throttled" lines make the
  RPC contention VISIBLE (was silent) and the AIMD floor (10/cycle) keeps
  promotions flowing through it. Railway is uncontended -> strictly better.
  Cycle-40 telemetry: checked=715 ok=130 (82% throttled away!) promoted=24
  queue=45859 — 24 watch pools from 130 successful audits. Fresh-pool
  recheck ladder proven live: doooooooog/SHORK/Blink Dog promoted at age
  0.0-0.1h, 530A/BlockAI at 0.5h. watch=24 taped=19 evals=366 @5.3min.
  NOTE: config/rh_liq_seed.json regenerated 09:39 UTC post-move (data/ is
  the gitignored Railway VOLUME — seed had to move to tracked config/).

## Env knobs added (all optional; service needs NO changes)
- RH_FEED_LIQ_SEED (default config/rh_liq_seed.json), RH_FEED_LIQ_BURST (120),
  RH_FEED_LIQ_BURST_CYCLES (240). Regenerate seed pre-deploy (optional):
  python scripts/rh_liq_seed_export.py

## Housekeeping
- pytest promotion test briefly appended 3 fake "0xseeded" rows to the LIVE
  scratchpad/robinhood_tapes/pools_meta.jsonl (relative OUT_DIR + pytest cwd
  = repo root). Test now redirects meta_path to tmp_path; exporter now
  strict-validates addresses (42-char hex) so the artifact can't ingest
  them. The 3 rows remain in pools_meta.jsonl (harmless; permission to
  rewrite the live file was denied — main session may scrub lines matching
  '"pool":"0xseeded"' if desired).

## BEFORE repro result (10 min, unmodified code, Railway env, temp cwd)
- backfill: 52,257 creations -> 49,106 candidates (matches Railway 49.3k)
- watch=0 taped=0 at every stats line through 9.6 min; ALL 13 racers
  "blocks: -", quotes=0 evals=0; zero [disc]+ promotions; zero errors.
- Symptom fully reproduced locally => cold-start-in-aged-mode starvation,
  not a Railway-environment bug.

## Fix implemented (working tree, no commits)
- scripts/rh_chain_feed.py: COLD-START knob block (LIQ_SEED_PATH / LIQ_BURST
  / LIQ_BURST_CYCLES / LIQ_RECHECK_LADDER_S / LIQ_RECHECK_MAX_AGE_H); pure fns
  candidate_tiers()/audition_order()/liq_budget()/schedule_recheck();
  Feed.load_liq_seed() (called at end of backfill_discovery — covers all three
  entry points); _refill_liq_queue aged branch -> audition_order + tier-
  composition log line; process_cycle: burst budget, due-recheck priority with
  dedupe, aged requeue-on-missing-batch-result, recheck-ladder scheduling.
- scripts/rh_liq_seed_export.py (NEW): pools_meta.jsonl -> config/rh_liq_seed.json.
- config/rh_liq_seed.json (NEW, 400 pools, 22KB): ships in the deploy
  (.railwayignore only excludes scratchpad/ + root _*.json — data/ uploads).
  UNTRACKED — main session must `git add config/rh_liq_seed.json
  scripts/rh_liq_seed_export.py` when shipping.
- tests/test_rh_chain_feed.py: +19 tests (order/budget/ladder/seed/
  process_cycle integration/exporter); 1 aged-order assertion updated to the
  interleaved queue.
- Default mode byte-identical: every new branch gated on AGED_MODE; guarded by
  test_default_mode_wiring_identical + the existing default-mode suites.

## Repro
- Cold run from temp cwd (isolated OUT_DIR), env = Railway aged mode:
  RH_FEED_MAX_AGE_H=72 RH_FEED_LOOKBACK_H=72 RH_FEED_CAND_MAX=60000 RH_FEED_LIQ_PER_CYCLE=40
- `python scripts/rh_paper_lane.py 10` → backfill found 52,257 creations → 49,106 WETH-quoted
  candidates (matches Railway's 49.3k). Watching stats lines for watch=0.
- Log: <session scratchpad>/coldrun1/coldrepro_before.log

## Code-path analysis (rh_chain_feed.py)
Audition loop in the LANE context = Firehose.maintenance() every ~2.5s:
poll_cycle() + process_cycle([]) → LIQ_PER_CYCLE (40) balanceOf checks per cycle,
queue refilled ONLY when empty (`if not self.liq_queue: self._refill_liq_queue()`).

Cold in aged mode:
1. Queue = full 49k ranked sweep (all liq=None → tier2 young youngest-first,
   then tier3 aged-unknowns youngest-first). One sweep ≈ 49k/40 cycles ≈ 1200+
   maintenance cycles ≈ 60-90 min wall — refill (and therefore any RE-check)
   happens at most ~once/hour.
2. Front of the queue = youngest pools = bot-era spam (49k/72h ≈ 685/h, bot era),
   checked at age≈minutes when LP often not yet added → fail the $5k floor once.
3. Fresh pools DO queue-jump (insert(0)) but are checked at age≈seconds (pre-LP),
   fail once, and are NOT re-checked until the whole 49k sweep drains → no
   promotions from launches either.
4. Aged/established pools (the aged racers' thesis) sit ~16k deep (behind all
   young) → never audited within a session cold.
5. Local "works immediately" ≠ disk warm state (Feed has NO disk cache): local
   runs used default mode / default CAND_MAX=5000 → queue ≈ minutes → frequent
   refills → prompt rechecks + promotions.
6. Zero logging in the audition path until a promotion → looks like "evaluates
   nothing".

## Fix plan (working tree only, no commits)
- config/rh_liq_seed.json shipped in the deployable: {pool: last-known liq} from
  local pools_meta; loader stamps cand[pool]["liq"] post-backfill (aged mode) →
  tier-1 promotable → audited in cycle 1-2. Stale-safe: seed only orders the
  queue; promotion still requires a fresh passing balanceOf.
- Fresh-pool recheck ladder: below-floor young pools re-audited at +60s/+180s/
  +600s (bounded, ~1-2 extra checks/cycle) → launches promote mid-sweep.
- Cold-start liq burst: env-bounded larger budget for the first N cycles
  (aged mode only).
- Audition-order interleave young/aged unknowns so aged thesis pools get
  budget share cold.
- Refill/queue logging.
Default mode (MAX_AGE_H<=24): byte-identical (all changes gated on AGED_MODE
or no-op empty seed).
