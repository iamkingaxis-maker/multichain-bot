# ENTRY-SOURCE DECISION MEMO — 2026-07-19

(6 Fable finders + 6 adversarial verifiers, all survived; fidelity-honest throughout)

DECISION MEMO — SOURCE OF BAD ENTRIES (fidelity-corrected, adversarially verified)
For: AxiS | 2026-07-19 | All numbers below are dead-token-corrected honest dollars unless marked raw.

================================================================
1. PER-CHAIN: HIGHEST-LEVERAGE SURVIVING SOURCE + SHIP FILTER
================================================================

RH — SOURCE: KNIFE-CATCHING (buying the first leg while the seller is still working)
- The 19.1% of entries that never bounce ≥3% before dropping ≥10% more sum to −139k pct-points = 177% of the lane's entire −79k drag. All other entries NET +61k. Median entry sits ~20% above the subsequent low; 76% of entries fall a further ≥6% — this IS the 51% pre-TP1 −6% touch rate.
- Verified hard: exact reproduction on fresh 63,972-row re-pull; honest under phantom-zeroing AND rug=−100; kept-beats-removed 21/26 days (~11 SD outside a placebo null); within-pool-day paired test proves it's TIMING, not pool selection.
- SHIP (lane-computable, t0 stamps only): SKIP dip entry if (s30 > b30) OR (dip < 8% AND athdd > 15%). Do NOT add wait-for-lower-price — proximity-to-low gating tested anti-predictive.
- Volume cost: ~half the bleed removed; kept lane is still red at −0.55%/entry. This is the biggest single RH dollar move available at entry.

RH — SECOND FILTER, corrected form: BLANKET age<1h BLOCK (not the flow-refined version)
- The "manufactured-launch" finding survived but its carve-out FAILED: young-quiet pools are WORSE per entry (−3.07 honest) than the blocked hot-flow cell (−2.41). Blanket age<1h strictly dominates on every dollar metric: −1.050 honest vs −1.226, green direction 9/11 days, 25.3% volume cost. The hot-flow cell is still the diagnosis (27% corpse rate, 58% of ALL phantom wins, invisible on raw P&L — the self-concealment is real) — but the shippable rule is: block ALL pool_age_h < 1.0.

SOL — SOURCE: FRESH-HYPE BLOWOFF DEFLATION (the only STRONG finding of all six)
- Never-green entries are dip-buys into distribution after a first-attention-wave pump: h1_peak_in_window median +162% (NG) vs +56% (winners), AUC 0.73 overall / 0.68–0.88 within badday_young family — tape mechanism, not bot proxy. Self-concealing: 81% of phantom wins sit in h1peak≥100 vs 62% base; fidelity correction STRENGTHENS it (blocked cell −$964 raw → −$1,238 honest).
- SHIP (fields already stamped in entry_meta, zero new plumbing): BLOCK when h1_peak_in_window ≥ 150 AND attn_profile_fresh == 1. Apply to badday_young/flush lanes; admission lanes exempt (within-family AUC 0.53–0.59 — they already gate this).
- Volume cost: ~26% of lane volume, removes ~30% of honest bleed. Blocked honest-win rate 2.4–4.3% vs kept 21–23% EVERY instrumented day.
- Do NOT ship the ≥250 variant yet — winner-kill re-measured at 5.1%, over the 5% bar. Re-audit first.

No chain came up empty — but note what DIDN'T survive: the maker-composition dimension (the "strong dimension we never mined") verified only WEAK on RH — 93.5% of tape coverage is one day (07-10), the only other day flips the mean-return sign, and the pool-clustered CI on mean return crosses zero. The robust part is rug/phantom avoidance only. It is a shadow flag, not a lever, until multi-day tape accrues.

================================================================
2. HONEST VERDICT ON THE FRAMING
================================================================

"Bad entries" is HALF the problem, and that half is now mapped to its ceiling. The evidence is consistent across both chains:

- Entry separability is real but capped. Tape-shape AUC tops out at 0.59–0.62 (GBM and logistic agree). The best RH filter halves the bleed and the kept lane is STILL red (−0.55%/entry, green only 15/28 days). The best SOL filter removes 30% of bleed and kept entries STILL bleed −$1.52/entry. Stacking every surviving filter plausibly cuts 50–60% of entry-attributable bleed. It does not flip green.
- Strength ranking for moving net-$: only ONE finding is strong (SOL fresh-hype). Two are solid-moderate with real dollars (RH knife-timing, RH age<1h). The rest are marginal: SOL thin-burst is worth −1.42pp/entry within-bot (the −7.79 headline was bot-composition inflation; effective sample 59 unique tokens); SOL concentration is robust but ~5% of honest bleed (−$374 of −$7,510); RH maker-composition is one-day data.
- The single biggest "entry" discovery is actually a GRADING discovery: the dead-token illusion is self-concealing — it inflates exactly the best-looking cells (58% of RH phantom wins in one 18% cell; 81% of SOL phantom wins in the h1peak≥100 zone). This is WHY months of paper-graded iteration never removed these entries. Every future grade must be fidelity-corrected or the loop will re-select the corpse factory.
- Where the rest of the money is: exactly where the prior work already pointed — dollar-conversion (SL1 ladder: losses close full-size at 2× win size), and regime stand-down (67% of bleed = trading sick tapes at all). Entry filters shrink the losing tail; they cannot conjure demand under a dip. "Admission gates can't conjure bounces in a demand vacuum" was re-confirmed from three independent directions this round.

Bottom line: ship the three real entry filters below, then stop mining entries. The marginal AUC point is not there. The green line runs through SL1 dollar-conversion + regime router validation, with these filters as the floor under it.

================================================================
3. RANKED SHIP LIST — pre-registered paper A/Bs
================================================================

Common grading bar (per safe-live framework, all arms): n≥30 affected slots, ≥5 distinct days, ≥20 unique tokens, fidelity-corrected dollars (phantom wins zeroed), drop-top-2 must stay positive, benchmarked vs same-window tape. Shadow-count blocked entries (counterfactual) — never grade on kept-arm alone.

1. SOL_HYPE_BLOCK (build first — only STRONG finding)
   Rule: block if h1_peak_in_window ≥ 150 AND attn_profile_fresh == 1; badday_young/flush lanes only, admission lanes exempt.
   Cost: ~26% volume. Expected: ~30% of honest bleed removed.
   Bar: standard + winner-kill ≤5% on the ≥150 variant; only 3 clean instrumented days exist → the ≥5-day requirement is doing real work here, accrue 2+ more before verdict. Note attn_fresh base rate drifts (31%→51%) — log daily block-rate.

2. RH_KNIFE_SKIP
   Rule: skip if (s30 > b30) OR (dip < 8% AND athdd > 15%). No proximity-to-low rule.
   Cost: keeps most volume; removes the strict-knife cohort feeding 177% of drag.
   Bar: standard + kept-beats-skipped on ≥70% of days (verified benchmark was 21/26). Pre-register that kept lane will likely remain mildly red — success = bleed halved, not green.

3. RH_YOUNG_BLOCK
   Rule: block ALL pool_age_h < 1.0 (blanket — pre-registered explicitly AGAINST the flow-refined carve-out, which loses −3.07/entry on the quiet-young side).
   Cost: 25.3% volume.
   Bar: standard + 9/11-day directional consistency reproduced on fresh window. Also A/B the OVERLAP with #2 (arms: knife-only, young-only, both) — populations likely intersect; we need the marginal value of each.

4. SOL_THINBURST_TAILGATE
   Rule: block if unique_buyers_n < 50 AND top10_buyer_time_spread_sec < 120; missing-field policy = fail-open (fields absent on only 3–5% of recent entries).
   Cost: ~32% volume TODAY (cell share tripled to 31.6% — that trend itself is worth watching).
   Bar: grade on floor-hit rate (expect ~14pp reduction on blocked slots) and within-bot honest mean (+1.4pp) — pre-register that −7.79pp/entry is NOT the target; it was clone-bot inflation.

5. SOL_CONC_GATE + BUNDLE_SCRUB (cheap hygiene, ship together)
   Rule: block if top10_holder_pct ≥ 50 OR top1_holder_pct ≥ 20 OR hidden_supply_share_pct < 50. Separately: bundle_v2_suspected=true routes wins with hold<10s into fidelity scrub (grading flag, never a P&L gate).
   Cost: ~4% volume, near-zero winner-kill (best 2 blocked tokens net +$1). Only ~5% of bleed — ship because it's free, don't expect it to show on the top line.

6. RH_RECYCLED_FLOW — SHADOW ONLY, DO NOT GATE
   Log the wash/round-tripper flag on every entry (pre-t0 tape, fail-open on quiet tapes) but take no action. Regrade at n≥30 across ≥3 tape days. The rug/phantom enrichment (+7.9pp / +4.4pp) is real; the mean-return gap is not validated (one day = 93.5% of data, sign flips on the other day). If it confirms cross-day, it becomes the current gates' fix — they preferentially ADMIT this class (34/36 live-ledger entries flagged).

0. PREREQUISITE FOR EVERYTHING: fidelity-corrected grading in the harness itself — every A/B verdict computed on phantom-zeroed / dead-token-corrected dollars, automatically. Without this, items 1–6 get graded by the same illusion that hid them for months.

WHAT NOT TO BUILD: more entry-feature mining (AUC ceiling confirmed twice), wait-for-lower-price timing rules (anti-predictive), the RH quiet-young carve-out (dominated), the SOL h1peak≥250 variant (winner-kill 5.1% until re-audited), any gate graded on raw paper P&L.

After this list ships, the open dollar levers are the ones already on the board: SL1 loss-ladder grading (n≥30) and the regime router's sick-tape stand-down — that is where the sign flip lives, not in another entry filter.