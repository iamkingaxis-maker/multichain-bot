# DEEP-cohort EXIT optimization — PROGRESS

Goal: optimal exit ladder for DEEP-capitulation entries (SOL pc_h1<=-45; RH dip<=-20),
maximizing realized capture of the flush-bounce minus giveback. Depth-conditional?
Barbell? Chain-specific? Ship RH racer (exclusion_group "deepexit") + SOL shadow spec.

## Data / method
- RH: real forward-tape replay over rh_history/sweep_logs (10.36M swaps) — rigorous
  continuation. Sweep 22 exit ladders on deep-dip (<=-20) entries, 4-half OOS.
  Harness: scratchpad/deep_exit/rh_deepexit_sweep.py (re-streams; ~10min).
- SOL: trip summary MFE(peak)/MAE only — peak is TRUNCATED by the live exit, so
  patient-trail upside is UNOBSERVABLE. Fast-harvest variants ARE testable (peak>=target
  => would fire). OHLC replay for the 54 deep tokens if warranted.

## Status
- [x] Read mines, RH factory harness, fill_fidelity pattern. Deep cohorts sized:
      SOL 269 legs/54 tokens (pc_h1<=-45), peak p75=15.3 p90=31.3 (fat tail present).
      RH deep-dip<=-20: 19,523 candidate entries in factory file.
- [~] RH deep-exit sweep RUNNING (~10M rows, ~10min) -> rh_deep_cands.jsonl.gz.
- [x] SOL position-level giveback + fast-harvest + barbell + OOS.
      FINDINGS (summary-stat replay, HONEST LIMITS):
      * DEEP(pc_h1<=-45): 196 pos/54 tok, realized med -5.85 mean +0.12, tokmed_ex2 -3.44,
        giveback mean +10.8pp. MFE p75 +15 p90 +29 (fat tail present).
      * Fast-harvest (sell100 @+3/+4): tokmed_ex2 -3.44->-2.19, wr 40->46% -- lifts the
        MEDIAN but MEAN collapses +0.12->-5.3 (caps the tail). LIVE ladder already banks
        gap-tail wins; from truncated MFE, fast/barbell CANNOT beat LIVE on expectancy.
      * Depth-band positive mean lives in <=-60 (fat bounces, MFE>=50 concentrate there)
        but is NOT OOS-robust (n=76, W1 -1.6/even +7.9 half-driven).
      * VERDICT: SOL summary data inconclusive on barbell>live expectancy (MFE truncation +
        live already caps tail). Fast-harvest = median/giveback win, tail cost. Shape
        deferred to RH real tape; SOL ships SHADOW spec for forward grading.
- [x] RH sweep #1 aggregated (33,557 deep-dip<=-20 entries, real forward tape).
      KEY FINDINGS:
      * Fat tail RISES with depth (MFE>=50: 30.4% @-20..-30 -> 38.9% @<=-45; p90 MFE
        +148 -> +260). Deep flushes bounce MORE and FATTER -> REFUTES "deeper->faster".
      * Two Pareto champions (dip<=-25): FAST5_all tokmed_ex2 +5.05/min+5.02 but mean
        -3.17 (clips the tail); PATIENT mean +0.49 (ONLY positive) tokmed +1.10/min+1.04.
      * Best-mean per band = patient/patient_wd ALL bands; best-tokmed = fast/wide-stop.
      * Time-box (tbox5) middling (tokmed -2.2): deep bounces do NOT die in 20min.
      * BARBELL (swept proxy, -15 runner stop) = mid-frontier: barbell8020 tokmed +2.76
        mean -2.10; barbell7030 +2.19/-1.74. Recovers tail-expectancy vs fast, tokmed cost.
- [x] SHIPPED: RH racer rh_deep_barbell (dip<=-25, tp1 5/.60, tp2 12/.10, moonbag .30
      breakeven-floor trail12, stop -15; exclusion_group "deepexit"). SOL shadow-tag
      deep_exit_spec_shadow (BARBELL_DEEP/VDEEP, runner grows with depth; measure-only).
      Tests: test_deep_exit_spec_shadow.py (4) + TestDeepBarbellRacer (3) + roster count
      -> 22->23. Suites: RH factory+fleet+deep+combo 98 passed; pre_live_invariants 8
      passed (exit 0). py_compile OK.
- [x] RH sweep #2 DONE (rh_moonbag_sweep.py, 26,881 dip<=-25 entries): EXACT house-money
      moonbag floor. SHIPPED mb_60_30_t12 = tokmed_ex2 +2.51 (min-half +2.33, GREEN 4/4),
      mean -1.18, med +4.53, wr 62%, cat 2.2% -> DOMINATES scalp (+1.93/-2.51) on BOTH
      axes; recovers +1.9pp expectancy vs fast5_all (+4.90/-3.09, tail-clipper). Floor
      beats -15-stop proxy (+2.19/-1.74). Runner-size = depth knob (mb_70_20 +3.06/-1.76;
      mb_50_35 +1.86/-0.82). Depth bands green 4/4; <=-45 mean rug-limited not exit-limited.
- [x] DELIVERABLE _deep_exit_optimization.md written. Racer comment updated to +2.51 number.
- [x] FINAL suites: RH factory+fleet+deep+pre_live 102 passed (pytest exit 0). py_compile OK.

## DONE. Optimal deep exit (per chain):
- RH deep (dip<=-25): BARBELL rh_deep_barbell = tp1 +5/0.60, tp2 +12/0.10, moonbag 0.30
  breakeven-floor + 12pp trail, stop -15. tokmed_ex2 +2.51 green 4/4; +0.6pp median /
  +1.3pp expectancy vs current scalp. NOT faster-harvest: the deep bounce tail rises with
  depth (p90 MFE +148->+260); fast harvest clips it. Barbell = house-money runner catches
  the tail free. exclusion_group "deepexit"; pre-registered n>=30 confirm bar.
- SOL deep (pc_h1<=-45): SHADOW ONLY. Summary MFE truncated -> runner unprovable; live
  already banks gap-tail. deep_exit_spec_shadow stamps BARBELL_DEEP(mb .25)/VDEEP(mb .35)
  for forward grading. No live-exit change (needs AxiS).
