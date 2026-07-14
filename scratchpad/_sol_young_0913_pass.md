# YOUNG (<6h) x 09-13 UTC block candidate — four-half validation pass (2026-07-12)

Follow-up to the 07-11 young-regime mine, which lifted 03-08 for the young lane and flagged:
"young 09-13 looks genuinely bad (wr 25.9%, med -8.0, n=108/23tok) — deserves its own 4-half pass."
Three bots trade this band LIVE (badday_young_rt / absorb / vsnap_ab @ $22.50). Same bar as the
lift, pre-registered: BLOCK only if 09-13 worse in 4/4 halves on BOTH own-trade AND universe lenses.

## VERDICT: NO BLOCK. Pre-registered rule #2 fires: own-bad + universe-BETTER = it is OUR
## SELECTION in that window, not the clock. Blocking 09-13 would forfeit what the universe says is
## the market's BEST gated young window. The fixable thing is churn/never-green re-entries in a
## sparse-supply window (details below), not the hours.

## Evidence base (all local caches, zero new pulls — same unions as the 07-11 mine)
- Own: scratchpad/sol_young_regime/positions.jsonl (11,710 closes, 05-16..07-11, 41 days).
  09-13 was NEVER blocked (configs block 03-08 only; trader CT window 3-17 CT = 8/9-22/23 UTC
  covers 09-13) -> full history, no survivorship hole. Verify Railway TRADING_*_CT unchanged.
- Universe: 50,125 deduped recorder dip events (05-16..06-11 + 07-03..05), age_hours + fwd-30m
  exit_pct. Scripts: sol_young_regime/{own_0913,universe_0913,comp_0913}.py, outputs _*_0913_out.txt.

## THE KEY TABLE — young 09-13 vs young rest, four halves (chrono W1/W2 + odd/even dom)

| Lens | half | 09-13 cell | rest cell | delta (wr pp / tokmed pp) |
|---|---|---|---|---|
| Own closes (all data) | W1 | n=3/3tok wr 66.7 tokmed +20.5 | n=94/42 wr 56.4 tokmed -2.5 | +10.3 / +23.0 |
| | W2 | n=105/20 wr 24.8 tokmed -8.8 | n=461/102 wr 44.3 tokmed -5.8 | **-19.5 / -3.0** |
| | even | n=12/6 wr 58.3 tokmed -10.5 | n=209/68 wr 43.1 tokmed -6.8 | +15.3 / -3.7 |
| | odd | n=96/17 wr 21.9 tokmed -8.2 | n=346/84 wr 48.3 tokmed -3.9 | **-26.4 / -4.3** |
| Universe raw young | W1 | n=730/110tok | n=4235/525 | **+5.8 / +14.6** |
| | W2 | n=1140/161 | n=6628/682 | **+4.4 / +5.7** |
| | even | n=979/140 | n=5544/646 | **+4.1 / +7.6** |
| | odd | n=891/131 | n=5319/645 | **+5.9 / +8.5** |
| Universe GATED young (lane proxy: liq>=25k, pc_h1<=-30, bs_m5>=1) | W1 | n=45/17 | n=370/103 | **+2.9 / +8.7** |
| | W2 | n=58/21 | n=437/126 | **+35.7 / +31.1** |
| | even | n=57/21 | n=438/125 | **+23.6 / +22.9** |
| | odd | n=46/17 | n=369/109 | **+18.6 / +32.6** |

- Own lens: "09-13 worse" = **2/4 wr, 3/4 tokmed** (block-era-only slice: 3/4 wr, 4/4 tokmed with
  n=5..11 cells) — bad-leaning but FAILS 4/4, and the two "09-13-good" halves are n=3 and n=12.
- Universe raw: **0/4 on every metric — 09-13 is BETTER in all four halves**; cat30 (exit<=-30% in
  30m) LOWER in 4/4 (-3.7/-2.9/-4.8/-1.5pp).
- Universe gated: **0/4 — 09-13 is the single best gated young cell of the day** (per-hour gated
  tokmed h09 +10.1, h10 +19.7, h11 +20.3, h12 +2.5, all-day best; cat30 lower 3/4).
- July block-era slice (current market): young 09-13 wr 58.3 tokmed +3.2 cat30 16.7% vs rest
  wr 41.6 tokmed -14.1 cat30 26.2% — again BETTER. One 3-day slice: color, not a half.
- **BLOCK bar (4/4 on BOTH lenses): decisively FAILED. Universe points the opposite direction.**

## Why our 09-13 trades lose anyway — the compositional decomposition (the fixable thing)
It is NOT "same quality, worse outcomes"; it is fewer qualifying tokens + our own worse fills:
1. **Supply drought**: universe gated-pass rate in 09-13 is the lowest of the day (h09-h12:
   5.6/7.4/4.9/4.1% of young dips pass the lane-gate proxy, vs 7-17% other hours; median 1-2
   gated tokens/day in the window). The window is thin, but what passes is GOOD.
2. **Never-green entries**: own young 09-13 fills never go above entry 58.3% of the time vs 37.7%
   rest (young-LANE bots: 64.6% vs 44.1%) — the slow-bleeder ENTRY class from the 06-25 finding.
   Median peak 0.0% vs +5.3% rest. Our 09-13 fills are the not-dipping/never-green kind the
   universe gate would mostly reject.
3. **Churn on sparse candidates**: 09-13 mean fills/token 4.70 vs 3.85 rest; top-2 tokens = 32% of
   all 09-13 fills (vs 8% rest). Single-day pile-ups: CS 14 fills -$235 (06-23), WCANIME 10 fills
   -$55 (06-23), LIZARD 18 fills (07-11), ARROW 17 fills (07-09). When the window offers 1-2
   candidates, the bots re-enter the same dying token.
4. **Slightly lighter books, zero deep-liq winners**: 88.9% of 09-13 fills sit in the 25-50k liq
   bucket, 0% >=100k (rest: 2.2% >=100k at wr 66.7%). Median hold 70s vs 102s (lane).
5. **Day concentration caveat on the scare number**: the flagged wr 25.9%/n=108 rests on 3 days
   (06-23: 27 fills -$317; 07-09: 33 fills -$41; 07-11: 27 fills **+$95**). 07-11's 09-13 window
   was actually the day's PROFIT (blackfebu +$97) at wr 33% — fat-tail shape, exactly what
   tokmed-not-wr scoring exists for.

## What WOULD decide the own lens later (pre-registered, no action now)
Universe 0/4-better makes a block unshippable under the symmetric bar regardless of own accrual.
If anyone re-raises it: re-run own_0913.py when the young lane has >=20 distinct 09-13 tokens per
half across >=10 distinct days with 09-13 fills (currently 16-20 tok/half over effectively 3 heavy
days); block requires own 4/4 AND universe (fresh recorder slice) no longer better in >=2/4.

## Follow-up flags (separate candidates, NOT this verdict)
- Universe says the genuinely weak young cell is **13-14 UTC** (raw wr worse 4/4 vs rest-excl-0913,
  tokmed 1/4 — mixed, cat30 ~35-36%) right at the prime-window open; a 13-14 pass would need its
  own 4-half run before anyone acts. 08-09 boundary is clean (wr 3/4 but tokmed 0/4, mixed).
- The fixable lever the data points at (needs its own A/B, ship-with-consent per standing rules):
  per-token re-entry cooldown / daily per-token fill cap in the young lane (mechanical, measured —
  the allowed exception class). CS-style 14-fills-one-token days are the actual 09-13 loss, and
  the live per-token cap 1 (07-11 commit ff840aa) already covers the live side; the churn shows in
  paper twins.

## Config change: NONE. No code touched, no commits. AxiS decides.
