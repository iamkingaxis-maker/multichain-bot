# Mission: TRUE scan->fill latency fix (2026-06-18, ~7h autonomous, AxiS asleep)

**Start:** ~03:15Z  **Deadline:** ~10:15Z  **No questions — I decide. AxiS away.**

## GOAL
Get scan->fill from 83-220s to ~1-3s WITHOUT a paid API tier (AxiS rejected that). Deploy the
cache/throughput fix when reviewed-clean, MEASURE a WARM cycle, iterate if not fast, monitor.

## HARD CONSTRAINTS (do not violate)
- ⛔ LIVE STAYS PAUSED. Do NOT flip PAPER_MODE=false / re-go-live. AxiS said deploy the FIX +
  keep working on latency — NOT resume live. Re-going-live needs explicit AxiS approval.
- ⛔ Profit-sweep STAYS PAUSED (PROFIT_SWEEP_DRY_RUN=1).
- ⛔ No unilateral live-money config changes (lesson: feedback_no_invented_live_authority).
- Everything behind flags; verify (my own tests) + reviewer sign-off (0 blocking) before deploy.
- Phantom-aware, egress-discipline, Opus agents, negatives = "what we learned + next thing".

## DIAGNOSIS (confirmed by the true-fix fleet)
NOT a rate-limit wall. The io.dexscreener.com binary chart endpoint is ALREADY wired + active
(dexs_client, curl_cffi present). The 83-220s is 3 code things: (1) slug/quote cache is in-memory
-> wiped every restart -> cold cycles re-resolve ~190 tokens (THIS contaminated all my earlier
measurements — I always measured cold-after-deploy); (2) speedup flags CHART_DS_PRIMARY +
PARALLEL_SCAN_MODE default OFF; (3) DS fetch executor capped at 4 threads + bar-cache TTL (60s) <
cycle so sticky tokens re-fetch every cycle. #1 lever = a signal-neutral tiered prefilter (cut the
~190 fetched). Streaming (axiom_price_feed) = a later option.

## STATE AT MISSION START
- All correctness fixes shipped+committed (re-price BUY_REPRICE shadow; phantom EXIT_SANITY on;
  sweep SOL-floor [paused]; gate recal enforce; signal-neutral prefilter shadow).
- Flags now at safe baseline: CHART_DS_PRIMARY=off, PARALLEL_SCAN_MODE=off, FAST_PREFILTER=shadow,
  REGIME_BUY_GATE=enforce, PAPER_MODE=true, PROFIT_SWEEP_DRY_RUN=1.
- IN FLIGHT: cache/throughput fix fleet wf_e212a17a (slug-cache persist + bar-TTL + executor) —
  build+verify+review running. Deploy when reviews=0-blocking.

## PLAN (each wake)
1. date -u; if > ~10:15Z, write FINAL SUMMARY + stop.
2. If the cache/throughput fleet finished: read reviews; if 0 blocking + my tests pass -> commit +
   deploy. If blocking -> fix (follow-up agent) then deploy.
3. After deploy: flip CHART_DS_PRIMARY=on + PARALLEL_SCAN_MODE=on (PAPER, safe) -> let the cache
   WARM (several cycles, no new deploy) -> MEASURE a warm cycle (Refreshing->Cycle done duration +
   GT 429 count + chart-empty rejects). This is the real before/after vs 83-220s baseline.
3b. CRITICAL measurement hygiene: do NOT measure within ~3-4 min of a deploy (cold cache). Wait for
    warm cycles.
4. If warm cycle is fast (~seconds-to-low-tens-of-s) AND signal-drop=0 + no chart-empty -> WIN.
   Log it. Monitor stability over remaining time.
5. If still slow -> iterate: next lever = signal-neutral tiered prefilter (extend the FAST_PREFILTER
   harness with cheap trigger-preconditions, shadow-validate signal-drop=0 before enforce) and/or
   streaming. Build+review fleet, deploy, re-measure.
6. Monitor: cycle time trend, errors (tick failed / tracebacks), no regressions, paper fleet alive.
7. ScheduleWakeup ~1800s to continue.

## LOG
### Mission start ~03:15Z: cache/throughput fleet (wf_e212a17a) in flight. Live+sweep paused. Baseline flags safe.

### ~03:35Z — A+B DEPLOYED (paper). Cache-persist + bar-TTL + executor. Flags ON.
- Committed slug/quote-cache persistence + CHART_BAR_TTL_SECS + DS_FETCH_WORKERS (reviews 0-blocking, 27 tests).
- Flags set (PAPER): CHART_DS_PRIMARY=on, PARALLEL_SCAN_MODE=on, CHART_BAR_TTL_SECS=180, DS_FETCH_WORKERS=12.
- NOTE: this first post-deploy boot has NO saved slug cache yet (first run with persistence) -> it
  SAVES during this run; the WARM benefit (slug-load-from-disk) shows on the NEXT restart + as bars cache.
- ⭐ REALISM (speedup reviewer): A+B = 1.3-3x (cycle ~10s best / 80-160s typical), NOT the ~1-3s goal.
  NEXT BINDING CEILING = DS client self-throttle rate_per_min=90 on bar fetches (dexscreener_client.py:404,
  RAISABLE — DS real ceiling higher) + the ~190-token count.
- NEXT LEVERS to reach seconds: (1) raise the DS bar-fetch rate_per_min above 90 (self-imposed; test for
  429s as we raise); (2) signal-neutral TIERED PREFILTER to cut tokens needing bars from ~190 to the few
  that could fire (extend FAST_PREFILTER harness w/ cheap trigger-preconditions, shadow signal-drop=0 first).
- MEASURE a WARM cycle next (after warming, NOT within ~4min of deploy). Then iterate (1)+(2).

### ~03:43Z — ⭐ WARM-CYCLE MEASURED: SCAN/FETCH 83-220s -> ~13s (the fix WORKS)
- Cycle 3: Refreshing 03:42:37 -> fetched-summary 03:42:50 = **13s SCAN** (the signal->fill path, was the dominant 83-220s cost). -> Cycle done 03:43:33 = **56s FULL cycle**.
- So: SCAN/FETCH latency (entry freshness) collapsed ~6-15x to ~13s. Cache-persist + bar-reuse (309/473 sticky cached) + DS-primary + parallel + 12 workers all landed. Universe ~473 (bigger than the 190 est; mostly sticky=cached -> few real fetches -> the 13s).
- HEALTH: 0 chart-empty, 0 errors (no tick-failed/tracebacks), signals 10-13/cycle (NOT over-blocked), 429s minimal/stale.
- ⭐ NEW bottleneck = the ~43s POST-SCAN position-tick (_tick_all_bots_positions: serial exit-price fetch per open position across the big paper fleet). This caps cycle CADENCE (how often we re-scan), not entry latency. Entry latency (the money concern) is already ~13s.
- NEXT: iterate on the 43s tick (parallelize/cache per-open-position exit fetches) to tighten cadence; tiered prefilter still available to cut the 13s further. Confirm 13s is stable over more cycles next wake.

### ~04:10Z — STABILITY CONFIRMED (not a one-off)
- Cycle-to-cycle ~42-56s across consecutive cycles (two scan summaries 04:09:05 & 04:09:47 = 42s apart), down from 83-220s baseline = ~1.5-4x full-cycle, ~6-15x on the scan portion (~13s).
- HEALTH GREEN: 429=low/stale, chart-empty=0, errors=0, signals 8-9-13/cycle (flowing, not over-blocked). PAPER_MODE=true, sweep dry-run (paused) intact.
- Entry-latency goal (scan->fill freshness) = MET (~13s). Remaining ~43s = post-scan position-tick (exit mgmt, serial price fetch per open position across the big paper fleet) -> caps cadence.
- DECISION: iterate on the tick (signal-safe: exit-price guard already protects bad ticks; flag-gated; reviewed). Launching build+review fleet to parallelize/cache the per-open-position exit fetches.

### ~04:16Z — TICK-PARALLEL deployed (PARALLEL_TICK_MODE=on, paper)
- Step 2: the ~43s post-scan tick now parallelizes the per-unique-token exit-price FETCH (bounded-12 gather), decision/sell loop stays serial+address-keyed (no double-sell, exit-guard intact). Reviews 0-blocking; 143 tests verified.
- Flags now (PAPER): CHART_DS_PRIMARY=on, PARALLEL_SCAN_MODE=on, PARALLEL_TICK_MODE=on, CHART_BAR_TTL_SECS=180, DS_FETCH_WORKERS=12, LIQ_DRAIN_MODE=off (isolation per reviewer for first parallel-tick validation; re-enable shadow after confirming no DS 429s).
- EXPECTED full cycle: scan ~13s + tick ~43s->~4s = ~17-20s (was ~50s, was 83-220s baseline).
- MEASURE next wake (warm, not within ~4min of deploy): full-cycle Refreshing->Cycle done; confirm 429s stay low, chart-empty 0, errors 0, signals flowing. Then consider re-enabling LIQ_DRAIN_MODE=shadow if no 429s.

### ~04:52Z — ⚠ HONEST CORRECTION + true bottleneck found (code+stream evidence)
- WARM measurement (deploy was 04:16Z, +36min, confirmed warm; flags all live incl PARALLEL_TICK=on, LIQ_DRAIN=off, PAPER_MODE=true): steady-state cycle cadence is ~100-178s, NOT the 13-56s I logged earlier. The 13s was a CACHE-SATURATED BEST CASE (a cycle dominated by sticky/cached tokens); it is NOT the steady state. Correcting the record per calibration discipline.
- 160s continuous stream (04:49:07->04:50:48) shows tokens processed ONE AT A TIME (ERRORA 04:49:07, KOL :12, FRAG :29, ICPX/WELLTH 04:50:12, WORLDCUP :16, COD :26, GYM :33) = the SCAN DECISION LOOP is serial over ~485 fetched / ~150-230 survivors. THIS is the dominant cost.
- ⭐ TICK FIX IS CURRENTLY INERT: `Watching: 0 tokens` (zero open positions) -> PARALLEL_TICK has nothing to parallelize right now. It will help only when positions are open. The ~100s is ENTIRELY the scan loop.
- CODE-VERIFIED (systematic-debug, didn't build on a guess): mcap_low (256/cycle), mcap_high (91), age, vol are CHEAP EARLY `continue`s at dip_scanner.py:2563-2586 using marketCap already in the discovery payload (NO bar fetch). So "prefilter microcaps to save cost" is MOOT — they're already cheap rejects. Good that I checked.
- TRUE bottleneck = the ~150-230 SURVIVING tokens each pay a SERIAL per-token async decision stage (price/chart/slippage-curve/trade-log-for-rug-bundle + ML fusion), awaited inside the `for pair in pairs` loop, which ALSO fires buys (`_execute_bot_buy`) inline. Serial awaits x ~200 survivors = ~100s.
- NEXT LEVER (proven-safe tick pattern, but on the BUY-FIRING money path -> higher blast radius): parallelize ONLY the read-only per-token FETCH stage (gather prices/charts/slippage for survivors), keep decision+buy-firing SERIAL/ordered (exclusion pool, concurrency caps, same-token dedup, address-keyed). Flag default OFF. Adversarial review MUST target double-fire / exclusion-pool race / same-token dedup. Build+review fleet launching; deploy INERT; enable+measure on a warm cycle ONLY if review is squeaky-clean on race/double-fire + my tests + pre-live invariants pass (same bar as the tick). If ANY doubt -> leave OFF for AxiS.

### ~05:04Z — SCAN-FETCH PARALLEL deployed (PARALLEL_SCAN_DECISION_MODE=on, slip-warm OFF, paper)
- Step 3: prefetch the read-only per-token fetches (chart_data + recent_trades; slip-warm held OFF this first validation) under bounded gather (clamp<=16, address-keyed) BEFORE the serial decision loop. Buy-firing stays serial under _buy_fire_lock + _cycle_bought_addrs (5 attack vectors proven impossible by adversarial reviewer). 2x ship/0-blocking; 20 tests + 16 invariants verified by me.
- Why slip-warm OFF first: reviewers flagged the only operational risk = Jupiter slip-warm burst (~128 in-flight quotes). Isolating it (PARALLEL_SCAN_DECISION_SLIP_WARM=off) validates the chart+trades prefetch cleanly; enable slip-warm next only if cadence improves AND no Jupiter/DS 429s.
- Flags now (PAPER): CHART_DS_PRIMARY=on, PARALLEL_SCAN_MODE=on, PARALLEL_SCAN_DECISION_MODE=on, PARALLEL_SCAN_DECISION_SLIP_WARM=off, PARALLEL_TICK_MODE=on, LIQ_DRAIN_MODE=off, PAPER_MODE=true, sweep dry-run.
- MEASURE next wake (WARM — wait ~25min past this 05:04Z deploy, do NOT measure cold): full-cycle cadence vs the ~100-178s steady-state baseline; confirm 429s stay low, signals still flow, no double-fire/extra buys, no tracebacks. If cadence drops materially + clean -> consider enabling slip-warm. If no change -> the bottleneck was compute (ML/GIL) not fetch -> rethink.

### ~05:33Z — ⚠ FETCH-BOUND HYPOTHESIS FALSIFIED; scan-fetch flag DISABLED (back to baseline)
- WARM 7-cycle measurement (cycles 1-7, container restarted ~05:11): Cycle-done intervals 143/190/141/204/153/162/148s, avg ~163s, warmest (05:29->05:31)=148s. NO improvement vs the ~100-178s baseline. PARALLEL_SCAN_DECISION_MODE (chart+trades prefetch) gave ZERO cadence benefit.
- WHY (systematic-debug, hypothesis falsified by measurement): the ~160s is COMPUTE-bound, not fetch-bound. The serial per-token work for ~150-230 survivors = ML fusion model + rug_bundle + trigger features + decision logic, which is CPU/GIL-bound -> async fetch parallelization cannot speed it up. The earlier 13s was a genuinely cache-saturated cycle (few survivors needing work), NOT representative.
- Errors seen are PRE-EXISTING, not from my change: one stuck token (3nMFwZXwY1s1) times out 5s on DS every cycle (a real per-cycle tax worth fixing separately); Axiom WS proxy reconnects (background); GT 429s are non-fatal fallback under CHART_DS_PRIMARY (likely cold-start).
- DECISION: disabled PARALLEL_SCAN_DECISION_MODE (decisions were proven byte-identical, but no benefit + slight 429 ambiguity -> don't run an unproven change on the buy-firing path). CODE KEPT (committed, reviewed, flag default-off) for a future fetch-bound regime. KEPT WINS: cache-persist (step1, real cold-start reduction), tick-parallel (step2, helps when positions open).
- REAL next lever (AxiS decision, NOT autonomous tonight — it's architecture): cut per-token COMPUTE for survivors (cheaper pre-screen BEFORE the ML fusion model, or a dedicated fast-path that re-checks already-watched sticky tokens every few seconds independent of the full 485-token universe sweep). Multiprocess/GIL escape = big change. Also: fix the 3nMFwZXwY1s1 5s-timeout-per-cycle stuck-token tax.

### ~05:36Z — _SCAN_INTERVAL confirmed 30s (minor); STOPPING the build cycle
- _SCAN_INTERVAL=30s hardcoded (feeds/dip_scanner.py:65, no env override; historically 90->30). So the inter-cycle ~120s gap is NOT sleep — it's actual scan+tick WORK (~113-174s/cycle). Confirms work-bound, consistent with compute-bound (fetch-parallel gave 0 benefit).
- DECISION (autonomy discipline): STOP building. Safe autonomous fetch-levers are exhausted/falsified. The real remaining lever is ARCHITECTURE (per-token compute reduction OR a separate fast-path that re-checks already-watched sticky tokens every few seconds independent of the 485-token sweep) — a money-path change that needs AxiS awake + a design pass, NOT an unsupervised overnight build. Will monitor stability + write the honest final summary at the 10:15Z deadline.
- KEPT (shipped, paper, reviewed): (1) slug/quote-cache PERSIST + bar-TTL + 12-worker executor (step1 A+B) = real cold-start cost reduction; (2) PARALLEL_TICK_MODE=on (step2) = parallelizes exit-price fetch when positions are open (inert at 0 positions). (3) PARALLEL_SCAN_DECISION code committed but flag DISABLED (no benefit; kept for a future fetch-bound regime).
- RECOMMENDATIONS for AxiS (final-summary candidates, NOT built): (a) profile per-token compute (fusion model vs rug_bundle vs trigger features) to target the real ~0.5-1s/survivor cost; (b) dedicated fast re-check loop for the sticky/watched cohort (closest to the "1-2s to fill" goal — actionable dip tokens are already watched); (c) make _SCAN_INTERVAL env-tunable + trim (modest ~15%); (d) fix the 3nMFwZXwY1s1-style stuck-token 5s-DS-timeout-per-cycle tax.

---

## FINAL SUMMARY — 2026-06-18 ~10:14Z (deadline reached, AxiS away ~7h)

### What the mission was
Get scan->fill latency down WITHOUT a paid API tier (AxiS rejected that conclusion outright). Iterate, measure WARM cycles, deploy fixes flag-gated/paper, monitor. LIVE + sweep STAYED PAUSED throughout.

### The honest arc (what we learned)
1. **DIAGNOSIS was right that it's not a paid-tier wall** — the io.dexscreener.com binary chart endpoint was already wired; the cost was code (cold in-memory cache + serial work).
2. **Step 1 — cache-persist + bar-TTL + 12-worker executor (SHIPPED, kept):** slug/quote cache now persists to disk + bars reuse cross-cycle. REAL win on COLD-START cost (no more re-resolving ~190 tokens every restart). This is a genuine improvement.
3. **Step 2 — PARALLEL_TICK_MODE (SHIPPED, kept, flag on):** parallelizes the post-scan exit-price fetch. Currently mostly INERT because the paper fleet sits at ~0 open positions most cycles; it helps only when positions are open. Proven safe (serial sells, address-keyed).
4. **The "13s scan" was NOT the steady state** — I over-claimed it mid-mission and CORRECTED it: 13s was a cache-saturated best-case cycle. Steady-state cadence is **~150-165s**.
5. **Step 3 — PARALLEL_SCAN_DECISION_MODE (BUILT, reviewed 2x ship/0-blocking, FALSIFIED, DISABLED):** parallelized the read-only per-token fetch stage on the buy-firing path (all 5 race/double-fire vectors proven impossible; off-mode byte-identical). WARM 7-cycle measurement showed **ZERO cadence benefit** (~163s avg, unchanged). This **falsified the fetch-bound hypothesis**. Disabled the flag (back to proven baseline); code kept for any future fetch-bound regime.
6. **TRUE bottleneck = COMPUTE, not fetch.** The ~150-165s cadence is dominated by SERIAL per-token work (ML fusion model + rug_bundle + trigger features + decision) over ~150-230 surviving tokens — CPU/GIL-bound, so async fetch parallelization can't help. `_SCAN_INTERVAL=30s` sleep is minor.

### Where it stands NOW (all healthy at 10:14Z)
- Guardrails INTACT: **PAPER_MODE=true** (live paused), **PROFIT_SWEEP_DRY_RUN=1** (sweep paused), PARALLEL_SCAN_DECISION_MODE=off, PARALLEL_TICK_MODE=on, CHART_DS_PRIMARY=on.
- ⚠ NOTE for AxiS: LIVE_CONFIRMED=true is still set, but it is HARMLESS while PAPER_MODE=true (no live route without PAPER_MODE flipped). The ONLY switch between paused and live is PAPER_MODE — by design (go-live runbook), but flagged so you know.
- Health green: no tracebacks/scan/tick errors across ~5h monitoring; 429s low (1-4/window, all non-fatal GT fallback); signals flowing every cycle.

### RECOMMENDATIONS for AxiS (NOT built — these need you awake; some touch the money path / architecture)
- (a) **Profile per-token compute** (fusion model vs rug_bundle vs trigger features) to find the real ~0.5-1s/survivor cost — THE lever, since cadence is compute-bound.
- (b) **Dedicated fast re-check loop for the sticky/watched cohort** — re-price already-watched tokens every few seconds independent of the full 485-token sweep. This is the closest path to your "1-2s to fill" goal, because actionable dip tokens are usually ALREADY watched (their freshness, not the long tail of new tokens, is what matters for a fill).
- (c) Make `_SCAN_INTERVAL` env-tunable + trim (modest ~15%, free).
- (d) Fix the recurring stuck-token tax (e.g. 3nMFwZXwY1s1 timed out 5s on DS every cycle).

### Bottom line
No paid tier needed (you were right). Shipped a real cold-start cache win + a safe tick-parallel (inert until positions open). Honestly falsified the fetch-parallel lever for steady-state cadence and reverted it. The remaining latency is COMPUTE-bound and the next lever is an architecture call (b is the highest-leverage). Nothing live, nothing swept, nothing unproven left running on the buy path. Loop closed.
