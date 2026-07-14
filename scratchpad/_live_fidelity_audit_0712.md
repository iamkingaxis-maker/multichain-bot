# LIVE-VS-PAPER FIDELITY + FRICTION AUDIT — 2026-07-12 (16:45-17:15 UTC)

Mission question: does the young-lane edge survive real fills?
Data: /api/live-swaps (375 recs, 67 since 07-09), /api/trades?full=1&limit=5000 (covers 07-08..07-12 16:30), /api/wallet-truth.
Method: 26 live round trips reconstructed by joining trades-feed live sells (bot_id + booked pnl + live_signature) to swap-log legs by tx signature, buys matched bot+token+time (all joins < 2.5s off). Per-trip wallet truth from `sol_before` balance deltas between consecutive buys — **reconciles EXACTLY with /api/wallet-truth** (C+D era sum −0.00116 SOL vs API delta −0.001157 SOL). Working files: `scratchpad/live_fidelity/` (audit.py/audit2.py/audit3.py, trips2.json, raw pulls).

## Eras
| era | window (UTC) | bots | size | trips |
|---|---|---|---|---|
| A pre-pause probe | 07-09 .. 07-11 02:45 | rt | $11-25 | 3 closed + 1 orphan |
| B resume probe | 07-11 18:52-21:28 | rt | $11-17.5 | 9 |
| C $100 | 07-11 23:58 .. 07-12 06:13 | absorb (+1 failed rt) | $100 | 7 (+2 failed buys) |
| D $22.5 (go-forward) | 07-12 13:00-16:30 | absorb/rt/vsnap | $15.75-22.5 | 7 |

## Q1 — FRICTION per round trip (entry fill_vs_mid + proceeds-weighted exit fill_vs_mid + priority fees)

| era | n | entry slip med/p90 | exit slip med/p90 | fees pp med | TOTAL friction med/p90 |
|---|---|---|---|---|---|
| A ($11-25) | 3 | 2.99 / 4.67 | 0.19 / 4.97 | 0.09 | **4.58 / 6.52** |
| B ($11-17.5) | 9 | 3.23 / 5.23 | 0.95 / 4.23 | 0.17 | **5.39 / 6.45** |
| C ($100) | 7 | 3.61 / 10.45 | 2.21 / 6.11 | 0.02 | **8.70 / 16.37** |
| **D ($22.5)** | **7** | **1.64 / 1.85** | **0.37 / 1.46** | **0.09** | **2.10 / 3.54** |

**$22.5-era answer: median round-trip friction = 2.1pp, p90 = 3.5pp.** Fees are noise (~0.1pp); friction is almost all fill-vs-mid drift, and post-prewarm it is mostly pure impact (Jupiter-quoted buy impact at $22.5 on ~$25-32k pools ≈ 2.4-2.5%, realized 1.6 — quotes overstate).
One structural tail stands: selling INTO a cascade (15:20 trip, one exit leg 22.8pp adverse vs decision-mid). That is timing cost during dumps, not size impact; the trip still netted only −2.0% on-chain because the decision mid was up. p90 covers it.

### Prewarm grade (fix shipped ~10:00 UTC 07-12): WORKED
Entry fill_vs_mid, 07-12 buys: pre 10:00 [2.27, 2.41, 2.89, 6.47, 10.45, 13.85] med 4.68 / p90 10.45 → post 10:00 [−0.99, −0.52, 1.41, 1.64, 1.65, 1.85, 2.16] med **1.64 / p90 1.85**. On comparable small sizes (eras A/B med 2.9-3.2 pre-fix) the fix cut the median ~1.3-1.6pp and **killed the tail entirely** (post-fix max 2.16 vs 14.66 pre). Timing drift is no longer the entry-cost driver; impact is.

## Q2 — EDGE VS FRICTION (the number)

Wallet-truth per trip (balance deltas), era D, n=7, 3.5h, **one distinct token (PumpfunLife)**:
+10.51, −8.05, −0.26, +14.56, −2.02, +0.17, −0.49 %

- **Sum: +0.0309 SOL = +$2.38. Mean +2.1pp/trip, median −0.26pp/trip — AFTER all friction.**
- Implied gross edge ≈ mean net (+2.1) + mean friction (~3.4) ≈ **+5pp gross**, of which friction eats ~2-3pp.
- Era C ($100, pre-prewarm): −0.0321 SOL = −$2.47 over 7 trips (mean −0.35pp/trip net) — friction (8.7pp med) ate an era whose gross was ~positive.
- Confidence: **LOW**. n=7, single token, single afternoon. This is a live-positive READ, not a proven rate. (The booked USD pnl line understates: booked D sum +$0.19 vs on-chain +$2.38 — booked USD uses inconsistent SOL prices per leg; per-trip drift ±1-2pp. Wallet truth is the honest line, as standing rule says.)

Paper-twin check (scrubbed per SCRUB RULE, ret>0 & hold<10s dropped): live did NOT underperform paper twins on the same token-window — era D gap live−paper med **+7.0pp** (n=6). But the twins are exit-config siblings (moonbag/adaptsize/rt_paper stop fast at −8% where live absorb/rt held the bounce), and paper twin fills carry their own artifacts both directions. At this n the paper twins are execution-artifact-dominated; the on-chain line above is the better estimator. Verdict: **no live<paper fidelity gap is visible at $22.5** — the old live=scrubbed-paper relationship holds or better.

### When does $100 become correct?
Measured: f($22.5) = 2.1pp med; f($100, pre-prewarm) = 8.7pp med. Post-prewarm $100 estimate = 8.7 − ~3pp timing ≈ **~5-6pp** (needs a re-probe to confirm; impact quotes at $100 on $25-33k pools ran 0.7-7.0%, med 2.4 — sub-linear vs the 4.4x size, as expected).
Dollar breakeven: 100·(E−f100) > 22.5·(E−2.1) → with f100=5.7: **E > ~6.7pp gross/trip** (f100=4.5 → E > 5.2pp). Current measured gross ≈ +5pp mean, median lower → **$22.5 is the correct size today; revisit $100 when the gross edge proves ≥ ~6pp median on n≥20 trips, and re-measure $100 friction post-prewarm first.**

## Q3 — RATE + WALLET TRUTH BY ERA

Closed live round trips/day: 07-10: 2 (+1 orphan), 07-11: 11, 07-12: **13 by 16:30 UTC** (7 of them in the $22.5 era's first 3.5h of prime tape, 3 bots).
Projection: at era-D cadence (~2/hr in the 13-22 UTC prime window) → **~10-18 trips/day; 20 fills in ≤2-3 days.** The binding constraint on prove-the-rate is the ≥4-distinct-days leg → earliest gate completion ~07-15/16 if the tape holds.

Wallet-truth delta by era (on-chain SOL):
| era | SOL | ~USD @ $77 | note |
|---|---|---|---|
| A | indeterminate | booked −$19.7 | HOODLANA rug −$24.6 dominated (gates since shipped); one UNEXPLAINED +0.3 SOL inflow 07-10 between 01:13 and 18:29 + orphan mogdog exit (sell-canary incident window) — needs chain history to close |
| B | −0.005 | −$0.39 | matches booked −$0.66 |
| C | −0.0321 | −$2.47 | booked −$6.39 overstates loss (USD conversion noise) |
| **D** | **+0.0309** | **+$2.38** | booked +$0.19 understates |
| C+D | −0.0012 | −$0.09 | = /api/wallet-truth −0.001157 exactly ✓ |

Flags: (1) orphan mogdog buy 07-10 16:53 has no sell in swap log OR trades feed — exited off-log during the 07-10 sell-path incident; (2) the +0.3 SOL era-A inflow is either a deposit or that off-log exit. Neither affects C/D truth.

## Q4 — SELECTION on live trips (n=26, 13 distinct tokens — HONESTLY THIN)

Joined all 26 trips to entry_meta (hidden_supply_share_pct, top10/top1_holder_pct, total_holders, rugcheck, lifecycle_age, dev_pct_remaining, liq, hour, entry drift): **no static feature separates green from red at this n** — hidden supply 69.6 vs 68.2, top10 20.7 vs 21.2, top1 3.1 vs 3.2, rugcheck 1.0 both, entry slip 3.0 vs 2.2 (wrong sign), liq 32.5k vs 27.3k. Hold time separates (green med 261s vs red 76s) but that is outcome, not selection.
One tentative REAL pattern: **re-entry into a token already traded that hour skews red** — first-touch trips: 9 green / 6 red, med +8.4pp; re-entries: 2 green / 9 red, med −3.9pp (BARKITO2, GHOSTI2, memecat2, PumpfunLife #2/3/5/6/7...). n=11 vs 15, could be regression-to-the-mean on bounced tokens; worth a shadow counter (re-entry cooldown per token per fleet), NOT a live gate yet.
Also flag: the entire $22.5 era so far is ONE token — the prove-the-rate gate's ≥20-distinct-token leg needs the discovery funnel feeding more names, and 7 same-token trips across 3 bots in 3.5h says the per-token fleet cap conversation (open since golive audit) is live again.

## VERDICT
At $22.5 the young-lane edge SURVIVES real fills so far: friction is now small and stable (2.1pp med / 3.5pp p90 round trip, prewarm fix confirmed working), and the wallet made +$2.38 over 7 trips (+2.1pp/trip mean net) this afternoon — but n=7 on one token is a read, not proof. $100 was tried pre-prewarm and friction ate it (−$2.47); stay at $22.5 until gross edge proves ≥~6pp median at n≥20. Keep the 3-bot cadence running: 20 fills arrives in ~2-3 days and the 4-day leg is the real clock.
