# Experiment Scorecard — build checkpoint

Goal: ONE read-only command that reads every pre-registered "grade at n>=X ex-top-2"
bar and reports PROMOTE / RETIRE / ACCRUING / NO-DATA. It FLAGS; AxiS/main promotes.

## Honest metric (the standard)
ex-top-2 token-median = group trips by token, per-token median return, DROP the 2
tokens with the highest per-token median, median of the remaining per-token medians.
GREEN = ex2 > 0 AND >=50% of tokens green. LIFETIME SUM BANNED as a verdict.
Per-cohort top-2 drop (each experiment drops its OWN 2 best), matching the task spec.

## Data sources (all local, pull-once, fail-soft)
- SOL trades: `_trades_cache.json` (sync via `scripts/sync_trades_cache.py --full`).
  buy->sell join by (bot_id,address) + entry_price match, SCRUB (drop ret>0 & hold<10s).
  Shadow stamps live in buy `entry_meta`: deep_capitulation_shadow ("DEEP"),
  deep_combo_shadow ("FAVOR"), aged_pond_absorb_shadow ("FAVOR"),
  deep_exit_spec_shadow ("BARBELL_*"), green_cohort, rug_gate_buy.
  Exit A/B bots: bot_id badday_young_exit_{control,minhold,barbell,heatrunner,minhold_heat}.
- RH ledger: `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (ev=buy|sell|rug_signals).
  Reconstruct closed trips per (bot_id,pool) split at fully==True. Control=rh_young_v1.
- Rug cohort: `scratchpad/rug_cohort_labels.jsonl` (built by scripts/rug_cohort_label.py).
- bs_ vs eth_getLogs: wrap `scratchpad/rh_blockscout/compare.py` (subprocess, capture).

## Enumerated bars (see _experiment_scorecard.md for full list)
SOL shadow: deep_capitulation, deep_combo, green_cohort, aged_pond_absorb,
  deep_exit_spec, variance/heat/min-hold counterfactuals, rug_gate/hidden_supply.
SOL A/B: badday_young_exit_* vs exit_control (n>=30).
RH: all racers vs rh_young_v1 (n>=30): deep/barbell/lowvar/f_*/aged/strength_trail.
bs compare, rug cohort labels.

## Status
- [x] enumerated all pre-reg bars from scratchpad/_*.md + configs
- [x] confirmed stamp keys land in entry_meta; RH ledger structure; rug labels
- [x] scripts/experiment_scorecard.py
- [x] tests/test_experiment_scorecard.py (pure ex-top-2 + verdict logic)
- [x] scratchpad/_experiment_scorecard.md (sample run + enumerated bars)

## Constraints honored
utf-8 on ALL writes. Pull once (reads local caches; --sync opt-in). READ-ONLY:
no config/live/promotion/commit. Fail-soft: any missing source => "no data yet".
