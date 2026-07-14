# SOLVEIT — CONSTRUCTIVE thread: most profitable DURABLE selection config (held-out)

## TL;DR VERDICT
**No selection config durably reaches a positive token-MEDIAN at honest fills. Best achievable is ~breakeven: a fat-tail-positive MEAN with a still-negative MEDIAN.** The "deep-stack" winner cohort (dev>=20, ~n=16; the n=9 from the MAE mine) is **OVERFIT** — it is negative pre-cutover and its out-of-sample positivity is 1-2 moonshot tokens. The least-bad DURABLE selectors are the BROAD ones: `pc_h6<=0 + 1m_body_pct_avg>=2.2` or `rsi_15m<=44 + dev_pct_remaining>=10`.

## Method
- `_full_trades.json`: 2142 buys / 2858 sells. Joined sells→buys by (address, bot_id), nearest preceding buy. **122 distinct tokens**, 2109 buy-events (~17x fleet inflation).
- **HONEST book**: recomputed every trade as `exit_price / buy.entry_price − 1` (fresh-fill basis) weighted by sell_fraction — uniform across the cutover, removes the stale-flush illusion.
- Winsorized honest return to [−100, +200] (18 records >106% were glitches up to 1.1e8%, the known returns>1000% artifact).
- Unit = distinct token (the n=9 trap is exactly fleet/small-n inflation). Robustness = (a) time-split train 06-23..26 / test 06-27..30, (b) 5-fold by token × 20 seeds, (c) pre/post cutover.
- Post-cutover is desperately thin: only **13 distinct tokens** (124 events). Cannot split it; treated as a thin held-out check.

## Honest baseline (whole strategy, fresh fills)
- Token: tmed −2.98, twin 39%. Event: median −4.73, win 32.9%, mean +0.15 (cap100).
- Pre-cutover token-median is ALSO negative → the booked +3.4/+5.7/+10.1% on 06-26..28 was partly the stale-flush illusion, not a real green median. Consistent with the fidelity finding.

## Selector sweep (full data, token-level)
| config | nev | ntok | tok_med | tok_win | event_win | mean(cap100) |
|---|---|---|---|---|---|---|
| baseline | 2109 | 122 | −2.98 | 39% | 32.9% | +0.15 |
| pc_h6<=0 + body>=2.2 | 538 | 47 | +0.18 | 51% | 42.6% | +3.86 |
| rsi<=44 + dev>=10 | 580 | 45 | +0.29 | 51% | 45.5% | +5.32 |
| dev>=20 (deep stack) | 173 | 16 | +6.70 | 62% | 54.9% | ~ |

The deep stack looks best in-sample — but see robustness.

## ROBUSTNESS (the actual question)

### Time-split (train 06-23..26 / test 06-27..30)
Most configs are NEGATIVE token-median in TRAIN and POSITIVE in TEST → **sign flips with regime, not durable.** Baseline itself: train tmed −3.83 / test −0.72. The only config positive in BOTH periods: **`rsi<=44 + dev>=10`** (train +0.29/52%win/27tok, test +2.41/52%win/21tok).

### 5-fold by token (20 seeds) — removes time regime
% of folds with positive token-median:
- `dev>=20`: 69% pos, medmed +5.61 — BUT this is the overfit signature (small folds dominated by a few winners).
- `pc_h6<=0 + body>=2.2`: 49% pos, medmed −0.40 — coin flip / breakeven.
- `rsi<=44 + dev>=10`: 46% pos, medmed −0.66 — coin flip / breakeven.
- `pc_h6<=0` alone: 21%, `rsi<=44` alone: 19% — single selectors do NOT hold.

### Pre vs Post cutover (post = 06-29/06-30, 4-9 tokens, THIN)
- `dev>=20`: **PRE tmed −4.19 / 25%win** (8 tok), POST +101 (2 tok, one moonshot). → **OVERFIT CONFIRMED: negative pre-cutover, post is one lucky token.** This is the n=9 trap.
- `pc_h6<=0+body>=2.2`: PRE +0.18/52%, POST −2.44/40% (5 tok).
- `rsi<=44+dev>=10`: PRE +0.29/52%, POST −2.44/43% (7 tok).
- Almost everything goes negative token-median post-cutover → matches the real-world collapse; honest fills do NOT rescue it.

## Why the median stays red but the mean is green
Event-level median is **−4 to −4.7%** for every config; win rate 33–46%. The positive MEAN comes entirely from the fat tail — the top-3 tokens in each surviving config all hit the +200 winsor cap. This is the structural fat-tail property already in MEMORY ("every selector lifts mean/rate, median stays −3..−5"). Profit is NOT a green median; it is trim-the-tail (loss gates) + ride-the-tail (exits/sizing).

## Volume cost
- pc_h6<=0+body>=2.2: 47/122 tokens kept (39%) — well above the 25-token bar.
- rsi<=44+dev>=10: 45/122 (37%).
- dev>=20: 16/122 (13%) AND overfit — do not tighten here.

## RECOMMENDATION
1. **Do NOT chase the deep-stack cohort** (dev>=20 / dev>=30 / the n=9). It is overfit: negative pre-cutover, out-of-sample positivity is 1-2 moonshots. Exactly the trap this thread was built to catch.
2. The **durable** selection is one of the BROAD pairs — `pc_h6<=0 + 1m_body_pct_avg>=2.2` (deep decliner + real candle body) or `rsi_15m<=44 + dev_pct_remaining>=10`. Both: lift event-win 33%→43-46%, token-median −3→~0, keep ~39% of volume, and `rsi<=44+dev>=10` is the single config positive in BOTH time halves.
3. Honest framing for AxiS: **best achievable durable config ≈ breakeven** (fat-tail-positive mean ~+4-5%/trade winsorized, negative median ~-4%, win ~45%). The edge is real but it lives in the TAIL — the lever is EXIT/SIZE to harvest it, not a tighter entry stack (which just overfits to fewer tokens).
