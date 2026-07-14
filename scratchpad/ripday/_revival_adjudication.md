# REVIVAL POND — REAL EDGE vs SURVIVORSHIP MIRAGE (adjudication, 2026-07-05 03:35 UTC)

Analysis only. Inputs: `_revival_eps_0703.json` (131 eps / 13 tokens / 53 winner wallets),
`_revival_grid.json`, ledger3_wallets.json, live_tapes/ + ripday tapes + FRESH pulls at 03:28-03:36 UTC:
`_adj_fresh_trades.jsonl` (io trade-log, 15 pairs x 250), `_adj_ds_now.json` (DexScreener pairs batch),
`_adj_gt_hour/` (GT hourly bars to 03:00 UTC). Scripts: `adj_fetch.py`, `adj_analyze.py`.
Outputs: `_adj_results.json`, `_adj_bank_rows.json`. Window close = 07-04 04:26 UTC; NOW = ~23h later.

## VERDICT: MIXED — H2 on the winners' bags, H1 (narrowly) on the entry signal.
**The deciding number: post-window matched realized by winner wallets on revival tokens =
−$284 on $4,868 sold (−5.8% of sold USD).** They did NOT bank later. H1's "lumpy monetization
beyond the window" is falsified at the 23h horizon. But the pond signal itself kept producing
huge forward excursions with liquidity intact — the failure mode is ROUND-TRIP, not fake marks.

## Test 1 — Bag marks NOW (MARKS, not realized; n=131 eps / 13 tokens)
| measure | at measurement (07-04 ~04:26) | NOW (07-05 03:34) |
|---|---|---|
| aggregate ret on $26.9k buy USD | **+16.6% (MARKS)** | **+0.4% (MARKS)** |
| aggregate ex-manlet ($25.9k) | +9.4% | **−3.0%** |
| per-ep median ret | +6.6% | +0.0% |
| med px-now vs entry VWAP | — | **−17.4%** (eps), −13.0% (token-level) |
| window matched realized | −17.5% | (unchanged; that money is gone) |

- Tokens still green vs entry: **4/13** (manlet +128%, Hobbes +64%, Zeus +51%, CHANCE +18%).
  Red: TATE −67, FOMO −59, ??? −39, MITCH −33, NEIL −32, GOON −13, Udin −12, Martolexx −6, TMB −70.
- Price vs the implied mark at measurement (med per token): TATE −77%, NEIL −58%, TMB −59%,
  manlet −40%, Udin −36% — **the measurement marks WERE near the top tick** for the big names.
- BUT liquidity is NOT draining: med liq now $59k (range $24k-$332k), vol24 still $65k-$20.9M.
  These are live, hot pools — retraced price, intact market. Pure-rug H2 is rejected; mark-mirage H2 is confirmed.
- manlet concentration: the whole cohort-level green is one token. manlet eps: window realized
  −84.1% of buy USD (they sold the early chop LOW); the +205%→+91% is a bag mark they never banked in coverage.

## Test 2 — Did they bank? (matched realized, union entries; sell px from hourly bars = approx)
Coverage honesty FIRST: post-window tape has **12.9-22.8h max gaps** per pair (recorder rotates
targets; fresh sweep reaches back only minutes on these still-hot pairs). Observed sample skews
to early-07-04 + the last few hours. "Silent" below = not seen in observed windows, not proof of zero activity.
- Winner activity observed since window: **30/53 wallets**, 52 wallet-token pairs, 41 sellers.
- **Matched realized since window: −$284 on $4,868 sold = −5.8% of sold USD** (union entry VWAP =
  window basis + post-window rebuys; sells capped at held tokens).
- Sell VWAP vs union entry: med **+3.0%**, 17/31 above entry — the scratch-machine signature again
  (small wins above entry, sized losses below: 15 banked >+$1 totaling +$381; 10 lost totaling −$665).
- By token: Hobbes +$265 (19 sellers, the one genuine monetizer), NEIL −$223, FOMO −$115, ??? −$82,
  GOON −$81, manlet −$42 (7 sellers on $895 — selling the +100%+ bag near flat vs THEIR vwap).
- Bag holders (>$5 cost remaining): 72 wallet-tokens; **55 (76%) silent** in observed tape.
- Post-window REBUYS: $5,192 across 33 wallet-tokens — they are still churning the pond, not exiting it.

## Test 3 — The pond itself, forward to NOW (first-match per token, 25-51h elapsed)
07-03 matches n=11: **med max-CLOSE since +146.6%, 10/11 hit ≥+15% close** — the prior 6h stat
(med max-close +55%) EXTENDS, hugely. But **med NOW −16.5%, green-now 5/11, med min-close −27.9%** — matches ROUND-TRIP.
07-04 fresh matches n=3 (Udin re-fire, Pauly, BABYANSEM): 3/3 hit +15% close (max +30/+104/+51),
med now −18.9%. Same shape: real spike, full fade. (Caveat: 12/14 pond pairs = the winner-token
set itself — circular universe; n=3 fresh is anecdote-thin.)

## What this means for `badday_revival_absorb`
**Neither kill nor build-now. Keep the plan: build only on the 07-05 decode confirm — but with the
thesis CORRECTED.** The falsified part is the "absorb/hold like winners" half: winners' +16.6% was a
mark illusion (now +0.4% agg, −3.0% ex-manlet) and they banked −5.8% post-window. The REAL part is
the entry signal: pond matches keep producing +30..+590% max-close excursions on intact liquidity
within 6-48h. A bot can only own this pond as a **fast-taker**: TP1 +6/75% + TP2 +12 as drafted are
mandatory (they harvest the real half), hard floor −12 stays, and DO NOT widen floors or add
hold-the-bag logic — med min-close −27.9% and med end −16.5% is what "absorbing" buys you.
Judge at build time on REALIZED only; a marks-based scoreboard will reproduce this exact mirage.
07-05 confirm bar (unchanged from the report): fresh-match hit(CL15) healthy on full 07-04/07-05 bars
(3/3 here, n thin) AND winner re-allocation green at base — plus this file's standing rule: no
"absorb" language in the jersey; rename to `badday_revival_taker` if built.

## Honesty ledger
- n=13 tokens / 131 eps / 53 wallets — thin; manlet dominates every aggregate (shown ex-manlet).
- All "now" valuations are MARKS at 03:34 UTC px; labeled throughout. Realized figures are matched, union-of-entries, per-wallet-per-token dedup.
- Post-window banking is computed on PARTIAL tape (12.9-22.8h max gaps); sell/buy px approximated by GT hourly close at trade ts.
- Test-3 universe is the runner-tape grid (survivorship-hot control); 07-04 fresh n=3.
- 23h is one horizon; a 3-7d re-mark of the same bags (5 min with `adj_fetch.py`) would close the remaining H1 tail claim — worth re-running before any build.
