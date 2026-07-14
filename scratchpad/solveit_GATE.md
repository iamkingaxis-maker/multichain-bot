# GATE thread — did an entry gate get silently disabled / inverted / saturated at the cutover?

Cutover = 2026-06-28T18:13 UTC. Source = `_full_trades.json` entry_meta verdict fields (FIRED buys only) + code.
Sample: pre = 2013 raw buys (128 distinct tokens), post = 129 raw buys (**only 13 distinct tokens** — thin, fleet ~10x inflated). Green-regime confound present. Numbers are directional, not precise.

NOTE on verdict fields: almost every `filter_*_verdict` in entry_meta is recorded in **SHADOW** (log-only). BLOCK among FIRED trades therefore does NOT mean the gate blocked — it means "this filter would have flagged the token we bought." It is a window into the *character* of what we buy, and into which ENFORCED gates are actually firing.

## HEADLINE: full_thesis_cohort (ENFORCE) SATURATED at the cutover — exactly the brief's predicted failure mode.

`full_thesis_cohort_eval` (core/bot_evaluator.py:639) + enforce at feeds/dip_scanner.py:2286.
Its ENFORCE arm blocks ONLY a *confirmed out-of-cohort* candidate = `pc_h6 > 0` (pump-retrace). pc_h6<=0 + buyer present = PASS. No magnitude floor.

pc_h6 distribution of FIRED buys:
| bucket | PRE | POST |
|---|---|---|
| pc_h6 > 0 (pump-retrace, the thing FTC blocks) | 52.8% | **10.9%** |
| -20..0 (the validated mild-dip sweet spot) | 13.7% | **0.0%** |
| -50..-20 | 27.4% | **66.7%** |
| <= -50 (crater) | 6.2% | **22.5%** |
| median pc_h6 | **+2.81** | **-37.75** |

Consequence: pre-cutover FTC's enforce arm was doing real work (rejecting the 53% pump-retrace stream). Post-cutover only 11% of the candidate stream is pc_h6>0, so the gate's enforce arm is **nearly inactive** — it waves through a wall of pc_h6 −20..−70 decliners (median −37.75) that all "pass" the sign test but are FAR deeper than the validated profitable cohort (whose worst WINNER was −32% pc_h6, per the terminal_collapse comment). The mild-dip bucket FTC was designed to admit is now **empty (0%)**. The gate gates on pc_h6 SIGN, not MAGNITUDE → it cannot tell a healthy −5% dip from a −40% crater. = SATURATED / no-op.

median_buy_size_usd actually ROSE 16.5 → 36.9 (>34 threshold), so by FTC's *other* criterion the tokens look fine — which is exactly why FTC passes them. The cohort definition is satisfied on paper while the real entries are death-spiral decliners.

## Corroboration: shadow trend/structure gates show selection shifted to "dead-cat green tick in a structural downtrend"

BLOCK-rate among FIRED buys (shadow gates), pre → post:
- trend_score_verdict: 48% → **98%** (+49pp)
- vwap_h24_verdict: 52% → **98%** (+47pp)
- filter_fusion_floor_verdict: 46% → **87%** (+41pp)
- filter_mtf_strong_downtrend_verdict: 50% → **88%** (+39pp)
- filter_lp_drain_verdict: 36% → 57%; filter_lower_low_verdict: 5% → 26%; filter_buyer_fomo: 24% → 36%

Post chart_mtf_verdicts sample = `{1m:bear, 5m:bear, 15m:bear, 1h:bear}` (all four TFs bear). The bought tokens are near-uniformly strong all-bear MTF, below VWAP, bad trend score.

BUT the same post buys show falling_knife BLOCK 26% → **0.8%** and above_vwap_chase 19% → **0.8%** — i.e. the *latest 1m candle is GREEN* at fire. Signature = **buying a momentary green 1m uptick inside a structural multi-TF downtrend** (a bounce/dead-cat in a cratering token). This is consistent with the fresh-fire fork: arm at the low, fire on the +4% bounce candle.

## Enforced gates that are NOT broken
- **falling_day_flush** (enforce, pc_h24<0 AND pc_h1<=-35): **0 violations pre AND post** — still correctly blocking. Not the issue.
- No `filter_*_verdict` went from high-BLOCK to inverted-PASS in a way indicating a code disable. The shifts are input-distribution driven.

## Secondary flag (for the code-side thread): terminal_collapse may be bypassed on the fire path
`terminal_collapse_blocks` (enforce, pc_h6<=-60 floor, dip_scanner.py:19599) lives in the MAIN-SCAN filter stack (`_evaluate_pair`).
- POST: 10 fires with pc_h6 <= -60 (all GOKHSHTEIN, pc_h6 = **-70.9**, buyer $5.2) cleared it. WYNN at -57.3 (within floor) also fired 9x.
- PRE: 42 fires with pc_h6 <= -60 also cleared it.
Because pre had it too, this is NOT a clean cutover regression. BUT the rate rose (2.1% pre → 7.8% post) and the mechanism is suspect: with arm_only, the buy happens on the FAST-WATCH fire path, not at main-scan. Worth verifying whether the arm→fast-watch fire path re-runs the main-scan filter stack (incl terminal_collapse) on the FRESH pc_h6, or whether arm-time pc_h6 cleared the floor and the token cratered before fire (entry_meta logs the fire-time −70.9). If the fire path skips the main filter stack, ALL the main-scan enforced filters (terminal_collapse, post_pump_corpse, etc.) are bypassed on every arm_only buy.

## Recommendation (paper-safe, flag-gated)
1. **Give full_thesis_cohort a pc_h6 magnitude floor / mid-band.** It currently discriminates on sign only; post-cutover that is useless. Add an env-gated lower floor (e.g. block pc_h6 < −25..−30, the gap below the validated worst winner of −32%) so it distinguishes a buyable dip from a death spiral. Shadow-first, then enforce. This restores the selection that the cutover silently removed.
2. **Verify the arm→fast-watch fire path re-runs the main-scan enforced filter stack on fresh data** (terminal_collapse et al.). If not, that is a real fire-path gate-bypass bug — hand to the code/buy-path thread.

## Verdict
No gate was silently disabled or inverted in CODE at the cutover. But the gate that matters — **full_thesis_cohort (enforce) — effectively SATURATED**: the arm_only/fresh-fire path (+ green regime) feeds it a uniformly deep-negative pc_h6 stream (median +2.8 → −37.8; sweet-spot −20..0 bucket 14% → 0%), and because FTC gates on pc_h6 SIGN not MAGNITUDE, its enforce arm went from rejecting 53% of candidates (pump-retraces) to near-inactive, admitting the entire deep-decliner wall. Shadow trend/vwap/mtf gates corroborate: we shifted to buying green 1m bounce-ticks inside all-bear structural downtrends. Real, fixable selection regression on top of the fill-honesty + regime story — fix = add a magnitude floor to FTC.
