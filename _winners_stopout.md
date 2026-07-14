# Stop-Out / Downside-Discipline: 10 Winners vs Our Dip-Buy Fleet (2026-06-26)

Risk-side companion to the hold-time study. Skeptical + quantitative. Data is LOCAL only.

## TL;DR
- **Their realized loser-exit band is -13% to -49% (median-of-medians ~-21%). Ours is -7.1% median, with 80% of losers cut at >=-10%.** We cut ~3x tighter than the typical winner.
- **No multi-hour-holding winner uses anything like a -7% price stop.** The only -7/-8% cutter (DaxfeJ) achieves it by TIME (~6 min), not price — and still eats occasional -96% rug gaps.
- **If we extend holds to chase the tail, a -6/-7% price floor is self-contradictory** — it would cut every position before it can run. Their data points the held-position floor at roughly **-20% to -25%**.
- Trip data is entry->exit only — it CANNOT show intra-trip max adverse excursion, so I cannot directly prove their big winners dipped below -7% before running. I infer it (below) but flag it as inference, not measurement.

---

## 1. THEIR LOSS DISTRIBUTION

Two data layers:
- **Per-wallet loss medians (full sample, from the dataset table — authoritative):** -7.9, -13.0, -13.0, -13.6, -16.1, -25.6, -34.3, -42.6, -48.1, -48.9. Median-of-medians **-20.9%**, range **-7.9% to -48.9%**.
- **Pooled visible trips (last-12 per wallet, n=106 trips, 38 negative — a SAMPLE, not full):** loss median **-17.8%**, p25 -36.9%, p75 -7.2%, worst **-96.1%**. Deep losses: **34% of losses <= -30%, 16% <= -50%.**

Their losses run **dispersed and deep**, not clustered at a single tight stop. They do NOT cut at a consistent shallow level (except the time-boxer).

### Per-wallet archetype classification
| wallet | full-sample loss med | visible loser holds | archetype |
|---|---|---|---|
| DU25Xy | -48.9% | days (1354-5186 min) | **lets-losers-run / deep-or-no-stop** (days-holder, monster tail) |
| DznHqB | -42.6% | 2-2782 min | **lets-run / deep-stop** (huge size 24 SOL, few names) |
| 7d54Pt | -48.1% | ~31 min | **deep-stop scalper** (n=1 visible loss; lotto fast-flips) |
| jStURX | -34.3% | 16-2367 min | **lets-run / deep-stop** (eats -90.9%, catches the +9.8M% lotto) |
| 2tYcX | -25.6% | dispersed | **discretionary / mid-deep** |
| C3zP | -16.1% | hours (216-627 min) | **disciplined cutter ~-16%** (price/discretion) |
| ArWird | -13.6% | 15-395 min | **disciplined cutter ~-14%** |
| B1zhrW | -13.0% | 8-230 min | **disciplined cutter ~-13%** (tightest multi-hour holder) |
| Zsp75 | -13.0% | 30-415 min | **disciplined cutter ~-13/-16%** |
| DaxfeJ | -7.9% | **~2-6 min** | **TIME-BOXER** (cuts by clock, not price; shallow median but eats -96% rug) |

Two real families plus the time-boxer:
1. **Disciplined cutters (-13 to -16%), hold 1-7h** — C3zP, B1zhrW, Zsp75, ArWird. Modest-tail (win medians +12 to +86%).
2. **Lets-run / deep-or-no-stop (-34 to -49%), hold hours-to-days** — DU25Xy, jStURX, DznHqB, 7d54Pt. These are the ones that catch the monster tail (+178k%, +9.8M%, +162k%). They pay for it with -50% losers.
3. **Time-boxer (DaxfeJ)** — exits ~6 min regardless of price; shallow -8% median, but the clock can't save a -96% rug gap-through at hold=0.

**Nobody who holds for hours cuts at single-digit %.** The only single-digit loss median (Dax -7.9%) comes from cutting on TIME in minutes, not from a tight price stop on a held position.

---

## 2. OUR STOP BEHAVIOR

Closed legs n=2210; losers n=1558 (70%).
- **Loser pnl: median -7.1%, p25 -9.4%, p75 -4.8%, worst -99.9%.**
- **80% of losers cut shallow (>=-10%).** Deep losses are rare: only 2% <= -30%, 1% <= -50% (the -50/-99% tail = rugs/gap-throughs, not stop behavior).
- **Loser hold median 3.7 min** (p25 1.1, p75 11.4). We cut fast.

Exit mechanics (by `kind`, median pnl / median hold / median MAE):
| kind | n | med pnl | med hold (m) | med MAE | note |
|---|---|---|---|---|---|
| IN_FLIGHT_FLOOR | 780 | -5.2% | 1.4 | -5.1% | the workhorse stop; fires fast+shallow, only 11% had gone green |
| NEVER_RUNNER | 392 | -8.4% | 5.4 | -7.4% | never-green eviction |
| GIVEBACK_FLOOR | 125 | -8.2% | 24.2 | -6.9% | gave back gains (100% had peaked) |
| HARD_STOP | 72 | -19.1% | 18.4 | -15.1% | the -25% backstop (mean -25.8) |
| FAST_BAIL | 44 | -17.4% | 5.3 | -16.6% | |
| TIME_STOP | 108 | -3.5% | 6.6 | -3.4% | our own time-box |

We already own a -25% HARD_STOP, but the **IN_FLIGHT_FLOOR at ~-5 to -6% is what actually governs downside** (28% of all exits). That floor is the lever, not the -25% backstop.

---

## 3. THE KEY RISK QUESTION — "if we hold longer, where do we stop?"

### Can their trips show whether big winners dipped below -7/-15/-25 before running?
**No — explicitly.** Trip records are entry-timestamp -> exit-timestamp + a single net return. There is **no intra-trip price path / max adverse excursion** in the decode. I cannot directly count how many of their winners traversed -7% before recovering.

### What I CAN establish
**Inference from their realized loser exits:** every multi-hour holder realizes losers at -13% to -49%. A trader whose stop fired at -7% could not produce a -16%, -34%, or -49% loss median — so their effective price-tolerance on held positions is **far below -7%, in the -13% to -49% range.** Given memecoin 24h volatility (our own entries: median ~116%/24h) and multi-hour holds, it is near-certain their eventual winners crossed -7% (and the deep-stop crowd's winners crossed -25%) intra-trip before running. Strong inference, not measurement.

**Evidence from OUR data is CENSORED and cannot answer it:** our winners' MAE tops out at **-12% worst; only 1% (6 of 652) ever dipped <= -7%; 0% dipped <= -15%.** This looks like "recovery never needs depth" — but it is pure survivorship. We sell everything at -6%, so a trade that dipped to -20% and recovered is *impossible to observe* in our winners; it was logged as a -6% IN_FLIGHT_FLOOR loser. **Our floor blinds us to exactly the question we are asking.** Their wallets are the only window, and the window says holding requires eating depth.

### The trade-off, quantified
- There is **no tight stop that simultaneously (a) lets you hold hours and (b) bounds losses to single digits.** The disciplined-cutter floor is **~-13 to -16%**; the tail-catcher floor is **-34 to -49% (or none).** The only single-digit discipline (Dax) is a TIME box, not a price stop.
- **If we kept our -7% floor and extended holds, we would stop out essentially every position before it could run** — converting the held cohort into a -7% loss machine. The -7% floor and "hold for the tail" are mutually exclusive.
- To preserve the upside tail, the held-position floor must move to roughly **-20% to -25%** — the median-of-medians of their realized loser exits, and the natural seam between the disciplined cutters (-13/-16) and the deep-stop crowd (-34/-49).

### Concrete shadow-first testable rule
Strategy is fat-tail (no per-token caps, no downsizing — those are off the table by mandate). The lever is **stop level + hold extension on a shadow cohort:**

1. **Shadow a -22% held-position floor (band -20% to -25%) REPLACING the -6% IN_FLIGHT_FLOOR**, paired with extended hold/time-box, on a sampled cohort. For every trade the -6% floor currently fires, log the forward-candle realized return if it had instead been held to a -22% floor (or time-box) — net of haircut. Decision metric: does the recovered upside tail outweigh the deeper losers vs the -6% baseline? Enforce only if the held cohort's mean (not median — this is fat-tail) beats the -6% baseline at n>=30.
2. **Keep / lean on the TIME-box alternative (Dax model):** cut by CLOCK (~6-12 min) rather than price, which keeps a shallow loss median without a deep price stop — but it REQUIRES a rug gate, because the clock cannot save a -96% gap-through (we have RUG_BUNDLE; gate it on).
3. **Shadow-first is mandatory:** exit-paper overstates realized vs live because deep stops gap THROUGH the level on-chain (cf. badday-gap audit; HERALD live bought 31% off the stale low). The measured live giveback at a -22% floor will be worse than paper — judge on live/forward-candle numbers, never paper fills.

---

## 4. ARTIFACT CHECK

**Extreme WINS are decode artifacts in magnitude (sign plausible):** +9,834,860.9% (jStURX), +436,989.4% (jStURX), +178,097% / +73,314% (DU25), +162,261% / +14,089% (ArWird), +47,544% / +18,451% (7d54Pt). These are computed from entry/exit price *ratios*; on tokens that ran enormous multiples (or had tiny dust entries / feed-unit shifts) the ratio is dominated by precision/feed noise. Treat the *numbers* as unreliable. The *direction* (they held to a large up-move) is plausible and consistent with their multi-hour/days holds — but do NOT bank these into any expectation; they are lotto outliers, not a repeatable median.

**Extreme LOSSES at near-zero hold are rugs, not stops:** -96.1% at hold=0min (DaxfeJ CHBPw8NYTR), -90.9% (jStURX, 37 min), -58.5% at 2 min (DznHqB), -66.3% (DU25). The 0-2 min, -58 to -96% cluster is LP-pull / gap-through that **no price stop can catch** — same class as our own -99.9% worst. This means the deep tail of *both* loss distributions overstates what any stop could have prevented; a -22% floor would have caught the *discretionary* -16/-34% losers, NOT these rugs. The rug defense is the entry gate (RUG_BUNDLE), not the stop.

**Net:** strip the lotto wins and rug losses and the real, stop-relevant signal is intact: held winners realize losers at -13% to -49%, median-of-medians ~-21%; we realize at -7%. Holding longer requires moving our floor to ~-20/-25%, shadow-tested forward, or switching to a time-box with a rug gate.
