# RH Candidate Factory (2026-07-12)

AxiS: "you need better candidates." The 10-scalp paper fleet is net red and the 3 aged
racers are a day old; passive accrual toward the Phase-1 bar (n>=20 distinct tokens,
tokmed green ex-top2, >=5 days, cat<=1/20) is too slow. This factory mined the FULL
replayable history (rh_history/sweep_logs.jsonl.gz — 10.36M swaps, every WETH pool,
2026-07-01..11) for entry configs that would PASS that bar under realistic exits and
fills, graded with four-half discipline (chrono W1 07-01..05 / W2 07-06..11 x odd/even
day-of-month; a config ships only on 4/4), and wired the survivors as paper racers.

Code + data: scratchpad/rh_factory/{factory_mine.py, factory_sweep.py,
factory_adversarial.py, winner_delta.py, candidates.jsonl.gz, sweep_results.json,
adversarial.json, winner_delta.json, PROGRESS.md}.

## 1. Winner-delta on <1h pools (what winners buy that our scalps don't)

Cohorts (hist_decode.py definitions, rerun on hist_* + tape_* tapes, 691 pools):
93 audited day-robust winner makers (294 <1h-band entries on 26 pools over 9 days) vs
61 repeat pure-ontape loser makers (311 entries, 76 pools, 4 days). Features
reconstructed AT ENTRY TIME from the pool tape (p25/p50/p75, winners | losers):

| feature                    | WINNERS            | LOSERS              |
|----------------------------|--------------------|---------------------|
| dip vs 10-min high (pct)   | -28.8 / -8.6 / +0.8| -33.6 / -15.6 / -0.1|
| pool age (s)               | 393 / 1615 / 5528  | 477 / 1048 / 2343   |
| arc: px vs first print (pct)| +191 / +540 / +2132| +526 / +1240 / +3081|
| pre-entry cum volume ($)   | 3.4k / 16.0k / 100k| 2.0k / 6.6k / 64k   |
| pre-entry swaps            | 59 / 198 / 1207    | 26 / 77 / 735       |
| net inflow 120s ($)        | -94 / +175 / +731  | 0 / +187 / +1983    |
| distinct buyers 120s       | 3 / 8 / 57         | 2 / 9 / 65          |

**The signature in one paragraph:** at <1h pool age, the audited winners buy MODERATE
pullbacks (median -8.6% off the 10-min high) EARLY in the launch arc (median +540% over
the first print) on pools with PROVEN demand (median $16k volume / ~200 swaps before
entry, two-sided tape) — while the repeat losers buy DEEPER flushes (median -15.6%)
LATE in the arc (median +1240%) on thinner pools ($6.6k / 77 swaps), chasing larger
120s inflow spikes. Demand-at-the-moment (net inflow, distinct buyers, buy counts) does
NOT separate the cohorts — position in the arc and proven volume do. Our own fleet sits
on the LOSER side of every axis it touches: all-buys dip p50 -17.2%, and, decisive:
OUR PAPER FLEET HAS ZERO <1h ENTRIES (345 buys) — min_pool_age_h=1.0 walls off the
entire band (the 91-wallet cohort ran 88% win / +$9,128 there), and rh_launch_scalp's
strength-mode never fired in it either. Caveats: entry-level separation is strong but
pool-level support is thin (26 W vs 76 L pools, non-random tape selection) — so the
signature only PROPOSED sweep axes (dip band cap, arc cap, volume floor, age band);
shipping decisions came from the full-history sweep below, never from this table alone.

## 2. Replay + realistic exits (the harness extension)

factory_mine.py extends rh_regime/mine_regimes.py (same block->ts anchors, registry
age, establishment bar >=10 swaps / >=0.3 ETH cum, 600s per-pool cooldown), with:
- LOOSE trigger superset: dip >=6% off the 10-min high + last-30s buys >=0.015 ETH
  (the sweep re-applies tighter cuts offline). 64,164 candidates, 63,972 written,
  63,473 graded (day >= 07-01).
- RICH stamps: dip depth, age, b30/s30/nb30/ns30, b120/s120/nb120, session cum ETH
  (liq proxy), arc vs first print, drawdown vs session ATH, pop recency/magnitude
  (pop = latest px >=1.35x the 10-min low, 600s cooldown — 31,208-pop-family analog),
  hour, npph, day.
- THREE exit-ladder simulators mirroring PerBotPositionManager order (hard stop ->
  pre-TP1 peak-armed trail arm>=5/gap2 -> TP1 partial -> TP2 partial -> post-TP1
  trail; one action per tick):
  scalp = tp1 +6 (75%) / tp2 +12 (25%) / stop -15 / trail 3pp   (rh_young_v1 shape)
  aged  = tp1 +6 (50%) / tp2 +16 (30%) / stop -15 / trail 10pp  (rh_aged_hold shape)
  tbox  = tp1 +5 (90%) / tp2 +10 (10%) / stop -8 / 20-min box
- CONSERVATIVE fills: entry px*1.01, every exit px*0.99 (>=2% RT, the minimum the
  mission mandates; the lane's rt-cost gate allows up to 6%), minus 0.2pp flat gas.
  Stops/TPs fill at the NEXT OBSERVED swap price — gap-through-stop keeps the
  catastrophic fill. TP legs book at most threshold+15pp (phantom-print guard);
  any leg caps at +300%.
- DEATH HONESTY (the v1 self-audit catch): a pool that never trades again within
  the stream books remaining exposure at -90% (res="dead") — v1 booked these at the
  last observed px as if sellable, which masked rugs/abandonment in 15-21% of trips
  in the then-"best" cells and produced a fake 619-survivor wall. Only trips cut by
  the END of the stream keep last-px booking (res="stale_end", stress-tested).

## 3. Sweep results (four-half, Phase-1 bar per half)

983 cells graded (540 primary age x dip x vol-floor x demand x exit + refinement axes
arc/pop/nb30/hour/athdd on 60 seeds). 562 cells pass the bar in ALL FOUR halves —
but the bar's letter (tokmed ex-top2 green + cat<=1/20) admits many cells whose MEAN
is negative (median pool green, dead-pool left tail eats the sum; e.g.
u10m|mod|v.3|d50n|scalp: tokmed_ex2 +$1.55 with net -$1,344). Shipping therefore
required, on top of 4/4: positive overall net, high MIN-half tokmed_ex2, neighborhood
robustness, and runtime expressibility (d50x2 ratio cells skipped — no lane knob).

ETH ~$1,570-1,610 over the window, so vol floors v.3/v3/v10 = ~$480/$4.8k/$16k.
Band pattern: u10m (<10 min) and >24h dominate the strong cells; the >24h aged-ladder
cells are the full-history-decode thesis CONFIRMED under realistic exits (dead 0.2%,
cat ~0). The five shipped cells, per half (n / pools / tokmed_ex2 / cat):

**u10m|sh|v3|d25|aged+arc<=300  ->  rh_f_pullback**
  W1 163/163/+$2.68/0.0%   W2 495/495/+$2.33/0.8%
  odd 247/247/+$1.97/0.4%  even 411/411/+$2.52/0.7%
  overall n=658 pools=658 tokmed_ex2 +$2.46 cat 0.6% net +$738 med_ret +9.9 dead 21.6%

**u10m|mod|v3|d25|scalp+arc<=300  ->  rh_f_arc_scalp**
  W1 239/239/+$2.17/0.0%   W2 786/786/+$1.85/0.5%
  odd 396/396/+$1.93/0.2%  even 629/629/+$1.97/0.5%
  overall n=1025 pools=1025 tokmed_ex2 +$1.97 cat 0.4% net +$337 dead 20.7%

**u10m|deep|v.3|d50n|scalp+popret  ->  rh_f_popret**  (pop-retrace family)
  W1 118/118/+$2.15/0.0%   W2 858/858/+$1.92/0.0%
  odd 324/324/+$1.94/0.0%  even 652/652/+$1.93/0.0%
  overall n=976 pools=976 tokmed_ex2 +$1.94 cat 0.0% net +$142 dead 17.8%

**>24h|vdeep|v10|d25|aged  ->  rh_f_reload24**
  W1 247/75/+$1.08/0.0%    W2 2346/594/+$1.83/0.0%
  odd 738/264/+$1.56/0.0%  even 1855/524/+$1.50/0.1%
  overall n=2593 pools=605 tokmed_ex2 +$1.78 cat 0.0% net +$1,285 dead 0.2%

**6-24h|vdeep|v.3|d50n|aged  ->  rh_f_reload_mid**
  W1 77/51/+$2.41/1.3%     W2 553/312/+$0.76/0.4%
  odd 231/148/+$0.81/1.3%  even 399/239/+$0.89/0.0%
  overall n=630 pools=362 tokmed_ex2 +$0.93 cat 0.5% net +$214 dead 1.3%

Notable non-ships: pop-chasing (momentum) stays dead on non-young pools (regime v1
kill stands); tbox ladders were never better than scalp/aged on the same admission;
vdeep-without-pop u10m cells were 3/4 (W1 fails) — the pop context is what makes the
deep flush buyable there.

## 4. Adversarial pass

- NEIGHBORHOOD (rh_factory/adversarial.json): every perturbation notch of every
  shipped cell (dip edges +/-3pp, vol floor x0.5/x2, demand +/-50%, arc 200/450,
  popret 1200/2700) stays GREEN (tokmed_ex2>0 + cat<=5% in all four halves at a
  relaxed >=8 pools/half bar). None of the five is a lone spike.
- STALE_END STRESS (stream ends inside the trip horizon; the only unknowable left):
  drop-variant passes 4/4 for all five. Worst-case (-90 on every stale_end trip):
  popret/arc_scalp/reload24/reload_mid stay 4/4; rh_f_pullback degrades to 2/4
  (tokmed_ex2 still +$1.94, cat 4.1%) — stated caveat, watched at confirmation.
- SURVIVORSHIP & BACKTEST-VS-PAPER GAP (assumed, not proven):
  1. Substrate = registry WETH-quoted pools only; non-WETH routes invisible.
  2. Maker-less replay: no competition from our own clips or other snipers; no
     honeypot/rt-cost gate in the backtest (both RESTRICT paper entries further —
     conservative for selection, but paper takes fewer trips).
  3. Demand breadth nb30 = buy PRINTS (1 swap = 1 tx in sweep rows), not distinct
     buyers; historical liq unknown -> cum-volume proxy; runtime racers use the REAL
     liq floor ($5k = the watched-substrate floor) + the proven-vol gate.
  4. Fills: next observed swap px +/-1% haircut, TP fills capped at threshold+15pp,
     any leg capped +300%. Real detect->fill is 1.7-2s on fast movers; the +1% entry
     haircut is the assumed cover. Runtime decides on QUOTED sell-side prices
     (better information than the backtest's pool-mid proxy).
  5. Runtime demand_turn requires net>0, which d25 cells didn't — an extra
     restriction (subset); conservative direction but selection may shift.
  6. Arc basis: mine = first swap in the stream (= birth for young pools); lane =
     first-seen quote px, persisted. Same object for pools discovered at creation.
  7. max_concurrent=2 and the factory exclusion group are not simulated — paper
     throughput will be a fraction of backtest n.
  8. Block->ts interpolation smears minutes near block-rate shifts (no shipped
     racer is hour-gated, so not load-bearing).
  9. W1 = launch week; the parity split exists to catch W1-only artifacts, and no
     shipped cell leans on W1 (it is the THIN half for all five).

## 5. Shipped racers (exclusion_group="factory", scripts/rh_paper_lane.py)

All five: $25/entry, max 2 concurrent, 600s re-entry cooldown (the mine's trigger
cooldown), min_liq_usd=$5k (cell-verbatim substrate floor — the cat rates above
PRICED the rug tail with no liq gate; honeypot fail-closed, rt-cost<=6%, LP-drain
veto and exit, and same-tick sibling arbitration all still apply). New gates wired
for them: dip_max_depth_pct (dip_too_deep), min_session_vol_usd (thin_session_vol,
lane cum_vol tracker, persisted), max_arc_pct (arc_late, first-seen px basis,
persisted), require_pop_within_s (no_recent_pop, pop_book fed by the mine's 1.35x
detector over the quote series, 600s pop cooldown).

| racer          | admission                                                     | exits                                  |
|----------------|---------------------------------------------------------------|----------------------------------------|
| rh_f_pullback  | age<10m, dip -6..-12, vol>=$4.8k, arc<=+300%, buys30>=$25     | tp1 6/50% tp2 16/30% trail 10 stop -15 |
| rh_f_arc_scalp | age<10m, dip -6..-25, vol>=$4.8k, arc<=+300%, buys30>=$25     | tp1 6/75% tp2 12/25% trail 3 stop -15  |
| rh_f_popret    | age<10m, dip<=-12, pop within 30m, vol>=$480, buys30>=$50 net | tp1 6/75% tp2 12/25% trail 3 stop -15  |
| rh_f_reload24  | age>24h, dip<=-25, vol>=$16k, buys30>=$25 (DORMANT until RH_FEED_MAX_AGE_H>24; arms automatically) | tp1 6/50% tp2 16/30% trail 10 stop -15 |
| rh_f_reload_mid| age 6-24h, dip<=-25, vol>=$480, buys30>=$50 net               | tp1 6/50% tp2 16/30% trail 10 stop -15 |

rh_f_reload24 ships regime_hours=False deliberately: its cell passed 4/4 WITH 19-21
UTC included — cell-verbatim; the aged hour gate stays the existing aged racers' A/B.

Tests: tests/test_rh_factory_racers.py (33: pure gates, _note_px/pop_book/cum_vol
mechanics + persistence, roster specs, entry routing incl. pop-then-dip entry and
no-pop block). Full RH suites post-wiring: 428 passed / 2 skipped (pytest exit 0).

## Pre-registered paper-confirmation bar (backtest earns a RACE seat, never a live seat)

Each factory racer must CONFIRM its backtest in the live paper lane before any
promotion talk: n>=30 CLOSED positions, and at that n: token-median (per-token
realized, ex-top-2) green, catastrophe rate <=1/20, direction consistent with its
backtest cell. Grading vs the 10-scalp fleet as control, per-token medians, never
sums. Failure = the racer retires from the roster (the cell goes to the documented-
kills list); no re-tuning on the same tape.
