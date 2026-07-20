# EXIT DECISION MEMO — 2026-07-20

(6 Fable lenses + adversarial verify: 4 survived, 2 refuted; all dead-corrected + phantom-scrubbed)

# EXIT DECISION MEMO — where dollars die after entry (post-SL1)
6 lenses run, adversarially verified. 4 survived, 2 refuted. All numbers dead-token-corrected, phantom-scrubbed.

## 1. Highest honest-dollar exit change per chain (survivors only)

**RH — ship the long-hold TP side fleet-wide (aged_sl1 ladder on every scalp_sl1-style bot).**
- Rule: switch scalp_sl1 bots to aged_sl1 ladder parameters (later TP2 ~16%, wider trail). Loss side untouched: SL1 bank 0.75@−6 + hard stop exactly as shipped. No stop-grace, no min-hold, no TIME_STOP change (all verified strictly worse).
- Honest number: +0.45 to +0.84pp/trade by age band under stricter scrub (verifier's reproduction, ~30% below claim) = +$0.11–0.21 per $25 trade, ~$45–90/day fleet upper bound at 428 trades/day. Positive 11/11 days n≥100, ex-top-2 robust per band/day/pool, long-hold fidelity clean (dead pools book losses, winfrac 0.10).
- Why this one wins RH: the residual leak SL1 doesn't touch is winner-side — exits close winners at median 1.4–2.3 min capturing 7–21% of MFE. Both other RH survivors are smaller (+$6–10/day bail-frac) or latency-fragile (notp2).

**SOL — promote ng_faststop shadow → enforced full-close (never-green early exit).**
- Rule: positions with peak_pnl_pct < +2% exit at the shadow's drop-velocity fire (median −4.99% at 115s) instead of waiting for the −7 in-flight floor. 90s min-hold retained (sol_bail churn lesson). TP/trail untouched — winners never arm.
- Honest number: +$150/4d ≈ +$37/day after charging the measured −1.83pp decision-to-fill gap (NEVER_RUNNER analog) — NOT the claimed +$145/day. At the pessimistic −2.55pp gap it goes to −$25/4d. This is a marginal, direction-correct edge whose entire economics is the enforced-fill-vs-stamped-fire gap.
- Diagnosis is solid and inverts RH: IN_FLIGHT_FLOOR = 65% of gross loss, fills median −10.05 vs −7 intent, only 11.3% of fired never-greens ever recover. SOL dips continue; waiting for the floor guarantees a worse fill.

## 2. Honest verdict — how much of red-to-green can exits close

- **RH**: honest survivor stack (aged ladder + bail-frac, sequenced not summed) ≈ $50–100/day upper bound, replay-era, 79% of mass on 3 days. Against the established fact that 67% of bleed = trading SICK tapes (healthy×green = +$67 vs actual −$401), exit changes plausibly close **~25–40% of the gap on healthy tapes only**. Sick-tape bleed is not an exit problem — dip ladders fire MORE in sick tapes. That stays regime-router / stand-down territory.
- **SOL**: floor+hard-stop sink is 80% of gross loss (−$3,871/window) but only ~5% of window bleed is honestly capturable by ng_faststop (+$150 vs claimed +$580 of a ~$3.2k window). The rest is never-green admission + regime, not ladder shape.
- **Bottom line**: exits are a real second lever after SL1, worth roughly a quarter to a third of the remaining gap. They do not flip the book green alone. The sign-flip still lives in SL1 + regime routing + stand-down, exactly as the 07-19 memo said.

## 3. Ranked ship list — pre-registered paper A/Bs

| # | Change | Chain | Exact rule | Bar (all dead-corrected) | Kill condition |
|---|--------|-------|-----------|--------------------------|----------------|
| 1 | **aged_sl1 ladder fleet-wide** | RH | scalp_sl1 bots → aged_sl1 TP params; loss side frozen | n≥30/bot, net-$ > twin AND ex-top-2 positive | ex-top-2 negative, or delta <0 at n≥30 |
| 2 | **Bail-fractionalization** | RH | PRE_STOP_BAIL closes frac 0.75 (not 1.0) when no TP1 fired; retained 0.25 under existing tail machinery. One-line change. | n≥30 bail-fractionalized positions, net-$ vs full-close cohort | tail-cohort net-$ < full-close; worst observed downside −$1.11/$25, bounded |
| 3 | **tp2_40 half-step** (not notp2 first) | RH | Raise TP2 16→40 on aged family; f2 unchanged | n≥30, net-$ + drop-top-2-positive; measure realized trail slip on divergent exits | trail slip ≥9.3pp (notp2's breakeven); if tp2_40 greens AND slip <5pp, then A/B full notp2 |
| 4 | **ng_faststop enforce** | SOL | peak<+2% arms; exit at velocity fire; 90s min-hold; full close | n≥30 live-vs-shadow parity, **specifically enforced fill vs stamped fire price** | measured gap ≥2.5pp, or winner-kill >20% (sim 17.5%) |
| 5 | (optional, execution not ladder) **SOL pre-submit price recheck** | SOL | Re-check price immediately before reprice submit | Measure in-flight decay vs current −$482/window | — small, safe, only surviving piece of the fast-poll lens |

Sequencing note: #1 and #3 interact (both winner-side, same family). Run #1 first; #3 A/Bs against #1's winner, never stack projections.

## 4. Do NOT change — verified-fine or verified-worse

- **Hard-stop level/timing (RH)**: wider stops w20/w25/w30 = −$250 to −$350 worse; sell-side confirm worse; hold-all −$394 worse; stop-delay/grace worse in every band. Sub-minute stops sell real deaths, not recoverable bottoms.
- **b30 buy-gated bounce-exit — REFUTED, do not ship**: dead-corrected delta −$4,270; the b30≥0.05 gate anti-selects imminent rugs 3.4× (20.3% vs 5.9% death rate). The +$1,222 headline was dead-pool "limit touches" on final prints.
- **Red-zone fast-poll — REFUTED**: 67% of overshoot is gap-concentrated (24:1 single-swap teleports vs slides); SOL's own 3s fastwatch is the natural experiment and fills WORSE (−12.5 vs −7.04 median, 5/5 days). Cadence cannot catch AMM gaps. Keep the valid piece: the 2x decomposition (exit-% carries everything; fraction F=1.00 both sides, sizing null).
- **TP1 raise**: forfeits the +2.5pp favorable overshoot winners collect. Leave it.
- **Moonbag-after-TP2, tighter TP1 banking**: both graded worse in the giveback replay.
- **TIME_STOP**: n=6, −$7.19 — noise. Leave it.
- **SL1 itself**: triple-validated, shipped, do not re-tune. Its fill gap (−6 → −10.47 mean) is the gap-through problem above, not a trigger problem.
- **No RH phoenix-patience on SOL**: 0/26 floored SOL tokens ever hit +3% post-exit. The bounce thesis inverts across chains.

## Caveats that bound every number above
- RH replay evidence is one 10-day era with 79% of mass on Jul 8–10; SOL is one 4-day era. Every bar above is mandatory, not decorative.
- Bail-frac payoff estimator rests on n=66/2 days/4 bots and flips sign by day — its A/B is the evidence, the projection is not.
- Regimes flip day-to-day: all grades trailing-window, benchmark vs tape median per the market-context rule.