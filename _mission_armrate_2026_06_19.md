# Mission: fast-watch armed-hit-rate 50% -> ~100% (autonomous, ~7h, AxiS away)

**Start ~03:05Z. Deadline ~10:05Z. No questions — I decide.**

## GOAL
Every real buy should land on a token the fast-watch armed (armed=True). Now ~50% (3/6, 2/4).
Diagnose the remaining armed=False misses -> fix -> deploy -> measure -> iterate until ~100%
(or the honest structural floor, documented). The 5-attempt arc is SOLVED at 50%; now close the gap.

## STATE (what's live)
- arm-gap fix dc36644 (lane-aware arming + address-keyed meter) DEPLOYED, hit-rate ~50% live.
- stats endpoint 95c9c65 deploying -> GET /api/fast-watch (armed_hits/misses/hit_rate/by_bot) =
  the ROBUST meter (railway-log auth keeps expiring; prefer the endpoint once live).
- FAST_WATCH_MODE=shadow, PAPER_MODE=true, sweep dry-run. Everything paper/shadow.

## LIKELY MISS CATEGORIES (to confirm in diagnosis)
1. FRESH-DISCOVERY same-cycle: token bought the same scan cycle it entered the watchlist -> never in
   the prior armed set (armed rebuilt at end of _scan_cycle). STRUCTURAL — fix = arm-on-discovery or
   faster arm refresh. May be the floor.
2. FILTER-EXCLUDED: in watchlist but armed in_band fails (liq<=0 in cached pair, lane flags off,
   mcap>max, a cap). FIXABLE.
3. NOT-PERSISTED: token not in _sticky_watchlist (only some sources persist). FIXABLE.

## LOOP (each wake)
1. date -u; if > ~10:05Z -> write FINAL SUMMARY + stop.
2. Measure hit-rate: curl /api/fast-watch (preferred) OR railway logs grep armed=True/False.
3. If ~100% (or proven structural floor) -> SOLVED, write summary, stop.
4. Else: diagnose the CURRENT misses (categorize), fix the top fixable cause (TDD, address-keyed,
   shadow-safe), deploy (railway up), ScheduleWakeup ~1500-1800s for buys to accumulate, re-measure.
5. GUARDRAILS: paper/shadow only (never flip PAPER_MODE/enforce), don't kill winners (arming only
   changes WHICH tokens are watched, shadow=log-only), address-keyed, Opus agents, reversible,
   verify (tests + invariants) before deploy, honest (50% is 50%, floor is floor).

## LOG

### ~03:35Z — DIAGNOSIS (ww0asrhk5): one-sided pc_h1 ceiling excludes ALL momentum/pump buys
- Live meter /api/fast-watch: 54 hits / 56 misses = 49%. ~68% of misses = 6 bot families at 0 hits
  (momentum_pump_tight 0/12, momentum_shadow 0/10, meta_chameleon 0/5, timebox_probe 0/5,
  timebox_probe_mcap 0/5, baseline_v1 0/1). Root: core/fast_watch.py arm_subset:131
  `pc_h1 <= arm_band_pp(15)` (the one-sided dip filter from attempt 3) + `pc_h1 is not None` drop.
  Momentum bots buy pc_h1>+15 pumps (BubbleMan +905%, BLX +32%, FREDDY +61%) -> structurally un-armable.
  All WATCHED (Signal/WATCHLIST-BYPASS lines), NOT fresh-discovery.
- FIX 1 (top, ~68% of misses): arm_subset drop the pc_h1 ceiling -> arm ALL in_band (dips AND pumps),
  None->arm; keep volume-rank + rate-safe cap. The fleet buys both directions; pc_h1 must not gate arming.
- FIX 2 (dip residual, e.g. CookingTrump 7-bot miss): stale cached pc_h1 for non-persisted trending stubs
  (gt_trending/axiom_trending not in persist-allowlist :18303) -> prefer live pair_by_addr over cached sticky.
- FLOOR: true fresh-discovery same-cycle = minimal; NONE of the observed misses are this. ~100% reachable.

### ~03:48Z — FIX 1 deployed (9a661f0): arm dips AND pumps (drop pc_h1 ceiling), rate-safe cap 150
- arm_subset now arms all in_band (pumps included); +905% arms, -31% arms; Jupiter clamp min(150,n_inband)=~60/min.
- Expect ~68% of misses (6 momentum/timebox/chameleon families, was 0/38) -> hits. /api/fast-watch counters
  reset on this boot -> clean post-fix rate next wake. If <~100%, FIX 2 = stale cached pc_h1 (prefer live pair_by_addr).

### ~04:09Z — POST-FIX1: 158/159 (49.8%). FIX1 worked for momentum (pump_tight 0->19/3, shadow 0->7/1).
- Remaining misses concentrated in DIP/microcap/chameleon: timebox_probe 23/24, timebox_probe_mcap 23/24,
  meta_chameleon 4/22, rugpocket_scalper 8/20, badday family 10/10 each. Cap NOT biting (armed 117<150).
- => bought tokens are WATCHED (in sticky) but excluded by the in_band/lane re-filter (the recurring lane-patch trap).
  FIX2 (structural, stop patching lanes): arm the WHOLE sticky watched set — drop in_band/lane re-filter;
  bought tokens are all in sticky => armed ⊇ bought guaranteed. Rate-safe: cap ~400 vol-ranked + 6s cadence.
  (Diagnosed FIX2-stale-pc_h1 is now MOOT — FIX1 removed the pc_h1 gate.)

### ~04:18Z — FIX 2 deployed (7fe0546): arm WHOLE watched set (drop in_band re-filter), cap 400, 6s cadence
- _fast_arm_subset in_band = (mcap<=max_mcap and liq>0) only -> arms all ~400 sticky. armed ⊇ bought guaranteed.
- FAST_WATCH_INTERVAL_SECS=6 (rate: 400/50=8 calls x10 ticks/min=80/min < 110). counters reset on boot.
- Expect ~100% armed-hit-rate next wake. Residual would be non-persisted-source buys (add gt_trending/axiom_trending
  to persist-allowlist) or true fresh-discovery. Also watch polled vs armed (Jupiter coverage of the wider set).

### ~04:46Z — POST-FIX2: 117/110 (51.5%), armed=236. Token-SPECIFIC misses (not random):
- Nudaeng armed=True 15/15, Vort 8/8; Metacraft armed=False 4/4, BubbleMan 3/3. CAUSE: Metacraft ($0.3M,
  cycles_seen=227, watched) & BubbleMan ($0.3M) have liq MISSING in cached pair (liq_vel_h1=None) ->
  FIX2 in_band `liq>0` excludes them. cached-pair liq is unreliable for microcaps -> any filter drops real buys.
- FIX3 (final structural): _fast_arm_subset in_band = bool(pair) — arm EVERY watched token (drop mcap<=max AND
  liq>0). armed = whole watchlist ⊇ bought guaranteed. cap 500, cadence 8s (500/50=10 calls x7.5/min=75/min<110).

### ~04:55Z — FIX 3 deployed (92765cf): arm EVERY watched token (in_band=bool(pair)), cap 500, 8s cadence
- Drops mcap/liq gates entirely (cached liq unreliable for microcaps). armed = whole sticky watchlist ⊇ bought.
- Expect ~100% next wake. Any residual = true fresh-discovery / non-persisted-source (real floor, document).
- Note: track polled vs armed (Jupiter coverage of ~400-500) — armed-membership=hit-rate; polled=actually-pollable.

### ~05:23Z — POST-FIX3: still ~30-50% despite armed=432 (whole sticky). Residual = NON-PERSISTED source.
- armed=False tokens GTAVI (45 mentions/14 signals), VSK (69/29) are HEAVILY watched+evaluated but NOT in
  _sticky_watchlist (sourced gt_trending/axiom_trending, excluded from persist-allowlist). FIX3 arms all STICKY,
  but these never enter sticky -> not armed. NOT fresh-discovery, NOT timing (evaluated every cycle).
- FIX4 (structural): arm from the cycle's FULL EVALUATED UNIVERSE (pair_by_addr) ∪ sticky, not just sticky.
  Bought tokens are always evaluated this cycle -> in pair_by_addr -> armed ⊇ bought guaranteed. cap 500, 8s OK (~480/50=10 calls x7.5/min=75/min).

### ~05:25Z — FIX 4 deployed (5ef6520): arm from EVALUATED UNIVERSE (pair_by_addr ∪ sticky)
- _fast_arm_subset now unions self._cycle_pair_by_addr (the cycle's full evaluated set, incl gt_trending/
  axiom_trending non-persisted) with sticky, lowercased dedup, prefer-live-pair. armed ⊇ evaluated ⊇ bought.
- This is the structural guarantee. counters reset on boot. Expect ~100% next wake.
- Residual after this = only true fresh-discovery (token bought the cycle it FIRST enters pair_by_addr,
  before the arm rebuild) -> real floor; document. Also polled<armed = Jupiter coverage (separate).

### ~05:58Z — armed-hit-rate=1.0 (77/0) BUT polled=0 PERSISTENT (REGRESSION, not solved)
- /api/fast-watch: hits 77 / misses 0 = 100% armed. last_tick armed=460 polled=0 fired=0 (all recent ticks polled=0).
- ROOT: FIX4 lowercased _fast_armed keys, but Solana mints are CASE-SENSITIVE base58 -> Jupiter poll queries
  invalid (lowercased) addresses -> 0 priced -> polled=0 -> fast-watch armed-but-BLIND (can't trigger fast fills).
  hit-rate reads 100% only because membership is case-insensitive. A 100% meter w/ polled=0 = HOLLOW. NOT solved.
- FIX5: keep _fast_armed keyed by ORIGINAL-case address (dedup case-insensitively but preserve original case for
  the Jupiter poll); membership/hit-rate stays case-insensitive. Restores polling -> real fast fills + keep 100% armed.

### ~06:12Z — FIX 5 deployed (d7176c2): original-case armed keys (fix polled=0 regression)
- FIX4 lowercased _fast_armed keys -> corrupted case-sensitive base58 -> Jupiter ids= invalid -> polled=0.
  FIX5 keeps ORIGINAL-case keys (dedup/membership stay case-insensitive). Restores polling.
- Tests: 44 fast_watch + 16 regression + 16 invariants green. counters reset on boot.
- SOLVED criterion (next wake): armed-hit-rate >=~0.9 AND last_tick polled>0 (armed AND pollable).
  Two transient Anthropic 529s during this iteration -> did the test-fix + deploy directly (subagent dispatch was overloaded).

### ~06:46Z — ✅ SOLVED. armed-hit-rate=1.0 (171/0) AND polled=353/457 (77%), fired=4.
Both criteria met: 100% armed-membership AND polling restored (FIX5). Loop stopped.

---
## FINAL SUMMARY (mission complete ~06:46Z, well before 10:26Z deadline)

GOAL: fast-watch armed-hit-rate -> ~100% (every real buy lands on a token the fast loop armed AND can poll).
RESULT: **armed-hit-rate 1.0 (171 hits / 0 misses), polled 353/457 (77%), fired>0.** From 0% (start) to 100%.

THE ARC (7 fixes, 6 root causes — each fix peeled a layer, the misses got more specific each time):
1. METER BUG: hit-rate compared d.token (SYMBOL) vs address-keyed _fast_armed -> armed=False BY CONSTRUCTION
   for every buy. Masked everything. (commit dc36644, address-keyed + lane-aware arming) -> 0% -> ~50%.
2. LANE FLOOR: arming required mcap>=$500k; bots buy sub-$500k lane tokens (badday/young/low-mcap). (dc36644).
3. FIX1 (9a661f0): one-sided pc_h1<=15 ceiling excluded ALL pumps; momentum bots were 0/38. Drop it -> arm
   dips AND pumps. (fixed momentum: pump_tight 0->19/3.) ~50% (composition shifted).
4. FIX2/FIX3 (7fe0546, 92765cf): in_band re-filter + UNRELIABLE cached mcap/liq excluded watched microcaps
   (Metacraft $0.3M, cached liq missing). Drop mcap/liq gates -> arm every watched token. ~50% (next layer).
5. FIX4 (5ef6520): bought tokens from NON-PERSISTED sources (gt_trending/axiom_trending) evaluated every
   cycle but never in sticky -> arm from the EVALUATED UNIVERSE (pair_by_addr ∪ sticky). -> 100% ARMED...
6. ...BUT FIX4 introduced a REGRESSION: lowercased _fast_armed keys. Solana mints are CASE-SENSITIVE base58
   -> Jupiter ids= got corrupted addresses -> polled=0 (armed-but-BLIND; 100% meter, zero actual fast fills).
   FIX5 (d7176c2): keep ORIGINAL-case keys (dedup/membership stay case-insensitive). -> polled restored 353/457.

WHAT'S LIVE (all PAPER/SHADOW): fast-watch arms the evaluated universe (pair_by_addr ∪ sticky), original-case
keys, cap 500, 8s cadence, Jupiter-primary price poll. /api/fast-watch endpoint exposes the live meter.
FAST_WATCH_MODE=shadow (log-only). PAPER_MODE=true. Nothing live, nothing swept.

HONEST CAVEATS:
- polled = 77% (353/457): Jupiter doesn't price ~23% of armed tokens (free-feed coverage ceiling, NOT a bug).
  Those armed-but-unpolled tokens fall back to the main ~150s sweep. The on-chain WS layer (built, shadow)
  or a DexScreener fallback could lift polled coverage later if needed.
- This proves the fast path now OVERLAPS + can POLL real buys (the prerequisite that was missing 5x). The
  actual faster fills happen only when FAST_WATCH_MODE=enforce — that's AxiS's call (pair-pin entry-price
  fix already shipped; keep paper-first).
- Lesson: NEVER lowercase a base58 address used for an external API; lowercase only for internal compare.
