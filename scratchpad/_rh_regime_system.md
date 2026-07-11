# RH Chain — Regime System v1 (2026-07-11)

AxiS ask: "we have no regime gates or system for robinhood chain." This is the mined rulebook
v1, what shipped as STAMP vs GATE, and the pre-registered grading plan. Built under the Solana
hard constraints: two-window bar for any enforcement, no overgating (per-racer opt-in only),
no paper expectancy halts (dial = stamp), hour + demand-composition axes first, ETH move as a
candidate expected to fail. REQUIRED interaction axis (AxiS hypothesis): pool AGE BAND
(young <6h / mid 6-24h / aged >24h) on every regime axis.

## Data & method
- Substrate: the full-history sweep (scratchpad/rh_history/sweep_logs.jsonl.gz — 10.36M swaps,
  every WETH pool, 2026-07-01..11) + pools_registry (61k creations) + block anchors + an
  ETH/USD curve rebuilt per-window from the WETH/USDG pool's own sweep rows.
- Unit of outcome: SYNTHETIC DIP TRIPS — the paper lane's own trigger replayed maker-less over
  every pool (>=10 swaps, >=~$500 cum vol, px <=12% off the 10-min high, last-30s buys >=~$50
  and > sells, 600s per-pool cooldown). Resolved at +20m (ret20/win), rug = trough <=0.2x entry
  within 60m. 39,132 dip trips + 31,208 pop events (pop = +35% off the 10-min low; follow-through
  measured the same way). No fees — relative comparisons only, never absolute edge.
- TWO-WINDOW DISCIPLINE: a rule ships as a gate only if its DIRECTION holds in ALL FOUR halves:
  chrono W1 (07-01..05) vs W2 (07-06..11, incl. bot era) AND odd vs even day-of-month (era-
  balanced). Everything else is a stamp.
- Mining/analysis code + full tables: scratchpad/rh_regime/{mine_regimes.py, analyze_regimes.py,
  corroborate_paper.py, rulebook_v1_tables.json, analysis_out.txt, windows.json, trips.jsonl.gz}.

## Rulebook v1 — what PASSED all four halves
Deltas are win-rate pp vs the band's own half base; (W1 / W2 / even / odd).

1. **AGED band (>24h) 19-21 UTC is BAD** — the one enforcement-grade hour rule.
   d = -7.3 / -1.3 / -0.7 / -3.6 (n = 340/1421/776/985), median ret NEGATIVE in each half.
   Held in the human era AND the bot era -> era-UNconditional. => **GATE (shipped, below).**
2. **AGED band 02-10 UTC is GOOD**: 02-07 d = +6.5/+1.9/+1.5/+7.9; 08-10 d = +3.7/+2.4/+1.2/+9.8.
   The v0 "08-10 whale session" is REAL and it favors AGED pools. Favorable blocks are
   scheduling guidance, never gates. => documented; aged racer sessions should cover 02-10 UTC.
3. **YOUNG band 02-07 UTC is GOOD**: d = +6.2/+2.0/+2.2/+3.8 (n=673..3528/half), best young
   block in both eras (9pm-2am CT). Directly contradicts extrapolating Solana's 03-08 sleep to
   RH. => scheduling guidance: overnight-UTC recorder/racer sessions are prime, not dead.
4. **YOUNG band bot-era discovery bursts are TOXIC (rug axis)**: in windows with pool-creation
   rate >=200/h, young-trip rug rate ~doubles (W1 8.2 vs 5.7, W2 10.7 vs 5.9, even 10.4 vs 6.8,
   odd 11.2 vs 4.6) and win rate is lower in all four halves (human-window young dips beat
   bot-window ones by +2.2/+8.4/+11.7/+8.1pp). BUT 77% of all young trips (92% in W2) happen in
   bot-regime windows — enforcing this on paper = a buy cliff that starves the sample.
   => **STAMP (disc field) + live-lane candidate**, graded per the plan below. NEVER a paper gate.
5. **Pop-chasing on mid/aged pools is bad everywhere**: pop follow-through win 33-47%, median
   -2..-10% in essentially every mid/aged cell, every half. => documented kill: no momentum/pop
   lane on non-young pools (reinforces the wallet-decode "don't flip to momentum").

## What FAILED the bar (documented kills — do not rebuild without new data)
- **22-01 "dead zone" (v0 causal candidate): REFUTED market-wide.** Young 22-01 d =
  -3.5/+1.0/+0.3/-0.7 — sign flips across halves. Our own 07-10 evening bleed was real but it
  is NOT a market hour effect (v0's confound (b) — pool age/arc — stands as the live suspect).
  rh_prime_hours' A/B keeps running as an experiment, but the hour theory gets no gate.
- **19-21 "prime" (v0): REFUTED for young** (d = -3.8/-0.9/+0.3/-3.7 — flat-to-negative).
  v0's label came from ONE evening of our own trips.
- **The v0 human-era 14-23 UTC regime_hours rule: REFUTED and REMOVED.** It was built on VOLUME
  tables; outcome tables say human-era 02-07 was the BEST young cell. Volume != outcome —
  the exact Solana composition-artifact trap the constraints warned about.
- **ETH move (1h and 24h): CONFIRMED IRRELEVANT** in every band — deltas within noise, signs flip
  across halves (young 1h: -0.2/-0.5/+0.2 terciles in W2; 24h lo/hi inconsistent between parity
  halves). Pre-registered expectation met. Do not re-mine price macro on this chain.
- **Composition buy_share<50% (prior 30m) hurts YOUNG**: direction passed all four halves
  (d = -4.5/-1.4/-1.8/-2.6) but median ret stays positive in half the cells and the bin holds
  27% of entries -> fails the EV/overgating bar. => STAMP only; re-test at v2 with realized
  ledger joins. Mid/aged: inconsistent.
- **distinct_pools (prior 30m)**: quieter-breadth windows better wherever measurable (lo tercile
  +2.4..+6.0) but W1 has no contrast (all-lo) -> only ONE chrono window => stamp, accrual.
- Era base drift (context, not a gate): young dips decayed from 59.6% win / +11.0 med (W1) to
  49.5% / -0.5 (W2); young rug rate doubled 4.3% -> 9.0%. The market that rewarded naive young
  dips in launch week no longer does. Aged rug rate is ~0.3-0.7% in ALL halves (rug risk is a
  young-pool phenomenon — matches the 20-min median-death census).

## Corroboration from OUR paper fleet (n small, 1 evening, honest weight: weak)
All 72 closed positions were YOUNG band. By block: 14-18 -$4.24/trip (n=8, rug-driven),
19-21 +$0.05 (n=38), 22-01 -$1.14 (n=26). Consistent with "no strong young hour rule"; the
22-01 bleed is our arc/age artifact candidate, not the market clock. (scratchpad/rh_regime/
corroborate_paper.py.)

## What shipped (working tree)
- **core/rh_regime.py** (pure, no network): age_band, discovery_regime (200/h split),
  hour_block, CompositionTracker (rolling 30-min feed-wide buy/sell USD, netflow, buy_share,
  distinct pools — O(1) ingest), expectancy_dial (offense/defense STAMP over last 20 closes,
  min n=10), regime_stamp, and the ONE enforced rule: aged_hour_gate_ok (aged band blocked
  19-21 UTC, fail-open on unknown age/hour).
- **scripts/rh_paper_lane.py**:
  - STAMP fleet-wide: every buy ledger row now carries `regime` = {hour_utc, npph, disc, band,
    buy_share_30m, netflow_30m_usd, distinct_pools_30m, n_swaps_30m, dial, dial_exp_usd,
    eth_usd}. CompositionTracker fed from the tape drain; per-racer realized record
    (recent_realized, capped 50) feeds the dial and persists in rh_lane_state.json.
  - GATE: regime_hour_ok rewritten to v1 (thin wrapper over aged_hour_gate_ok; block reason
    stays "hour_regime"). The refuted human-era-14-23 logic and REGIME_HUMAN_HOURS removed.
    Opt-in unchanged: the 3 aged racers have regime_hours=True (default ON), the 10 scalp
    racers stay OFF (mid-flight A/B untouched). NOTE: the gate keys on the POOL's band — while
    the feed still caps at 24h the aged racers trade the mid band and the gate is dormant; it
    arms automatically when RH_FEED_MAX_AGE_H widens (the >24h thesis cohort).
  - The expectancy dial NEVER gates paper entries (paper = data): it is recorded so its own
    record can grade it for the live lane.
- **Tests**: tests/test_rh_regime.py (18 pure + 3 lane-integration tests) + test_rh_aged_racers
  regime tests rewritten to v1 semantics. RH suites: 275 passed, 2 skipped.

## Pre-registered grading plan (stamp -> gate promotions)
Promotion requires ALL of: (a) n>=30 affected entries with realized outcomes in the ledger,
(b) >=20 distinct tokens and >=5 distinct days, (c) effect direction matches the mine on
realized tokmed (per-token medians, not sums), (d) winner-kill <=5% (the gate would have
blocked <=5% of realized winners), (e) AxiS approval. Specifically:
1. **disc="bot" young caution** -> candidate for the LIVE lane only (sizing/defense, e.g. half
   size in bot-burst windows). Grade young-band entries disc=bot vs disc=human at the bar above.
   Never a paper block. ALSO calibrate: lane npph (feed-discovered candidates) vs the mined
   chain-wide registry rate — same 200/h threshold, different instruments; check the stamp's
   npph distribution after ~3 sessions before trusting the split point.
2. **buy_share_30m < 0.50 young caution** -> grade at the same bar; v1 failed the EV bar.
3. **dial=defense** -> at n>=30 defense-stamped entries, compare realized vs offense entries;
   promotion target = live-lane sizing dial, never paper.
4. **aged 19-21 gate audit** (it shipped): once the feed widens past 24h and the aged racers
   accrue n>=30 blocked-hour counterfactuals, verify the block isn't killing winners
   (winner-kill <=5%); demote back to stamp if it fails live grading.
5. Hour rulebook v2: re-run mine_regimes.py + analyze_regimes.py after ~1 week of new chain
   history (one command each); v1 cells that flip lose their status.

## Honest caveats
- Synthetic trips are maker-less and fee-less; they measure the MARKET's dip follow-through,
  not our net edge. All v1 claims are relative (cell vs band base, same half).
- Block->ts interpolation is anchor-based (25k-block anchors); hour labels near block-rate
  shifts can smear a few minutes — hour-BLOCK conclusions are robust to this, single-hour
  cells less so.
- W1 (07-01..05) contains launch week; the parity split exists precisely to catch W1-only
  artifacts — nothing shipped on W1 evidence alone.
- The eth_usd stamp uses feed.eth_price (refreshes on the feed's own cadence) — good enough
  for the documented-dead ETH axis.
