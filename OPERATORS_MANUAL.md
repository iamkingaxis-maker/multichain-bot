# THE OPERATOR'S MANUAL
### Distilled 2026-07-19 (last Fable-5 session) — so any future session executes decisions already made instead of re-deriving them.
Every rule here was paid for with real losses or verified with adversarial checks. When in doubt, this document outranks intuition. Only AxiS overrides it.

---

## 1. THE FIVE HONESTY RULES (non-negotiable, in order)

1. **Freshness first.** Before reasoning from ANY number: fresh `date -u`, check the data's age/span. A dead pipe serves plausible stale numbers. CHAIN = truth, ledger = claim.
2. **Fidelity currency only.** Paper wins on dead/unsellable tokens are FAKE (proven ~$3,967 illusion). Every grade, mine, map, and promotion uses dead-token-corrected dollars (`fidelity_pnl_usd` on the dashboard; auto-refreshes 30min). **The illusion is self-concealing — it inflates exactly the best-looking cells** (58% of RH phantom wins in one 18% cell; 81% of SOL phantoms in the hype zone). A map mined in paper dollars is not a map.
3. **Big-number audit.** Any bot day beyond ±$50: (a) per-token concentration, (b) phantom-class legs (win >+50% or fast-hold spikes), (c) paper-vs-fidelity delta. Three fake-classes on record: stale-guard phantom (CASHBULL), bad print (SPCX), dead-token illusion (SHERIFF).
4. **Market context on every check.** Measure the tape (median drift, % down, rug rate) before judging any bot. Flat in a bloodbath = GOOD. Never "no edge" without the benchmark.
5. **Attribution before narration.** Decompose every number: TAPE (market direction) / INSTRUMENT (phantoms, stale pipes) / STRUCTURE (which mechanism). A number you can't attribute is a number you don't understand yet — say so.

## 2. THE STRATEGIC PICTURE (as of 2026-07-19, verified)

- **The market's shape:** the consistent winners on these tokens are POSITIONED (deployers, snipers, extractors) — they sell to takers like us. Bad entries = **staged exits dressed as demand** (all 6 lenses of the entry-source dig, all adversarially verified). Flow is forgeable; **authenticity** (pool age, buy-side wallet history, holder concentration, hype freshness) is what separates real dips from staged ones.
- **The honest regime map is INVERTED from paper:** RH drift-positive "healthy" windows = corpse-commitment windows (90% of dead-token $, 9:1). Collectible profit lives in SICK-window dips (small, real, repeated). Cross-chain convergent.
- **Entry mining is CLOSED.** AUC ceiling 0.59–0.62, confirmed twice (GBM + logistic). The shipped filters (knife-skip, young-block, hype-block) are the ceiling. **The sign flip lives in dollar-conversion (SL1) + regime stand-down**, with entry filters as the floor.
- **Fleet ≠ P&L.** The fleet is a selection instrument; its aggregate is research cost. The deliverable is always the ONE live-ready config ("professional shape": few entries, paying regimes, honest signals, conviction size).
- **RH > SOL mechanically** (dips bounce, slippage ≈0 at $25, 4.8% failed-tx friction). SOL = demand vacuum until its regime pays; judge day-by-day.

## 3. STANDING EXPERIMENTS + THEIR PRE-REGISTERED BARS
**Universal bar: n≥30 affected closes, ≥5 distinct days, ≥20 unique tokens, fidelity dollars, drop-top-2 still positive, tape-benchmarked.** No promotion below it; no retirement by anyone but AxiS.

| Experiment | Arms | Success/kill criteria |
|---|---|---|
| RH dipall quartet (07-19) | ctrl / knife / young1h / both | knife: bleed halved, kept-beats-skipped ≥70% of days (kept lane may stay mildly red — pre-registered). Measure the OVERLAP (marginal value of each filter) |
| SOL hype-block A/B (07-19) | badday_young_hypeblock_ab vs parent absorb | ~26% volume cost, ~30% bleed reduction target; winner-kill ≤5%; log daily block-rate (attn_fresh base drifts) |
| slcut SL1 trio (07-17) | slcut_agedhold/ageddeep/demand vs parents | paired same-token; SL1 relative win STANDS (fidelity-neutral); absolute level failed fidelity — no promotion without clean fidelity re-grade |
| rh_phoenix (07-18) | post-stop bounce catcher | KEEP at n=32 bar-grade; kill if deaths eat the bounces or fidelity drifts below −$20 |
| SOL admission arms | admission_x_liq / _liqdemand / _liq_sl1 | volume unlock + quality; grade on NG/win/$/entry/buys-day |
| Regime router Gate A | flip-sim (armed vs off, auto-accruing) | flipper must beat always-on at n≥30 windows in honest $ or IT DIES and envelope-only carries live risk |

**Ship-list remainder (mechanical, no Fable needed):** #4 SOL thin-burst gate (unique_buyers_n<50 AND spread<120s, fail-open; target = floor-hit −14pp NOT the −7.79 headline); #5 SOL concentration gate (top10≥50/top1≥20/hidden<50 block — free) + bundle scrub (grading flag only); #6 RH recycled-flow SHADOW logging (no gate until ≥3 tape days confirm).

**DO-NOT-BUILD (verified dead ends):** new mined entry features; wait-for-lower-price timing (anti-predictive); RH quiet-young carve-out (dominated); SOL h1peak≥250 variant (winner-kill 5.1%); any gate graded on raw paper P&L; bot-P&L-based routing (mean-reverts, inverts at 12h); the 5 rulebook do-not-builds; local always-on collectors.

## 4. REGIME ROUTER — STATE + THE V2 FLIP CRITERION (spec, to be scored before use)

- **State:** ROUTE_MAP is EMPTY both chains (07-18 fidelity cascade — correct until something measures clean). Sensor: `/api/regime` (drift, dip_bounce_rate, hysteresis 2-reads, blind-sensor expiry); self-snapshots every 15min → `/api/regime/history`; flip-sim re-scores armed-vs-off continuously.
- **V1 criterion (drift-healthy⇒TRADE) is REFUTED** — it armed exactly the corpse windows.
- **V2 criterion (spec — do NOT arm live; score via flip-sim first):** ARM a sick-window seat when: chain drift ≤ −3% (sick) AND dip_bounce_rate ≥ 40% AND the candidate bot's trailing-5-day fidelity ≥ 0. DISARM on: envelope breach (minutes), 2 consecutive reads leaving the armed condition (hours), map cell going red on the daily re-mine (days), any sensor blindness (any timescale). Cadence cap 1 flip/4h. **Gate A applies to V2 exactly as to V1: beat always-on at n≥30 windows or die.**
- Possible V3 feature: per-window DEAD-COMMIT RATE (the 9:1 signal) — needs per-window dead-token attribution wired into the sensor before it can be a criterion.

## 5. DAILY GATE CYCLE (the /loop, restart it each session)
**CAREER MODE (07-20, REVENUE_PLAN.md):** every pass ALSO runs `python scripts/revenue_check.py` (distance-to-revenue: go-live gate status + today's blockers); once per daily cycle run the **desk-review workflow** (`Workflow({name:"desk-review"})`) → the daily desk memo with THE one most valuable action. Distance-to-revenue is reported to AxiS on every check.
1. Freshness + market analysis (rule 1, 4) → `/api/regime`, both chains.
2. Pipes: ledger-upload count in lane logs, fidelity_ts age <45min, zero Tracebacks.
3. Big-number audit (rule 3) on any ±$50 bot day.
4. Grade any experiment crossing its bar (§3 table); update its row.
5. Re-run flip-sim; note armed-vs-off delta.
6. Daily (once): regime map re-mine on FIDELITY dollars (scripts/regime_map_mine.py + dead-token sweep); heavy ledger pull ≤1×/day.
7. Fix what breaks; ship paper-side levers freely (standing consent); **live changes need AxiS explicitly**.
8. Session end: update project_bot_handoff.md.

## 6. LIVE / SAFETY INVARIANTS (all currently PAUSED-safe)
- Live is PAUSED: `RH_LIVE_PROBE_BOTS=__disabled__` (+ paper mode). Wallet 0xa454…9c05 ≈ $40.71 flat. **Position-safe kill = `__disabled__` (sells still route); `RH_PAPER_MODE=true` strands positions.**
- Never live without: test_pre_live_invariants.py green + explicit AxiS approval + RE-ARM CHECKLIST (safe-live framework memory). Safety envelope binds at arm: 0.35×wallet cap, 3-loss/1h breaker, 1.5× stop, concurrency+balance rails, sell-canary (buys halt when sells break).
- Never touch: AxiS's key (image on his PC), GFOF + Cmoon tokens. Hot wallet stays CLEAN (auto dust-sweep; sell leftovers, never explain them away).
- No paper↔live switches without explicit instruction. Recommendation ≠ consent. Watch-live = surface only.
- Railway: env vars override code (update both); commit → push → deploy; ≤1 routine deploy/day (bug fixes exempt); bill target <$25/mo (check Usage page; egress trims = fidelity 30→60min, uploads 2→5min if drifting).

## 7. OPERATOR'S EPISTEMICS (why we ran in circles for months, never again)
1. **Winner's curse:** in a noisy negative-sum pond, the recent race winner is usually noise and regresses. Promote on MECHANISM + cross-regime survival, never on a good fortnight.
2. **Structure changes day-to-day** (AxiS). Every decode is a one-regime sample; ship regime-conditional, keep decode scripts re-runnable, let the ≥5-day bar do its work.
3. **Instrument before strategy.** When numbers surprise (good OR bad), suspect the instrument first. If AxiS says numbers look wrong, believe him and check the pipe.
4. **Small real > big fake.** The honest edge found so far is small-per-trade (sick-window dips, bounces, SL1). The path to green = concentrate + size the small real edge, not hunt a big one that takers don't get.
5. **Never doom, never dismiss, iterate** — report learned + next try; only AxiS retires candidates. Deadlines are floors. Thoroughness over speed.
