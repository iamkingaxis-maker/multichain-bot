# RH Stable-3 — cap the left tail on the high-WR entries (2026-07-13)

AxiS: "both sides show extreme volatility with profit AND loss from each individual bot;
we need stability." STABILITY = kill the per-bot P&L swings, NOT chase a higher ceiling.

Data: local `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (accumulated, append-mode-
fixed ledger; 457 closed trips / 43 tokens / 07-10..12). Trips reconstructed with the
scorecard `load_rh_trips()` join (sells per `(bot,pool)`, split at `fully==True`,
ret = Σpnl_usd / $25 × 100; 1970-epoch test rows scrubbed). Script:
`scratchpad/_rh_stable3_analyze.py`. Builds on `_rh_winner_decode2_0713.md` (entry decode),
`_rh_rug_v2_0713.md` (concentration gate), `_rh_exit_rug_0713.md` (fast-liq-bail).

**HONEST LOW-N up front:** most racers are n<30 distinct tokens (greens 7-12) — everything
here is DIRECTIONAL. No lifetime-SUM verdicts; ex-top-2 token-median + green-rate + trip-
return stdev, OOS-split by DISTINCT TOKEN (no single-token appears in both halves — the
prior agent's odd/even leak is avoided by splitting the TOKEN list, not the trip list).

---

## 1. CONFIRMED: the trip-WR vs token-level gap is real

High per-sell-leg win-rate does NOT translate to a green TOKEN-level result, because a few
big-loser / rug tokens dominate the per-token medians. The three "highest-WR" entries:

| racer | leg-WR% | leg-med | trip-WR% | trip-med | trip-STD | tok-green% | **tok-med** |
|-------|--------:|--------:|---------:|---------:|---------:|-----------:|------------:|
| rh_demand_heavy | **78.2** | +6.4 | 70.0 | +6.0 | 11.7 | 75.0 | **+5.5** (green) |
| rh_deep_only | **71.8** | +6.8 | 62.5 | +6.0 | **23.0** | 60.0 | +3.7 (fragile) |
| rh_aged_deep | **71.4** | +7.0 | 54.5 | +1.3 | 10.8 | 42.9 | **−4.7 (RED)** |
| rh_young_v1 (ctrl) | 64.4 | +5.9 | 52.4 | +2.0 | 14.0 | 40.7 | **−4.0 (RED)** |

- The 78/72/71% figures AxiS cited are per-**sell-leg** WR — inflated by the partial-TP1
  ladder (TP1 legs are always +). At the TOKEN level the gap is stark:
- **`rh_aged_deep` is the clean illustration: 71% leg-WR but −4.7 token-median (RED).** It
  banks small wins on most legs, yet a handful of losing tokens drag the per-token medians
  under water. The control does the same: 52% of its trips win and the median trip is +2.0,
  but the token-median is −4.0.
- **`rh_deep_only` shows the volatility directly: trip-return stdev 23.0** — the single
  CASHCATWIF −100.1 trip nearly doubles its dispersion vs the rest of the fleet.

The fleet-wide tail (from `_rh_rug_v2`): closed-trip returns p10 −16.3, and ~9% token-level
rug rate — CASHCATWIF −100, CASHCATGAME −98, Halp −90, plus QUANT/KUNA/Ape −23..−31. **A
thin ~1.5% of trips wipe a position, concentrated in ~4 tokens — that IS the volatility.**

## 2. QUANTIFIED: what capping the left tail does

Decomposed each lever on the ledger. The headline: **a hard downside cap floored at −15/−20
is a PURE stability win** — it collapses dispersion and zeroes the catastrophic rate while
leaving win-rate, token-median AND throughput UNTOUCHED (medians ignore tail magnitude; the
cap only compresses the losers already past the floor).

### 2a. Trip-return STDEV (the direct P&L-swing metric) + worst trip

| racer | baseline std / worst | floor@−15 std / worst | Δ |
|-------|---------------------:|----------------------:|---|
| rh_deep_only | 23.0 / **−100.1** | **10.2** / −15.0 | **−56%** |
| rh_young_v1 | 14.0 / −90.0 | 10.3 / −15.0 | −26% |
| rh_demand_heavy | 11.7 / −30.6 | 10.1 / −15.0 | −14% |
| rh_aged_deep | 10.8 / −15.3 | 10.8 / −15.0 | ~0 (no tail) |

### 2b. Full-metric decomposition (floor is the idealized cap; see 2d for realizability)

`rh_deep_only` — the racer the cap helps MOST:

| variant | nTrip | nTok | tripWR | tokGrn | tokMed | tripStd | worst | cat |
|---------|------:|-----:|-------:|-------:|-------:|--------:|------:|----:|
| baseline | 24 | 10 | 62.5 | 60.0 | +3.74 | 23.0 | −100.1 | 0 |
| **floor@−15 ONLY (free)** | 24 | 10 | **62.5** | **60.0** | **+3.74** | **10.2** | **−15** | **0** |
| +bite2 (no floor) | 14 | 10 | 57.1 | 50.0 | −1.07 | 11.0 | −18.0 | 0 |
| +rug-gate + floor | 12 | 9 | 50.0 | 44.4 | −1.24 | 9.8 | −15 | 0 |

`rh_demand_heavy` — the healthiest parent:

| variant | nTrip | nTok | tripWR | tokGrn | tokMed | tripStd | worst | cat |
|---------|------:|-----:|-------:|-------:|-------:|--------:|------:|----:|
| baseline | 50 | 12 | 70.0 | 75.0 | +5.51 | 11.7 | −30.6 | 1 |
| **floor@−15 ONLY (free)** | 50 | 12 | **70.0** | **75.0** | **+5.51** | **10.1** | **−15** | **0** |
| +bite2 + floor | 20 | 12 | 70.0 | 66.7 | +5.37 | 10.1 | −15 | 0 |

**Reading the decomposition (the key finding):**
- **The floor/hard-stop is the pure lever.** Dispersion DOWN, catastrophic-token rate → 0,
  and win-rate / token-median / token-green / throughput ALL HELD. This is exactly the
  stability signature the task predicted, and it is FREE (a % stop costs no latency).
- **The concentration rug-gate is a pre-buy defense with a throughput TAX.** Enforced, it
  removes the 1 ledger-stamped flagged token (CASHCATWIF, top1 10.6 / top10 50.6) — but
  demand_heavy traded CASHCATWIF 17× ~breakeven (banked TP1s before the dump), so dropping
  it costs 17 trips and ~6pp of trip-WR for little stability gain the floor didn't already
  get. Its real value is avoiding the concentrated-DUMP class (CASHCATWIF/CASHCATGAME)
  BEFORE entry; the floor handles realized-tail magnitude for tokens that do get traded.
- **The bite-cap is diversification insurance with an in-sample cost.** It adds NOTHING to
  swing-reduction beyond the floor (deep_only 10.2 → 10.6) and, being token-blind, clips
  profitable re-entries too (deep_only tokMed +3.74 → −1.07). But it bounds worst-case
  single-token concentration — demand_heavy put **34% of its trips (17/50) into ONE
  concentrated token**, which is structurally the swing AxiS named. Kept as insurance,
  flagged honestly; the in-sample degradation is a low-n artifact of a token-poor ledger.
- **`fast_liq_bail`: 0 staged pulls in the ledger → no measurable effect** (Halp was
  single-block). Reported, referenced, not enforced.

### 2c. Catastrophic-token rate & dispersion (std of per-token medians)

Under floor@−20 EVERY racer goes to **0 catastrophic tokens** (from demand_heavy 8.3%,
control 11.1%, moonbag/wide_ladder ~7%), and dispersion of per-token medians drops sharply
where a tail exists (control 18.9 → 9.5 at −20, → 8.5 at −15). floor@−15 gives strictly
lower dispersion than −20, so the tighter effective cap is the better stability setting.

### 2d. Latency & realizability — HONEST mapping

The floor@−X simulation is the IDEALIZED cap (every below-X trip exited at exactly −X). The
config levers that realize it, all **zero added latency, well inside the ≤2s detect→fill**:
- **`hard_stop_pct=−15`** — realizes the floor for the STAGED/bleed class (a book still
  exists to sell into: QUANT −31, KUNA −25, Ape −23, deep bleeders).
- **`derisk_after_s` + `derisk_max_frac=0.25`** — the catastrophe cap: force exposure to
  25% EARLY, so a LATER rug/LP-drain gap hits a quarter position (a −90 gap at t>window →
  ~−22.5% on position ≈ floor@−20). This is the ONLY realizable defense against gap-through,
  and it works because median time-to-death is ~20 min (most tail events land after a 5-min
  scalp derisk fires). Corroborated by the 07-12 variance mine (stdev −20%, mean lifted).
- **Residual the cap CANNOT reach (flagged):** the single-block LP-pull (Halp −90, 10s) —
  holder-invisible, book gone in one block, so flooring it at −20 is optimistic and NO stop
  or holder-gate saves it. It is already fenced by MIN_LIQ 30k + MIN_POOL_AGE 1h (Halp was
  $17k / 7min). Its only defense is LP-custody (`lp_any_eoa_owner`, shadow, fires 0 today).
- **rug-gate enforcement, when promoted (n≥30 rugs + AxiS), lands as an arm-time Blockscout
  PREWARM** (2 calls, cached, 0 latency at fill) — NEVER the 90s eth_getLogs recon.

---

## 3. The 3 stable racers (added to ROSTER, PAPER, not deployed)

Highest-WR entries (demand $150 / deep −25 / aged-deep) with the tail-cap baked in. Uniform
recipe (same cap on all three) so the confirm cleanly attributes results to the ENTRY, not
to per-racer knob-tuning. All levers are computed from tape already in hand each tick.

| bot_id | entry (parent) | exit | tail-cap baked in |
|--------|----------------|------|-------------------|
| **`rh_stable_demand`** | demand $150 (rh_demand_heavy) | scalp | stop −15 + derisk **5min**→25% + 2-bite + group |
| **`rh_stable_deep`** | dip −25 (rh_deep_only) | scalp | stop −15 + derisk **5min**→25% + 2-bite + group |
| **`rh_stable_ageddeep`** | 6-24h + reentry −26 (rh_aged_deep) | aged ladder | stop −15 + derisk **20min**→25% + 2-bite + group |

Shared cap (`_rh_stable3_0713.md`):
- **`hard_stop_pct=−15`** — the price stop.
- **`derisk_after_s` + `derisk_max_frac=0.25`** — catastrophe cap. SCALP racers use 5 min
  (`LOWVAR_DERISK_AFTER_S`); the AGED racer uses 20 min (`DERISK_AFTER_S`) so it does NOT
  amputate the fat-tailed aged holds (p75 924m) the aged thesis rides.
- **`max_bites_per_token=2`** — bounds single-token concentration (the 34%-on-CASHCATWIF
  swing-source). Honest in-sample cost flagged for the confirm.
- **`exclusion_group="stable"`** — cross-sibling de-cluster: the three never pile the SAME
  token, so one rug can't hit all three at once (fleet stability; ~zero n-cost — the three
  triggers rarely overlap).
- **rug-gate / fast-liq-bail = REFERENCE** (shadow, forward-grading; enforce via prewarm on
  promotion). Not enforced inline (latency).

### Projected stability (parent + bite2 + floor@−15, the simulable proxy)

| racer | tripStd (parent→cap) | worst (→cap) | tokGrn | tokMed | cat | OOS green-rate (odd / even) |
|-------|---------------------:|-------------:|-------:|-------:|----:|-----------------------------|
| rh_stable_demand | 11.7 → **10.1** | −30.6 → −15 | 66.7% | **+5.37** | **0** | 66.7% / 60.0% (both ≥55) |
| rh_stable_deep | 23.0 → **~10** | −100.1 → −15 | 44-60% | −0.3..+3.7 | **0** | 20% / 75% (fragile) |
| rh_stable_ageddeep | 10.8 → 10.2 | −15.3 → −15 | 42.9% | −4.68 | 0 | 50% / 33% (thin n=7) |

**Against the STABILITY BAR** (tok-med ≥0, ≥55% tokens green, cat ≤5%, green in a majority
of OOS windows):
- **`rh_stable_demand` clears it directionally** — cat 0%, dispersion cut, tokMed +5.37
  (green), tokGreen 66.7%, and BOTH OOS halves' green-RATE ≥ the 55% floor. The strongest.
- **`rh_stable_deep`** — the cap delivers the biggest raw stability win (tripStd 23→~10,
  worst −100→−15, cat 0), but the bite-cap tips its token-median negative in-sample and its
  OOS token-green is fragile (20% / 75%). The floor-ONLY variant keeps it green (tokMed
  +3.74 / 60%); the bite-cap cost is a low-n artifact to watch at the confirm.
- **`rh_stable_ageddeep` does NOT clear the bar today (HONEST)** — n=11, 43% token-green, red
  median. Its instability is thin-n + a red median, which the tail-cap does NOT fix (it has
  no catastrophic tail to cap). The cap is forward insurance for when it eventually hits a
  deep-aged rug. It needs the most n before any read, and may fail the confirm.

### Pre-registered confirm (paper race seat, never a live seat)
Grade each at **n≥30 CLOSED positions** vs its PARENT as control. STABILITY bar (not a higher
ceiling): trip-return stdev DOWN vs parent AND catastrophic-token rate (<−20%) ≤5% AND
token-median NOT worse than parent AND green in a MAJORITY of OOS windows (odd/even by
DISTINCT token). FAIL = retire to the documented-kills list, no re-tune on the same tape.

### bot_ids to add to the scorecard / watch
`rh_stable_demand`, `rh_stable_deep`, `rh_stable_ageddeep`

## 4. Files touched (working tree, PAPER — no deploy, no push)
- `scripts/rh_paper_lane.py` — 3 `LaneBot`s appended to ROSTER (now 32 racers); no new gate
  logic (every knob — `hard_stop_pct`, `derisk_after_s`/`derisk_max_frac`,
  `max_bites_per_token`, `exclusion_group` — is already wired/tested).
- `tests/test_rh_paper_fleet.py` — two fleet-routing expectations updated: `rh_stable_ageddeep`
  legitimately enters on the test facts (dip −20 passes its −12 trigger; $65 buys pass default
  $50 demand; unknown age fails open on the 6h gate), so the expected entry set is 10, not 9.
  `rh_stable_demand` (needs $150 demand) and `rh_stable_deep` (needs a −25 dip) correctly
  stay out.
- `scratchpad/_rh_stable3_analyze.py` — the quantification script (read-only, local ledger).
- **Suites green:** test_rh_paper_lane + test_rh_paper_fleet + test_rh_factory_racers +
  test_rh_aged_racers + test_rh_rug_signals = **215 passed**; pre-live invariants + endpoint +
  fill_probe + experiment_scorecard = **99 passed**.

## Bottom line
The high-WR RH entries already bank small wins consistently; the volatility is the LEFT TAIL
(a few rug/big-loser tokens). The stability lever is a **hard downside cap** — floored at −15,
it cuts trip-return stdev (deep_only 23→10) and zeroes the catastrophic rate while win-rate,
token-median AND throughput HOLD, at zero latency. Baked into 3 stable racers on the proven
entries via `hard_stop −15 + derisk-to-25% + 2-bite + cross-sibling de-cluster`, with the
concentration rug-gate + fast-liq-bail referenced (shadow → prewarm on promotion). Honest:
`rh_stable_demand` clears the stability bar directionally; `rh_stable_deep` gets the biggest
raw swing-reduction but the bite-cap makes its token-median fragile at low n;
`rh_stable_ageddeep` does not clear the bar today (thin-n + red median, no tail to cap). All
DIRECTIONAL at n<30 — pre-registered to confirm at n≥30 vs parent on the stability bar.
