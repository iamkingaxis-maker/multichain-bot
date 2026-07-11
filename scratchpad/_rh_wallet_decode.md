# RH Chain — Wallet Behavior Decode v1 (2026-07-11)

READ-ONLY analysis of local tapes. Makers ecrecovered from signed txs (reliable identity per address).

## Data & honest n
- 452 tape files, 47,548 maker-level trades, 454 pools, 17,713 distinct makers.
- COVERAGE CAVEAT: tape spans 2026-07-10 03:06 UTC -> 2026-07-11 00:23 UTC only, in two recorder
  sessions (~03-10 UTC and ~15-00 UTC). This is ~1 day, not 3. All conclusions = accrual-stage
  unless noted. (Per no-24/7 rule, gaps are normal.)
- P&L proxy = per (maker,pool) USD net flow (sells - buys). Open holdings are UNPRICED: a maker
  still holding is unknowable; classified per ledger as closed (sold >=70% of buy USD), partial,
  open (no sells -> excluded from realized), or sell_only (no on-tape buy).

## Repeat winners (union-counted: 1 maker = 1 winner regardless of pool count)
Definition: realized net > +$1 in >= 3 distinct pools (closed or sell_only ledgers).

**n = 94 distinct winner makers.** Class breakdown is the headline:

| class | makers | realized net $ | interpretation |
|---|---|---|---|
| pure sell_only | 33 | +$68,018 | sold without ever buying on tape. Cost basis INVISIBLE (pre-tape buy, launch-block snipe, or token transfer/insider allocation). This is gross extraction, NOT proven profit. |
| mixed (some buys) | 37 | +$49,535 | partly same caveat |
| pure on-tape traders | 24 | +$2,862 | the only fully-audited winners |

Plain conclusion: the big extraction on RH chain is done by actors whose entry we never see
(snipers/insiders). Genuine on-tape trading edge exists but is small per actor (~$120/maker median,
top trader +$3.2k).

## What the audited (pure on-tape) winners actually do
Top 12 by realized net (medians per maker):

| maker | net$ | pos/all pools | medHold | medBuy$ | medEntryAge |
|---|---|---|---|---|---|
| 0x602ffc88b2cd... | 3,174 | 8/12 | 1.0m | 72 | 8.1m |
| 0xd8454bec97a2... | 1,263 | 4/8 | 0.3m | 89 | 14.0m |
| 0xdb8f093f28dd... | 358 | 3/4 | 1.6m | 1,770 | 0.0m |
| 0x9f251064c9bd... | 207 | 3/3 | 1.5m | 351 | 1.3m |
| 0x31f60f3f0f15... | 203 | 3/4 | 0.7m | 54 | 16.8m |
| 0x2e6a2c09ab02... | 169 | 4/4 | 1.2m | 61 | 0.4m |
| (6 more, +$58-116 each) | | | 0.6-1.2m | 35-357 | 0.4-90m |

Behavior signature (winners n=60 decoded vs repeat losers n=11):
- **They buy STRENGTH, not dips**: 184 of 229 classifiable entries (80%) came after positive
  120s net inflow. Losers: 88% strength. Dip-buying is NOT the tape-winner pattern on RH —
  opposite of our Solana young-lane edge. (Losers chase strength too; the differentiators are below.)
- **Entry timing**: median first buy 0.4-17 min after pool first-seen — launch-window scalpers.
- **Hold**: winners median 4.4 min vs losers 0.9 min. Winners give it a few minutes; losers panic-churn.
- **Size**: winners median buy $54 vs losers $177. Losers bet ~3x bigger. (Confirms our
  ruin-math/sizing doctrine on a second chain.)
- **Exit vs peak** (crude cumulative-netflow peak proxy): winners ~50/50 before/after peak —
  no clean "sells the top" signature at this n.

## Rug actor decode (Halp 0x8fe3889cbe..., TREAT 0x9925048c66..., KUNA 0xd139e1ad29...)
- **Halp**: rug NOT visible in swap tape — total sells only $381 and every top seller is net
  NEGATIVE (victims exiting). The rug was an LP pull / mint dump outside maker swaps. Maker tape
  cannot blacklist this actor class; would need LP-event / token-transfer decode (flagged, not built).
- **TREAT** (tot sells $2,843): top seller 0xde8e08277035 sold $932, net +$165 (only green actor);
  elsewhere net negative in 3 other pools (Robincat, Dagster, FTW). Second: 0xad388f2e1635 net +$152
  with sell_only pattern (no on-tape buy) — sniper class, also +$6.1k across 31 pools overall.
- **KUNA** (tot sells $2,551): green exits = 0x311cdebbe67a (+$267; a uniform ~$179 spray-buyer
  who is net -$1,024 overall with 15 open bags — got lucky or informed, ambiguous) and
  0x6b6e4c1afd72 (+$275 sell_only; also a top-5 overall winner, +$5.3k across 38 pools — reads as
  a professional sniper, not rug crew).
- **Cross-rug fingerprint**: 0x2e209a99c452 appears as a top-6 seller in ALL THREE rug pools,
  net negative in each, active in 81 pools — a high-frequency bot that eats every rug. Not a
  blacklist candidate; potentially a "sucker index" (its heavy presence != safety).

Actor-blacklist candidates (FLAG ONLY, n=1-2 obs each, all speculative):
1. 0xde8e08277035 — only net-green seller on TREAT rug, red everywhere else.
2. 0xad388f2e1635 — sell_only extractor in 33 pools (+$6.1k gross); its large sells = distribution signal.
3. 0x89e5db8b5a / 0x5b8d85ebab / 0x65050a9b7e — pure sell_only whales (+$25.9k / +$21.4k / +$4.7k
   across 3-16 pools, zero on-tape buys) — insider/sniper class; presence of their sells early in a
   pool's life is a candidate avoid-signal.

Same-actor speculation (timing/size fingerprints only — funding invisible in tapes):
- ~$18-uniform buy cluster: 5 wallets (0x5eb1f5fa..., 0x8fb0fe8c..., 0x14eb7253..., 0xb1ed2625...,
  0x51b45e5c...) all buy $17.8-18.0 flat, small pool overlap, all net NEGATIVE (spray-and-hold
  farmers). Possibly one operator; irrelevant to winner decode, useful as noise-flow marker.

## Top-3 actionable (candidate gates for RH fleet racers — all accrual-stage)
1. **Sniper-extraction gate (shadow)**: compute per-pool share of sell volume from sell_only makers
   in first 30 min; pools where sell_only extraction > ~40% of sells = distribution underway ->
   avoid/exit candidate. (Directly targets the $68k invisible-cost class.)
2. **Size/hold doctrine confirmed cross-chain**: keep RH bets small; the tape's repeat losers are
   the big-bet fast-churn cohort. No sizing increase from any single green day.
3. **Do NOT port the Solana dip-entry thesis blindly**: RH tape winners buy launch strength and
   scalp minutes-scale. Candidate: launch-momentum scalp lane (entry < 20 min pool age, exit < 5 min)
   as a SHADOW racer next to the dip lane — measure, don't switch.

Artifacts: scratchpad/_rh_winners.json, scratchpad/_rh_rug_actors.json, scratchpad/_rh_paper_closed.json
(decode scripts in session scratchpad rh_decode.py / rh_decode2.py).
