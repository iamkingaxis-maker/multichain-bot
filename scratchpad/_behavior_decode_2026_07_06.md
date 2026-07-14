# Wallet-BEHAVIOR Decode — 2026-07-06 (standing daily)

**Fresh window:** 2026-07-05T12:00Z → 2026-07-06T11:29Z (~23.5h of tape).
**Recorder health:** alive — up at 11:03Z today, sweeps landing (+1.4-2.9k trades/sweep); gap 04:03-11:03Z = machine off overnight (normal, no-24/7 rule). `scratchpad/ripday/live_tapes/recorder.log`.
**Coverage:** 112 pairs with fresh records, 162,648 fresh trades, 0 duplicate records (sweeps don't overlap). Analyzable set: **89 pairs** (≥200 trades and ≥$5k gross each). Stale tapes (untouched since 07-04) skipped by ts filter.
**Scripts:** `scratchpad/_decode_0706.py` (main), `scratchpad/_decode_0706b.py` (refinement + robustness).

## Method (this window's constraints)
- Tape schema is minimal: `{kind, volume_usd, ts, maker, pair, sym}` — **no price, no token amount, no tx sig** in any tape.
- **Price proxy:** cumulative signed USD flow per pair (constant-product AMM ⇒ price monotone in cumulative net flow). **Flush trough** = local min of cum-netflow with drawdown ≥ D and rebound ≥ 0.35·D after, D = max($400, 1.5% of pair gross). Yield: 539 troughs, 87/89 pairs, median 5/pair.
- **Wallet P&L (union-counted):** per wallet-pair episode, delta = Σ sell_usd − Σ buy_usd over ALL legs. Dedupe key (ts, maker, kind, usd). Never counted a leg twice across buckets.
- **Round-trip filter:** ≥1 buy AND ≥1 sell AND sell_usd ≥ 50% of buy_usd (drops pure holders whose delta is mechanically negative). **Scrub rule applied:** delta>0 AND hold<10s dropped (1,005 episodes scrubbed).
- Result set: 67,275 raw episodes → 14,449 round-trip → 13,350 classified after scrub: **7,494 winner eps (5,062 wallets)** vs **5,856 loser eps (4,545 wallets)**, 89 pairs. Base episode WR = 56.1%. Winner delta med +$11.2, loser med −$3.4 (fat tails both sides).
- **Known bias:** a peeler holding a moonbag with sells covering only 50-99% of cost can show as a "loser" despite unrealized profit — this biases AGAINST peel/whale cohorts, so peel numbers below are conservative.

## Q1 — Timing vs flush trough (buy_ts − trough_ts)
Buys within ±30min of a detected trough. WIN: n=11,858 buys (3,364 wallets, 87 pairs); LOS: n=10,388 (3,193 wallets, 87 pairs). Distributions are near-identical at the median (both ≈0s; p25 ≈ −9min, p75 ≈ +8min) — timing alone is NOT the winner/loser axis. The signal is in fine buckets (winner share of buys; base 53.3% at buy level):

| bucket (vs trough) | nW | nL | count-WR | USD-wtd WR |
|---|---|---|---|---|
| −30..−5m | 3686 | 3426 | 52% | 46% |
| −5..−1m | 1045 | 927 | 53% | 43% |
| −60..0s | 820 | 636 | 56% | 48% |
| **0..60s** | **1594** | **1190** | **57%** | 48% |
| 60..120s | 502 | 471 | 52% | 52% |
| 2..5m | 638 | 632 | 50% | 45% |
| 5..10m | 933 | 876 | 52% | 49% |
| 10..30m | 2639 | 2230 | 54% | 49% |

- Winners concentrate in the **first 60s after the low** (57%, the only bucket clearly above base). Pre-low knife-catching (−5..−1m: 53%) and the 2-5min "wait" zone (50%) both underperform.
- Episode-level (first buy): 0-120s post-trough WR 58% (n=1,470 ep / 1,139 wallets); 60-300s "HL-zone" WR 53% (n=774 ep / 710 wallets); 0-300s pre-low 56% (n=1,359).
- Per-pair robustness: first-60s beats the 60-300s zone in 26/45 pairs (≥5 eps each) — directional, not overwhelming.
- **HL-confirm read:** the lever's core assumption (post-low > mid-knife) is SUPPORTED — pre-low buys underperform and our live +5.1% entry premium is exactly the pre-low bucket's cost. But the 60s-bucketed confirm lands entries in the 60-300s zone, which is BELOW the 0-60s sweet spot (52-53% vs 57%). And note USD-weighted WR ≤50% in almost every trough bucket: big dollars at flush bottoms lost on this window regardless of timing — timing is a small edge, exit shape is the big one (Q3). Suggestion: test a 30s bucket / first-higher-tick variant in shadow before concluding the 60s confirm gives back too much.

## Q2 — Buy-size composition, winners vs losers
- Episode level: WIN per-ep median buy p25/med/p75 = $3.6/$14.1/$47.9; LOS = $4.1/$18.1/$64.1. **Losers buy BIGGER on this window.** Total deployed: WIN med $26.6 vs LOS med $37.1.
- Threshold check (per-ep median buy ≥$300): WR 50% (n=430 eps) vs <$300: WR 56% (n=12,920).
- Wallet level (≥3 buys, across pairs; closest to prior method): 1,586 net-pos vs 982 net-neg wallets. Pos med-buy $14 vs neg $17. medbuy≥$150 wallets: WR 49% (n=215). **medbuy≥$373 wallets: WR 36% (n=56).**
- Worst-quartile losers (n=1,464 eps): median total-in $239, 2 buys, held ~82min — the "size up into the flush and hold" shape is the biggest single destroyer this window.
- **Verdict: the whale-median-buy separator ($373 vs $153) BROKE on the fresh window — it inverted.** Caveats: (a) prior mine was per-wallet across a curated top-trader cohort, this is all-anonymous per-pair; (b) moonbag bias penalizes partial-exit whales. But even directionally there is no positive size effect here; do not lean on buy-size as a winner marker for this tape regime.

## Q3 — Exit shape of winners
Shapes: single (1 sell), burst (multi-fill <5s span), peel (first slice ≤70% of exit USD, spaced), big-then-dust (first slice >70%).
- WIN: peel 51%, single 44%, big-then-dust 4%. LOS: single 71%, peel 26%.
- **WR by shape: peel 72% (n=5,354 eps) vs single 44% (n=7,456)** — burst 59%, big-then-dust 68%.
- At meaningful size (total-in ≥$100): **peel WR 62%, med delta +$32.8, med ret +9.8% (n=2,004 eps / 1,231 wallets)** vs **single WR 38%, med −$5.7, −2.9% (n=1,353 / 1,225 wallets)**.
- Winner peel anatomy (n=3,839 eps / 2,348 wallets): first slice med **32%** of exit USD (p25 17%, p75 49%), peel span med **141 min** (p25 45m, p75 290m).
- **Per-pair robustness: peel WR > single WR in 68/68 pairs** with ≥10 eps of each shape. This is the cleanest behavioral separation in any decode so far, and moonbag bias makes it conservative.
- **Verdict: conditional-peel SHIPPED CORRECTLY.** Fresh-tape winners peel ~1/3 first, trail the rest over 1-5h. If our peel trails shorter than ~45min, we're tighter than the winning population.

## Q4 — New / uncovered behavior
- **Candidate family: `flush_sniper_peel`** — first buy 0-60s after a flow-trough + peel exit: **WR 71%, med ret +20.2%, n=463 eps / 336 wallets / 72 pairs** (0-120s variant + hold≥15m: WR 67%, +14.4%, n=481/359/69). Survives scrub + union-counting, n≫15 wallets. Not covered by racers: HL-confirm deliberately waits past this window, wickride/adolescent don't key on flow-trough recency. The HL-zone twin (60-300s + peel) is nearly as good (WR 69%, +24.7%, n=219/197/57) — i.e., **entry timing ±2min barely matters once the exit is a peel.**
- **Hold-time gradient (monotone, both cohorts pooled):** hold<2m WR 36% (n=1,554) → 2-15m 52% → 15-60m 58% → 1-4h 63% → >4h 66% (n=2,233). Sub-2-minute churn is a death zone; patience is monotonically paid on this tape. `peel + hold≥1h`: WR 70%, med ret +25.2% (n=3,662 eps / 2,098 wallets) — consistent with adolescent_absorb's patient thesis.
- Falsified this window: scale-in (≥3 buys) ≈ single-buy (55% vs 56%) — laddering adds nothing; straddling the low (buys both sides) 54% — no edge.
- Winner first-buy hours UTC: active 12-23 + 00-03, peak 12-16 — consistent with the 13-22 prime-window rulebook; no new hour pocket.

## Lever verdicts
1. **HL-confirm timing: PARTIALLY SUPPORTED.** Post-low > pre-low confirmed (the premium-kill rationale holds); but the 60-300s confirm zone underperforms the 0-60s sweet spot (52-53% vs 57% buy-level; 26/45 pairs). Keep the lever; shadow a 30s-bucket / first-higher-tick variant.
2. **Whale-buy separator: BROKE** on fresh window (inverted: ≥$373 med-buy wallets WR 36%, n=56; losers buy bigger everywhere). Don't use size as a positive marker this regime.
3. **Conditional-peel exit: STRONGLY SUPPORTED** — peel 72% vs single 44%, 68/68 pairs, first slice ~32%, trail 45-290min. Best-attested behavior on the tape.
