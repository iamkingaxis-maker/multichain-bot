# THE PLAN — Fleet Diagnosis 2026-07-01 (synthesis of 4-agent audit)

## 0. THE HEADLINE (read this first)

Three separate things broke, and they are NOT the same problem:

1. **The "great era" P&L was never real.** 06-26..28's +1929pp is >100% explained by 77 instant-spike prints (first sell <10s after fill at +22..+106%, price never below entry) on just 11 tokens, each duplicated ~6x across near-identical bots. Ex-spike, **every day 06-23..06-30 is negative** (06-28: -411pp on realizable trades). Live execution mechanically cannot capture a +106%-in-1.7s print. **"Get live to match paper" is inverted: paper must be scrubbed down to what live can fill — and it now nearly is.**
2. **Volume died at one cutover, then stayed dead via a 5-blocker funnel.** arm_only (06-28T18:13) zeroed the fleet instantly; arm-instant-fire partially fixed the handoff; the remaining 9-buys/day trickle is: regime breadth gate (killed 143/143 final-stage fires in today's windows), nf15>=0 now reading fresh thin data (-1.0 at every real flush; 6 of 11 bots), rug-gate 0-vs-None on fetch failure (~20% of tokens), 30-trade fetch window starving recur>=1, and a 25-77s hot-path fetch vs a 2s tick.
3. **Yesterday's emergency env flips were net harmful.** SOL_MACRO loose admitted the cohort that was ~100%+ of the 06-30/07-01 bleed (-3.86pp/18% win vs both-pass +2.86/59%). BUY_GATE_SOL_H24_OFF=-4 and FILTERS_RELAX_LIST were no-op-to-negative.

---

## 1. PROBLEM LIST (ranked by P&L impact)

| # | Problem | Evidence (one line) | Fix |
|---|---------|--------------------|-----|
| P1 | **Edge illusion: 77 unrealizable ignition-spike prints = the entire "great era"** | Spike pp share of day P&L: 06-26 414%, 06-27 139%, 06-28 132%; ex-spike pooled mean -2.51/med -4.75/win 31% (n=1987) | Add latency-illusion scrub (hold<10s AND mae>=0) to the book + analyzer; rebaseline ALL enforce/go-live math on scrubbed data |
| P2 | **Regime buy-gate (breadth>=40) = 100% final-stage kill on red-breadth afternoons** — the dip fleet's habitat | 143 hit-rate fires -> 144 BUY-GATE skips -> 0 fills, 19:39-19:47 UTC (4-min window; full-day share estimated) | Paper: shadow (or BREADTH_OFF 50). Live: keep enforce. AxiS call — it IS the crash protector you asked for |
| P3 | **nf15>=0 flipped from inert to family-killer on fresh data** (6/11 bots; n=1-6 trades in window reads -1.0 at every genuine flush) | Token "bull": bought ONLY by the 2 non-nf15 bots; 6 nf15 siblings = 0 buys today | Evaluate only when net_flow_15s_n>=3, else treat missing (fail-open). Do NOT re-stale the data |
| P4 | **SOL_MACRO loose admitted ~all of the 06-30/07-01 loss** | Loose-admits since flip: -3.86pp/17.9% win, sums -255pp (> the whole -210.8 day) vs both-pass +2.86/59.4% | Live config: strict. Paper: keep loose for data. Tape-confounded but the capital-preservation call is clear |
| P5 | **Rug gate blocks on fetch FAILURE, not just genuine zero buyers** (_empty() returns 0 not None) | 3/14 signal tokens fleet-blocked today; dexscreener_client returns [] on timeout/slug-miss/circuit-open -> ub=0 -> block | trade_log_features: return None for maker keys when fetch failed (fetch_ok param); ~20% token recovery |
| P6 | **Hot-path throughput: 25-77s recent_trades fetch inside a 2s loop** — flush lows last seconds; events/day 164->7 | "fastwatch_eval_loop took 79.57s (n_survivors=20)"; recent_trades_dexs=76.99s | Budget (wait_for ~2s) + gather across survivors + per-tick cache; debug _throttle queueing |
| P7 | **recur>=1 starved by 30-trade fetch window** (sell-dominated at flush moment) | recur=0 on 6/9 of today's fills; only "bull" (recur=2) passed | Raise limit 30->100 (same single call per docstring) |
| P8 | **Cohort selectors were spike-carried**: oversold_held +5.96 prior is inflated (72/77 spikes in-cohort); ex-spike cohort = -1.09 mean — a ~+1.9pp LOSS-REDUCER, not a profit source | Cohort ex-spike -1.09/-4.50/39% vs non -3.01/-4.80/28%; 06-30 book 100% in-cohort and still -3.19 | Enforce for any live config (it's still the best slice) but size/go-live only on scrubbed re-proof |
| P9 | **06-29 headline is 52% legacy-bag detonation** (3 champion_* HARD_STOPs, 30-day-old holds, -230.3pp) | Fresh-entry 06-29 = -4.02 mean (ordinary-bad, not catastrophic) | Attribute P&L by ENTRY date; audit + scrub remaining month-old bags in bot_state |
| P10 | **Remaining live-parity gaps ~1.0-1.5pp/trade at $5** (Ultra <24h fee unmodeled ~0.2pp avg; $5 fixed-fee drag ~0.7pp; slip unmeasured band -0.9..+4.5pp; GAP_THROUGH_HAIRCUT_PCT quietly 5->1 unjustified) | grep: no platform fee anywhere in fill model; paper books $100 positions vs live $5; PROBE_ULTRA_SLIPPAGE_BPS=250/600 = guesses | Model <24h fee; restore gap-through 5; slip probe n>=30; paper size = live size |
| P11 | **Live-flip danger state**: key in env + badday_fill_probe_live enabled+live_probe=true + floor=$500 vs ~$10-25 real; PAPER_MODE=true is the ONLY barrier | utils/config.py:434-441 key-blanking; should_route_live ignores PAPER_MODE | Full go-live checklist (section 3) before ANY flip; current env is a data-collection config, not a live config |
| P12 | Minor: BUY_GATE_SOL_H24_OFF=-4 (n=5, -5.67 mean), FILTERS_RELAX_LIST (2 trades, both losers), 6x bot duplication inflating n, wide-exit A/B has 0 closes and tight exits fit current tape (winner MAE worst -4.9; -12 floor rescues nothing) | per finding tables | Unset both envs; report per-token deduped stats; keep wideexit as paper A/B only, re-derive on scrubbed data |

Closed/no-action: entry stale-price gap (fidelity trio already books it — do NOT double-count 3.4pp), daily-loss-cap asymmetry (inert at current volume, actually favors live), mcap/volume-cap envs (cosmetic — bot-level mcap_min=50k governs), pc_h1<=-20 (validated thesis, not binding).

---

## 2. FIX SEQUENCE

**Phase 0 — truth first (today, code, paper-only)**
1. [safe-now] Ship the spike scrub (flag hold<10s AND mae>=0 in the book + analyze_live_faithful_pnl.py). *Effect: every future decision on honest numbers; costs nothing.*
2. [safe-now] Rug-gate fix: `analyze(recent_trades, fetch_ok)` -> None-not-0 on fetch failure (feeds/trade_log_features.py:50,63,71). *Effect: +~20% of surviving signal tokens fleet-wide.*
3. [safe-now] Fetch limit 30->100 (dip_scanner.py:8895). *Effect: family-eligible events ~x2-3; re-verify recur/buyers thresholds on the wider window before touching specs.*
4. [safe-now] Bound+parallelize hot-path recent_trades (2s wait_for budget, gather, per-tick pool cache); root-cause the 5s->77s throttle balloon. *Effect: raises events/day, the base every other lever multiplies.*
5. [safe-now] Scrub champion_* legacy bags from bot_state; switch daily P&L attribution to entry date. Unset BUY_GATE_SOL_H24_OFF and FILTERS_RELAX_LIST. *Effect: no more phantom catastrophe days; -3 confounds.*

**Phase 1 — restore paper volume (env, reversible, needs AxiS on 6-8)**
6. [AxiS-decision] REGIME_BUY_GATE_MODE=shadow on the paper fleet (biggest single unlock: est. 9 -> 40-80 buys/day on red-breadth days). Live keeps enforce. Alternative: BREADTH_OFF 40->50.
7. [AxiS-decision] MAIN_SCAN_BUY_MODE=on. With fidelity+reprice enforce, arm_only is redundant protection against an illusion the book no longer has. Fire-path restored, honest prices kept. *Effect: toward ~200-300 buys/day. Never revert the fidelity/reprice trio.*
8. [AxiS-decision] nf15 clause -> n>=3-or-missing across the 6 carrier bots (entry_gate is validated spec, so your sign-off). *Effect: family fan-out ~1.3 -> ~8-11 per event.*
9. [safe-now] Formalize TWO CONFIGS: Matrix A (paper/learning: SOL loose, cohorts shadow, regime shadow, wide universe) vs Matrix B (live: SOL strict, FULL_THESIS + OVERSOLD_HELD enforce, regime enforce, offset default). Commit Matrix B as a file so a live flip can't inherit Matrix A. Note 06-30 proof: cohorts and SOL-strict are LAYERS — cohort-only would not have saved 06-30.

**Phase 2 — parity hardening (paper, this week)**
10. [safe-now] Model Ultra 0.5%/swap fee for hours_since_graduation<24 in paper_fidelity (re-verify fee schedule vs live docs). Restore GAP_THROUGH_HAIRCUT_PCT=5 (MAE-clamped, can't over-penalize) unless AxiS justifies 1. Set paper position size = intended live size so pnl_pct is like-for-like.
11. [safe-now] Keep wideexit_ab and rsi_ab as paper A/Bs; re-derive the wide-exit thesis on spike-scrubbed data before any promote (the 2590-trade study included spike prints).
12. [safe-now] Add per-token deduped stats to daily reporting (effective n is ~1/6 nominal); consider retiring the _live mirror bots while live is paused.

**Phase 3 — go-live gate (blocked until the bar is met)**
13. [AxiS-decision] Live resumes ONLY when the scrubbed Matrix-B cohort shows mean >= +2pp over >=300 trades / >=50 distinct tokens / >=5 days at restored volume. Today it does not (post-cliff cohort -2.07, thin n=103). Then run the probe per section 3.

---

## 3. THE LIVE-PARITY PATH (the honest answer)

**Parity is ~already achieved — the paper was the thing lying.** With PAPER_FIDELITY + BUY/EXIT_REPRICE enforce, paper already books fresh price + 1.5% slip + fee (entry booked +2.2..+5.1%/day above decision snapshot). The old 3.4pp stale-fill gap is INSIDE the book; do not subtract it again.

**Closed gaps:** stale-entry pricing, daily-loss-cap asymmetry (inert; caps mildly favor live), buy-revert asymmetry (~0 at $5).

**Open gaps, with pp costs (at $5 size):**
- Fixed-fee drag $5 vs paper's $100 books: ~+0.7pp/trade (drops to ~0.2pp at $25)
- Ultra 0.5% platform fee on <24h tokens: ~+0.2pp avg (19% of buys, ~1.0pp each)
- Real slippage: UNMEASURED — band -0.9pp (model conservative; pool_a measured 0.6%/side) to +4.5pp (250/600bps caps worst case)
- Gap-through haircut env-cut to 1% with no measurement; sellability (EXIT_SLIP_LIQ) still shadow
- **Spike unrealizability: the biggest live-vs-paper divergence in history — killed by the Phase-0 scrub, not by execution work**

**Central estimate: live = scrubbed paper minus ~1.0-1.5pp/trade at $5 (~0.5pp at $25).**

**Expected live P&L today:** (a) full current book: -1.8 to -4.0pp/trade — do not run live. (b) oversold_held/full-thesis cohort, scrubbed: ~-1 to -2pp — **not go-live-ready either**. The +3.7pp projection only holds if 06-23..28-type flow returns AND the scrubbed cohort re-proves +EV. Volume restoration is therefore the prerequisite for even knowing.

**Go-live checklist (all required, in order):**
1. Phase 0-2 shipped; buys/day recovered to >=150-200; scrubbed Matrix-B cohort clears the +2pp/n>=300/50-token bar
2. Apply Matrix B env in full (cohorts enforce, SOL strict, regime enforce, offset unset, FILTERS_RELAX unset) — never flip live under Matrix A
3. Roster audit: badday_fill_probe_live ONLY (it is armed right now); confirm patient_sleeve/badday_allday have no live_probe; STRATEGY_ALLOWLIST=dip_buy (already correct)
4. WORKING_CAPITAL_FLOOR_USD = actual starting capital (currently $500 vs ~$10-25 real); DIP_POSITION_USD=5; PROBE_AGG_DAILY_KILL_USD=10
5. Run test_pre_live_invariants.py (13/13) + full suite
6. Probe design: n>=30 fills at $5, sole purpose = measure real entry/exit slip vs the 250/600bps guesses and vs PAPER_LIVE_SLIP_PCT=1.5; set the measured value; only then consider $25 sizing (kills the 0.7pp fee drag) with a fresh ruin sim on scrubbed cohort numbers
7. EXIT_SLIP_LIQ_MODE=enforce at flip; restore cost-control fidelity machinery (ONCHAIN_WS, FAST_WATCH 2s)

---

## 4. WHAT TO STOP DOING

1. **Optimizing toward unrealizable prints.** The fleet spent a week tuning to +106%-in-1.7s illusions. Guardrail: the spike scrub runs in every analysis; any enforce/go-live number quotes the scrubbed figure or is invalid.
2. **Emergency env-flip barrages.** 10 simultaneous flips; measured result: 2 negative, 2 no-op, several confounds. Guardrail: one flip at a time, each with a written expected-effect + revert-by date + realized verdict; Matrix A/B files replace ad-hoc env surgery.
3. **Fleet-wide cutover with no volume alarm.** arm_only killed all 9 dip bots in the same second and it took 26h+ to notice. Guardrail: buys/day + distinct-events/day alert (e.g. <30% of 7-day median pages you); pull per-bot last-entry timestamps FIRST in any drought.
4. **Snapshot conclusions.** Today's funnel read rests on ~4 minutes of logs and n=9 fills; yesterday's flips were justified the same way. Guardrail: minimum evidence bar stated on every claim (>=30 trades or >=1 full day of logs), thin-n findings labeled directional-only.
5. **Enforcing/relaxing gates on shadow-scorer or unscrubbed numbers.** Repeat offense (shadow scorer overstates — known since 06-29). Guardrail: enforce decisions require realized, scrubbed, per-token-deduped outcomes.
6. **Trusting aggregate n from 6 duplicate bots.** 11 tokens looked like 77 independent wins. Guardrail: per-token stats mandatory alongside per-trade.
7. **Sell-date P&L attribution.** A 30-day-old bag made 06-29 look like strategy collapse. Guardrail: entry-date attribution + monthly stale-bag audit.
8. **Chasing "restore 300 buys/day."** Part of that volume was stale-price illusory. The target is max FRESH-price catch rate, judged on realized fidelity-enforced P&L — not the old counter.

**Bottom line for AxiS:** the badday family didn't "go to shit" — its scoreboard was broken in its favor, then its fire path broke against it. Fix the fire path (Phase 0-1, mostly safe-now), and simultaneously accept the scrubbed truth: the realizable edge is currently negative and the cohort is a loss-reducer until re-proven. Live matches paper within ~1-1.5pp already; the work is making paper worth matching.