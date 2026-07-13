# SOL Young-Lane CAPTURE-Leak + Exit Ladder (2026-07-13)

**Brief:** the fleet is red because ~60% of young tokens bleed to a small loss and we under-capture
the ~40% that reach TP1. The one green-ish bot (`badday_young_absorb`) wins by riding upside. Task:
quantify the capture leak (peak_pnl_pct MFE vs pnl_pct captured), isolate what absorb does
differently, propose the best OOS-surviving capture ladder — ADDITIVE to the already-deployed exit
A/B family (`badday_young_exit_{control,minhold,barbell,heatrunner,minhold_heat}`), no live touch.

**Data.** `_trades_cache.json` is a RECENT snapshot (07-11..07-13 only, 1137 badday sell LEGS;
sell rows are per-LEG, not per-trip). The blessed per-trip tape `scratchpad/sol_selection/_trips.json`
(955 young-lane trips, 07-02..07-12, ret/peak/mae/hold) is used for the FOUR-HALF OOS (W1/W2 ×
odd/even, ex-top-2 token-median). Cache legs → capture-leak diagnosis + shadow attribution; trips →
OOS. SCRUB RULE and ex-top-2 (group by token, median per token, drop 2 highest-count, median rest)
enforced throughout. Scripts: `scratchpad/cap_analysis.py`, `cap_shadow.py`, `cap_detail.py`,
`cap_oos_replay.py`.

---

## 1. Capture-leak quantified (277 reconstructed positions, 07-11..13)

Reconstructed positions from legs (group by bot+address+peak; blend legs by inferred fractions).

- **40% of positions reach +6 (winners); 60% never reach TP1** (the loser cohort — min-hold
  territory, not a capture problem).
- **Winner capture efficiency = pnl/peak: median 0.82, mean 0.65.**
- **MFE left on the table (peak − captured): median +3.1pp, mean +7.4pp.**

| MFE bucket | n | med peak | med captured | capture-eff | med leak |
|---|---|---|---|---|---|
| **[6,12)**  | 28 | 7.5 | **+2.8** | **0.41** | +5.3pp |
| [12,18) | 28 | 15.0 | +11.2 | 0.83 | +2.2pp |
| [18,30) | 35 | 23.8 | +19.2 | 0.88 | +2.3pp |
| **[30+)** | 20 | 50.2 | +37.7 | 0.84 | **+8.9pp** |

**The leak is concentrated in two places, and neither is TP1:**

1. **[6,12) small winners — capture-eff 0.41.** TP1 banks 75% at +6 (reliable), but the **25%
   remainder round-trips to −9..−13** and drags the blend to ~0 or negative. Real legs:
   `peak 6.5 → [TP1 +3.9, trail −12.9] = −0.3`; `peak 7.4 → [TP1 +5.8, trail −9.3] = +2.1`.
2. **[30+) monsters — mean leak 8.9pp.** Most capture cleanly (peak 108→+104.8, 99→+96.2), but a
   few remainders catastrophically reverse: `peak 76.1 → [TP2 +56, hard −48] = +3.9`;
   `peak 52.9 → [trail −9.9]`; `peak 49.4 → [TP1 +44, TP2 +32, moonbag −21.3] = +34.8`.

### Where's the biggest leak? (shadow attribution)

- **NOT TP1 too early.** TP1 is the reliable money. High-runner-score positions (rscore 0.7-1.0)
  capture 22 of a 26 median peak (leak only 2.2pp). Raising TP1 loses (prior overhaul, confirmed).
- **NOT runner-not-armed.** `runner_score` cleanly identifies the big movers and they capture fine.
- **IT'S THE POST-TP1 REMAINDER + STALE TRAIL.** The in-code breakeven-lock is guarded to
  `not p.tp1_hit` (`core/per_bot_position_manager.py:769`) — so **once TP1 fires, the 25% remainder
  has NO breakeven protection** and rides the plain `trail_pp=2` / `hard_stop=-12` down.
  `trail_reprice_shadow` (a peak-tracking trail) recovers **+2.7pp median / +3.5pp mean** overall,
  and **+7..+10pp on the [0,12) small winners** where the live trail exits at a loss (live_med −0.7,
  reprice_med +4.9). `giveback_shadow` / `never_runner` fire only on LOSERS (0 winners) — not a
  winner leak.

---

## 2. Absorb vs the losing siblings — the upside edge is ENTRY, not exit

`badday_young_absorb` and `badday_young_rt_paper` have **byte-identical exit ladders**
(6/.75, 12/.25, trail 2pp). So absorb's upside edge is **selection**, not exit shape:

| bot | n | reach +6 | reach +30 | med peak | med ret | winner capture-eff |
|---|---|---|---|---|---|---|
| **badday_young_absorb** | 208 | **42%** | 7.2% | +2.8 | −0.7 | 0.83 |
| badday_young_rt (live) | 62 | 47% | 11.3% | +4.8 | +3.5 | 0.72 |
| badday_young_rt_paper | 48 | **25%** | 2.1% | 0.0 | −7.2 | 0.72 |
| badday_young_pump_dip_ab | 329 | 26% | 3.0% | 0.0 | −6.3 | 0.88 |
| **badday_young_moonbag_ab** | 48 | 44% | **16.7%** | +4.0 | **−7.7** | **0.05** |

- **absorb reaches +6 on 42% vs rt_paper's 25%.** rt_paper/pump_dip add
  `filter_knife_catch_peak` + `retrace_micro_avoid` — those entry filters **cut the runners**. An
  exit-ladder change alone will NOT equalize absorb vs its siblings; the runner-reach gap is entry.
- **`moonbag_ab` is the fat-tail cautionary tale:** it reaches +30 the MOST (16.7%) yet has the
  WORST median (−7.7) and capture-eff 0.05 — **a wide moonbag catches the tail but BLEEDS the
  median** (its bag round-trips: cache leg `moonbag −21.3`). This is the adolescent/adaptsize
  trap in live form: promote nothing that fattens the bag without protecting it.

---

## 3. FOUR-HALF OOS — what actually survives on ex-top-2 (955 trips)

| variant | mean | ex2 | W1 | W2 | odd | even | cat% | verdict |
|---|---|---|---|---|---|---|---|---|
| control (6/.75, 12/.25, 2pp) | −0.55 | **−5.69** | −2.20 | −6.41 | −4.41 | −6.57 | 1.5 | baseline |
| pure remainder breakeven-lock | −0.52 | −5.69 | −2.20 | −6.41 | −4.41 | −6.57 | 1.5 | **0/4 — fails** |
| min-hold floor 120s | +2.34 | +3.50 | +4.27 | +1.78 | +3.80 | +1.43 | 1.0 | GREEN 4/4 |
| **tight-trail barbell + min-hold** | **+3.37** | **+3.53** | +4.36 | +1.78 | +3.86 | +1.43 | 1.0 | **GREEN 4/4** |

**The disciplined finding (fat-tail trap respected): a PURE capture change does NOT move the robust
ex-top-2 median** — it is set by the loser cohort (60% never reach +6), which capture ladders don't
touch. A pure post-TP1 breakeven-lock improves ex2 in **0/4 halves** and touches only 4% of winners
on the full tape (the 2.5-day cache OVERSTATED the remainder leak — regime-specific chop). The
`trail_reprice` capture is median-positive across every split but **mean-fragile** (flips negative
on 07-12 / odd-second) and only 2.5-days observable → **forward-grade, do not promote alone.**

**The ONLY lever that moves the mandated median is the min-hold 120s floor** (already deployed).
So the honest additive play is to pair the proven ex2 mover (min-hold) with a **SAFE-direction
capture add** (tight peak-tracked runner) — the ex2 bar is cleared by min-hold; the capture add is
carried on the winner cohort as an MFE-truncated LOWER bound, forward-confirmed.

---

## 4. Proposed capture ladder — `badday_young_capture_ab` (paper A/B, shipped to working tree)

Additive to the deployed family (which lifts the TP2 *target*: heatrunner +12→+18; or fattens the
bag: barbell 0.30 @ 12pp trail). **This variant instead PROTECTS + tightly harvests the runner
remainder** — the exact leak in §1:

| leg | config | rationale |
|---|---|---|
| TP1 | +6 / **0.60** | the reliable scalp — keep most of it |
| TP2 | +12 / (remainder − moonbag ≈ 0.10) | small bank at +12 |
| **moonbag** | **0.30**, floor **0%**, trail **3pp** | breakeven-lock (can NEVER round-trip to a loss, fixes the [6,12) −13 leak) + TIGHT peak-track (harvests near the peak = the trail_reprice +2.7pp median; 3pp = winners' ~2.6% median give-back) |
| **min-hold floor** | **120s**, rug −25% | the proven ex2 mover (loser cohort), GREEN 4/4 |
| trail / stop | 2pp / −12 (unchanged); strength_trail off | |

- **vs deployed barbell:** identical split, but **3pp moonbag trail instead of 12pp** (harvest the
  runner near its peak instead of giving back 12pp) + adds the min-hold floor. Directly fixes the
  moonbag_ab/barbell bleed (peak 49→moonbag −21).
- **vs deployed minhold_heat:** heat lifts the TP2 *target* (widens exposure, needs a live hot
  regime); this **tightens the remainder trail + breakeven-locks it** (narrows the give-back).
  Opposite risk profile, complementary mechanism.
- **Replay:** ex2 **+3.53 GREEN 4/4** (min-half +1.43), mean **+3.37**, cat **1.0%** — improves ex2
  vs control in **4/4 halves**. ex2 lift is min-hold's; the tight-trail moonbag adds ~+1pp mean over
  plain min-hold (a lower bound). NO code change — all knobs (`moonbag_*`, `min_hold_floor_*`) exist
  and are tested.

**Shipped:** `config/bots/badday_young_capture_ab.json`. Entry **byte-identical** to
`badday_young_rt_paper` (verified: 0 entry-field diffs; only exit knobs differ). `live_probe=false`,
`base_position_usd=25.0`, own `exclusion_pool`. Loads clean; full 157-config fleet still loads. NOT
deployed / committed / pushed — working tree only.

### Pre-registered forward grade
n≥30 closes vs `badday_young_exit_control`, ex-top-2 token-median AND captured-pp, OOS ≥3/4 halves,
cat ≤1/20. **Winner = higher ex2 (carried by min-hold) AND higher captured-pp than plain
`badday_young_exit_minhold` (the capture add must pay for itself on the winner cohort).** The
moonbag/trail leg is a replay LOWER bound → the paper bot proves the capture add; min-hold's ex2 is
already OOS-green. Promote to live ONLY on forward-green + AxiS go.

## 5. Open lever (not shipped — flagged for AxiS)
The largest untapped upside is **ENTRY**: absorb reaches +6 on 42% vs the filtered siblings' 25-26%
because `filter_knife_catch_peak` + `retrace_micro_avoid` cut runners. Relaxing those on a paper
twin is a separate (entry) experiment with more median headroom than any exit change — capture
ladders can only harvest the runners we already reach.
