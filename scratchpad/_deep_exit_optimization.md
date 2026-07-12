# Deep-cohort EXIT optimization — both chains (2026-07-12)

AxiS: deep-capitulation entries are the edge on both chains (SOL pc_h1<=-45,
`_sol_selection_mine.md`; RH deep-dip racers, `_rh_candidate_factory.md`). We buy
deep flushes. **What exit captures the bounce while minimizing giveback?** Is it
depth-conditional (deeper -> faster harvest, since round-trip risk rises with
depth)? Does the fat tail deserve a barbell?

## Method / rigor
- **RH — real forward tape (rigorous).** Re-streamed `rh_history/sweep_logs.jsonl.gz`
  (10.19M swaps, 07-01..11) through the factory exit harness restricted to DEEP
  entries (dip<=-20, then <=-25), sweeping 22 exit ladders x 3 depth bands x 4
  halves (`scratchpad/deep_exit/rh_deepexit_sweep.py`, 33,557 deep entries / 7,024
  pools), then re-ran the EXACT runtime house-money moonbag shapes on dip<=-25
  (`rh_moonbag_sweep.py`, 26,881 entries / 6,644 pools). Fill model verbatim from
  the factory: entry px*1.01, exit px*0.99, 0.2pp gas, next-observed-swap fills,
  TP legs capped +15pp, dead pools (never trade again) booked -90.
- **SOL — summary-stat replay (bounded, honest).** Reconstructed 718 young-lane
  POSITIONS (merged TP1/TP2/trail legs) from the full trades; 196 deep positions
  (pc_h1<=-45) / 54 tokens. `scratchpad/deep_exit/sol_*.py`.
- **HONEST LIMIT (memory: trail conclusions not trusted from replay).** SOL's
  observed MFE (`peak_pnl_pct`) is TRUNCATED by the live exit -> patient/runner
  upside is UNOBSERVABLE on SOL, and the live ladder already banks the gap-tail.
  So SOL can test FAST harvests but CANNOT prove a runner beats live. The runner
  verdict is carried by the RH real tape; SOL ships as a forward-grading shadow.
- OOS = a variant must hold its pool-median (ex-top-2, "tokmed_ex2") across ALL
  FOUR halves (chrono W1 07-01..05 / W2 07-06..11 x odd/even day). Reported:
  mean (expectancy, tail-sensitive), tokmed_ex2 (robust), min-half tokmed, cat<=-50%.

---

## 1. The deep-flush bounce tail RISES with depth (both chains) — the crux

RH deep entries, MFE (peak favorable excursion, real tape) by dip band:

| dip band  | n     | % reaching MFE>=+50 | MFE median | MFE p90   |
|-----------|-------|---------------------|------------|-----------|
| -20..-30  | 22,725| 30.4%               | +23.8      | +148.2    |
| -30..-45  | 6,512 | 35.5%               | +28.6      | +169.7    |
| **<=-45** | 4,320 | **38.9%**           | +28.4      | **+259.8**|

Deeper flush -> MORE frequent AND fatter bounces (p90 +148 -> +260). SOL agrees
directionally (deep-cohort MFE p75 +15 / p90 +29; fat MFE>=50 concentrates in the
deepest <=-60 band) but the SOL tail is far THINNER than RH's.

**This REFUTES the prior "deeper -> faster harvest" hypothesis for EXPECTANCY.**
Round-trip/giveback risk does rise with depth (giveback mean +10.8pp on the SOL
deep cohort) — but bounce MAGNITUDE rises faster. Harvesting the deep flush fast
throws away the very tail that is the reason to buy it.

---

## 2. RH deep cohort (dip<=-25): the exit-variant frontier — REAL TAPE

Two metrics tell opposite stories (26,881 entries, house-money-moonbag run):

| exit variant            | mean  | med   | tokmed_ex2 | min-4half | wr% | cat% |
|-------------------------|-------|-------|-----------|-----------|-----|------|
| patient (ride 1/3)      | +0.06 | +2.20 | +0.15     | +0.14     | 58  | 2.4  |
| **mb_50_35_t15** (big run)| -0.82 | +4.09 | +1.86     | +1.68     | 62  | 2.2  |
| aged (tp1 6/.5 trail10) | -1.16 | +2.57 | +0.24     | +0.10     | 59  | 2.2  |
| **mb_60_30_t12 = SHIPPED**| **-1.18**|+4.53| **+2.51** | **+2.33** | 62  | 2.2  |
| mb_60_30_t15            | -1.19 | +4.37 | +2.33     | +2.16     | 62  | 2.2  |
| mb_70_20_t12 (small run)| -1.76 | +4.95 | +3.06     | +2.89     | 63  | 2.2  |
| scalp (tp1 6/.75 trail3)| -2.51 | +5.87 | +1.93     | +1.42     | 62  | 2.2  |
| fast5_all (sell all +5) | -3.09 | +5.36 | +4.90     | +4.85     | 64  | 2.2  |

- **fast harvest maximizes the robust median** (fast5_all tokmed +4.90) but has the
  **WORST expectancy** (mean -3.09) — it caps every trade at +5 and discards the
  +150..+260 tail. Winner-CLIPPING; violates the memory's winner-preservation rule.
- **patient maximizes expectancy** (the ONLY positive mean, +0.06) but the weakest
  robust median (tokmed +0.15) and highest per-trade variance.
- **The BARBELL (house-money moonbag) is the synthesis and DOMINATES the current
  scalp exit on BOTH axes**: mb_60_30_t12 tokmed +2.51 (vs scalp +1.93) AND mean
  -1.18 (vs scalp -2.51). It harvests 60% fast @ +5 (locks the green median),
  books 10% @ +12, and rides a 30% runner with a **breakeven floor** (house money:
  after TP2 the runner cannot lose) + a 12pp trail for the fat tail.
- The house-money floor is what makes it dominate: vs a -15-stop runner (sweep #1)
  the floor lifted BOTH mean (-1.74 -> -1.18) and tokmed (+2.19 -> +2.51). The
  shipped shape's real number is therefore BETTER than the first-pass proxy, and
  is a conservative lower bound on the live moonbag (whose runner rides live quotes
  past where the tape sample ended).
- **Runner-size is the depth knob**: bigger runner (mb_50_35) -> more expectancy
  (mean -0.82), less robust median (tokmed +1.86); smaller runner (mb_70_20) ->
  the reverse (+3.06 / -1.76). Runner grows with depth = capture more tail.
- **Time-box did NOT help** (tbox5_10m/5m tokmed -2.2 in sweep #1). Deep-flush
  bounces do NOT reliably die in 20 min — boxing them clips continuation. The
  "RH pops die in 20 min -> harvest faster" intuition is REFUTED for deep flushes.

### Depth-conditional (shipped mb_60_30_t12, by band)

| dip band | n      | mean  | tokmed_ex2 |
|----------|--------|-------|-----------|
| -25..-30 | 14,589 | -1.15 | +3.19     |
| -30..-45 | 7,994  | -0.02 | **+4.12** |
| <=-45    | 4,298  | -3.44 | +2.62     |

The barbell is green 4/4 in every band. The mean is best in -30..-45 (~breakeven)
and drags in <=-45 — the deepest flushes carry the fattest bounce tail AND the
heaviest RUG tail (more capitulation = more abandonment; cat/dead drag the mean
even as the median trade wins). So: **depth-conditional runner-sizing is real for
tail-capture, but the deepest band's mean is rug-limited, not exit-limited.**

---

## 3. SOL deep cohort (pc_h1<=-45): fast-harvest lifts the median, caps the tail

196 positions / 54 tokens. realized med -5.85, mean +0.12, tokmed_ex2 -3.44,
giveback mean +10.8pp, MFE p75 +15 / p90 +29.

| exit (SOL replay)         | med   | mean  | wr% | tokmed_ex2 |
|---------------------------|-------|-------|-----|-----------|
| LIVE (current ladder)     | -5.85 | +0.12 | 40  | -3.44     |
| fast harvest @ +3 (sell 100)| -5.12 | -5.34 | 46  | -2.19     |
| fast harvest @ +4         | -5.17 | -5.03 | 45  | -2.19     |

- Fast harvest **lifts the robust median** (tokmed -3.44 -> -2.19, wr 40 -> 46%)
  but **collapses the mean** (+0.12 -> -5.3): the SOL deep cohort's entire positive
  expectancy is carried by the gap-tail, which the LIVE ladder already banks (TP2
  sells the gappers at +50/+70). From truncated MFE, **neither fast harvest nor a
  barbell can beat LIVE on expectancy** — the summary data is INCONCLUSIVE on the
  runner (it cannot see past the live exit).
- Depth-band mean is NOT OOS-robust: <=-60 ALL +3.15 but W1 -1.6 / even +7.9
  (n=76, half-driven by 2-4 fat winners). The SOL fat tail (MFE>=50) does
  concentrate in <=-60 (6%), consistent with RH's depth->tail law.

**SOL verdict:** direction AGREES with RH (the book is tail-carried; do not
fast-harvest the whole deep flush), but the SOL tail is thinner and the runner's
value is unprovable from summary tape. Ship SOL as a SHADOW spec for forward
grading, not a live change.

---

## 4. Answers

**Q1 — optimal TP1/frac/trail/time-box, and is it depth-conditional?**
The optimum is a **BARBELL, not a faster harvest**, and it is depth-conditional in
the OPPOSITE direction to the prior: deeper flush -> FATTER tail -> BIGGER runner /
MORE patience (round-trip risk rises, but bounce magnitude rises faster). On RH
real tape the shipped shape (harvest 60% @ +5, 10% @ +12, 30% house-money runner
w/ breakeven floor + 12pp trail, stop -15) DOMINATES the current scalp exit on both
robust median (+2.51 vs +1.93) and expectancy (-1.18 vs -2.51). Time-box HURTS
(deep bounces don't die in 20 min).

**Q2 — does the fat tail live in a sub-band deserving a patient trail (barbell)?**
YES to the barbell, but the tail is NOT confined to a narrow sub-band — it is
present across the WHOLE deep cohort (30-39% of entries reach MFE>=50) and rises
with depth. So the answer is not "isolate a sub-band and trail it" but "give EVERY
deep entry a house-money runner, sized up with depth." The breakeven floor makes
the runner ~free (it cannot give back below breakeven after TP2), so the barbell
keeps the robust median green while recovering the tail expectancy fast harvest
discards (+1.9pp mean vs fast5_all).

**Q3 — cross-chain: same shape, or chain-specific?**
Same DIRECTION (tail-carried; keep a runner; don't fast-harvest deep flushes),
chain-specific MAGNITUDE. RH's tail is far fatter (p90 +260 vs SOL +29), so RH
warrants a real 30% runner with wide trail; the "RH pops die fast -> harvest
faster" hypothesis is REFUTED for deep flushes. SOL's tail is thinner and the
runner is unprovable from truncated summary tape, so SOL ships shadow-only.

**Barbell verdict:** WARRANTED on RH (dominates scalp on both axes, real tape,
green 4/4). Directionally warranted on SOL but unproven from available data ->
forward-grade via shadow.

---

## 5. Recommended deep-entry exit ladders (per chain)

**RH deep (dip<=-25) — SHIP as paper racer `rh_deep_barbell`, exclusion_group
"deepexit":**
- tp1 +5 sell 0.60  |  tp2 +12 sell down-to-moonbag (0.10)  |  moonbag 0.30
  breakeven-floored (0%) + 12pp trail  |  hard stop -15.
- Backtest (real tape, house-money floor): tokmed_ex2 **+2.51** (min-half +2.33,
  GREEN 4/4), mean -1.18, med +4.53, wr 62%, cat 2.2%. Beats scalp on BOTH axes;
  recovers +1.9pp expectancy vs fast harvest.
- Pre-registered CONFIRM bar (backtest earns a race seat, never a live seat):
  n>=30 closes, tokmed ex-top2 green, cat<=1/20, direction = median-green + a
  fat-tail lift over a pure scalp control. Fail -> retires to kills.

**SOL deep (pc_h1<=-45) — SHADOW spec `deep_exit_spec_shadow` (measure-only, live
exit UNCHANGED; forward-grading hypothesis):**
- band `deep` (-45..-60): tp1 +5/0.60, tp2 +12, moonbag 0.25 breakeven + 12pp trail, stop -15.
- band `vdeep` (<=-60): tp1 +5/0.50, tp2 +12, moonbag 0.35 breakeven + 15pp trail, stop -15
  (bigger runner = the fattest bounces live here).
- Grade forward on the realized join; do NOT touch the SOL live ladder without AxiS.

## 6. Expected capture gain
- **RH:** the deep-cohort exit moves from scalp (tokmed +1.93 / mean -2.51) to the
  barbell (tokmed **+2.51** / mean **-1.18**) — **+0.6pp robust-median and +1.3pp
  expectancy per deep trade**, winner-preserving (keeps the +150..+260 tail that
  fast harvest clips). Green in all four halves.
- **SOL:** no honest gain claimable from summary tape (live already banks the
  gap-tail; MFE truncation blinds the runner). Fast harvest is a median/giveback
  win (+1.25pp tokmed) but an expectancy LOSS — so NOT recommended. The barbell
  shadow tests whether the RH runner edge ports; enforce only on forward green.

## Wired (working tree, NO commits)
- `scripts/rh_paper_lane.py`: racer `rh_deep_barbell` (roster 22->23, exclusion_group
  "deepexit"). `feeds/dip_scanner.py`: `deep_exit_spec_shadow` stamp (measure-only).
- Tests: `tests/test_deep_exit_spec_shadow.py` (4), `TestDeepBarbellRacer` in
  `tests/test_rh_factory_racers.py` (3), roster-count 22->23 in `test_rh_paper_fleet.py`.
  Suites: RH factory+fleet+deep+combo **98 passed**; pre_live_invariants **8 passed**
  (pytest exit 0). py_compile OK.
- Harness/data: `scratchpad/deep_exit/{rh_deepexit_sweep.py, rh_moonbag_sweep.py,
  rh_aggregate.py, sol_deep_analysis.py, sol_barbell_oos.py, *_cands.jsonl.gz}`.
