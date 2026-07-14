# BIRTH-MINUTE TRACTION PREDICTOR — report (run 2026-07-03, data 05:30-14:20 UTC)

**Question**: ~92-97% of GT-visible Solana launches never reach our tradeable
floor (peak mcap >= $100k AND liq >= $25k). Using only what is observable in a
pool's FIRST 5-15 MINUTES, can the scanner separate eventual-traction launches
from the noise and WATCH them earlier (never a buy signal)?

**One-line answer: YES — traction is loud from minute one. Every tradeable-floor
pool in both datasets (6/6 unbiased + 40/40 known-traction) printed a near-full
first-15-minute tape with >= $5k volume; the separator is PARTICIPATION
(minutes printing + early $ volume), not price path. A birth filter passing
~8-15% of launches keeps 100% of tradeable traction and would have put every
recorder-discovered winner on watch ~1.5h earlier than discovery currently
sees it (median first-sight today: age 1.6h).**

## Data

- **Dataset A — unbiased birth cohorts (precision + pass-rate side), n=383
  analyzed**: 401 pools captured AT BIRTH from GT new_pools (median age at
  capture 2.1 min) in three windows: w1 = 2026-07-02 18:25-18:30 UTC (n=127,
  labels resolved at ~20h age), w2+w3 = 2026-07-03 04:47-05:42 (n=274,
  resolved at ~8.6-9.9h). Every pool in each window is in the denominator —
  no survivorship. Excluded: 18 infra/non-launch pools (non-SOL quote or
  birth reserve >= $1M, e.g. two USDC pools with $36M/$484M reserve).
- **Censoring**: window-4 (n=198, captured 13:35 UTC) is < 6h old ->
  EXCLUDED from labels entirely (not counted as no-traction); it is the
  armed forward cohort for the next rerun. Observation windows stated above;
  a pool first reaching the floor after ~9-20h would be uncounted — dataset
  B (p75 age at floor = 5.3h) says most floor-crossings happen well inside
  that, but late bloomers beyond ~20h are formally out of scope.
- **Dataset B — positive-enriched recall side, n=40**: unique pools from the
  production universe-recorder that met the floor (mcap >= $100k AND
  liq >= $25k at a recorder event) while YOUNG (age <= 24h), sampled evenly
  across 2026-06-30 .. 07-03 (174 such pools existed; 40 fetched). Birth
  bars via GT `before_timestamp = pool_created_at + 1h` (exact created ts
  from GT pools/multi). These are confirmed traction pools our discovery
  actually surfaced — the filter must not lose them. Zero overlap with A.
- Bar coverage verified structural: A pools have < 1000 possible traded
  minutes at fetch (limit-1000 reaches birth); B fetch window caps at 60
  bars vs limit 120. 40/40 B pools returned birth bars.

## Label (cleaned — the raw "peak mcap" field is artifact-ridden)

`traction` (mcap side) = sustained peak mcap >= $100k AND life volume >= $5k:
- sustained peak close = max over ADJACENT bar pairs of min(close_i,
  close_{i+1}) — kills single-print spikes;
- supply = DS mcap/price (fallback fdv/price); supply-unknown counted
  no-traction (26/383);
- artifact guard: sustained mcap > $20M on < $100k best liq = wash print
  (6 dropped: USWR $3.2-5.2B, NTFS x3 $0.5-3.9B, FCR $0.97B, THETOKEN $116M
  — all manipulation prints, all rugged to dust);
- volume guard drops seeded-never-traded pools (FCR: $100k reserve, 1 bar,
  $405 life volume).
- `traction_full` additionally requires best-observed liq >= $25k (liq
  history unavailable; liq_best = max(birth reserve, DS liq at check)).

Result: **11/383 (2.9%) traction by mcap; 6/383 (1.6%) full tradeable floor**
[positives n<15 — thin; that is why dataset B carries the recall claim].
The 5 mcap-only positives are thin-liq pools (liq $2-22k) the young lane
could mostly never enter anyway.

## Features (all observable by minute 15 of pool life)

From minute bars, first W min (W=5,15): `n_bars_W` (minutes that printed a
trade), `vol_W` (cum USD), max/end price multiple vs first open, green-volume
share, late-share (vol min 10-15 / vol 0-15). From the new_pools listing
itself (free, no per-pool call, age ~2 min): `reserve0` (initial liquidity),
`vol_h1_seen`.

134/383 A pools have no bar file (dead-screened: DS life-vol < $1k, liq
< $5k, reserve < $10k) — bar features treated as 0. Assumption risk
measured: among fetched pools with life-vol < $1k, only 1/72 (1.4%) print
>= 8 bars in 15m -> ~2 hidden passers expected among the 134; vol-based
thresholds are decidable-fail regardless.

## Top birth-window separators

Medians, traction vs rest (A): n_bars_15 16 vs 2; vol_15 $275k vs $357;
vol_5 $97k vs $328. Dataset B medians match A positives (vol_15 $171k,
n_bars_15 16; minima across all 40: vol_15 $18.2k, n_bars_15 15 — every
known winner printed a FULL first-15-min tape). Price path is NOT the
separator (maxmult_15 >= 1.5 loses 27% of A positives and 22% of B;
green-share is slightly ANTI-predictive — an all-green first 15m skews
sniper-pump-out).

| rule (first 15 min) | pass rate (A, n=383) | full-floor recall | mcap recall | B-recall (n=40) | prec (full floor) |
|---|---|---|---|---|---|
| **n_bars_15 >= 8** | 17.0% (65) | **6/6** | **11/11** | **40/40** | 9.2% |
| **vol_15 >= $5k AND n_bars_15 >= 8** | 15.4% (59) | **6/6** | 10/11 | **40/40** | 10.2% |
| vol_15 >= $25k AND n_bars_15 >= 8 | 11.7% (45) | 6/6 | 9/11 | 39/40 (miss: TJR vol_15 $18.2k) | 13.3% |
| stage-1 only: reserve0 >= $10k (free, listing snapshot) | 16.4% (63) | 6/6 | 9/11 | n/a (B lacks listing snapshot) | 9.5% |
| **funnel: reserve0>=$10k THEN vol_15>=$5k & bars>=8** | **12.5% (48)** | **6/6** | 9/11 | n/a on reserve leg | **12.5%** |
| funnel tight: reserve0>=$20k THEN tape | 7.8% (30) | 6/6 | 7/11 | n/a | 20.0% |

The mcap-recall misses of the funnels are the thin-liq never-tradeable
positives (birth reserve $267-$7.4k, liq never > $22k) — invisible to the
young lane's own entry gates anyway. Base full-floor rate is 1.6%, so
10-20% precision = 6-12x enrichment at zero measured recall cost.

**Split stability** (w1 evening n=124/posFull=2 vs w2+w3 overnight
n=259/posFull=4 — both positive cells n<15, thin):

| rule | w1 pass / full-recall | w23 pass / full-recall |
|---|---|---|
| n_bars_15 >= 8 | 13.7% / 2/2 | 18.5% / 4/4 |
| vol_15>=5k & bars>=8 | 12.1% / 2/2 | 17.0% / 4/4 |
| reserve0 >= 20k | 8.9% / 2/2 | 10.4% / 4/4 |

B split by event time: first-half 20/20, second-half 20/20 at vol_15>=5k
and at n_bars_15>=10. Recall is stable everywhere measured; pass rate
wobbles ~±5pp with time of day.

**Time-to-loudness**: known-traction pools cross $5k cumulative volume at
median minute 0 (their FIRST bars), p90 <= 2-5 min. The signal is fully
formed by minute 5-15; waiting longer adds nothing to recall.

## Watchlist-size math

GT-visible launch rate (measured per capture window): 30.5/min (18:28 UTC),
26.6/min (04:54), 21.3/min (05:40), **1.8/min (13:35 — the UTC 09-13 dead
zone is real in the LAUNCH rate too)**. Active-hours median ~24/min ~=
34,500 pools/day.

| pre-filter | pass | flagged/day (active rate) | concurrent watchlist (2h watch window) |
|---|---|---|---|
| none (watch everything) | 100% | ~34,500 | ~2,880 |
| tape only (vol_15>=5k & bars>=8) | 15.4% | ~5,300 | ~440 |
| funnel reserve0>=10k + tape | 12.5% | ~4,300 | ~360 |
| funnel reserve0>=20k + tape | 7.8% | ~2,700 | ~225 |

Two-stage cost note: stage-1 (reserve0) is FREE — read off the new_pools
listing the scanner already polls (p1-2). Only stage-1 survivors need
per-pool tape (stage-2), i.e. ~2.4-4/min. On free GT that is not sustainable
(this study ate 429s all day at ~0.3 calls/s); the natural production source
is the PumpPortal firehose (`subscribeNewToken` + trade taps, zero API cost,
core/pumpportal_feed.py) or the io.dexscreener tape path — compute
n_bars/vol natively, don't poll GT.

## SHIP / NO-SHIP

**SHIP (as WATCH-EARLIER signal only, never a buy signal)** — wire into the
scanner's young-token discovery lane:

- **Stage-1 (free)**: from the new_pools listing at age <= ~5 min, keep
  pools with `reserve0 >= $10k` (conservative; $20k halves the watchlist
  but its extra misses are only thin-liq mcap-only pools — start at $10k,
  tighten after a week of shadow counts).
- **Stage-2 (tape, evaluate once at age 15 min)**: promote to the young
  watchlist iff `first-15m minutes-printing >= 8 AND first-15m volume >=
  $5k`. Expected watch load ~360 concurrent at active hours (~4,300/day).
- Watched pools then flow into the EXISTING young-lane machinery: fast-watch
  + entry gates (liq >= 25k at entry, buyers >= 10, nf15 >= 0, young holder
  rug guard) unchanged — the pre-filter only decides WHO gets watched from
  minute 15 instead of from first discovery at median age 1.6h (p25 0.6h,
  p75 5.3h).
- Ship shadow-first: log stage-1/stage-2 verdicts + eventual floor-crossing
  per pool for >= 1 week before letting it feed real discovery (realized
  outcomes, per the shadow-scorer house rule).

Why ship despite thin A positives (n=11 mcap / n=6 full, both < 15): the
load-bearing claim is RECALL, and it is carried by dataset B (n=40, zero
misses, split-stable, sampled across 2.4 days of production discovery) plus
6/6 + 11/11 on the unbiased side; pass-rate denominators (n=383) are
healthy. Risk is bounded by construction — a false positive costs a watch
slot; a false negative costs nothing vs today (B pools ARE discovery's own
finds, so at worst the pool is found at age ~1.6h exactly as now).

What we learned for next pushes (map, not verdicts): price path in the
first 15m carries no selection power (don't build a birth-momentum screen);
all-green birth tape is a mild negative (sniper pump-out signature) —
candidate tiebreaker if the watchlist ever needs shrinking; the launch RATE
itself has the same UTC dead zone as demand (09-13) — the watcher can sleep
with the market.

## Reproduction / rerun

- Fetch (additive caches, single process): `python scratchpad/ripday/traction_fetch.py`
  (new_pools windows + w1 bars + recall set) and `traction_fetch2.py`
  (resume/extend + w2/w3 + DS refresh). Caches: `_gt_newpools_cache.json`
  (599 pools, 4 birth windows), `_gt_bars/` (284 files), `_gt_bars_b/` (40),
  `_recall_set.json`, `_ds_state_cache.json`, `_w23_dead.json`.
- Analysis (no net): `python -X utf8 scratchpad/ripday/traction_predict.py`
  (full output `_traction_predict_out.txt`; per-pool rows
  `_traction_rows.json`) then `traction_addendum.py` (full-floor sweeps,
  funnel, time-to-loudness, dead-screen risk).
- Window-4 (n=198, born ~13:30 UTC) resolves ~19:40 UTC 2026-07-03 — one
  rerun of fetch2 + predict adds a third independent split cell.
