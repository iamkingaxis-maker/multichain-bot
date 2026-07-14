================================================================================
LENS 1
All analysis complete. Compiling the deliverable.

---

# RIP-DAY ENTRY MECHANISM DECODE (lens: what winner wallets bought and WHEN, relative to the run)

Dataset: scratchpad/ripday/ tapes (19,598 trades, 152 runner tokens, mostly 07-01 13:00-21:20 UTC) joined to GT minute OHLC + wallet_pnl.json. Analysis scripts written: `scratchpad/ripday/entry_decode.py` ... `entry_decode6.py`; per-entry rows in `entry_decode_rows2.json`, population rows in `entry_decode_pop.json`.

## 1. Winner set (honest n)
Tier A (>=3 profitable tokens AND positive covered net): kEFiAX3jo5Nm (+$292, 3/4), J1sfMsbxGN (+$10, 3/3), BGzLYcFc (+$2, 3/3-ish). Tier B (2 profitable, net>0): 14 wallets incl DJocqRPK (+$488), 7JCe3GHw (+$309), DF8tRgFk (+$174), 4MB2yiq5 (+$167). SPRAY contrast (>=3 pos tokens, NEGATIVE net): 2tgUbS9UMoQD, AgmLJBMD, etc. Winner-wallet entry n is small (A/B = 53 closed entries), so I validated triggers on the FULL population: 686 closed wallet-token positions across all 8,238 makers.

## 2. Named entry archetypes (population, n=686 closed positions, base WR 36.3%, median net -$1.08)

```
archetype             definition (decision-time, io trade log)              n    WR%   medNet  meanNet medMinsFromIgnition
1 FLUSH-ABSORB        net signed flow last 300s <= -$100 (sell-dominant)   55   58.2   +1.20   +23.4       377
                      AND >=1 single buy print >=$75 in last 60s
2 WHALE-BURST         buy print >=$75/60s but flow NOT sell-dominant      157   41.4   -1.10   -15.4       368
3 QUIET-TAPE          <=2 trades in prior 5m                              205   31.7   -1.01    -9.1      1446
4 FLUSH-NO-WHALE      sell-dominant flush, no big buy print               105   36.2   -1.21    +5.7      1011
5 CROWD-CHASE         >=4 buys/5m AND net flow positive, no whale          71   19.7  -12.24   -42.2      1154
```

- FLUSH-ABSORB is the only archetype green on WR, median AND mean. Robustness: 24 distinct tokens (17 with a winner), 42 distinct wallets, top-token concentration only 5/55; mean excl. best position still +$16.3; tail ratio |net|>$50 = 6 wins vs 1 loss.
- Timing overlay: FLUSH-ABSORB fired **within 6h of the token's ignition event**: WR 61.1%, mean +$46.8 (n=18). Same trigger on day-old runs decays to ~random. WHALE-BURST >24h after ignition = WR 31.4%, mean -$66 (the "chase yesterday's runner" death).
- Flow-direction is monotone: WR by net300 decile falls 44.5% (most sell-dominant) -> 32.6% (most buy-dominant). **Buying WITH crowd buy-flow loses; buying INTO sell-flow met by a single large buyer wins.** This is the rip-day translation of our badday `median_buy_size_usd>=34` finding.

## 3. What winner wallets specifically did

A/B winner wallets' entry mix (closed entries): winning tokens = 31% flush-absorb + 26% whale-burst + 20% quiet; their LOSING tokens = 39% flush-no-whale + 22% crowd-chase. Even good wallets lose when the whale print is absent or they chase crowds.

Price-path position (rip_recon.jsonl, 164 closed-outcome buys with OHLC):
```
                              won(n=37)   lost(n=127)
pos_in_prior90m_range   med      0.16        0.75
prior90m_high vs entry  med    +76.3%      +18.0%   (winners enter ~43% below the 90m high)
mins_from_event         med      149          80
fwd_max6h               med    +8.25%      +4.63%
buys at pos<=0.35 of range       70%          37%
buys at pos>=0.75 (breakout)      5%          49%
```
Winners buy the **first deep pullback ~1-4h into an already-ignited run, near the 90m low**; losers buy at/near the high. Even on rip days, the money is dip-shaped — confirms the 06-29 green-day prior on an independent sample.

Other winner signatures (medians, winner entries vs multi-token-loser baseline): token age 14-21h vs 51h; mins_from_event 244-368 vs 1291; single-shot entries (n_buys=1, no DCA); size $87-164 vs $47 (conviction); hold to first sell 3-12 min vs 20-71 min (fast scalp, not moon-hold). Minor archetype: DJocqRPK made +$488 sniping REVIVALS of months-old dead tokens (Cobie 253d old) on zero tape — n=2, prior artifact says UNFOLLOWABLE custody; not speccable.

## 4. Executable trigger spec (all signals in our feeds)

**RIP_PULLBACK_ABSORB** (candidate bot, shadow-first):
- Regime gate: sol_pc_h6 > 1.5 (existing).
- Universe/arm: token ignited >=+25% (from 60m low) within last **6h** while regime green — our recorder already produces exactly this set (rip_runners pipeline); age <= 24h; liq floor as usual.
- Fire (evaluated per io.dexscreener trade-log poll, ~100 recent trades gives full 5m window on these tokens):
  1. `sum(buy_usd) - sum(sell_usd)` over last 300s <= **-$100** (sell-dominant pullback), and
  2. max single buy print in last 60s >= **$75** (not our own fill), and
  3. price in lower **35%** of prior-90m range OR >=30% below run high (io bars / GT minute / Jupiter buffer).
- Anti-triggers (hard blocks): net300 > 0 with >=4 buys/5m (crowd-chase, 19.7% WR); price in top quartile of 90m range (49% of all losing entries); >24h since ignition; age > 48h.
- Exit shape (for the exit-lens agent): winners scalp — median 3-12 min to first sell; won-entry forward path med +8.25% max6h vs -6% low15 → TP ~+5-8%, stop ~-7%, timebox <=60m. Conservative +8/-8 race on the tiny fwd-covered sample passed only 11% — TP likely needs to be nearer +5-6% or flow-based.

## 5. Limitations (do not oversell)
- **One-day sample**: all 55 FLUSH-ABSORB hits date 07-01 (tape recency limit); cross-token/cross-wallet spread is good but there is zero day-level replication. Needs forward shadow before enforce.
- Outcome = wallet's realized covered net → conflates entry with their exit skill; the signal-level forward-return check had almost no OHLC coverage (18 rows, 0 ARCH1) because tapes are late-run relative to OHLC windows.
- Tapes truncate the original pump leg — early-ignition buying behavior is under-observed; archetypes describe mid-run behavior (which is what our bot can actually reach anyway).
- Universe is conditioned on runners (peak>=25%); the 36.3% base WR and all lifts are within-runner discrimination — the bot must reproduce the runner-arm step, not screen all tokens.

Bottom line: the rip-day offense that survives decoding is **"buy the first sell-flow flush of a freshly-ignited runner only when a whale-sized buy print is absorbing it, near the 90m low, and scalp out in minutes"** — a 58-61% WR / +$16-47 mean-per-position pattern on n=55 across 24 tokens and 42 wallets, vs 36% base; crowd-chasing the same runners runs 20% WR. Files: `C:\Users\jcole\multichain-bot\scratchpad\ripday\entry_decode3.py` (population validation), `entry_decode5.py` (archetypes + recon), `entry_decode_pop.json` (686 labeled entries).

================================================================================
LENS 2
I have what I need. Assembling the deliverable.

# RIP-DAY EXIT + SIZE LENS — decoded from tape (scratchpad/ripday/)

## Who was decodable (honest n)
Winner bar (>=3 distinct profitable tokens, covered net > 0): only **kEFiAX3jo5Nm** (+$292, 3/4 tokens, 0 open bags) and **J1sfMsbxGN** (+$10, 3/3). I extended to Tier B = 2-token net-positive wallets (DJocqRPK +$488, 7JCe3GHw +$309, DF8tRgFk +$174, 4MB2yiq +$167, CAP9q6Sm, 8P1msjLV) and Tier C contrast = tail-catchers who are NET NEGATIVE (AgmLJBMD -$2,225, FYX5JQ2k, 8zkg, 2tgUbS9 spray bot). Total decoded: 12 wallets, 29 closed A+B positions ($1.9k gross winner P&L), 116 tier-C positions. OHLC-path sim coverage over their buys was thin (n~5) — cash legs, hold times, partials, sizing are exact from tape; ROI magnitudes cross-checked vs token full-run peak (11/27 tails flagged possible pre-tape-inventory inflation, e.g. AgmLJBMD DREGG "+1016%" vs token peak +31%).

## 1. EXIT MECHANISM of the net-positive wallets
```
metric (tier A+B closed positions)         winners(n=18)   losers(n=6)
median hold to FIRST sell                        5 min        --
median hold to LAST sell                        27 min      45 min
PnL-weighted hold to first sell                 41 min
PnL-weighted hold to LAST sell                 187 min
partial-exit rate (>=2 sells)                  10/16 (63%)   3/8
median n_sells                                     2           1
share of winner $$ from holds >=60m              35%
share from holds >=120m                          35%
loser cut level (median cash ROI)                          -23.1%
```
- **Ladder-out, not dump**: 2–5 partial sells; small early trim (minutes), final exit 30min–16h later. Examples: DJocqRPK ANTOT — $43 in, 5 sells, first at 137m, last at 960m, +294% (token peak +253% → captured ~full peak); DF8tRgFk LIFE — 10 buys/5 sells/6 re-entries, last sell 590m, +179% vs peak +382% (47% of peak); 4MB2yiq POINT — 11 buys/5 sells, +35% vs peak +33%.
- **They sell the BREAK, not the spike**: on OHLC-covered sells, price was already falling ~-3% in the 10m before their sell and kept falling (-4.6% median next 10m; only 25–40% of sells saw price rise after). This is trailing-on-confirmation behavior — mechanically reproducible, no insider timing.
- **Re-entry is common** (half of B winners pyramid/re-enter the same token) — winners treat one token as a session, not one trip.
- **Loser handling is barbell**: net-positive wallets either cut ~-23% (wider than our -12) or **write off the probe entirely** (open bags at -100%: 7JCe3GHw holds $1.6k of dead bags against +$309 closed — his "net win" is only real if bags are worthless-in, i.e. probe-and-abandon).

## 2. SIZE MECHANISM — the actual winner/loser separator
```
wallet          net$    medSize$  max/med   winMedSz vs losMedSz
A kEFiAX        +292      224      1.4x        192 vs 303 (flat)
A J1sf           +10       48      1.5x        flat
B DF8tRgFk      +174      121      1.2x        flat
B 8P1msjLV       +29      108      1.15x       flat
B DJocqRPK      +338*      44      3.4x        (*after writing off 1 bag)
B 7JCe3GHw      +309**    145      4.7x        (**$1.6k open bags unpriced)
C AgmLJBMD    -2225       333     11.9x        170 vs 443  <- OVERSIZES LOSERS
C FYX5JQ2k     -121       238      2.4x        152 vs 433  <- same
C 2tgUbS9      -182(57tok) 39      9.5x        churn spray, breakeven-neg
```
Tier C has the SAME tail-catching skill (AgmLJBMD caught DREGG/b40/FOMO tails) and still loses. The kill signature is **conviction-inverted sizing**: median loser position 2.6x the median winner, max position 10x median rammed into corpses (traindog $3,950 -40%, QBX $1,290 -97%, DAVID $1,247 -97%). Clean winners keep max/median <=1.5x and never average down big.

## 3. Their exits vs OURS on the same trades (cash counterfactual)
On the 14 plausible closed A+B winners: **THEM +$782 gross. Our current exit (tp1 +6% sell 75% + trail) = +$264 (34% capture)** — and that prices the 25% remainder at THEIR blended exit, i.e. optimistic. **The shipped wideexit variant (tp1 +13% sell 30%) = +$603 (77% capture)** under the same assumption. The capture gap is mechanical: 75% of the book exits at +6 while their P&L center of mass sits at +25%..+300% realized over 30m–16h.

Our own rip-window book (sol_pc_h6>1.5 sells with entry_meta, n=12 legs/10 positions): median hold **1 minute**, path peak median +3.9%, p90 +7.7%, zero positions ever peaked >=13%. So on OUR entries the wide TP never triggers — confirming the reachability/coverage findings that entry selection, not exit, caps us on rip days. **The exit change only pays if paired with the rip-day entry archetypes the other lenses found (their entries' paths actually reach +25..+300%).**

Floor check on THEIR entries: recon rows with >=60m forward coverage (n=19, skewed to AgmLJBMD chase buys): median fwd_low90 = **-15.8%**, 63% touch -12 within 90m, 32% reach +13. Their entries trade through our -12 floor routinely; A+B losers were cut at median -23%.

## 4. Minimal exit/size spec for a rip-day bot
1. **Invert the TP weights** (rip-window only): tp1 +6→trim 25–30% (their early protect-trim), ride 70–75% on a trail. The already-shipped `badday_flush_wideexit_ab` variant (tp1 +13/30%, floor -12) is ~77%-capture-shaped — promote it as the rip-window default rather than a generic A/B.
2. **Trail on break-confirmation, not tight giveback**: their sells fire after ~-3–5% roll-over from local high at 10-minute scale; a 1-minute-scale tight trail would have dumped DJocqRPK's ANTOT ride at minute 5. Trail lookback >=10m, giveback ~8–12% from post-TP peak.
3. **Kill the time-box in rip windows**: 35% of winner dollars need >=60m holds (PnL-weighted last exit 187m); our rip-window median hold is 1 minute. Allow multi-hour holds while trail is unbroken.
4. **Floor**: keep a hard floor but A/B -12 vs -15/-18 for rip entries (median MAE -15.8 on covered sample, their cuts at -23; n=19, weak evidence — measure in shadow). Do NOT copy the probe-and-abandon open-bag pattern.
5. **Sizing law**: fixed probe, max position <=1.5x median, hard ban on averaging down below -15% — the single variable separating +$292 kEFiAX from -$2,225 AgmLJBMD on identical token ponds. Re-entry ABOVE entry after a partial (pyramid-into-winner) is fine and common among winners.

Caveats: 8 net-positive wallets / 29 closed positions is thin; cash ROIs on 11/27 tails may be inflated by pre-tape inventory (timing/partial/sizing stats unaffected); 7JCe3GHw and CAP9q6Sm are only winners if their open bags are worthless (no mark-to-market possible — no token amounts in tape); OHLC path sim on their entries was mostly infeasible (buys outside harvested bar windows), so the 34%/77% capture numbers are cash-arithmetic bounds, not path replays.

Artifacts: C:\Users\jcole\multichain-bot\scratchpad\ripday\exit_lens.py, exit_lens2.py, exit_lens_rows.json (per-position decode table).

================================================================================
LENS 3
All analyses complete. Assembling the deliverable.

FINDINGS — CONSISTENCY + PORTABILITY LENS (the skeptic) — rip-day (sol_pc_h6>1.5) wallet decode

VERDICT UP FRONT: After killing survivorship, coverage artifacts, and bot-farm duplicates, the harvest does NOT contain a robust, decoded, copyable rip-day offense. The 36 "candidate winners" collapse to ~5 independent wallets with a combined ~$115 of verifiable clean profit, a pass-rate fully consistent with luck, and the one independent forward-EV check on their entry style is NEGATIVE. Do not spec a bot from this sample. The actionable output is (a) a data fix that would make the next harvest decisive, and (b) a pre-registered follow-list to test forward.

1. UNIVERSE AND BASE RATE (survivorship context)
- 8,238 wallets, 969 closed (wallet,token) positions >= $20 across 152 tapes. Token-level closed win rate = 29.5% — on a universe where EVERY token ran >= +25%. Even conditioned on runners, 70% of visible closed positions lost money.
- 38 wallets traded >= 3 closed tokens. Expected number with >= 3 profitable tokens by pure luck at p=0.295: 11.4. Observed: 13. The ">=3 profitable tokens" population is statistically indistinguishable from chance. Adding net>0 leaves 3 wallets.

2. STRICT-BAR SURVIVORS (>=3 distinct profitable tokens AND net>0) — all three dissected
```
wallet         closed pos neg  net$    verdict
kEFiAX3jo5Nm     4     3   1  +291.78  87% of profit is FAKE (see below); clean residue +$37
J1sfMsbxGNXD     3     3   0    +9.66  real but economic noise; +12% pops in <3min, $30-75 clips
BGzLYcFcUZkW     6     3   3    +1.68  breakeven; mark-to-zero with open bag = -$218. FAILS
```
- kEFiAX3jo5Nm (known follow wallet, resurfaced): the +$255 PEACE "win" is a coverage artifact — PEACE tape starts 20:46:35, wallet "buys" $256 at 20:46:51 and sells $260 the SAME SECOND, then $251 24s later; sells $511 vs $256 in-tape buys = pre-tape inventory booked as profit. Clean visible round trips: skew +$32 (+31% in 43min), GOU +$11 (+10% in 6min), DAVID -$6. Not a sniper (first buys ~10h after pool creation), not sub-second HFT. Real residual: ~+$37.

3. THE BIG-DOLLAR "WINNERS" ARE ALL ARTIFACTS OR NET LOSERS
Conservative re-scoring (chronological walk; sells credited only up to 2x cumulative in-tape buys; open bags marked to zero):
```
wallet         naive$   capped$   kill reason
DJocqRPK2uKW   +488      -63      Cobie "+$362" = unverifiable 9.3x intraday claim on 8-month-old token;
                                  ANTOT $124 of sells "covered" by $8.88 of dust buys; prior RPC decode
                                  artifact says UNFOLLOWABLE custody. KILLED.
7JCe3GHwkEr3   +309    -1,863     both wins at tape start (sell/buy ratio 4.4x and 8.1x); its VISIBLE
                                  clean trades lose (dwnd -$317). KILLED.
DF8tRgFkt1JS   +174      +98      LIFE: sells $176.71 twenty minutes after $22.47 of dust buys =
                                  pre-tape inventory. KILLED as evidence.
CAP9q6SmwGuf    +39     -424      3 open bags; also one arm of a bot farm (below). KILLED.
AgmLJBMDCqWy  -2,225      --      biggest gross winner in tape (+$6,305 gross wins incl DREGG +$1,719)
                                  but 39.5% WR and -$8,530 gross losses = NET LOSER. Exit liquidity.
2tgUbS9UMoQD    -182      --      21 "profitable tokens" but 37.5% WR spray bot, net negative.
gasTzr94/hnu5iBK -1,210/-1,704    high-activity churners, deeply negative.
```
Pattern: every wallet showing >$100 of "profit" gets it from sells right at tape start with no visible matching buys. The io recency limit (~100 trades/pair) means post-hoc harvests systematically MISS the entry leg of the original pump — this is the machine that manufactures fake winners.

4. BOT-FARM DEDUP (independent replication is lower than it looks)
Three duplicate-operator clusters among the 17 net-positive wallets (same tokens, first legs within 120s, matched sizes):
- CAP9q6 + 6LrfU8 + ENzTgCn (FOMO/BOGE, $24-27 clips) — farm combined ~breakeven
- DZcyYa9 + 3AoQmpHa (QBX, identical $29.20 buys) — combined ~-$10
- 24sFm6 + wJDv8gq (SHIH/SHROOM, identical timing) — both negative
17 positive wallets -> at most 12 independent operators, most at <$30 profit.

5. SNIPER / INSIDER / HFT SCREENS
- Snipers: ZERO wallets in the positive set bought within the first seconds/minutes of pool life (minimum first-buy age across candidates ~4.4h, median hours-to-days). No sniper edge visible to steal — snipers live in the first 100 trades, which are beyond io tape reach.
- Sub-second HFT: same-second legs exist (kEFiAX PEACE, 8P1msjLV sell 21:11:14 / buy 21:11:15) but these are split-fills/MEV one-offs or MM inventory management, not the source of the clean wins. The clean wins operate at 2-60 min horizons = latency-portable at 1-2s.
- 8P1msjLVVaZd (+$29) is a 2-minute-cadence grid/market-maker (9 flips on 'relax' at +1-5% each) — a different business (inventory risk during dumps; it holds PEACE bags), not our copy target.

6. WHAT SURVIVES — thin, and its forward-EV check is red
Surviving mechanism sketch (from ~5 independent, non-sniper, non-farm wallets with clean in-tape round trips): "established-runner pullback scalp" — enter a rip-day runner 1-6h AFTER its event on a pullback, exit +10-30% within 10-60 min, $25-300 clips, no slot churn:
```
wallet        clean$  style                                   replication note
kEFiAX3jo5Nm   +37    $90-300 clips, 6-45min, +10..31%        known follow wallet resurfacing
6FYgn2apNXSq   +21.5  $33 clips, 10-21min, +26..38%           ONLY cross-day replicated wallet:
                                                              validated 06-29 greenday winner (76% WR, 21 trips)
DkULcixfUQyg   +16.4  $59 clips, 23min-4h, +12..16%
J1sfMsbxGNXD   +9.7   $30-75 clips, <3min, ~+12%
8P1msjLVVaZd   +29    2-min MM flips (different mechanism)
```
Combined verifiable clean profit: ~$115 across 8,238 wallets. Against it, the independent mechanism check: of 309 recon buys only 19 have >=60min forward OHLC coverage, and dip-zone entries (bottom third of prior-90m range, event+30..360min) show med fwd_low90 = -15.8%, with a TP+12/SL-10 policy hitting the stop 13/18 times, EV ~ -6%/trade in the covered window. Small n, late-day-fade biased — but it is a red light, not a green one.

Out-of-sample cohort test: the 8 validated 06-29 greenday winners present in this tape netted +$81 combined (3 active: +$21.5, +$69.4 [with 4 unmarked open bags], -$9.9; 5 inactive). Weakly positive, tiny n. Our follow watchlist cohort: -$282.

7. WHAT I'D DO NEXT (not a bot spec — a data fix + forward test)
- The single decisive fix: record io tapes LIVE DURING the next sol_pc_h6>1.5 window (continuous sweeps from window start), so entry legs are captured and covered_net stops being forgeable. Every fake winner in this harvest is a tape-truncation artifact; a live-recorded window either produces real >=3-token winners or proves they don't exist.
- Pre-register and forward-track (no capital): kEFiAX3jo5Nm, 6FYgn2apNXSq, DkULcixfUQyg, J1sfMsbxGNXD, 8P1msjLVVaZd + the 06-29 greenday-winner cohort. 6FYgn2ap is the only wallet with genuine cross-day replication — two independent selection days, positive both — and its style (small pullback scalps on runners, ~76% WR, 10-20min holds) matches the prior greenday decode, which modestly re-confirms that finding at rip-day too.
- Kill list (do not re-surface as "winners"): DJocqRPK2u, 7JCe3GHw, DF8tRgFkt1, AgmLJBMD, 2tgUbS9, CAP9q6/6LrfU8/ENzTgCn farm, DZcyYa9/3AoQmpHa farm, 24sFm6/wJDv8gq farm.

Caveats on my own numbers: tape truncation cuts BOTH ways (these wallets' losses on non-runner tokens are equally invisible, so even the surviving win rates are overstated — the universe is 100% tokens that ran +25%); open bags are unmarked (no token amounts in tape); OHLC windows end ~event+4h so most 07-01 late-day trades cannot be price-verified.

Artifacts written: C:\Users\jcole\multichain-bot\scratchpad\ripday\skeptic_analysis.py, skeptic_deep.py, skeptic_artifact.py, skeptic_final.py, skeptic_wallets.json (per-wallet full stats incl losers, sniper/HFT/hold metrics).