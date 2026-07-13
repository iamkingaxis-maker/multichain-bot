# SOL red-fleet diagnosis — 2026-07-13 11:27 UTC

**Trigger:** scorecard flagged the SOL fleet red (`rug_gate_buy` BASELINE −6.4 ex2 / 10.9% green,
n=55). AxiS: "point the work at the red SOL entry population." Data: `_trades_cache.json`,
fresh to 2026-07-13T02:57 UTC; recent window = last 10 days (815 joined trips, scrub rule applied).

## What the redness ISN'T
- **Not a control-bot artifact.** The recent 10-day window has ZERO nofilter/baseline control
  bots trading — it's ALL production `badday_*` bots. AxiS's instinct beat my first hypothesis.
- **Not primarily the −7 velocity-bail floor.** Real MAE data (n=812 positions, `mae_pct` on
  sell rows) shows winners barely dip: **median winner MAE = −2.3%; only 3% of winners ever
  dipped past −7%.** The −7 floor cuts ~3% of winners → the June "near-zero winner-kill" audit
  (bot_evaluator.py:295) basically HOLDS on fresh tape. An earlier paired-sample read
  (absorb rescuing 5 tokens the floor cut) was real but rare — 5/166 winners, not the main leak.

## What the redness IS
Uniform floor: nearly every production bot clusters at **ex2 ≈ −6.4%, many at 0% green** —
a dozen radically different exit strategies (flush, wickride, moonbag, runner, absorb) converge
on the same number. Cause is shared, not per-ladder.

- Winners median MAE **−2.3%**, losers median MAE **−5.0%** → the whole fleet **bleeds to small
  losses**. Not a deep-stop problem.
- **~89% of tokens end with a red median trip; only ~11% run.** This is a **winner-capture +
  selection** problem — matches standing memory (`winner_selection`: all selectors fat-tail,
  median stays red).
- The one bot that escapes the wall — `badday_young_absorb` (−1.6 ex2 / 35% green) — wins by
  **riding more of the 11%**: 33% of its trips land in the +6..20+ buckets vs `young_rt_paper`'s
  14%. Its edge is UPSIDE capture, not the floor. (Paired-by-token vs siblings: absorb median-beats
  on 13/16, 13/14, 7/11, 5/7 shared tokens.)

## Levers
1. **Winner capture is the real lever** (already in flight): the barbell / runner / moonbag exit
   A/Bs + the `young_rt` min-hold LIVE A/B all test "let winners run / stop panic-cutting."
   Deployed 2026-07-12; 0 data yet (40 min pre-cache-cutoff). Scorecard grades at n≥30.
   Do NOT promote absorb-style live until n≥30 OOS — the held tail still produces −46/−16 (fat-tail
   promotion trap, cf. adolescent/adaptsize reversals).
2. **Rug tripwire tighten (small, safe, LOW-impact, QUEUED not shipped):** min-hold
   `min_hold_floor_rug_pct` −25 → −15. Data: catches 3–6 disaster losers / 576 (median final
   −16..−18% w/o it) at a cost of ~1% winner-kill. Touches ~1% of positions → not worth a solo
   deploy; fold into next batch.

## Honest bottom line
The SOL fleet is red because ~89% of young tokens bleed to a small loss and we capture too little
of the 11% that run — a selection/winner-capture problem, NOT a stop-loss problem. The panic-cut
exit work helps at the margin but won't move the median much (winners don't dip deep). The median
mover is riding winners further (absorb's edge), which the deployed exit A/B family already tests.
Next real gain comes from the scorecard grading those at n≥30 — measurement, not a new entry axis.
(Memory `entry_opportunity_mine` stands: entry axes exhausted; edge is singular.)
