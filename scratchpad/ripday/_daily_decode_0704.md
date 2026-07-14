# Daily Wallet-Behavior Decode — 2026-07-04 (07-03 tape delta vs 07-01/02)

Built 2026-07-04 from local tapes. Pipeline: `build_ledger3.py` (day-scoped union-of-entries ledger)
→ `score_days.py` (per-day winnability) → `delta_decode_0704.py` (4 finding deltas + rotation) →
`flush_medbuy_0704.py` (medbuy flush split) → `conv_check_0704.py`. Full console dumps:
`_delta_0704_full.txt`, `flush_events_0704.json`, `winners_by_day.json`, `ledger3_wallets.json`.

## Coverage (better than briefed)
- Dense tape: 07-03 ~13:30 UTC → **07-04 04:26 UTC** (recorder ran overnight); gap = 07-04
  04:26 → 12:13 UTC (restart), thin sweep backfill 09:00–12:15. The briefed "23:15 → 12:15" gap
  is wrong — we have the whole 07-03 evening and overnight.
- 07-03+ pairs: 107; bars 105/107 (GT extended to last-trade+4h this session); ages 107/107.
- Total in-window (07-01→): 298 pairs, 244k unique trades, 46.7k wallets, 65.7k episodes.
- Accounting: matched in-window realized ONLY (covered sells − buys, pre-window/pre-buy inventory
  capped, sell-only wallets excluded, barless episodes excluded via `no_px`), unrealized =
  mark-to-last-bar, labeled. Aggregators excluded per-day (≥25 pairs/day, ≥400 trades/day, churn
  spray) and full-window (≥40 pairs, ≥800 trades). Episode = (wallet,pair), day = date of first buy.

## Q1 — Is 07-03 winnable? YES on marks, BARELY on matched cash. (regime delta: extraction collapsed)

| day (buy≥$20 eps) | eps | base net>0 | med net/buy | multi-humans | net-winners | REALIZED-winners* | best matched realized |
|---|---|---|---|---|---|---|---|
| 07-01 | 3,098 | 37.3% | −3.4% | 61 | 12 | 5 | **+$74.8** (1eveYYxZ, 11 pairs) |
| 07-02 | 8,384 | 27.9% | −17.4% | 217 | 26 | 9 | **+$959.9** (CzYQ2kFn — one manlet campaign +$1,167 matched, cap=0) |
| 07-03 | 13,163 | 28.4% | −18.8% | 311 | 61 | 5 | **+$26.2** (2QZiYXDX, 4/4 pairs green, pure scratch-grinder) |

\* realized>0 AND ≥2 realized-positive pairs, human, ≥3 pairs.

- 07-03 produced the MOST net-winners (61 of 311 multi-humans; 45.3% of multi-humans net>0, best
  day of the three) but the FEWEST cash extractors: only **2.9%** of multi-humans have matched
  realized >0 (07-01: 8.2%, 07-02: 6.5%), and the best matched-realized wallet made **+$26** vs
  +$960 the day before. 07-03 "winning" = open bags marked at last price + small scratches.
- Caveat both ways: 07-03 episodes had overnight runway (tape to 04:26) but late-day buys are
  still young; some realized suppression is mechanical (tape starts 13:30 → pre-window caps).
  Direction is still stark: the tape got harder to pull cash out of, not easier.

## Q2 — Delta verdicts on yesterday's 4 headline findings

### (1) Winners sit through −7/−12 wicks before selling → **HOLDS, STRENGTHENS** ✅ (wickride premise SAFE)
dd between first buy and first sell (winner episodes): 07-01 med −6.4 / p25 −9.4 (n=46);
07-02 med −8.9 / p25 −18.6 (n=130); **07-03 med −11.0 / p25 −20.9 (n=269)**.
The tolerated wick DEEPENED on 07-03. A −4 velocity bail is even more inverted than yesterday;
even the −12 MAE floor now sits ON the winner median wick. Keep `wickride_ab` armed exactly as is
(if anything the data argues the −18 floor arm from the exit decode, on gated entries only).

### (2) Winners' pond = 6–24h tokens, 14–22 UTC → **BAND HOLDS; HOURS NEUTRAL; >72h ROTATED IN** ⚠️ (adolescent premise intact)
- 6–24h band on 07-03: winner n=94, ret **+17.4%** on buy USD vs base +6.5% (n=973) — still the
  most consistent winner band across all 3 days. `adolescent_absorb` premise INTACT.
- NEW: **>72h old tokens were the biggest winner allocation on 07-03** (n=131 eps, +16.6%) and the
  crowd was green there too (base +18.4%) — old-runner revival day. <2h/2–6h were winner-negative.
  24–72h flat for winners (+0.6%) while base bled −12.7%.
- Hours: winner buys 67% in 14–22 / 21.8% in 23–01 — but base is identical (67% / 24.2%) and the
  tape only covers 13:30→04:26, so within-coverage winners NO LONGER over-concentrate in 14–22.
  On 07-01 (24h tape) winners avoided 23–01 (4.7% vs base 8.9%); on 07-03 they participated at
  base rate. The prime-hours edge reads diluted on this tape, not broken — no hard flag, but stop
  treating 23–01 as winner-empty.

### (3) Scratch machines (median exit ≈ 0 vs VWAP, losses cut −3%@15min) → **SHAPE HOLDS, LOSS-CUT DISCIPLINE DEGRADED** ⚠️
- Sell vs entry VWAP, 07-03: median **+1.6%** (n=806 covered sells), USD-weighted 33% of exit
  dollars in −12..0 (highest of the 3 days) — still scratch-machine shape. Closed-episode WR 64%
  (n=196), median closed realized +2.1%.
- BUT loss cuts on 07-03: median **−6.8% at 110 min** (n=70; p25 −12.8) vs yesterday's −3%@15min
  (and 07-02's −3.7%). Winners bled ~2x deeper, ~4-7x longer before scratching. The "cut small and
  fast" leg of the exit decode is the part that did NOT survive the day; the "median exit ≈
  breakeven scratch" leg did.

### (4) Loser signature medbuy $8–13 vs $26 winners → **REVERSES at the flush level; toxic band is <$8** ❌ (do NOT enforce a $8–13 block)
See Q3 below.

## Q3 — Medbuy band vs realized bounce/death on flush events (−25% vs 60m high, trough +30m; bounce = ≥+15% off trough within 60m, death = <+5%; medbuy = median tape buy ≥$1 in [−30m,+5m] around trigger)

**POOLED 07-01→03, n=245 flush events** (per-day n thin in low bands — pooled is the number):

| medbuy band | n | bounce% | death% | TP1(+6 from trigger) reach |
|---|---|---|---|---|
| <$8 | 29 | **66%** | 10% | **59%** |
| $8–13 | 43 | **84%** | 5% | 77% |
| $13–26 | 82 | 83% | 4% | 77% |
| ≥$26 | 91 | 82% | 8% | 78% |

07-03 alone (n=112): $8–13 = 95% bounce / 0% death (n=19); <$8 = 64% / 18% (n=11, thin).

- **The $8–13 band bounces like the big-buyer bands. The cliff is at <$8** — and even there it's
  66% bounce / 59% TP1-reach, i.e. a de-size candidate, not a block. The full-thesis gate should
  NOT enforce a $8–13 (or ≤$13) block on this evidence; if the buyer-size axis is used at all,
  gate/de-size at **medbuy <$8** and keep it shadow until n grows (n=29 pooled).
- Our 07-03 structural losses re-checked against tape: **Martolexx** flush medbuy $8.19 → bounced
  +24.2%, TP1 reachable (our loss = exit mechanics, not a dead flush); **RUSH** (07-02 flushes)
  medbuy ~$10 → +15.5/+55.7, TP1 reachable both times; **BINDY** medbuy $5.90 → TP1 NOT reached
  (the one true <$8 signature loss). USA250 not in tape universe (runner-set tapes don't cover it).
- Selection caveat: flush events require ≥3 buys ≥$1 pre-flush and the tape universe = runners
  (already-liquid tokens). Silent zero-bid deaths can't produce a medbuy reading here; the
  yesterday $8–13 claim came from token-level day medians on OUR loss set (n≈5) — this event-level
  test with n=245 supersedes it.

## Q4 — What winners did NEW on 07-03 (rotation check)

1. **Pack hunting intensified**: winner buys with ≥2 co-winner buys same pair ±15m:
   26% (07-01) → 41% (07-02) → **56%** (07-03); ≥3 co-winners 43%. (Metric partly circular —
   winners share tokens — but same construction each day, so the TREND is real.) Demand-wave
   convergence is becoming the entry signal.
2. **Old-token revival trade**: >72h band went from minor (n≈26-31 eps) to the largest winner
   allocation (131/359 eps, 36%, +16.6%) — and base was green there too. 07-03's money was in
   revived old runners + 6–24h adolescents, not fresh launches (<6h winner-negative).
3. **Slower, deeper campaigns**: n_buys/episode mean 3.0 (07-01: 1.6), rebuy-after-sell 56%
   (07-01: 41%), hold-to-first-sell p75 108m (07-02: 57m), tolerated wick med −11. They are
   DCA-ing capitulations harder and waiting longer for the bounce.
4. **Not banking**: matched-realized extraction collapsed (Q1). The 07-03 winner cohort is
   mark-dependent; if the 07-04 tape fades their bags this cohort's "win" unwinds — treat 07-03
   behavioral reads as one notch softer than 07-01/02's.

## Live A/B calibration takeaways
- `badday_flush_wickride_ab` (velocity-bail off): premise STRENGTHENED — keep running.
- `badday_adolescent_absorb` (6–24h, prime hours): band premise INTACT (+17.4 vs +6.5 base,
  n=94); hours gate neutral-on-tape — don't widen yet, but log 23–01 shadow fills.
- Full-thesis buyer-size axis: do NOT enforce ≤$13; candidate shadow = de-size at medbuy <$8.
- Loss-cut note for our exits: winners' 07-03 loss cuts at −6.8 med corroborate the wideexit
  floor direction (−12 → −18 arm) but ONLY behind the rug-gate stack (their deep-cut day was
  also their worst cash-extraction day).

## Honesty ledger
- n stated everywhere; thin flags: <$8 flush band n=29 pooled (n=11 on 07-03), 07-01 winner eps
  n=67, per-day $8–13 flush n=6-19 (pooled n=43 is the citable number), realized-winner counts
  are single digits per day.
- 07-03 hour-of-day claims are within-coverage (13:30→04:26) and only valid vs same-day base;
  cross-day hour comparisons are coverage-confounded and were not used for verdicts.
- 07-03 realized suppression is partly mechanical (tape starts 13:30 → pre-window caps; late-day
  buys young at window end). Mark-to-last positions labeled unrealized throughout.
- Bars 105/107 pairs (2 pairs GT returns nothing); barless episodes EXCLUDED (no cash-flow
  fallback — trial run showed the fallback fabricates +$1.8k "realized" wallets; fixed via no_px).
- Winner-set overlap across days is low (07-02∩07-03 = 9 of 26/61) — day-scoped winner sets are
  mostly different people; behavior deltas are cohort deltas, not "the same 14 wallets changed".
- Recorder: `_recorder_0704.log` restarted 12:13 UTC 07-04; keep it running for tomorrow's decode.
