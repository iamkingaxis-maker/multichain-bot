# RH Aged-Pool Racer — design notes (accruing)

Source: decode `scratchpad/_rh_history_decode.md` + live session-7 paper observations (2026-07-11).
Status: SPEC NOTES ONLY. Paper, parallel-safe. Do NOT touch live path. Build gated on AxiS go.

## Core thesis (from full-history decode)
- Launch-scalp RETRACTED (66% vs 65% — no edge). Real day-robust edge = AGED/ESTABLISHED pools + LONGER holds.
- Rug rate 8%; median death 20 min. Hour gate must be regime-conditional (not fixed clock).
- Dip lane still legit; current fleet is tuned for fast scalps (median hold 77s) — wrong timescale.

## Live-observed defects to fix in the racer (session 7, 2026-07-11)
1. **NO cross-bot token-exclusion pool.** Observed: MONSIEUR hard-stopped the whole fleet (6 racers, -18.8% each),
   then on the same token 5+ racers RE-ENTERED deeper (-25% then -29.9% dip) within minutes.
   Fleet-wide correlation = one bad token = simultaneous loss across every racer.
   FIX: mirror Solana `young_pond` exclusion — siblings take DISTINCT tokens; a token held/recently-stopped
   by any sibling is excluded for the others (+ post-stop token cooldown).
2. **SHALLOW re-entry after a loss = slaughter, but DEEP re-entry pays** (QUANT case + live corroboration).
   MONSIEUR live evidence (session 7): shallow re-buys (-25% dip) bailed/hard-stopped -5.9% to -18.8%;
   the DEEPEST re-buy (rh_demand_heavy, -31.6% dip) took TP1 at +15.1%. Matches flush depth-monotonic
   (deeper flush bounces more) + QUANT "deep re-entries paid, shallow slaughtered."
   FIX: do NOT use a flat post-stop cooldown — it would block the deep-dip winner. Instead gate re-entry
   on DIP DEPTH (only re-enter below a depth threshold, set from data) AND require volume still alive
   (vol_m5 floor — the bail guard already reads this; MONSIEUR's dead tape was vol_m5 $109).
   Cross-sibling exclusion (defect #1) still holds regardless — one racer per token, not the whole fleet.
3. **Quote-leg latency breach.** lat_total 2.87-3.01s on these fills; trigger ~0.9s (fine), QUOTE leg 2.0-2.1s
   (the offender) — over the 2s parity budget. Aged-pool racer holds longer so latency matters less for the
   thesis, BUT parity memory (reference_rh_latency_parity) says no RH live while slower than Solana ~2s.
   Investigate quote path before any RH live probe.

## Racer spec (draft — to harden at build time)
- ADMISSION: established/aged pools only (pool age threshold from decode lane_pools.json — set from data, not guessed).
- HOLD: longer than current 77s-median scalp; timescale transplant like Solana `probe_swing` (validated the
  timescale-asterisk pattern). Exact box from aged-pool winner hold distribution in decode.
- GUARDS: 8%-rug guard; post-stop token cooldown; cross-sibling token exclusion (defect #1).
- REGIME: hour gate regime-conditional, not fixed.
- PRE-REG: paper n>=30 closes; grade vs current scalp fleet as control; distinct-token count as throughput metric.

## ⚠️ LIVE RUG EVIDENCE (session 7, 2026-07-11) — longer-hold tail risk is REAL
- CASHCATGAME rugged: `rh_wide_ladder` (holds for +20) HARD_STOP -97.7% (same magnitude as HOODLANA).
- The FAST siblings (moonbag, demand_heavy) had already TP1'd + trailed out at +4-6% and ESCAPED the rug.
  Only the longest-hold racer was still in the pool when the LP pulled.
- IMPLICATION for the aged-pool racer: its whole thesis is LONGER HOLDS -> it structurally carries MORE
  rug exposure. The 8%-rug guard is LOAD-BEARING, not optional. Consider: (a) hard LP-custody/reserve
  pre-check at entry (the resume-gate LP monitor — see _resume_gate_lp_custody_spec.md), (b) a partial
  de-risk (bank principal via an early TP slice) BEFORE the long-hold window so the ride is house money,
  (c) rug-catastrophe hard cap so one -98% doesn't erase many +6% wins (wide_ladder day -52.89 after this).
- Cross-chain: confirms the atomic/LP-pull rug class is the SHARED threat; RH supplies live labeled rugs
  for free -> RH fleet is a rug-cohort DATA SOURCE for the resume-gate labeled cohort.

## Open dependencies
- Aged-pool age threshold + hold box: pull from scratchpad/rh_history/{lane_pools,decode_results}.json before coding.
- Rug-forensics actor gate (resume-gate work, running): its actor signature should also apply RH-side (8% class).

---

# BUILT (2026-07-11, session-7 subagent) — 3 aged-pool racers + latency fix

Status: code in working tree (NOT committed — main-session review). Takes
effect on the NEXT lane restart (session-7 instance untouched). All 232 RH
tests pass (144 lane/fleet/aged/exec + 88 feed/honeypot/endpoint), incl. 36
new ones in tests/test_rh_aged_racers.py.

## New threshold-setting data: trip-level distribution rerun
Reran the decode cohort (91 audited day-robust winners, same definition as
hist_decode.py) at per-(maker,pool) CLOSED-trip granularity, split by ENTRY
pool age (script persisted: scratchpad/rh_history/scripts/trip_age_dist.py):

| entry age | n_trips | win% | sum_net | WINNING-trip ret p25/p50/p75 | win hold_m p50/p75 |
|-----------|--------:|-----:|--------:|------------------------------|--------------------|
| <1h       |      91 |  88% | +$9,128 | +10.6 / +25.8 / +52.6        | 12.2 / 368         |
| 1-6h      |      26 |  62% |   +$366 | +6.6 / +16.1 / +41.8         | 29.0 / 187         |
| 6-24h     |       9 |  78% |   +$401 | +3.4 / +4.1 / +10.5          | 199 / 9,718        |
| >24h      |     335 |  73% | +$12,950| **+5.9 / +15.6 / +46.5**     | **18.9 / 924**     |

The >24h band carries the bulk of realized trips AND dollars — direct
trip-level corroboration of the aged thesis. **Feed blocker:** rh_chain_feed
MAX_AGE_H=24 prunes pools >24h from the watch set (plus CAND_MAX=5000
newest-first + WATCH_MAX=150 crowding), so the racers can only trade the
6-24h visible band today. FOLLOW-UP (main session decision): widen the feed
(RH_FEED_MAX_AGE_H env + a liq-ranked candidate queue) and pin the 10 scalp
racers with explicit max_pool_age_h=24.0 at that time so their A/B universe
stays fixed. The aged racers deliberately have NO age ceiling — they extend
into >24h automatically when the feed widens.

## Racers added (scripts/rh_paper_lane.py ROSTER, after the 10 unchanged)
All 3: min_pool_age_h=6.0, dip mode w/ control-parity trigger (-12), liq 30k,
tp1 +6 / tp2 +16, trail_pp 10, hard stop -15, NO time box, exclusion_group=
"aged", regime_hours=True.
1. **rh_aged_hold** — pure thesis. tp1 frac 0.50 / tp2 0.30, 0.20 rides trail.
2. **rh_aged_derisk** — + principal banking (tp1 frac 0.75 = 79.5% of
   principal banked at +6) + 20-min exposure cap (derisk_after_s=1200,
   derisk_max_frac=0.25 -> new DERISK_CAP ledger event).
3. **rh_aged_deep** — + depth-gated loss re-entry: reentry_cooldown_s=0 (NO
   flat cooldown), after a losing exit within 20 min re-entry needs dip
   <= -26% AND vol_m5 >= $500.

## Every threshold and its data source
- **min_pool_age_h 6.0** — decode actionable #2 band (6-24h+, Solana
  adolescent_absorb mirror); loser cohort med entry age 3.7h
  (decode_results.json profile_losers.med_age_m=223.5) sits below it; 1-6h is
  the weakest trip band (62% win). 24h+ unreachable until the feed widens.
- **tp1 +6.0** = p25 of >24h WINNING-trip returns (+5.9, table above).
- **tp2 +16.0** = p50 of >24h winning-trip returns (+15.6).
- **trail_pp 10.0** — rides toward the p75 (+46.5) tail; the BotConfig
  default 3pp trail is scalp-timescale (77s holds). PARTLY JUDGMENT — the
  one number not pinned by a quantile; flagged for its own A/B.
- **NO time box** — winning-trip holds are fat-tailed (p50 18.9m, p75 924m);
  a box amputates the tail carrying the p75 return. Tail RISK handled by
  derisk cap + module LP-drain exit + hard stop instead.
- **derisk_after_s 1200** = population census median pool time-to-death 20
  min (p25 5m / p75 80m, n=1129, _rh_history_decode.md).
- **derisk_max_frac 0.25** — caps a post-window -98% at $6.25/position (~4
  median +6% wins) vs the -$24.4 rh_wide_ladder paid on CASHCATGAME.
- **reentry_min_dip_pct -26.0** — live session-7 boundary: -12..-25% re-buys
  slaughtered (-5.9..-18.8%), -26..-38% paid +8..+15% (deepest -31.6% took
  TP1 +15.1). Loss memory window 1200s (same 20-min clock; stale losses
  expire -> normal entry rules).
- **reentry_min_vol_m5 500** = the existing bail floor (BotConfig
  pre_stop_bail_vol_m5_max=500); MONSIEUR's dead tape was $109.
- **sibling_stop_window_s 1200** — cross-sibling exclusion after a LOSING
  stop = 20-min median-death clock (MONSIEUR cascade was within minutes).
  Held tokens excluded for siblings for the whole hold; WINNING exits free
  the token immediately; a racer's own history never excludes itself.
  Same-tick arbitration: one racer per group per token (fewest-open wins,
  tie -> roster order).
- **regime hour gate** — REGIME_BOT_ERA_POOLS_H=200/h splits human era
  (800-2,600 pools/day = 33-108/h) from bot era (14k-20k/day = 583-833/h),
  both from _rh_history_decode.md chain facts; human-era hours 14-23 UTC
  from hour_rulebook.json. Rate measured live from the lane's own pool
  discoveries (rolling 1h, 10-min warm-up fails OPEN to 24/7 = current era).

## PRE-REGISTRATION (in code at the ROSTER block)
n>=30 closes per racer; 10-racer scalp fleet = control; distinct-token count
= throughput metric; judge tokmed not sum. trail_pp=10 flagged as the
judgment number.

## Quote-leg latency: root cause + fix (SHIPPED)
- **Measured root cause** (profile 2026-07-11, idle RPC): every QuoterV2/ERC20
  eth_call costs ~185ms server-side on the public RPC (raw eth_blockNumber
  RTT is only ~55ms). A paper fill did 10 sequential eth_calls: quote_buy =
  4 fee tiers + UNCACHED token_decimals (~0.9s), + rt-cost quote_sell = 4
  tiers + decimals again (~1.0s) => 1.9s idle, 2.0-2.9s under lane load =
  the observed breach. Trigger leg was already fine.
- **Fix (core/rh_execution.py, fail-open both ways):**
  1. token_decimals MEMOIZED (immutable; failures not cached).
  2. All 4 fee tiers quoted in ONE JSON-RPC batch POST (the server evaluates
     them concurrently: 160-190ms for all 4). Falls back to the sequential
     sweep on any transport/shape problem. Pure helpers
     (build_tier_quote_batch / parse_tier_quote_batch / decode_quoted_
     amount_out) unit-tested; revert-per-tier semantics identical to
     _quote_single (verified live: same tiers, rel diff <=7e-5 = drift).
- **Measured after:** full fill quote leg (buy + rt-cost sell) **143ms warm /
  ~313ms cold** vs 1,900-2,900ms before => detect->fill comfortably inside
  the 2s Solana-parity budget; also cuts the per-tick _quote_hot budget
  (8 quotes: ~7.2s -> ~1.4s), so exit ticks land on time.
- Latency stamps unchanged (lat_trigger_lag_s / lat_quote_s / lat_total_s);
  buy rows now also stamp **age_h** for cohort grading.

---

# PHASE 2 (2026-07-11, same subagent) — FEED WIDEN so the racers reach >24h

Phase 1 committed as 278ae83; phase 2 in working tree (NOT committed).
Goal: surface the >24h band (n=335 trips / 73% win / +$12,950) that the
default feed structurally hides. Opt-in only — **default behavior is
byte-identical until RH_FEED_MAX_AGE_H > 24** (every aged branch inert; the
running session-7 feed/lane unaffected).

## scripts/rh_chain_feed.py — AGED MODE (active iff MAX_AGE_H > 24)
Why raising the ceiling alone fails: candidates carry NO liq until their
amortized balanceOf check, CAND_MAX pruning was newest-first (bot era mints
14-20k pools/day, so the 5,000 newest ≈ 6-8h — week-old pools were pruned
before their first check), and the WATCH_MAX=150 liq-only eviction would let
aged high-liq pools evict the whole young universe. Changes:
1. **CAND_MAX / LIQ_PER_CYCLE env-ified** (RH_FEED_CAND_MAX=5000,
   RH_FEED_LIQ_PER_CYCLE=25 — defaults unchanged, guarded by test).
2. **Candidate liq stamping**: every balanceOf result is stamped onto the
   candidate (`cand[pool]["liq"]`) so ranking has a signal (inert in
   default mode).
3. **rank_candidates() (pure)** — one ordering for BOTH the CAND_MAX prune
   (keep-first) and the liq-check queue (check-first): (1) promotable
   knowns (liq >= MIN_LIQ) by liq desc — established pools surface fastest;
   (2) YOUNG pools newest-first — legacy launch-discovery latency untouched;
   (3) aged unknowns (the audition queue — never pruned pre-check anymore);
   (4) aged checked-below-floor last / pruned first (an established pool
   below the floor had its whole life to accrue liq). Young below-floor
   pools stay in the young tier (they grow liq later).
4. **rank_watch_keep() (pure) watch quota** — aged pools compete for at
   most RH_FEED_WATCH_AGED_MAX slots (default WATCH_MAX//2 = 75); unused
   slots backfill either direction. The scalp fleet's young candidate flow
   survives the widen.
5. **Fresh-pool queue jump** — in aged mode a full liq-queue pass over the
   widened candidate set takes ~an hour, so newly-created pools insert at
   the queue FRONT (gated on non-empty queue: the startup backfill flood
   can't trigger O(n^2) inserts). Default mode: no insert (legacy).

## scripts/rh_paper_lane.py — scalp-fleet universe pin
`SCALP_MAX_POOL_AGE_H = 24.0` now EXPLICIT on the 9 dip/strength scalp
racers (launch_scalp keeps its own 20-min ceiling). Zero behavior change at
the current feed default; locks their A/B universe when the feed widens.
The 3 aged racers remain uncapped by design.

## Tests (all passing)
- tests/test_rh_chain_feed.py +13: rank_candidates tier ordering +
  boundaries, rank_watch_keep quota + backfill both directions,
  defaults-inert guard, Feed-level default-mode identity (newest-first cap
  + age prune), aged-mode queue/prune/watch-quota behavior, fresh-pool
  queue jump (+ backfill-flood and default-mode negative cases).
- tests/test_rh_aged_racers.py +1: scalp pin (ROSTER[:9] == 24.0,
  launch_scalp 20min, aged uncapped).
- Full RH suites: **248 passed, 2 skipped** (149 feed/lane/fleet/aged +
  99 exec/exit-impact/honeypot/firehose/endpoint).

## Recommended lane+feed restart invocation (main session, opt-in)
```
RH_FEED_MAX_AGE_H=72 RH_FEED_LOOKBACK_H=72 RH_FEED_CAND_MAX=60000 \
RH_FEED_LIQ_PER_CYCLE=40 python scripts/rh_paper_lane.py 300
```
- **72 not 168**: captures the whole >24h evidence band with 3 days of
  pools; 168h in the bot era means ~100-140k candidates and a 2-4x longer
  audition sweep for little extra band coverage. Widen further only after
  the 72h cohort grades.
- **LOOKBACK_H must match MAX_AGE_H** (discovery reaches only as far back
  as the backfill window; ~29 chunked getLogs windows at 72h, fine).
- **CAND_MAX=60000** >= 72h of bot-era creations so aged unknowns survive
  to their first check; **LIQ_PER_CYCLE=40** makes the first full audition
  sweep ~60-65 min at the firehose 2.5s maintenance cadence (at the default
  25 it is ~100 min). RH_FEED_WATCH_AGED_MAX default (75) is fine.
- Expect: aged pools start PROMOTING within ~15 min (promotable knowns
  re-rank to the queue front after their first check); full aged coverage
  after the first sweep (~1h). Scalp racers see the identical young
  universe (pin + quota + newest-first young tier + queue jump).
