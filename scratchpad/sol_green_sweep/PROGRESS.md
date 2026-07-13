# SOL Green Cohort Sweep — PROGRESS

## Task
2-axis entry-meta sweep for GREEN ex-top-2 cohorts, ranked by VOLUME-SHARE.
Beat/augment deep(pc_h1<=-45)+liq>=30k (green +4.6 ex2, ~19% vol).

## Metric (banked)
- GREEN = ex-top-2 token-median > 0 AND >=50% tokens green
- OOS: green in >=3/4 halves (CH1/CH2 x ODD/EVEN)
- n>=15 distinct tokens or UNDERPOWERED
- report p90 (winner-preserving)
- RANK green cells by volume-share

## Status
- [x] Data confirmed: 955 trips, 07-02..07-12, entry_meta present
- [x] Baseline + axis coverage inspected
- [ ] 2-axis sweep script built
- [ ] Green cells ranked by volume-share
- [ ] OOS 4-half on top cells
- [ ] SHADOW-stamp top 1-2 in dip_scanner.py (OFF)
- [ ] Tests + deliverable md

## Notes

## RESULTS (2-axis sweep, 624 cells, 22 green)
deep+liq is the single highest-vol green cell (19.1%). Real lever = ORTHOGONAL
green cells whose UNION with base expands volume while staying green.

### Top higher-volume cohorts (UNION with deep+liq base):
1. liq>=45k & bs_h1>=1.6  -> UNION vol 28.1% @ ex2med +4.9 (grn 61%, n=41, p90 +31.2), 4/4 halves.
   EDGE-PRESERVING (+4.6 -> +4.9). Incremental slice standalone +2.1 / 4/4 (orthogonal, genuine new green vol).
   Caveat: incremental CH2/EVEN thin (2,1 tok); union halves well-powered (32,9,31,12).
2. liq>=35k & unique_buyers_n>=50 -> UNION vol 30.7% @ ex2med +2.5 (grn 55%, n=58, p90 +31.2), 4/4 halves.
   MAX VOLUME (+11.6pp) but DILUTES edge (+4.6->+2.5). Incremental alone marginal (-1.4/1/4).

Both lift OOS 3/4 -> 4/4 and preserve p90 (~32.5 -> 31.2, no winner-clip).

## TODO
- [x] sweep + union done
- [ ] SHADOW-stamp cohort A (liq>=45k & bsh1>=1.6) + B (liq>=35k & ubuy>=50) in dip_scanner.py, OFF
- [ ] pure tests + suites
- [ ] deliverable md

## DONE 2026-07-12
- [x] 2-axis sweep (624 cells, 22 green) + union/orthogonality analysis
- [x] core/bot_evaluator.py green_cohort_membership() pure fn
- [x] feeds/dip_scanner.py GREEN-COHORT shadow stamp (GREEN_COHORT_MODE=shadow, enforce OFF)
- [x] tests/test_green_cohort_membership.py (8 pass); evaluator suite 107 pass
- [x] deliverable scratchpad/_sol_green_cohort_sweep.md

## SHIP SUMMARY
- Cohort A liq>=45k & bs_h1>=1.6: UNION w/ base = 28.1% vol @ +4.9 ex2 (edge-preserving, 4/4) — TOP PICK
- Cohort B liq>=35k & ubuy>=50: UNION w/ base = 30.7% vol @ +2.5 ex2 (max vol, dilutes edge, 4/4)
- deep+liq stays highest-edge single cell; both A/B lift OOS 3/4->4/4, p90 preserved (~31)
