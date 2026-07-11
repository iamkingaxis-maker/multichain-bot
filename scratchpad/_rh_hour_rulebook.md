# RH Chain — Hour Rulebook v0 (PROVISIONAL, 2026-07-11)

STATUS: v0-provisional. Tape = ~21 hours across 2026-07-10/11 in two recorder sessions
(03-10 UTC, 15-00 UTC). Our paper trades span ONE evening (07-10 16:26 -> 07-11 00:13 UTC).
Every cell below n=20 distinct trips/day-hours = accrual-stage. Do not enforce anything from this
file; shadow only.

## 1. Our realized paper P&L by UTC buy-hour (72 round trips, scrub rule dropped 0)
Scrub note: no ret>0 & hold<10s fills existed — the RH paper path shows no fast-green illusion.

| UTC hr | trips | pnl$ | wins | note |
|---|---|---|---|---|
| 16 | 6 | -31.56 | 1 | -26.70 of this = Halp + TREAT rugs; ex-rug ~ -4.9 over 4 |
| 18 | 2 | -2.35 | 1 | |
| 19 | 11 | -0.75 | 6 | |
| 20 | 17 | -13.74 | 9 | CASHCOW -7.29 and Ape -5.83 drag an otherwise green hour |
| 21 | 10 | +16.52 | 8 | only clearly green hour |
| 22 | 14 | -15.44 | 4 | |
| 23 | 10 | -2.93 | 5 | |
| 00 | 2 | -11.32 | 0 | MONSIEUR -7.03, QUANT -4.29 |

Buckets: 16-18 = -33.91 (n=8, rug-dominated) | 19-21 = +2.03 (n=38, +0.05/trip) |
22-00 = -29.69 (n=26, -1.14/trip). Day totals (scrubbed): 07-10 -$50.25, 07-11 (partial) -$11.32.

## 2. Overnight bleed: hour or token-mix? -> HOUR (or pool-age), not token-mix
Same-token control — 4 tokens traded in BOTH buckets (MONSIEUR, RANGER, KITTY, THROBBIN, 48 trips):
- 19-21 UTC: +$16.22 over 27 trips (+0.60/trip)
- 22-00 UTC: -$14.68 over 21 trips (-0.70/trip)

The SAME tokens flipped from +0.60 to -0.70 per trip after 22:00 UTC. Not token selection.
Two confounds we cannot split at n=1 day: (a) clock hour, (b) pool age — by 22-00 these pools were
simply older/later in their arc. Tape volume on our pools did NOT die late (22h $75k, 23h $97k, more
than 19-21), and market-wide buy-share actually ROSE to 79-91% — late US-evening flow is one-sided
retail buying with poor follow-through for our scalps. Liveness is not the problem; follow-through is.

## 3. Tape-wide market rhythm (all pools, 47.5k trades)
| UTC hr | trades | vol$ | buy% | new pools (age<=1h) | active pools | top1 vol share |
|---|---|---|---|---|---|---|
| 00 | 1,210 | 127k | 90.7 | 17 | 67 | 46% |
| 03 | 3,022 | 396k | 58.2 | 21 | 12 | 43% |
| 08 | 5,571 | 1.49M | 54.6 | 38 | 30 | 33% |
| 09 | 16,672 | 3.77M | 52.7 | 95 | 46 | 21% |
| 10 | 1,191 | 368k | 61.9 | 63 | 5 | 46% |
| 15 | 108 | 7k | 66.3 | 31 | 16 | 62% |
| 16 | 512 | 43k | 72.7 | 94 | 36 | 19% |
| 17 | 2,066 | 256k | 64.4 | 47 | 77 | 31% |
| 18 | 2,266 | 204k | 69.5 | 70 | 99 | 13% |
| 19 | 2,433 | 163k | 67.3 | 103 | 78 | 13% |
| 20 | 4,454 | 182k | 59.9 | 64 | 78 | 29% |
| 21 | 1,758 | 142k | 72.3 | 88 | 79 | 14% |
| 22 | 2,816 | 171k | 79.2 | 62 | 95 | 13% |
| 23 | 3,469 | 221k | 84.6 | 56 | 100 | 16% |
(01-02, 04-07, 11-14: ZERO tape coverage — recorder off, unknown not dead.)

Two distinct markets on this chain:
- **08-10 UTC "whale/flagship session"** (3-5am CT): monster volume ($1.5-3.8M/hr) but concentrated
  in a handful of flagship pools (ROBINHOOD $798k, ilysm, ROBINHULK, RWA...), balanced buy/sell
  (~53-55% = two-sided battle). NOT US retail; reads as launch-wave / EU-Asia / bot session. Untested
  by our bot.
- **17-00 UTC "US retail evening"** (noon-7pm CT): moderate volume ($140-260k/hr) but BROAD
  (78-100 active pools), highest new-pool creation (47-103/hr), buy-share climbing 64% -> 91% into
  the evening. This is where micro-cap breadth lives — partially mirrors the Solana 13-22 UTC prime,
  shifted later.

## 4. Hour map v0 (UTC) — provisional labels
| block | label | evidence n | note |
|---|---|---|---|
| 19-21 | PRIME (candidate) | 38 trips, 1 day | our only green window; broad pools + peak new-pool rate |
| 22-00 | CAUTION (candidate bail-only) | 26 trips, 1 day | same-token decay -1.14/trip; do not open new entries, manage exits |
| 16-18 | NEUTRAL-noisy | 8 trips | rug losses dominate; ex-rug roughly flat-negative |
| 08-10 | SPECIAL/untested | tape only | different market (flagship concentration); needs 2+ recorded sessions before judgment; possibly its own lane |
| 03 | thin | tape only | 12 active pools, one token = 43% of volume |
| 01-02, 04-07, 11-15 | UNKNOWN | zero coverage | recorder off; label after 2+ covered days |

## 5. What firms this up (v0 -> v1)
- >=20 round trips per hour cell across >=4 distinct days (mission-standard), especially 19-21 vs 22-00.
- Tape coverage of 01-07 and 11-15 UTC at least twice each (per-session recorder runs, no 24/7).
- Disentangle hour vs pool-age: tag each trip with pool age at entry; re-run the same-token control
  controlling for age.
- ~1 week at current fill rate (~30-70 trips/day) reaches the bar.

## Top-3 actionable (shadow only, accrual-stage)
1. **Shadow gate: no NEW entries 22:00-01:00 UTC** (exits/bails still allowed). Basis: -1.14/trip
   n=26 + same-token flip. Log the counterfactual for a week before any enforce.
2. **Concentrate racer budget 19-22 UTC** (arm ~18:30, wind down 22:00) — align recorder + racer
   sessions to this window first since machine time is finite.
3. **Record the 08-10 UTC session twice before judging it** — it is a different market (concentrated
   flagship battles, 2-sided flow); evening rules must not be extrapolated onto it, and it may
   deserve its own lane spec (bigger pools, momentum style per the wallet decode).
