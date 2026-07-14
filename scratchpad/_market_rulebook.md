# THE MARKET RULEBOOK — synthesis of 3 lenses (time-of-day, SOL lead-lag, cross-coin structure), data 2026-06-23..07-01

## 1. TIME-OF-DAY STRUCTURE (the master table) + sleep-window verdict

Three independent event definitions were run: A = unselected recorder stream (pc_h1<=-20, 1019 dips/139 tok — closest to our real candidate population), B = 88-tok panel (-15%/60m, bounce +8%/60m), C = same panel (-20%/30m close, bounce +10%/30m). Panel lenses (B,C) are survivor-tilted; use their SEPARATIONS, A's LEVELS.

```
UTC     CT        Flush supply      Bounce evidence (A recorder / C panel / B panel)     Tape $/min   VERDICT
block                (/100tok-hr)
00-03   7-10pm    low  (65-72)      A: h00,h01 GOOD (dedup ~55-73%) | C: 00-04 70%,      thick 3-6.5k CONFLICT (A vs B/C) —
                                    h00 worst hr 47% | B: 23-02 weak 0.57                             reduced confidence, keep gates tight
03-06   10pm-1am  trough (62-66)    demand-PASS share worst of day (29%); demand-met     thick        KEEP SLEEP — market bounce is ok
                                    dips that exist bounce fine (A 57%, C 05-08 81%)                  but composition is demand-less bleeders
06-09   1-4am     trough            A: BEST demand-met block of the day (dedup 67%,      thick ~5k    SHADOW CANDIDATE — currently slept
                                    med fwd peak 27.8 vs 9-13 elsewhere; but 41/51                    through; one-day-dominant, 18 tok. Do
                                    events from 06-30) | C: 05-08 81% | B: weak                       NOT go live on this yet
09-13   4-8am     mid               A: h10 39%, h12 41% dedup — weak BOTH days, weak     THINNEST     BLOCK/DOWNSIZE — top new ship.
                                    EVEN demand-met (46%, med peak 9.0) | C: 09-12       1.5-2.1k     2-of-3 lenses + thin-tape mechanism.
                                    73% (panel-weak) | B: DISAGREES (10-16 strong,                    B conflict noted (coarse 10-16 block
                                    but block lumps in 13-16)                                         likely driven by 13-16)
13-18   8am-1pm   peak (86-95)      strong in ALL THREE: C 13-19 = 83%/+23.4 (robust     thick 4.5k   PRIME WINDOW — full size, current
                                    all 3 days) | A: h14, h18 top composite | B: strong               gates
18-22   1-5pm     peak (94.9 @h22)  A: h18 63.6/73.4% dedup, h21, h20 top; robust both   mixed        PRIME per the honest (recorder) lens —
                                    days | C: strong thru 19, 20-23 72% | B: mid,                     full gates; watch med_fwd30 drag late
                                    med_fwd30 -1.38
22-24   5-7pm     high supply       A: h23 near-worst composite | B/C: weak              thin ~1.5k   reduced confidence
```

**Sleep-window (UTC 03-08) verdict: do NOT widen, do NOT simply narrow — REALIGN.**
- Market data says the fleet's overnight losses were **composition, not regime**: only 29% of 03-06 dips pass a demand gate (worst of day), but the demand-met ones bounce fine (57-81%). The sleep window is justified by our P&L/composition, not by market bounceability — keep it.
- The genuinely weak MARKET zone is **UTC 09-13** (h10/h12 weak on both split days, weak even demand-met, thinnest tape of the day) and it is currently OUTSIDE the sleep window. Add it as a no-fire (or half-demand-bar) block — shadow first.
- **UTC 06-09** (inside current sleep tail) is the single richest demand-met block found (dedup 67%, median fwd peak 27.8%) but is one-day-dominant. Shadow-log only; no live change.
- Lens B's "widen to 23-09" is the minority report (survivor panel, coarse blocks) — rejected by 2-of-3, but it's why 00-02 and 23 stay "reduced confidence" rather than "fine".

## 2. THE SOL RELATIONSHIP, PLAINLY

- **Lead-lag: NULL at minute scale.** Cross-corr of SOL vs token 5m returns peaks at lag 0, median +0.025; |r|<=0.019 at every SOL lead +1..+10m; per-token peak lag median = 0. There is NO "SOL dropped, flushes incoming in X minutes" pre-arm signal. Coupling only faintly appears at 60m horizon (corr ~0.13, 66% positive).
- **Asymmetric beta: too weak to trade.** 60m pooled beta downside 3.88 vs upside 2.54 (downside-heavier, as suspected) but implied corr 0.04-0.07 and the sign flips by day (06-30 SOL-up med -6.32; 07-01 SOL-dn -7.80). No beta hedging, no beta-conditioned sizing.
- **The only real SOL effect = crash-drag on ACTIVE decline:** dips taken while sol_pc_h1<=-1 bounce 44.0% vs 51.7-53.2% otherwise (n=50/29 tok). After a SOL 15m drop <=-1%, token fwd15/30 mildly suppressed (med -1.32 vs -0.72; -2.10 vs -1.40) — BUT already-formed demand-met flushes after the drop bounce normally (0.678 vs 0.642, no penalty). Only 26 dedup drop events.
- **Regime buckets: flat.** Bounce by SOL h6 momentum spans 5pp total (0.64-0.69). Re-confirms the 06-04 audit: regime is not the lever.
- **Both shipped gates get market-side corroboration:** GREEN_DAY direction correct (SOL-red days bounce 55.3% vs 50.4% green — real capitulation vs pump-retrace); SOL_MACRO_GATE crash-only-loose correct (only active-crash drags, everything else is noise).

**PRE-ARM rule: none exists — do not build one.** **PRE-BLOCK rule (optional, shadow): after SOL 15m <=-1%, delay NEW arms 15-30m unless the candidate is an already-formed deep flush meeting demand (those bounce normally; never block them).**

## 3. CROSS-COIN FEATURES FOR THE SCANNER

Almost everything tested is a validated NEGATIVE — this saves build time:

| Candidate feature | Measured separation | Verdict |
|---|---|---|
| Concurrent-flush count (fleet-wide) | observed 4.76 vs time-shuffled 4.71+-0.18 (z=0.3) — pure hour-of-day proxy | DO NOT WIRE; use UTC hour directly |
| Market-wide flush-wave block | cluster flushes bounce 78% vs isolated 55% (n_iso=11, flips 06-30) | do not add a wave block; waves are buyable |
| Hi-liq leads lo-liq trigger | \|r\|<=0.043 at all lags -5..+5m | dead, do not build |
| Runner-ignition rotation de-risk | flushes elsewhere post 4.65 vs pre 4.49; fwd30 -1.00 vs -1.12 | null, do not build |
| Memecoin common factor / breadth index | pairwise 5m corr -0.02..+0.01, \|r\|>0.3 in 0-1% of pairs | no factor; breadth gate can't predict per-token flushes |
| **UTC hour bucket on every entry** | the ONLY surviving cross-coin structure (see table sec 1) | **WIRE IT: stamp utc_hour + block label into entry_meta — zero risk, enables all promotions below** |
| Slot-cap risk math | concurrent dips ~independent (corr ~0) | positions diversify for real; no correlation penalty needed on the 3-slot math |

## 4. RANKED SHIP LIST

**Enforce-grade (no new code risk — confirmations + validated negatives):**
1. KEEP SOL_MACRO_GATE_MODE=loose (crash-only) — corroborated by all 3 lenses.
2. KEEP GREEN_DAY gate as-is — market sign agrees (+4.9pp red-day bounce); no tightening.
3. HARD "do-not-build" list: SOL lead-lag pre-arm, concurrent-flush-count feature, leader-laggard trigger, ignition-rotation de-risk, SOL-beta sizing/hedge. Any backtest edge these show is an hour-of-day proxy.
4. Wire utc_hour/block stamping into entry_meta + recorder events (instrumentation; ships immediately).

**Shadow-grade (day-split passed, needs realized-P&L verify per predict-don't-lose-to-learn):**
1. **UTC 09-13 no-fire block** (or demand-bar 2x during it). Basis: A h10 39%/h12 41% dedup weak both days, weak even demand-met (46%, med peak 9.0), C agrees (09-12), thinnest tape ($1.5-2.1k/min = mechanism). One lens (B) disagrees — hence shadow, promotion bar: blocked-cohort realized <= pass-cohort on 5+ trading days, n>=30 distinct tokens.
2. **Demand-gated firing UTC 06-09** (inside current sleep). Basis: best demand-met block of the day (67% dedup, med peak 27.8) — but 80% of events from one day, 18 tokens. Shadow-log what WOULD have fired; do not wake the fleet until it replicates on 3+ days.
3. **SOL crash-onset arm-delay**: SOL 15m <=-1% -> hold NEW arms 15-30m, exempt formed demand-met flushes. n=26 events; needs ~3x the n.
4. **Prime-window treatment UTC 13-22** (loosen demand bar or +size). Direction robust everywhere, but sizing changes ride on realized fleet P&L, not market bounce rates.

**Observation-only (log, don't act):**
- 00-02 / 23 UTC weakness (lens conflict, thin), isolated-flush-is-worse (n=11), flush supply is round-the-clock (1.4x range — never chase a "flush hour"), 07-01 was a uniformly bad fade-day (regime contaminates all absolute levels).

## 5. STANDING DAILY CADENCE (re-run each of the 7 days)

1. ONE pull of `/api/universe-recorder?limit=5000` (gzip) + GT minute-bar top-ups for new tokens and the SOL series (~3s paced, single process). No /api/trades pulls.
2. Recompute the Section-1 table: per-UTC-hour dip share, bounce rate (raw + token-dedup), median fwd peak, demand-met split, tape $/min — always day-split, always distinct-token n.
3. Track four open questions to resolution: (a) does 06-09 demand-met golden block replicate? (b) does 09-13 weakness hold (and does lens-B's contradiction dissolve when 10-12 is split from 13-16)? (c) 00-02 conflict — recorder vs panel; (d) first WEEKEND coverage (currently zero — all conclusions are weekday-only).
4. Accrue SOL 15m-drop events toward n>=75 before any pre-block promotion.
5. Flag regime: tag each day pump/mixed/fade (06-29/30/07-01 pattern); any hour claim that only holds on one regime type gets demoted.

**Bottom line:** the market runs on hour-of-day and demand composition, not on SOL and not on cross-coin contagion. Our sleep window is defensible but misplaced by ~6 hours of the day's true dead zone: keep 03-08 for composition reasons, shadow-block 09-13, shadow-watch 06-09, and press hardest 13-22 UTC.