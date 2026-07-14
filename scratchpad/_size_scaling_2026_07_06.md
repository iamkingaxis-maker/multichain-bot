# Size-Scaling Stress Test — Solana Dip-Buy Fleet (2026-07-06)

**Question.** The funding thesis assumes the per-trade RATE (net EV pp) is size-invariant: "prove
it at $25, scale the base to $1.5–3k." It is not. Larger orders pay CPMM market impact on both the
entry and the forced exit-into-weakness. This quantifies how net EV decays with position size and
finds where the rate breaks.

## Method & data
- **Positions**: reconstructed exactly as the EV model — grouped every buy + its sell legs by
  `(bot_id, address, entry_price)`, blended pnl = Σ(sell_fraction·pnl_pct)/Σsell_fraction. Kept each
  position's entry `entry_meta.liquidity_usd` = **L** (pool depth, the scaling variable).
  Gross reproduces the EV model to the decimal: young **+1.80**, flush **−0.90**, probe **−0.39**. ✔
- **Impact model (CPMM)**: quote reserve R_q = L/2 (liquidity_usd is two-sided); average fill-through
  impact = `100·(S/R_q)/(1−S/R_q)`. Paid once on entry, once on exit.
- **Calibration against /api/live-swaps (293 real legs)**: at the live size (**$5**) into ~$38k pools,
  S/R_q ≈ 0.026% so the CPMM term is ~**0.02pp** — yet measured slip is **buy 2.73pp / sell 0.70pp**.
  So the live floor is almost entirely **structural** (spread + latency drift + priority), NOT depth
  impact. Calibration therefore pins the size-invariant floor (buy 2.73, sell 0.70 = **F=3.43pp**,
  matching the EV model); AMM physics supplies the size-scaling term on top.
- **Exit** = CPMM impact + 0.70pp structural sell-into-weakness floor. **Entry** = CPMM impact + 2.73pp
  buy floor. **Fees** = fixed **$0.17/leg** → shrink as pp when size grows (0.17/S·100 per leg).
- Cohorts: young = `badday_young_absorb` (n=68), flush = `badday_flush*` paper (n=1347), probe =
  `badday_young_absorb_live` real fills (n=6; friction already inside → added F back to recover gross).

## 1. Net EV (mean pp) vs size — the rate curve

| cohort | n | $25 | $50 | $100 | $200 | $400 |
|---|---|---|---|---|---|---|
| young | 68 | −3.26 | −2.85 | −3.05 | −3.97 | −6.11 |
| flush | 1347 | −5.96 | −5.55 | −5.74 | −6.65 | −8.76 |
| probe | 6 | −2.05 | −1.66 | −1.92 | −2.95 | −5.31 |

Median (fat-tail-honest) net EV is far worse (young −7.6 → −10.5; flush −9.4 → −11.8) — consistent
with the EV model's finding that young lane's positive mean rides two right-tail runners.

**Every cohort is net-negative at every size.** The curve is U-shaped: least-negative near **$50**
(fees dominate below, impact dominates above), but the whole curve sits below zero because the
size-invariant structural floor (3.43pp) already exceeds gross EV at $25.

## 2. Friction decomposition (median L=$38k, pp round trip)

| S | structural | CPMM impact | fees | TOTAL |
|---|---|---|---|---|
| $25 | 3.43 | 0.26 | 1.36 | **5.05** |
| $50 | 3.43 | 0.53 | 0.68 | **4.64** ← min |
| $100 | 3.43 | 1.06 | 0.34 | 4.83 |
| $150 | 3.43 | 1.59 | 0.23 | 5.25 |
| $200 | 3.43 | 2.13 | 0.17 | 5.73 |
| $300 | 3.43 | 3.21 | 0.11 | 6.75 |
| $400 | 3.43 | 4.30 | 0.09 | 7.82 |
| $500 | 3.43 | 5.41 | 0.07 | 8.90 |

Impact adds **~4pp round trip** going $25→$400. It is real but modest relative to the 3.43pp
structural floor — impact is the *second* problem, not the first.

## 3. Break-even size

**None.** Mean net EV never crosses zero (it peaks at **−2.84pp @ $56** for young, −5.54 @ $56 for
flush, −1.66 @ $54 for probe). The rate doesn't "break" at some size — it starts below zero at $25
and the ~$50 fee/impact minimum is still deep in the red. **Size scaling is not the failure; the
per-trade edge is already negative at any size.**

## 4. Thin-pool absorption

Pools are deliberately $25k–100k (young: min $25.3k, p10 $27k, med $37.6k). Nominal absorption is
fine — **0%** of positions have S/L > 2% even at $400 (all L > $20k). But the *impact tax* bites the
majority: at **$400, 91% of young / 86% of flush positions carry >3pp round-trip impact** (17%/100%
for probe on its thinner pools). So "the pool can absorb it" ≠ "cheap" — $400 orders pay a meaningful
depth tax on ~9-in-10 fills. Impact sensitivity: R_q=L/2 gives 2.13pp RT @ $200; the optimistic
one-sided R_q=L halves it to 1.06pp — the L-convention is the model's main uncertainty.

## 5. $/day (mean EV · size · fills/day) — the funding number

Because net EV is negative at all sizes, **$/day is negative at all sizes and grows more negative
with size** (you lose more dollars per bigger trade). At 20 fills/day: young −$16 ($25) → −$488
($400); flush −$30 → −$701. The "$/day-maximizing" size is therefore the *smallest* (least-negative),
i.e. bigger size strictly destroys value on today's gross. **No size yields positive $/day.**

## 6. What gross EV would be required to net $100/day

| S | fills/day | net EV needed | friction | GROSS needed | (young now = +1.80) |
|---|---|---|---|---|---|
| $100 | 20 | 5.00 | 4.83 | **9.83** | gap −8.0 |
| $150 | 20 | 3.33 | 5.25 | **8.58** | gap −6.8 |
| $250 | 12 | 3.33 | 6.23 | **9.57** | gap −7.8 |
| $300 | 10 | 3.33 | 6.75 | **10.09** | gap −8.3 |
| $500 | 6 | 3.33 | 8.90 | **12.24** | gap −10.4 |

The friendliest route to $100/day is **many small fills** (~$150 × 20/day → needs ~8.6pp gross), and
impact makes every fewer-but-bigger configuration *harder* (needs 10–12pp gross). Current young gross
is +1.8pp (mean, fragile) — a **~7pp gap** regardless of sizing.

## 7. Conditional test — *if* the edge were fixed, does it survive scaling?

Suppose the peel/HL-confirm work lifts young gross by +3.26pp so it exactly breaks even net at $25.
Scaling that marginal strategy up:

| S | $25 | $50 | $100 | $200 | $400 |
|---|---|---|---|---|---|
| net EV | −0.00 | **+0.41** | +0.21 | −0.71 | −2.84 |

Even a strategy tuned to breakeven at $25 **peaks at ~$50 and goes net-negative again by ≈$126**,
purely from impact. So the size-invariance assumption is falsified: a barely-profitable edge only
survives in a narrow **$25–$125** band and is optimized near **$50**, not at funded size.

## Caveats (this is an upper bound)
Static replay: it assumes the *same fills* trigger at bigger size. In reality a $200–500 order moves
the price it's reacting to, would change which dips clear the gates, and on the 9-in-10 thin-pool
positions the CPMM approximation (constant R_q, no book, no LP migration) is least reliable — real
$400 fills would likely be *worse* than modeled. Treat every $/day number as a generous ceiling.

## Verdict
**The "prove at $25, scale the base" plan does not survive — and not primarily because of impact.**
The rate is size-*variant* (impact adds ~4pp RT by $400 and a breakeven-at-$25 edge dies by ~$125),
but the dominant fact is that the per-trade rate is **already negative at $25** for every cohort: the
3.43pp structural friction floor exceeds today's gross EV before a single dollar of extra size. So
scaling the base just multiplies a losing rate — bigger size loses *more* dollars/day, and no size in
{$25…$400} produces positive $/day, let alone $100/day. The prerequisite is untouched by sizing:
young-lane gross must climb from **+1.8pp to ~8.6pp** (the +7pp/peel-and-stop target already named in
the EV model). *If* that lift lands, the funding-relevant sweet spot is **many small fills at ~$50–150**
(≈$1.5–3k base ÷ 15–20 fills/day ≈ $100–200 each) — deliberately *not* fat positions — and even then
$100/day needs ~20 fills/day of a genuine +8-9pp edge. Impact caps us well below a naive
"same-rate-at-bigger-size" projection; prove and lift the rate first, size small when you do.
