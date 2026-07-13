# RH Winner Decode 2 — deep+demand, and 3 new racers (2026-07-13)

AxiS: the RH paper ledger now ACCUMULATES (append-mode fix landed today). Decode WHAT
`rh_demand_heavy` (+5.4 ex2, 75% green, n=12 tok) and `rh_deep_only` (+60% green, n=10
tok) do right, and design racers that push it. Everything else is red while n climbs to 30.

Data: local `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (= the accumulated ledger;
its per-bot token-median ex-top-2 now matches AxiS's railway read to within a snapshot).
Trips reconstructed with the scorecard's `load_rh_trips()` join (sells by (bot,pool), split
at `fully==True`, ret = Σpnl_usd / $25 × 100), 1970-ts test rows scrubbed. Scripts:
`scratchpad/_rh_decode2_analyze.py`, `_rh_decode2_analyze2.py`.

## ⚠️ This REVERSES the 07-12 snapshot call
`_rh_deep_decode.md` (07-12) called `rh_demand_heavy` the WORST racer (−$14.61) and flagged
it for the cut, concluding "demand-at-the-moment is non-separating / inverting." That was a
ONE-DAY railway snapshot. On the ACCUMULATED ledger `rh_demand_heavy` is now the BEST
ex-top-2 racer. The larger sample refutes the earlier call. **Do not cut demand_heavy.**

## Per-bot summary (accumulated ledger, trip-level)

| bot | nTrip | nTok | tokmed ex2 | retMed | green% | dipMed | exit mix (last leg) |
|-----|------:|-----:|-----------:|-------:|-------:|-------:|---------------------|
| **rh_demand_heavy** | 50 | 12 | **+8.54** | +6.02 | **70%** | −18.2 | TP2:19 TRAIL:18 BAIL:7 STOP:6 |
| **rh_deep_only** | 24 | 10 | −3.32 | +5.96 | **62%** | −28.1 | TP2:8 TRAIL:7 BAIL:6 STOP:2 |
| rh_moonbag | 63 | 13 | −4.52 | +5.16 | 60% | −18.4 | TRAIL:20 BAIL:17 MOON:18 STOP:8 |
| rh_aged_deep | 11 | 7 | −4.84 | +1.28 | 55% | −16.6 | TRAIL:6 BAIL:3 STOP:2 |
| rh_bites2 | 27 | 15 | −5.44 | −3.36 | 44% | −15.7 | BAIL:11 TP2:8 TRAIL:4 STOP:4 |
| rh_wide_ladder | 65 | 14 | −11.54 | −3.16 | 49% | −17.9 | BAIL:22 TRAIL:19 TP2:13 STOP:11 |
| **rh_young_v1** (control) | 124 | 27 | **−13.60** | +1.46 | 52% | −18.8 | TP2:41 BAIL:38 TRAIL:25 STOP:20 |
| rh_liq40 | 31 | 8 | −18.66 | −3.52 | 42% | −18.5 | BAIL:11 TP2:9 STOP:6 TRAIL:5 |

(bites2/deep_only look worse on ex2 than green-rate because ex-top-2 drops their 2 best
tokens at tiny n — see the OOS/fragility note.)

## Q1 — What the two greens do right: TWO independent, STACKING entry levers

The shared SCALP exit is **NOT** the lever — the red control `rh_young_v1` runs it verbatim
(TP1 +6/0.75, TP2 +12/0.25, −15 stop, 3pp trail) and is the worst ex2 in the fleet. What
separates the greens is pure ENTRY SELECTION, and it decomposes cleanly:

**Lever 1 — DEMAND confirmation ($150 buy-side floor).** This is a near-perfect controlled
comparison: `rh_demand_heavy` and `rh_young_v1` enter at the SAME median depth (−18.2 vs
−18.8), run the SAME exit, and differ in ONE knob — `demand_min_buy_usd` $150 vs $50.
Result: demand_heavy +8.54 ex2 / 70% green vs young_v1 −13.60 / 52%.
*Mechanism:* the $150 floor selects dips with real follow-through — demand_heavy's **TP2-reach
is 38% (19/50), the highest in the fleet** — instead of dead-cat knives that stall at TP1 and
fade. Raising the demand floor buys pools where the bounce actually completes.

**Lever 2 — DEPTH (−25 capitulation trigger).** Bot-INDEPENDENT: pooling all six scalp-exit
racers and bucketing by entry dip gives a monotonic gradient (deeper = greener):

| entry dip band | nTrip | nTok | ex2 | retMed | green% |
|----------------|------:|-----:|----:|-------:|-------:|
| **≤ −25 (deep)** | 73 | 17 | −1.60 | **+5.96** | **63%** |
| −18 .. −25 | 78 | 17 | −8.20 | +4.56 | 55% |
| −12 .. −18 | 121 | 23 | −10.32 | −1.28 | 48% |

Only the deepest band has a positive median return AND green>50%. `rh_deep_only`'s −25
trigger IS this lever; it beats the control's green-rate (62% vs 52%) on the same demand/exit.

**The levers STACK.** Within `rh_demand_heavy`, splitting by depth:
- deep subset (dip ≤ −18): **+8.42 ex2 / 76% green** (n=25)
- shallow subset (dip > −18): −2.04 ex2 / 64% green (n=25)

So deep + heavy-demand is the best cell we can observe. (Demand-$ is not stamped on buy rows,
so I can't retro-build a "deep AND heavy-demand" cohort from `young_v1` trips directly — the
within-demand_heavy depth split is the proxy for the interaction, and it's clean.)

## Q1b — What does NOT separate (checked, discarded)
- **Liquidity:** does not sort green from red — `rh_liq40` (min_liq 40k) is the WORST racer
  (−18.66). More liq is not the answer. Greens and reds both sit at ~$38k median liq.
- **Depth as a WITHIN-bot winner predictor:** among trades a bot already took, winners and
  losers have ~identical median dip (demand_heavy W −18.7 / L −17.8; deep_only W −28.2 / L
  −27.8). Depth is a TRIGGER-level lever (which trades get taken), not a within-cohort sorter.
- **Holder structure (top10 / n_holders from rug_signals):** the join is too sparse — the
  "winner" medians are byte-identical across three different bots (top10=45.88, holders=226),
  i.e. one dominant pool. Not trustworthy; not used.
- **Age:** not reliably stamped on buy rows (`age_h` mostly null). Not usable here.

## Q2 — HONESTY: this is DIRECTIONAL, low-n; ex-top-2 is fragile
- n is small: 10–12 distinct tokens per green racer. No lifetime-SUM verdicts.
- **OOS odd/even split (trips ordered by sell_ts):**
  - demand_heavy: ODD ex2 +7.86 / 76% green — EVEN ex2 **−1.06** / 64% green
  - deep_only: ODD ex2 +1.74 / 67% green — EVEN ex2 **−9.84** / 58% green
  The **ex-top-2 median FLIPS negative on one half for both greens** — it is NOT robust at
  this n. What SURVIVES the split is the **green-RATE (64–76%)** and the **retMed (~+6)**.
  So the confirm grade must lean on green-rate + retMed + tokmed together, not ex2 alone.
- Latency parity: every lever here (dip off the 10-min high, 30s buy-$ sum, 30s buy-print
  count) is computed from tape already in hand each tick — inside the ≤2s detect→fill budget.
  demand_heavy and deep_only already run live-feasibly; the new racers add no new data.

## New racers (added to ROSTER in `scripts/rh_paper_lane.py`, PAPER, not deployed)

All three keep the proven SCALP exit verbatim (LaneBot defaults: TP1 +6/0.75, TP2 +12/0.25,
−15 stop, 3pp trail, NO moonbag, NO time box), default liq $30k, max_pool_age 24h, and
`exclusion_group=None` like their scalp parents (demand_heavy/deep_only) so each accrues
INDEPENDENT n toward the n≥30 confirm bar fastest. No new gate logic — every knob is already
wired and tested (`dip_trigger_pct`, `demand_min_buy_usd`, `min_buys_30s`→`buys_breadth_block`,
`max_bites_per_token`, `derisk_after_s`/`derisk_max_frac`).

| bot_id | push | admission delta vs control | exit |
|--------|------|----------------------------|------|
| **`rh_deepdemand`** | combined stack (core) | dip **−25** + demand **$150** | scalp |
| **`rh_demand_broad`** | demand QUALITY | dip −12 + demand **$150** + **≥3 buy prints/30s** | scalp |
| **`rh_deepdemand_capped`** | tail-defended stack | dip −25 + demand $150 + **2-bite cap** + **5-min derisk→25%** | scalp |

- **`rh_deepdemand`** — the direct "push both proven levers together": deep_only's −25
  capitulation × demand_heavy's $150 floor. This is the primary deliverable of the decode.
- **`rh_demand_broad`** — pushes demand on a QUALITY axis: the $150 must be ≥3 distinct buy
  prints in the 30s window (real demand from multiple buyers), not a single whale print (the
  "one big buy = late-arc top" trap). Keeps the shallow −12 trigger so breadth, not depth, is
  the variable under test and throughput stays reasonable.
- **`rh_deepdemand_capped`** — `rh_deepdemand` + the variance mine's #1 lever (early
  catastrophe cap, 5-min derisk to 25%) + a 2-bite cap. Deep flushes carry the
  gap-through-stop / rug left tail (demand_heavy booked 6 HARD_STOP + 7 PRE_STOP_BAIL of 50);
  this tests whether flooring that tail lifts the FRAGILE ex-top-2 the OOS split exposed,
  without touching the green median.

**Throughput caveat:** the deep+demand cell fires ~1/5 the rate of demand_heavy (10 of its 50
trips were dip≤−25), so `rh_deepdemand`/`_capped` will take LONGER to reach n≥30 than the
parents. `rh_demand_broad` keeps the shallow trigger to stay faster.

### Pre-registered confirm bar (paper RACE seat, never a live seat)
Grade each at **n≥30 CLOSED positions** vs the scalp fleet as control, per-token medians
(ex-top-2) **and** green-rate, NEVER sums. CONFIRM = tokmed ex-top2 green (or clearly beats
`rh_young_v1`) AND green-rate ≥ its parent's AND cat ≤ 1/20 AND direction = deep/demand. FAIL
= retire to the documented-kills list, no re-tune on the same tape.

### bot_ids to add to the scorecard
`rh_deepdemand`, `rh_demand_broad`, `rh_deepdemand_capped`

## Files touched
- `scripts/rh_paper_lane.py` — 3 new `LaneBot`s appended to ROSTER (now 28 racers).
- `tests/test_rh_factory_racers.py` — exempt `rh_demand_broad` from the "factory gates off"
  invariant (it intentionally uses the breadth gate). Suite: 67 passed.
