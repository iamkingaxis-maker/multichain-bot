# Green-Day Forecaster — first-2h demand composition vs family day outcome

Date: 2026-07-03. Question: from the first ~2h of the trading day (08:00–10:00 UTC), can day-level
demand composition call whether the badday family's day closes green or red?

## Data

- Source: dashboard per-bot endpoints `/api/bots/{id}/trades?limit=10000` (the global `/api/trades`
  caps at 5000 rows ≈ 8.5 days; per-bot goes back to 2026-06-12 with full `entry_meta`).
- 15 family bots (all `badday_*` excl. `badday_young_absorb`, `badday_fill_probe_live`;
  `badday_swing_latch`/`badday_admit` have no trades). 12,448 records → 5,100 closed episodes
  (per-bot per-token buy→sells pairing).
- SCRUB RULE applied: sells with `pnl_pct>0 AND hold_secs<10` excluded from all P&L.
- Trading day = 08:00 UTC → next 08:00 UTC (post sleep block). Episode assigned to day of its buy.
- Day outcome = per token: sum `pnl_pct*sell_fraction` per bot → average across bots → sum over
  tokens (pp). **Target = rest-of-day outcome (episodes opened ≥10:00)** — that is what a 10:00
  stand-down decision actually affects. Full-day target gives the same ranking.
- 22 days total; **17 usable** (≥1 fill in 08–10 UTC). Skipped: 06-11 (history edge), 06-15, 06-29,
  06-30, 07-01 (zero early fills — drought/overgating days). Of the skipped, 3 red / 1 green /
  1 edge — "no early dips by 10:00" is itself weak-red, not neutral.
- Class split: 7 green / 10 red. ⚠️ **Greens are ALL ≤06-19, reds ALL ≥06-20** — outcome is
  calendar-clustered, so every day-level result below is partially confounded with "era".
  16-day data cannot separate signal from regime drift. Forward harness is the only resolution.

## Ranked candidate signals (n=17 days, green µ vs red µ, Mann-Whitney AUC/p, LOO)

LOO = leave-one-day-out: threshold refit on 16 days, held-out day predicted. Base rate = 0.59
(always-red). LOO 0.76 = 13/17; binomial P(≥13/17 | 0.59) = 0.11 → **not significant at this n**.

| rank | feature (08–10 UTC, first fill per token, union across bots) | µ green | µ red | AUC | p | LOO | verdict |
|---|---|---|---|---|---|---|---|
| **1** | **med_nf60** — median `net_flow_60s_usd` at early entries | **−78** | **+132** | **0.17 (inv)** | **0.025** | **0.76** | best candidate; see below |
| 2 | n_early_dips — distinct (token × 5-min) dip count | 7.4 | 3.5 | 0.66 | 0.26 | 0.76 | LOO flatters it: Spearman vs day-magnitude ≈ 0 (−0.03) → threshold artifact, weak |
| 3 | wr_first5 — win rate of first ≤5 token-rounds closed by 10:00 | 0.60 | 0.39 | 0.66 | 0.28 | 0.59 | LOO = base; directionally sane, no lift |
| 4 | absorb0 — share of early tokens whose episode ever went green | 0.96 | 0.77 | 0.66 | 0.28 | 0.53 | weak |
| 5 | absorb3 — share bouncing ≥+3pp | 0.81 | 0.68 | 0.60 | 0.50 | 0.47 | weak |
| 6 | n_early_tokens (breadth) | 4.3 | 2.3 | 0.71 | 0.14 | 0.47 | in-sample AUC decent, LOO below base → unstable |
| — | med_buy_size (median `median_buy_size_usd`) | 22.9 | 26.7 | 0.47 | 0.85 | 0.00 | **NULL at day level** (direction flips every fold) — the per-trade winner-selector does not aggregate into a day call |
| — | early_realized_pp — scrubbed realized P&L of rounds closed by 10:00 | +1.3 | +0.1 | 0.53 | 0.85 | 0.18 | **NULL — vindicates "predict, don't lose-to-learn"**: 06-17 early −29.5pp → rest +27; 06-22 early +25.6pp → rest −136 |
| — | med_uniq_buyers, n_early_fills | — | — | 0.47–0.49 | — | — | null |

### The one real lead: early net-flow composition (med_nf60)

Median 60s net flow (USD) across the day's early entries, one value per token (first fill):

```
green: 06-12 +9 | 06-13 +44 | 06-14 +37 | 06-16 −185 | 06-17 −350 | 06-18 +15 | 06-19 −112   (all < +46)
red:   06-20 +159 | 06-21 +235 | 06-22 +139 | 06-23 +444 | 06-24 +87 | 06-25 −243 | 06-26 +159 | 06-27 −257 | 06-28 +552 | 07-02 +48
```

- Rule shape: **med_nf60 ≥ ~+50 at 10:00 UTC → red day** (in-sample balanced acc 0.90; catches
  7/10 reds at 0 false-stand-downs on greens). Misses are the two 1-token low-activity reds
  (06-25/27, very negative nf60) and 07-02 (+48, on the line).
- Spearman vs rest-of-day P&L magnitude = **−0.53** (only feature with real magnitude correlation).
- **Mechanism is coherent with prior per-trade work, not a fresh coincidence**: positive-but-modest
  net inflow at the dip = pump-retrace demand (the shipped nf5m_toxic_zone [0,+300) block found
  exactly this toxic band per-trade). Capitulation days enter on flat/negative flow that then
  absorbs; pump-retrace days enter on "demand" that is chase flow.
- Caveats (why this is a shadow candidate, not a gate): (a) median of **1–5 tokens/day** on most
  days — extremely thin per-day sample; (b) era confound above (nf60 drifts with day index,
  rank-ρ 0.35); (c) nf5m variant does NOT confirm (AUC 0.39) — only the 60s window separates;
  (d) LOO 13/17 is p≈0.11 vs always-red.

## Leave-one-day-out result

- med_nf60: **13/17 (0.76)** vs base 0.59, threshold stable near +46 across folds, direction never
  flips. Not statistically significant at n=17 (binomial p≈0.11).
- n_early_dips also hits 0.76 LOO but fails the magnitude check → treat as noise.
- Everything else ≤ base under LOO.
- Conclusion: **one candidate worth accruing forward data on (med_nf60), nothing enforceable.**

## Daily measurement harness — what to log at 10:00 UTC (1 data point/day)

New JSONL `greenday_forecast.jsonl`, one record per trading day, written by a small scheduled job
(or dashboard tick) at 10:00 UTC; outcome back-filled at next 08:00 UTC.

```json
{
  "day": "2026-07-04",                       // trading day (starts 08:00 UTC)
  "window": "08:00-10:00Z",
  "per_token": [                              // union across family bots, FIRST fill per token
    {"addr": "...", "t_first_fill": "...",
     "net_flow_60s_usd": -112.4, "net_flow_5m_usd": -56.0,
     "median_buy_size_usd": 21.5, "unique_buyers_n": 14,
     "rsi_15m": 38.2, "pc_h6": -12.1, "liquidity_usd": 45210,
     "bounced_3pp_by_10z": true,              // fast-watch price ≥ fill*1.03 within 60min
     "went_green_by_10z": true}
  ],
  "agg": {
    "n_early_tokens": 4, "n_early_dips_tok5min": 7, "n_early_fills": 19,
    "med_nf60": -112.4, "share_nf60_pos": 0.25,
    "med_buy_size": 21.5, "absorb3": 0.75, "absorb0": 1.0,
    "wr_first5_closed": 0.6, "n_closed_by_10z": 4, "early_realized_pp_scrubbed": 3.1
  },
  "prediction": {"call": "green", "rule": "med_nf60<+50 v1", "no_signal": false},
                                              // no_signal=true when n_early_tokens==0 (log it — 3/4 such days were red)
  "outcome": {"rest_sum_pp": null, "full_sum_pp": null, "rest_green": null}  // backfill at 08:00 next day
}
```

Implementation notes:
- All inputs already exist at decision time: `entry_meta.net_flow_60s_usd` etc. are logged on every
  buy; absorption needs only the fast-watch price buffer (fill price vs max price in following 60min).
- Scrub rule + per-token bot-averaging for the outcome backfill must match this report
  (exclude `pnl_pct>0 & hold_secs<10`; token = avg across bots; rest = episodes opened ≥10:00).
- Keep the family roster in the record if bots change, so outcomes stay comparable.

**Validation bar before ANY enforce discussion** (per honesty constraints): ≥30 forward days with
≥1 early fill, both classes present, forward accuracy of the logged v1 call ≥70% AND binomial
p<0.05 vs the trailing base rate, AND the confound check passes (signal not merely tracking a
green-era/red-era calendar split — require both classes on each side of the sample midpoint).

## What this run rules out / confirms

- **med_buy_size does NOT day-forecast** (the per-trade ≥34.3 selector story doesn't aggregate).
- **Reactive early P&L does NOT day-forecast** — direct quantitative support for AxiS's
  "predict, don't lose-to-learn": lose-to-learn on the first rounds would have been wrong on the
  two biggest swing days in the sample.
- Breadth/dip-count/absorption: directionally sane (more early dips, more absorption → green) but
  no LOO lift; keep them in the harness as cheap covariates, don't rank them as signals.
- No SOL-based features touched (per do-not-build list).

Artifacts: `scratchpad/gd_analysis.py`, `gd_stats.py`, `gd_nf_check.py`, `gd_days.json` in the
session scratchpad (temp dir); raw per-bot pulls `bot_*.json` same dir.
