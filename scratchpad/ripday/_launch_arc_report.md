# LAUNCH-ARC MINE — report (run 2026-07-03 04:52-05:06 UTC)

Hypothesis under test (AxiS): "nearly all newer tokens have a massive run-up
then dump within the first few hours, then a steady recovery phase. It is
universal."

**Verdict in one line: the pump->dump half is common (~42% of launches with any
life), but the "steady recovery" third act is the RARE outcome (1/10 resolved
arcs), not the universal one — the dominant post-dump path is rug/corpse. The
arc IS near-universal only inside the pre-selected traction world our bot
already lives in (runner-biased set: 3/5 recovered). What separates the two at
the trough is ABSORPTION (tape keeps printing), not dip depth.**

## Method

- **Unbiased birth cohort**: 127 Solana pools captured AT BIRTH from
  GeckoTerminal new_pools (2026-07-02 18:28-18:30 UTC, prime-time window),
  re-examined ~10.5h later. One GT minute-bar call (limit 1000 = 16.7h reach)
  covers each pool's entire life. No survivorship: every launch in the window
  is in the denominator.
- **Definitions (spec)**: pump = early peak (first 6h) >= 2x first-bar open;
  dump = close <= peak −40% within 4h of peak; recovery = close >= running
  trough +20% on a bar with >= $100 volume; **rugged** = trough <= −95% from
  peak (dust bounces are unrealizable and are NOT counted as recovery — without
  this guard 7 fake "recoverers" appear); corpse needs >= 3h post-trough tape.
- **Tradeable floor**: peak mcap >= $100k (DS supply x peak px) AND best-seen
  liq >= $25k (max of birth reserve, current DS liq; liq history unavailable →
  traction slightly undercounted).
- **Tournament**: causal entries, fleet exit stack TP1 +6 (half) / TP2 +12 /
  stop −12 / 45min timestop, same-bar TP-vs-stop resolved pessimistically
  (stop wins), gross of fees (~1-2pp haircut at $5 size).
- Supplementary: local 172-pair recorded set (**runner-biased** — we taped them
  because they moved; 14 scoreable with launch-anchored coverage).
- Driver: `scratchpad/ripday/launch_arc.py` (caches additive; daily rerun is
  ~5 min and ages new cohorts into scoreable range). Raw outputs:
  `_launch_arc_run3.txt`, `_launch_arc_results.json`, `pooled_disc.py`.

## Q1 — Arc prevalence (base rates)

**Full unbiased birth cohort, n=127:**

| bucket | n | share |
|---|---|---|
| never any traction (peak mcap < 100k or unknown) | 117 | **92%** |
| peak mcap >= 100k (any liq) | 10 | 8% |
| full tradeable floor (mcap >= 100k AND liq >= 25k) | 7 | 6% |
| sensitivity: peak mcap >= 50k | 14 | 11% |

Liquidity collapse is near-total by hour ~10: only 1 of 127 pools still holds
> $25k liq at re-examination.

**Any-life cohort (bar-fetched, n=24 scored)** — closest thing to "all newer
tokens with a pulse":

| class | n | share |
|---|---|---|
| never_pumped (<2x) | 9 | 38% |
| up_only (never −40% in 4h) | 4 | 17% |
| slow_fade (−40% but slower than 4h) | 1 | 4% |
| pump->dump arc, **rugged** (trough <= −95%) | 7 | 29% |
| pump->dump arc, corpse (no +20% bounce) | 2 | 8% |
| pump->dump arc, **recovered** (+20% bounce, real vol) | 1 | 4% |

- Pump->dump arc rate: 10/24 = **42%**. Recovery rate among resolved arcs:
  **1/10 = 10%**. [n<15 in several cells — thin]
- Traction cohort (mcap >= 100k, n=7 — **thin**): arc 3/7 = 43%; recoveries
  **0/3**; 2 of 3 arcs were full rugs. Full floor (n=4 — very thin): same 0
  recoveries.
- Median timings (arcs): launch->peak **4-15 min**, peak->trough **5-8 min**
  (rug/flush is nearly instantaneous), trough->+20% bounce **3-28 min** when it
  happens. The whole arc typically resolves inside the first hour, not "a few
  hours".
- **Runner-biased local set (n=14 scored)**: never_pumped 64%, arc 5/14 = 36%,
  recovery among resolved **3/5 = 60%**. I.e., in the world of tokens that
  already attracted our tapes/watchlist, recovery IS the majority outcome —
  which is why the hypothesis feels universal from inside the fleet's sample.

**Caveats**: single 2-minute birth window (one time-of-day); 10.5h observation
horizon (a recovery later than that is uncounted — but the hypothesis says
"first few hours"); pool-level view can miss a pump.fun bonding-curve run-up
that happened before the pool existed (our bot trades pools, so pool-visible
dynamics are the tradeable ones); all arc-class cells n<15 = thin.

## Q2 — Trough discriminator (recoverer vs corpse+rugged)

Pooled GT any-life + local arcs: **4 recoverers vs 11 non-recoverers** (thin —
treat as directional, but separation is total and matches three prior
missions: winner-selection "flush met by buyer size", wallet-mine 60m
absorption, fleet buyers>=10/nf15>=0 gates).

| feature (median) | recoverer | non-recoverer |
|---|---|---|
| drawdown at trough | −58% | −100% |
| minutes since peak | 3.5 | 202 |
| vol in trough+15m | $207k | **$0.4** |
| vol ratio (trough15/peak15) | 0.89 | 0.00 |
| green-volume share, trough+15m | 0.43 | 0.00 |
| minute-bars printed, trough+15m | 15/15 | **1/15** |

Threshold sweep (prec = recoverer precision; 4 rec / 11 non base):

| rule | precision | recall | non-recov passed |
|---|---|---|---|
| bars printed in trough 15m >= 8 | **100% (4/4)** | 100% | 0/11 |
| trough-15m vol >= $20k | 100% (4/4) | 100% | 0/11 |
| green share >= 0.25 | 100% (3/3) | 75% | 0/11 |
| vol_ratio >= 0.05 | 67% (4/6) | 100% | 2/11 |
| minutes-since-peak <= 30 | 44% (4/9) | 100% | 5/11 |
| **dd <= −85%** | **0% (0/9)** | 0% | 9/11 |

Readings:
1. **The discriminator is absorption, not depth.** Recoverers keep a live tape
   through the trough (every minute prints, real $ volume, buyers present).
   Corpses/rugs go silent: 1 bar per 15 min, ~$0 volume.
2. **Depth is anti-predictive**: every trough deeper than −85% from peak (n=9)
   was a non-recoverer. A deeper "discount" on a young token is a rug print,
   not an opportunity.
3. Fast trough (<=30 min from peak) is necessary but not sufficient — instant
   rugs trough fast too; it only works combined with the live-tape condition.

## Q3 — Entry-timing tournament (fleet exit stack, gross)

All cells n<15 = **thin**; pre-rug-guard wider run (run1, includes dust arcs
that a live bot could actually attempt to buy) shown for the knife row too.

| rule | cohort | n | mean/token | win% | stop% | worst |
|---|---|---|---|---|---|---|
| knife pc_h1<=−30 | GT arcs | 2 | −12.0% | 0% | 100% | −12.0% |
| knife pc_h1<=−30 | local arcs | 3 | −9.0% | 0% | 100% | −12.0% |
| knife pc_h1<=−30 | run1 wide (GT 9 / local 5) | 14 | −6.9% | 14% | 64% | −12.0% |
| higher-low 10m confirm | GT arcs | 2 | −1.6% | 0% | 50% | −3.0% |
| higher-low 10m confirm | local arcs | 3 | −0.5% | 33% | 0% | −7.6% |
| trough-held-30m | GT arcs | 2 | −0.2% | 0% | 0% | −0.3% |
| trough-held-30m | local arcs | 3 | −3.9% | 0% | 0% | −9.4% |

- **Unconditioned knife-catching a young launch is a stop-out machine**
  (−7 to −12%/token, 64-100% stopped). Ranking: trough-held-30m ~ higher-low
  >> knife; the patience rules mostly lose small instead of winning.
- **No rule is positive unconditioned.** This is the key reconciliation with
  the fleet: badday_young_absorb runs a knife entry and is GREEN (+24.6/token
  live-paper) — because its demand gates (liq>=25k AT entry, buyers>=10,
  nf15>=0, mcap 100k-1M) are exactly the absorption filter Q2 says separates
  recoverers from rugs. **The gates are the edge; the arc is not.**

## Q4 — Age band

- Arc geometry is fast: median peak at 4-15 min, trough 5-8 min later. Nearly
  every knife/higher-low signal fires at token age **<2h**; the 2-8h band
  produced only n=1-3 entries per rule (all ~flat-to-negative, 0-33% win,
  thin).
- Recoveries that happen resolve within ~30 min of the trough — inside the <2h
  life, matching the current young band and the 45m timestop.
- No observed 2-8h recovery edge in this cohort: by hour 2-8 the typical
  launch is already liq-drained tape (92% no-traction, liq collapse near-total
  by hour 10). Not "dead" as a concept — undetermined at current n; the map
  says the next data need is more aged cohorts, not a build.

## SHIP / NO-SHIP

**(i) badday_young_absorb tweaks:**
- **KEEP the current entry stack unchanged** — the mine vindicates it. Knife
  entry is only survivable because of the demand gates; do not loosen liq /
  buyers / nf15 to "catch more of the arc" — the arc mostly ends in rugs.
- **SHIP (shadow-first): trough-absorption gate** — require a live tape into
  entry, e.g. >= 8 of the last 15 minute-bars printed trades (or trough-window
  vol floor). 100% precision / 100% recall / 0 non-recoverers passed in-sample
  (n=15, thin); corroborates buyers>=10 + nf15>=0 and the wallet-mine 60m
  absorption pilot. Shadow-log realized outcomes first (forward-candle shadow
  scorer overstates — use realized).
- **SHIP (shadow-first): rug-floor depth block** — block young entries priced
  <= −85% from their 6h peak: 0/9 such troughs recovered; complements the new
  holder-concentration guard with a price-path rug signature.
- **No exit-stack change**: TP1+6/TP2+12/45m fits the measured arc rhythm
  (bounce completes in 3-28 min).

**(ii) badday_young_recovery (age 2-8h): NO-SHIP now.** The 2-8h phase shows
almost no qualifying entries and no wins in this cohort — but the cells are
n<=3, so this is "not yet measurable", not "refuted". Next probe, already
armed: the new_pools cache is additive (267 pools cached; +~140 per fetch) and
`launch_arc.py` reruns in ~5 min — each daily rerun ages a fresh unbiased
cohort into scoreable range. Re-decide when the 2-8h cells reach n>=15.

## Rerun instructions
`python scratchpad/ripday/launch_arc.py` (full, ~6-8 min: refreshes new_pools,
DS state >2h stale, fetches only uncached bars) or `--no-net` for cache-only
re-analysis. Pooled Q2: `python scratchpad/ripday/pooled_disc.py`.
