# REVIVAL-POND Definition Study — 2026-07-04 (analysis only)

Built from: `ledger3_wallets.json` + `winners_by_day.json` + bars (`_gt_bars/`, `ohlc2_*`) +
tapes + fresh pulls `_fleet_trades_0704.json` (/api/trades?full=1, span 06-26→07-05) and
`_urec_0704_big.json` (/api/universe-recorder?limit=20000 → 5000 events, 07-03 02:13 → 07-05 02:48).
Scripts: `revival_pond.py` (Part A), `revival_pond_b.py` / `revival_pond_c.py` (base rates),
inline checks for visibility/exits. Episode dump: `_revival_eps_0703.json`, grid: `_revival_grid.json`.

## Q1 — What a 07-03 "revival" looks like mechanically (n=13 tokens, 131 winner eps, 53 wallets)

Token-level at FIRST winner entry (per-token dedup):

| axis | p25 | med | p75 | note |
|---|---|---|---|---|
| age | 126h | 136h | 385h | mostly 3-16 days; the 2 ancient ones (300+ days: GOON, MITCH) were the FLAT ones |
| dormancy (prior-24h avg hourly vol / peak hourly vol in 48h coverage) | 0.08 | **0.12** | 0.17 | all ≤0.31 — cooled to ~10-20% of the recent hot era |
| prior-24h vol | $221k | $281k | $832k | NOT dead tokens — still doing $8-12k/h. These are cooled RECENT runners, not lazarus corpses |
| reawaken ramp (entry-hour vol / prior-24h hourly avg) | 1.0 | **3.6** | 5.3 | NOT universal: 5/13 entered at ramp ≤1.1 (winners anticipate); the one ramp-23x entry (TMB) was the worst loser (-26.8%) — blowoff chase |
| price vs 48h high | −38% | **−16%** | −11% | they buy the BASE near the recent high, not the crater; the 3 deepest (−55/−60/−75%) were flat/small |
| unique buyers, entry hour (tape) | 22 | 31 | 61 | participation signal present again |
| entry-hour vol | $10k | $59k | $162k | |

Key structural fact: bar coverage back from entry med 44h and the peak era sits INSIDE it —
"revival" on 07-03 = >72h-old token whose latest hot era was within the last ~48h, cooled to
~12% of it, re-igniting with real participation. Trailing-48h stats suffice; no lifetime ATH needed.

**Predicate (bars version, P0):** `age>72h AND peak48_hourly_vol>=25k AND dorm24<=0.35 AND
entry_hour_vol>=5k AND ramp>=1.5 AND px>=0.55*high48`.
Recall on the 13 winner tokens: **10/13**, firing at/BEFORE first winner entry (median lead ~0.7h).
Misses = Zeus/Udin/Martolexx (low-ramp anticipation buys or −75% base) — the predicate is a
follower of the reawakening, which is fine for a bot.

**Predicate (live-features version, from existing FeatureBundle):** `age_hours>72 AND
vol_h24>=100k AND vol_h1>=1.5*(vol_h24/24) AND unique_buyers_n>=10 AND liq_usd>=25k`.
Recall 10/13 on pre-entry recorder events (misses Zeus/Udin/GOON). A vol_h6-based dormancy
proxy was tested and REJECTED (halves the pool, no outcome gain).

## Q2 — Base rates (full pair set with bars, hourly grid, per-token dedup)

Universe caveat first: the grid universe = 292 ledger pairs = the RUNNER tape (already selected
for movement), so "non-matching" is a hot control group; and +15%-touch on minute-bar highs is
wicky. Both shown:

- **Wick outcome (max high ≤6h ≥ +15%):** matched tokens 19, hit **73.7%** vs never-matched
  active >72h tokens 19, hit 68.4%. Events: 81 vs 757, 72.8% vs 65.3%. FLAT on touch rate.
- **Holdable outcome (max CLOSE ≤6h ≥ +15%):** 63% vs 68% — still flat. BUT magnitude and
  persistence differ: matched med max-close **+55.0%** vs +21.8%, med 6h-END **+13.5%** vs
  +0.6%. Med drawdown ≤6h −20% both groups (wickride-consistent: this pond needs wide floors).
- Matched tokens/day (bars grid): 07-01: 3, 07-02: 7, 07-03: 11, 07-04(partial): 8.
  On live recorder features (looser, no dorm/base terms): ~35-40/day.
- Live-features corroboration on recorder events (30m outcome window only): matched 62 tokens
  peak30m med +5.7 / won10 27% vs non-matched 41 tokens +2.2 / 17%.
- Split-check by day: hit(CL15) 07-01 33% (n=3) / 07-02 86% (n=7) / 07-03 64% (n=11) /
  **07-04 partial 25% (n=8, medEND −9%)**. The pond's edge is REGIME-DEPENDENT and today's
  partial tape is NOT confirming for fresh signals. n thin everywhere (<15 per day) — pooled
  numbers are the citable ones.

**Honesty on the +16.6%:** the 131 winner eps' +16.6% is MARKS. Matched realized = **−17.5%**
of buy USD; net is positive only via unrealized mark-to-last-bar (partly mechanical: tape-start
caps + young buys). Per-ep med net +6.6%, winrate 79%. Day-2 confirm therefore = did the bags
monetize. Partial check with today's bars (to ~08-13 UTC): 7/13 tokens green on 07-04
(manlet +306%, Hobbes +64%, ??? +58%, Martolexx +40%, TATE +28%), 4 red, 2 flat — the 07-03
cohort's bags HELD, while fresh 07-04 revival signals underperformed. Mixed; full 07-05 decode decides.

## Q3 — Fleet visibility: NOT blind. Discovery sees it; the ENTRY layer ignores it.

- Universe-recorder emitted events for **13/13** revival tokens on 07-03, all with events
  BEFORE the first winner entry (3-27 pre-entry events each; recorder saw 103 distinct >72h
  tokens in ~2 days). No new watch/pin machinery needed for SEEING.
- Fleet TRADES on 07-03: only **5/13** tokens got any buy (Udin 17 buys, Martolexx 7, NEIL 1,
  GOON 1, Hobbes 1 — all flush-family entries), 0 buys on the other 8 including manlet (+204%
  winner ep, +306% next day). All fleet sells on these were RED (−1.8 to −7.3%) — we
  flush-bought the same tokens winners rode, and stopped out inside the −20% med 6h drawdown.
- So the gap is two-layer: (a) no bot's entry thesis covers "reawakening base near recent high"
  (flush bots need pc_h1<=−30-style craters; the revival entry sits −11..−16% under the 48h
  high, often GREEN on h1), and (b) exits: family stops sit inside this pond's normal wick.

## Q4 — Strawman jersey (BUILD ONLY IF 07-05 DECODE CONFIRMS DAY-2)

`badday_revival_absorb` — clone `badday_adolescent_absorb.json` mechanics, change the pond:
- **Age band:** `age_h_min: 72`, `age_h_max: null` (soft-avoid >30d: the 2 ancient tokens were flat — optional `age_h_max: 720`).
- **Entry gate (live features, all exist):** `unique_buyers_n>=10`, `wash_suspected<=0`,
  `liquidity_usd>=25000`, `vol_h24>=100000`, `vol_h1>=1.5x daily-avg-hourly` (needs a derived
  feature `vol_h1_vs_h24_ratio>=1.5` — vol_h1_accel_vs_h6 already exists as fallback),
  `mcap_psych_pc_h24_max: 80` (blocks the TMB blowoff-chase case), `entry_gate_require_data: true`.
  Keep a dip trigger but SHALLOW: pc_h1<=−10 (not −30) — winners buy the −10/−16 base, and med
  6h drawdown −20 means a dip entry inside the ramp is available; do NOT require a crater.
- **Exits:** winners here scratch-machine it: hold-to-first-sell med 24m, sells med +0.0 vs
  entry VWAP (p75 +10), frac_sold med 0.97, rebuy-after-sell 55/101 — i.e. TP1 +6/75% and
  TP2 +12 are compatible; the pond's payoff is the fat tail + re-entry, so
  `reentry` open + wickride arm (`velbail_pnl_pct: -8`) and hard_stop −12 as floor
  (med pond drawdown −20 says even −12 gets wicked ~half the time — log giveback shadow, don't widen live floor without the exit-decode's rug-gate stack).
- **Hours:** 13–22 UTC start (10/13 first winner entries in 12:00–23:59; decode says 23–01 no longer winner-empty — log 23–01 shadow fills).
- **Size/caps:** $100 base, max 2 concurrent, daily stop $40 — same as adolescent.
- **Judged against:** `badday_adolescent_absorb` (same mechanics, different pond) AND
  `badday_flush` (which already trades some of these tokens, red) at n>=30 distinct tokens scrubbed.

## Verdict for tomorrow's confirm (what to check on the 07-05 decode, instant)
1. Do 07-04 winner episodes again over-allocate to >72h tokens, and is base green there?
2. Did the 07-03 revival bags monetize (matched realized, not marks)?
3. Run `revival_pond_b.py` on the extended bars — is 07-04 full-day matched-token hit(CL15)
   back above ~60% (07-04 partial read: 25%, n=8 thin)?
If (1)+(3) fail → the pond was a one-day rotation; file it, no build.

## Honesty ledger
- n=13 tokens / 131 eps for the definition — thin; every threshold is a p25-ish envelope, not a fit.
- Grid "control" = runner-tape >72h tokens (survivorship-hot); true discovery-universe base rate unknown but recorder-event corroboration (27% vs 17% won10) is universe-native.
- +16.6% is marks (realized −17.5%); the study does NOT validate cash extraction.
- 07-04 numbers are partial-day; recorder trades pull capped at 5000 (span covered 06-26→07-05, OK).
- Per-day splits all n<15; pooled event n=81 matched.
