# Session Handoff — Smart-Wallet Rescue + Sustainability Day (2026-06-11)

**Bot URL**: https://gracious-inspiration-production.up.railway.app
**Mode: PAPER throughout** (`live_mode: False` verified after every deploy). No PAPER_MODE flip.
**HEAD**: `7756040`. 31 commits today on top of yesterday's 17 (the 06-10 "Bad-Day Playbook Day"
record is preserved below). Suite **692 passing**.

**THE HEADLINE: smart wallet went from "bleeding heavy, not ready" (AxiS, morning) to
effectively POSITIVE on the day (-$25.83 hot + $30 banked = +$4 since the 04:19 pool epoch,
peak recovery +$58 in under an hour after the bleed-cut). The fix was surgical, evidence-named
wallet cuts + size discipline + letting winners run — and the day ended with the
SUSTAINABILITY ENGINE built: a daily wallet-cycle loop (recruit -> vet -> judge -> cut) that
executed its first enforcement (V21GW8P, copy-tax TOXIC) within minutes of existing.
AxiS: "this is the type of daily profit im looking for... the correct cycling of new wallets
will be the key."**

---

## SHIPPED + DEPLOYED today (all paper, verified; newest first)

1. **WALLET CYCLE engine** (`4195d0a`, `scripts/wallet_cycle.py`) — the sustainability loop.
   Daily: DORMANCY (>36h silence = rotation cut) -> COPY-TAX verdicts (TOXIC at n>=10
   our-closes -> cut) -> vetted daily-positive RECRUITS promoted to consensus seats ->
   roster floor [6,12]. `--apply` executes the mechanical pre-registered rules with backups;
   pod seats never auto-assigned. **First run cut V21GW8P** (-$1.35/close, n=10 post-overhaul
   — the lifetime-COPYABLE wallet stopped being copyable under the new system). TAXED watch:
   HmP3 (-0.43/49), 45Sn (-0.10/167). k2 pod down to HmP3 until a recruit earns the seat.
2. **+4 daily-positive harvest keepers** (`1d2529d`): watchlist 6->10->9 (after the cycle cut).
   Wide-harvest (runner-recurrence + elite-cluster funnels) -> diversity scorer on a widened
   4-provider RPC pool (mainnet-beta, leorpc, publicnode, drpc — per-provider limits = ~4x
   headroom): 9 SELECTORS, 4 cleared the daily-positive bar: **1eveYYxZ (100% rWR, +5.39
   SOL), 2qnHs8fZ (25 tok, 100%), EGwERj1 (22 tok, 100%), HcLMmNx9 (42 tok, 75%)**.
   Heavy-history front-runners (AgmLJBMD = the documented 115-win reference, Em8J3gBW,
   gasTzr94) keep RPC-timing-out — queued for slow-paced re-score.
3. **Fleet token cap built then REVERTED same hour** (`35abf19`->`c0ca2eb`): AxiS — "fleet
   buying bad tokens is a sign of weakness across our bots, not a fleet issue." The cap also
   corrupts the selection instrument (bot #13's record depends on 12 neighbors). Memory
   strengthened (feedback_fleet_is_selection_instrument) — do NOT rebuild.
4. **Deploy 502s killed + daily-loss floor ENFORCED + attention flags** (`2a4020f`):
   - Web server binds BEFORE the ~2.5min fleet load; /api/stats answers {warming:true}.
   - `RISK_FLOOR_MODE=enforce` set on Railway: shadow data showed post-halt buys ran 51% WR
     / -$0.70/tr vs 60% baseline (net -$48 avoidable) — per-bot daily_loss_limit_usd +
     max_token_buys_per_day now BLOCK. A go-live prerequisite now aging in production.
   - `ds_boosts_active / ds_dex_id / ds_labels / ds_has_socials` stamped into entry_meta
     (the bad-day boosted-runner signal becomes minable).
5. **Funnel decomposition stamps** (`1a8ef02`): every unconverted smart_follow fire now logs
   {type:fire_unconverted, reason} — 6 named block points (low_score/already_holding/
   daily_limit/security_*/chart_dip_check/chase_guard/stale_score). Background: post-overhaul
   funnel was 14% converted / 16% flush-blocked / 6% already-open / 63% unnamed — now
   self-naming forward. Early pattern: security_BLOCK on convex fires (pre-registered:
   revisit only if >70% of convex fires at n>=30, judged vs blocked fires' universe outcomes).
6. **Permanent latency+conviction instrumentation** (`4b579ea`): every smart_follow position
   carries follow_fire_ts/price/tier/conviction_mult (buy size vs the wallet's 40-buy rolling
   median) — chase tax + latency now daily-auditable on closes. Also fixed sync --full to
   UPGRADE trimmed cache records (628 upgraded on first run) -> scorecard dial section went
   live: **first graded forecast = HIT (06-11 dial 0.5 vs realized -$11), 1/1**.
7. **TP1 fraction 0.65 -> 0.35** (`4c95721`): exit replay on 120 post-gate closes (per-trade
   peaks): entries already produce the convex shape (median peak +7.6%, p90 +18%); the 0.65
   dump capped it. Replayed +2.35%/tr -> +3.37 (+43%). Trail conclusions NOT trusted from
   replay (can't model continuation) — trail stays 4pp. Env SMART_FOLLOW_TP1_FRACTION.
8. **Daily-positive wallet finder** (`c9c9923`, `scripts/find_daily_positive_wallets.py`):
   the proven funnel formalized — recorder runners -> mid-tier buyers ($30-3k, skip the
   earliest-10% MM zone) -> >=3-runner recurrence -> SELECTOR class -> net-positive realized.
   First pass independently re-found AgmLJBMD (validation the method works).
9. **THE BLEED-CUT** (`1ec9c1c`) — the day's turning point. Post-overhaul per-wallet fire
   attribution named the bleed: **2tYcXQCf -$48.50/32tok + D1aDZ -$30.15/38tok = -$78 of the
   -$84 pool drawdown**. Cut both + dormant Abk9Efh (2+ days silent = rotated) + GGduK5 (0%
   own-WR). Watchlist 10->6; solo pod seat Abk9 -> 2x99WSHD; default size $100 -> **$50**
   (env SMART_FOLLOW_SIZE_USD). Vindicating detail: cut 2tYcX resurfaced in the harvest with
   GOOD signal hits — its tokens are fine, our copies of it bled. Quality != copyable.
10. **Badday admission lane** (`7b33bc8`, `core/badday_lane.py`): the zero-fires audit found
    the scanner's admission layer discards the family's prey (31 flush + 5 momo qualifying
    microcaps overnight, ZERO reached evaluation — mcap floor 500k, $200k/day vol floor, and
    9 regime rejects incl trend_reversal/red_h24/no_dip/bs_h6/seller gates). Lane mirrors the
    young/low-mcap probe pattern: ADMISSION (50-500k, age>=6h, liq>=15k, pc_h1<=-20 or >=+30;
    `badday_admit` cycle counter) + CONTAINMENT (sub-floor tokens tradeable ONLY by
    microcap-mandate bots or user-watchlist — **controls/production universes unchanged**).
    Env BADDAY_LANE. Memory saved: `feedback_pipeline_trace_before_build` — trace the FULL
    upstream pipeline at design time (AxiS: "build it that way from scratch").

Overnight (pre-bleed-cut, from the 06-10 evening): zombie-resurrection guard (`522206a` —
manual sells survive deploy overlap; MINER/ZOOMER were sold twice and resurrected twice),
trail peak-restore fix (`c8c0a51`), the CONVEX 4th tier (`3a94c85` — $25 probes, K=1 capped,
no flush gate, TP1 0.10, their -15 cut; **first fire today**, latent k2/solo $200-sizing bug
fixed), max-chase guard + copyability board (`dd5a69e`), smart-wallet own capital pool
(`5b3ae8c` — $1000/$1000 floor, epoch 06-11 04:19, virtual hourly sweeps; **$30 banked before
the overnight giveback** = banks-the-peak working; /api/follow-capital).

## LATE-SESSION ADDENDUM (~15:00-16:15 UTC) — the "why can they and we can't" arc

AxiS: "why are these smart wallets able to detect these great buys, but we arent?"
The answer (from the 500-rtrip decode + universe data): (1) they trade the ATTENTION layer
before price — our features were all price-derived echoes of their buys; (2) they sit
upstream in the cascade (we detect their footprints; they are the feet); (3) they watch
each other (a web; smart_follow buys us a node); (4) **the dirty secret: their WR is 51%**
— they are not detectors, they are HARVESTERS (tiny probes, minute cuts, breadth, uncapped
+107% p90 tails). Nobody picks winners reliably in this market; they built a machine where
picking barely matters. Two builds followed:

A. **THE CONVEX WING** (`57eec7b`): our proven lottery-segment entries + the elite payoff
   curve, judged head-to-head vs their grind parents:
   - young_probe_stair_convex / young_probe_baseflow_convex / badday_flush_convex
   - $25 probes, TP1 +5 sells 10%, TP2 +25 sells 20%, 70% rides the 4pp trail,
     -15 hard cut (their median loser), -9 fast bail.
   - PRE-REG: convex must beat parent $/tr at n>=25 closes each, or retire.
   - Endgame barbell: mid-cap grind pond (floor) + convex lottery wing (tails)
     + smart-follow (copy the masters). Candidate set 19 + smart_follow tiers.

B. **ATTENTION FEED** (`c8e4351`, `core/attention_feed.py`): the social/attention layer
   tapped for FREE — DexScreener token-boosts/latest (boosts being PURCHASED right now),
   token-boosts/top, token-profiles/latest (marketing pushes + `cto` flag). All keyless;
   3 tiny payloads / 5min. Validation: Gaejook+Jotchua (this week's missed bad-day runners)
   sit on the boost leaderboard RIGHT NOW. The feed keeps first-seen history across
   restarts -> boost RECENCY + VELOCITY (the derivative is the signal). Every entry stamps
   attn_boost_total/latest/velocity, attn_first_seen_min, attn_on_top_board,
   attn_profile_fresh, attn_cto, attn_links_n. `/api/attention` = velocity board.
   SHADOW-FIRST pre-reg: no gate uses attn_* until boosted-vs-not validates on our own
   outcomes at n>=200 stamped entries. Env ATTENTION_FEED=on|off.

Also: convex-wing deploy verified paper; fleet-cap revert stands (see standing rules).

## EVENING ADDENDUM 2 (~16:15-20:15 UTC) — guards, free firehoses, and three deploy-amnesia bugs

14 more commits (HEAD `5a5ecba`, suite **692**). The arc: AxiS's observations drove every fix.

### Smart wallet guard stack (all ENFORCE, all logged per-fire)
- **DISTRIBUTION GUARD** (`afeefff`): roster sell on token within 10min -> veto fire (both eyes
  record sells: RPC sweep + PumpPortal). Env SMART_FOLLOW_DIST_GUARD(_SEC). FIRST-SHIFT REPLAY
  (~90min): 6 vetoes = 3 dodges (MASCOTS k3 would-have-fired into **-43%**), 2 flats, 1 missed
  winner (ZOOMER +10.8) -> net +$22.75 raw / ~+$10-13 ladder-modeled. Refinement theory: sub-minute
  scalper sells carry little info (ANTH flat) — weight by seller hold time at n>=20 vetoes.
- **WON-TODAY VETO + 1h cooldown** (`fead8fe`->`e6bd5e8`): "8 losses on 2 tokens" autopsy —
  elonbucks was a WINNER (+$24 net; red rows = remainder slices), Deniz was the flaw: morning
  episode won+closed, 17:32 re-fire bought the exhausted run (-$40). Gap analysis n=49: after-WIN
  re-fires negative in EVERY gap bucket (-$58 <24h, still neg >24h); after-LOSS at 6h+ = **+$78**
  (re-accumulation). AxiS pushed back on my blanket 24h ("memecoins change a lot") -> replaced
  with outcome-conditioned: 1h anti-spam cooldown (persisted follow_fired.json) + veto ONLY
  tokens already won today (FollowCapital.token_pnl_today, persisted, day-rolls). After-loss
  re-buys flow again. won_today_veto records in follow log.
- Stack now: flush gate, chase guard, dist guard, won-today veto, elite-exit, conviction stamps,
  fractional copy-tax board, own capital pool.

### THREE deploy-amnesia bugs (the named pattern: in-memory state dies at cutover; 10+ deploys/day)
1. Fire cooldown wiped -> persisted (follow_fired.json).
2. FollowCapital exposure wiped -> deployed read $0 while $67 remainders rode; re-register
   restored positions after pool wiring in main (`78d4209`).
3. (Yesterday: trail peak amnesia.) Anything in-memory MUST persist or re-derive on boot.

### Wallet pipeline at full speed
- **ALCHEMY KEY live** (AxiS signed up; `02d2ed6` core/rpc_pool.py: env ALCHEMY_API_KEY or
  gitignored alchemy_key.txt; Railway var set; Alchemy-first + 4 public fallbacks in scorer/
  cycle/strategy). Heavy-wallet mystery SOLVED: AgmLJBMD/Em8J3gBW/gasTzr94 = **UNFOLLOWABLE**
  (Jupiter/proxy custody — owner-based parsing sees zero swaps; our sweep COULD NEVER see their
  buys either). Scorer verdict added (`672ea3d`). Thread closed permanently.
- **Wide harvest @ Alchemy speed**: 124 candidates / 3 funnels (runners 351, elite-cluster 292,
  roster 8543 rows — funnel C format bug fixed). **7 FRESH bench candidates** in
  _wide_harvest_results.json: AxQRySJb (83% rWR, 59 ndist, 2-funnel), CuTgJYbT (80%/10rt),
  7rbxsXch (79%/14rt), 5Er9zJ1V (69%/16rt), 3fuga4 (60%, 2-funnel), Ar2Y6o1Q, 2Lsypd.
  Per protocol: BENCHED, need time-separated re-measure at morning ritual before seating.
- **TOMBSTONE LEDGER** (`bc7cc57` config/follow_cuts.json): harvest resurfaced 2tYcXQCf (cut
  same morning) at 78% rWR -> quality != copyable. Cuts recorded+excluded from recruits();
  --apply auto-records. Cycle reruns: udH4u cut on FRACTIONAL verdict (-$2.22/close n=15);
  recruits 1eveYYxZ/HcLMmNx9 SPARED (frac n=4-5 under bar — multi-count artifact). Fractional
  attribution now permanent in wallet_cycle (`375b4db`). Roster 8/12 + bench 8.
- **PumpPortal firehose** (`f734ebe` core/pumpportal_feed.py): free keyless WS — watchlist
  account trades PARSED in realtime (0 RPC; signature-dedupe vs sweep via _seen),
  migrations->migrations.jsonl, launch registry. /api/pumpportal. Env PUMPPORTAL_FEED.
- GMGN probed: Cloudflare 403, dead keylessly.

### Fleet: the silence audit (AxiS: "young probe hasnt fired in days")
11/46 enabled bots had ZERO buys since the 06-09 entry-stack enforcement. ROOT CAUSE: the stack
(age>=24h, mcap>=500k) is the structural OPPOSITE of the young pond; family never exempted.
- 6 young bots stack-exempted (`7b1947d`), 2 sub-500k mcapgate bots exempted + low_mcap_probe
  mandate (`36cc420`). badday family: exempt but lane admissions episodic — tripwire stands.
- Fleet ~280 buys/day vs 724 pre-stack = ~60% intended selectivity + 40% this bug.
- NEW RULE: when a gate ships, audit every existing bot against it (pipeline-trace BACKWARDS).
  Morning ritual gains a silence check (any enabled bot 0 buys/48h = flag).

### Infra
- **20:00 SERVER-WIDE STALL solved** (`5a5ecba`): io.dexscreener rate-limited us -> each fetch
  hung a thread 10s -> scanner's DS calls saturated the GLOBAL to_thread pool (~32) -> dashboard
  serialization starved, ALL endpoints 000, buys 25s apart. Fix: private 4-thread executor for
  DS, timeouts 10->5s, circuit breaker (5 fails -> 5min open -> GT fallback). Verified healthy.
- Dashboard Open Positions card = SMART WALLET ONLY (`42c6092`, AxiS request) — fleet probes
  live in the Bots tab. Sizes decoded: odd numbers = remainders after banked TP slices.
- Cycle recruits() recognizes harvest keeper format (`0393fa7`).

### State at 20:12 UTC
Pool: -$41.71 hot + $30 banked = -$11.71 effective (morning low -$84). Day: 121 closes 81W/40L
(67% WR) net -$14.24 — hit rate fine, damage was the 4 oversized losers the new guards target.
4 open: WAR $50 fresh + GABLE/WERLD/Percolator remainders (all green, GABLE +27%@7h).

### MORNING RITUAL (updated)
sync --full -> badday_scorecard -> goal_tracker --cache -> wallet_cycle (--apply mechanical)
-> re-score the 7 bench candidates (2nd measure; survivors fill seats) -> silence check
(enabled bots 0 buys/48h) -> dist-guard veto replay (grade blocks) -> convex-vs-parent check.

## EVENING ADDENDUM 3 (~20:15-21:35 UTC) — THE $100/DAY PUSH (AxiS-approved 3 levers + floors)

Goal status at 21:15: live-set **+$34** (12 candidates green, none red), streak 0. AxiS: "what
else can we do to reach $100/day?" -> the gap is MAGNITUDE not edge. Shipped (`bb1aa96`,
`7756040`, suite 692, paper verified):

1. **OFFENSE DIAL UNLOCKED — for the qualified only** (`core/live_set.py` + bot_evaluator):
   P7's 1.5x upsize leaves shadow, applied ONLY to walk-forward LIVE-SET members (bot computes
   the set server-side from the same sources as /api/goal; 30min cache; fail-soft empty =
   defense-only). Offense lifts via max() — the defense floor NEVER weakens. Env
   REGIME_DIAL_OFFENSE=live_set(default)|off. Observable: `tier=...+dial1.5` on sized buys.
   Rationale: size-is-the-bleed was size on UNqualified bots; this is size on bots that earned
   it, regime-gated by the dial (forecast record 1/1).
2. **GOAL METER @$100 NORMALIZED** (goal_tracker): new column = live-set P&L at uniform $100
   positions ("would going live at real size have made the goal"); STREAK now counts the
   normalized line. Today +34/+34 (young earners already $100-sized; diverges when $50 ponds
   carry the set).
3. **YOUNG POND THROUGHPUT**: light/candidate closed the SAME 2 tokens today (+$15.81 each —
   the day's best live-set earners, pure duplication). All 4 young probes now share the
   `young_pond` exclusion pool (siblings take DISTINCT tokens) + max_concurrent 3->5 (proven
   pair) / 3->4 (stair/baseflow). Same entries, ~2x distinct at-bats.
4. **DAILY LOSS FLOORS ON ALL 22 GOAL CANDIDATES** (`7756040`): audit found ZERO candidates
   carried daily_loss_limit_usd despite RISK_FLOOR_MODE=enforce live (momentum_shadow bled
   -$34 unchecked today). Floor = max(15, 0.6x base size) ≈ 4 stops' worth — variance room,
   cascade-day halt.

**Deliberate non-actions**: do NOT pool the pond clones (same-token duplication IS the A/B
experiment until a winner is promoted at n>=50); do NOT add more bots/strategies (bottleneck =
verdict speed, not idea count).

**The math**: today's +$34 = ~2 effective young shots, unleveraged, smart wallet unguarded most
of the day. Tomorrow runs ~4 distinct young shots x 1.5x dial x guarded follow engine x (maybe)
badday conversions. Day P&L state at close of session: live-set +$34, smart-wallet pool -$41.73
hot + $30 banked = -$11.73 effective, 67% WR (81W/40L).

**MORNING RITUAL (final form)**: sync --full -> badday_scorecard -> goal_tracker --cache (note
the @$100 column + streak) -> wallet_cycle --apply -> re-score the 7 bench candidates (2nd
measure; survivors fill seats 9-12) -> silence check (enabled bots 0 buys/48h) -> dist-guard
veto replay -> convex-vs-parent + dial-offense first grades -> badday lane tripwire (~13:30 UTC,
1m-confirmation layer is the suspect if still dry).

## FILL-FIDELITY VERDICT (trust checkpoint — PASSED)

GT minute-candle method (trade-log endpoint self-throttled): 11/14 of today's fills INSIDE
real candle ranges; sell median gap +0.27% vs mid, buys -0.74%. **Paper fills are honest on
thin books.** Note: tracker SELL records store $-received in exit_price (decode trap) — real
exit price = entry_price * (1+pnl_pct/100). Script: analysis/2026-06/_fill_fidelity.py.

## SMART WALLET — state of the machine (the "huge potential one")

- **Roster (9)**: HmP3Txu, udH4u, 4jkL4dN, 2x99WSHD, 45Sn4KL1 + recruits 1eveYYxZ, 2qnHs8fZ,
  EGwERj1, HcLMmNx9. Pods: k2={HmP3}, solo={2x99WSHD}, convex={2x99, 45Sn, HmP3}.
- **Pool**: $1000/$1000 floor; day 1: realized -$25.83 hot, $30 swept = +$4 effective.
- **Fire path**: WS-latency sweep -> tier resolution (k3/k2/solo/convex, rate-capped) ->
  flush gate (pc_h1<=-10; convex exempt) -> chase guard (1.5%; convex 1.0%) -> $50 probes
  ($25 convex) -> security/chart -> pool capacity -> fill. Every fire stamps tier/conviction/
  fq/state; every non-fill logs a named reason; every position carries its audit trail.
- **Exits**: TP1 +5% sells 35% (convex 10%); peak-trail 4pp (restart-proof); elite-exit
  mirroring; stop-grace A/B (45min, -50 floor); gap guards.
- **Cycle cadence**: `wallet_cycle.py` daily (cuts+promotions), copyability board verdicts,
  finder feeds the bench, on-bot discovery 24/7.

## DAILY RITUAL (run every morning)

```
python scripts/sync_trades_cache.py --full
python scripts/badday_scorecard.py
python scripts/goal_tracker.py --cache _trades_cache.json
python scripts/wallet_cycle.py            # --apply for the mechanical rules
```
Pre-registered judgments: P7 dial KILL <50% acc @ n>=10 (record: 1/1 HIT); badday family
RETIRE <+$2/tr @ n>=30 dial-bad closes or cat>=10%; trigger-state ENFORCE @ n>=50/gate +8pp;
stop-grace arms @ ~20 closes; convex positive @ n>=25 probes; TOXIC wallets cut @ n>=10.

## PENDING / WATCH

- **badday family fires** — lane deployed ~13:30 UTC; `badday_admit` counter + first fires.
  If quiet by tomorrow, check the 1m-confirmation layer (deliberately left ON, the one
  un-bypassed gate; revisit with counter data).
- **Convex tier**: first fire happened; security_BLOCK pattern on its microcaps accumulating
  toward the n>=30 review.
- **Heavy-wallet re-score** (AgmLJBMD/Em8J3gBW/gasTzr94): slow-paced off-peak pass.
- **Goal meter**: live set was 6 bots today (4 ponds + 2 young probes); streak 0; smart_follow
  earns in via trailing-7d like everything else.
- **06-10 addenda still open**: gated-vs-control A/B, pruned-filter re-audit, stop-width
  audit ~06-16 (per-bot records still lack max_drawdown), pond_ugly_mtf 48h tripwire,
  wave-2 ponds + young stair/baseflow first closes, copyability board re-run ~06-12.
- Funnel C of the wide harvest (old-roster slice) has a format bug ('tuple' object) — fix
  before next pass.

## STANDING RULES (additions today in bold)

Paper only; never flip PAPER_MODE. $0 tools (4-provider free RPC pool now standard).
Commit→push→deploy; no camping. Timestamps from `date -u`. Fleet = selection instrument —
**fleet-aggregate caps corrupt it; rebuilt+reverted once, never again**. **Pipeline-trace
before build** (full upstream admission path, with data, at design time). Goal: $100/day
walk-forward live set, streak 5. **Wallet seats are cycled, never owned: dormancy >36h or
copy-tax TOXIC @ n>=10 = cut; daily-positive SELECTOR = seat.**

---

# PRIOR DAY (2026-06-10) — The Bad-Day Playbook Day (compressed)

17 commits: fleet-wide regime system. Five-lens study (own trades / 2,916-event universe
2-fold / DexScreener live tape / 10-elite on-chain / MAITIU case audit): bad days ROTATE the
market to fresh launches + running momentum + sub-500k microcaps; the pond band is the
bad-day dead zone; the middle (pc_h1 -5..+5) dies. Shipped: P7 regime dial (defense 0.5x
ENFORCED, consensus 1.5x shadow; study: -$677 -> -$250 over 9d), badday_flush/badday_momo
(rug-mined screens: age>=6h cuts 79% of catastrophes — rugs are YOUNG+FRANTIC), SOL gates
OFF the 8 bad-day vehicles (gate blocks 64% of bad-day opportunity at better-than-allowed
quality; elites bought 303x through red SOL), badday scorecard (the accountability loop),
walk-forward LIVE-SET goal meter (the -$635 fleet day was live-set +$46), per-trigger
token-state SHADOW (18 gates, 5 archetypes), young_probe_stair/baseflow (young winners are
in confirmed UPTRENDS — mirror of the pond thesis), momentum_shadow gap guards (giveback
floor + fast bail), pond wave-2 (ugly_rsi/sweep_flow/sweep_deep_thin), measurement-integrity
fixes (bot_id in trimmed responses, loud egress-throttle, off-loop serialization), hybrid
cost model (sync_trades_cache.py, ~300KB/sync), smart-wallet full loop SW1-SW5 (elite-exit,
K-tier pods, fire-quality shadow, realtime WS watch, on-bot 24/7 discovery), repo sweep
(root 450->68 files), 14 bots retired (41 active catalog), dashboard goal-first.
