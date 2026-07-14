# STATE OF THE EXPERIMENTS — scorecard

_Generated 2026-07-14 04:57 UTC · READ-ONLY (flags only; AxiS/main promotes)_

**Metric:** ex-top-2 token-median (per-token median, drop each cohort's 2 best tokens, median of the rest). GREEN = ex2 > 0 AND >=50% tokens green. Lifetime sum BANNED.

**Verdicts:** PROMOTE (bar met) · RETIRE (n>=bar, clearly failed) · MIXED (n>=bar, one criterion) · ACCRUING (n<bar) · NO-DATA.


## Data sources

- SOL trades: 16202 trips, newest 2026-07-13T02:57:39
- RH paper: 457 closed trips, newest 2026-07-12T14:14:31
- rug cohort: 198 labeled mints
- bs_ compare: ran (see section below)

## Ranked table

| verdict | chain | experiment | n_tok | ex2-med | %grn | bar | vs ctrl | pre-reg |
|---|---|---|---:|---:|---:|---:|---:|---|
| BASELINE · | SOL | `rug_gate_buy` | 55 | -6.4 | +10.9 | 30 | — | rug_cohort_labels.jsonl |
| accruing … | RH | `rh_young_v1` | 27 | -5.9 | +40.7 | 30 | — | rh_paper_lane.py |
| accruing … | RH | `rh_bites2` | 15 | -3.3 | +40.0 | 30 | +2.6 | rh_paper_lane.py |
| accruing … | RH | `rh_first_touch` | 15 | -3.4 | +46.7 | 30 | +2.5 | rh_paper_lane.py |
| accruing … | RH | `rh_wide_ladder` | 14 | -4.0 | +42.9 | 30 | +1.9 | rh_paper_lane.py |
| accruing … | RH | `rh_moonbag` | 13 | -3.6 | +38.5 | 30 | +2.3 | rh_paper_lane.py |
| accruing … | RH | `rh_aged_hold` | 12 | -3.6 | +33.3 | 30 | +2.3 | rh_paper_lane.py |
| accruing … | RH | `rh_demand_heavy` | 12 | +5.4 | +75.0 | 30 | +11.3 | rh_paper_lane.py |
| accruing … | RH | `rh_aged_derisk` | 10 | -5.3 | +40.0 | 30 | +0.6 | rh_paper_lane.py |
| accruing … | RH | `rh_deep_only` | 10 | -0.4 | +60.0 | 30 | +5.5 | rh_paper_lane.py |
| accruing … | RH | `rh_liq40` | 8 | -5.1 | +50.0 | 30 | +0.8 | rh_paper_lane.py |
| accruing … | RH | `rh_aged_deep` | 7 | -4.8 | +42.9 | 30 | +1.1 | rh_paper_lane.py |
| accruing … | SOL | `aged_pond_absorb_shadow` | 1 | -5.2 | +0.0 | 20 | — | _sol_aged_pond_mine.md |
| no data — | SOL | `deep_capitulation_shadow` | 0 | — | — | 20 | — | _sol_deep_gate.md |
| no data — | SOL | `deep_combo_shadow` | 0 | — | — | 20 | — | _sol_deep_gate.md |
| no data — | SOL | `green_cohort_membership` | 0 | — | — | 15 | — | _sol_green_cohort_sweep.md |
| no data — | SOL | `deep_exit_spec_shadow` | 0 | — | — | 30 | — | _deep_exit_optimization.md |
| no data — | SOL | `bleed_cut_would_cut` | 0 | — | — | 30 | — | _sol_bleed_detector_0713.md |
| no data — | SOL-A/B | `badday_young_exit_control` | 0 | — | — | 30 | — | _sol_exit_overhaul.md |
| no data — | SOL-A/B | `badday_young_exit_minhold` | 0 | — | — | 30 | — | _sol_exit_overhaul.md |
| no data — | SOL-A/B | `badday_young_exit_barbell` | 0 | — | — | 30 | — | _sol_exit_overhaul.md |
| no data — | SOL-A/B | `badday_young_exit_heatrunner` | 0 | — | — | 30 | — | _sol_exit_overhaul.md |
| no data — | SOL-A/B | `badday_young_exit_minhold_heat` | 0 | — | — | 30 | — | _sol_exit_overhaul.md |

## Rug cohort (labeled forward; definitional grade)

- 198 labeled mints
- composition: {'alive': 104, 'catastrophic': 60, 'dead': 34}
- feature separation (median cat vs alive):
    - lp_locked_pct: cat=100.00(n=59) alive=100.00(n=102)
    - rugcheck_score: cat=1.00(n=59) alive=1.00(n=102)
    - top10_holder_pct: cat=31.61(n=52) alive=59.16(n=102)
    - top1_holder_pct: cat=11.68(n=52) alive=20.69(n=102)

## bs_ vs eth_getLogs (graduation grader wrap)

```
rug_signals rows: 112   with BOTH sources: 0   tol=+-3.0pp

no dual-source rows yet — accrue stamps from a live lane session.
```

## Legend / next

- PROMOTE flags cleared their bar — bring to AxiS with the pre-reg file for the live/enforce decision (this tool never promotes).
- ACCRUING/NO-DATA is expected: forward tape just started; most shadow stamps and paper A/B bots have not reached their n bar.
- Re-run after each `sync_trades_cache.py --full` — idempotent.
