# SOL Exit-Ladder Overhaul — PROGRESS

Task: overhaul the SOL young-lane EXIT ladder. Build min-hold floor + hot-market
exit variants as PAPER A/B bots. Replay to RANK, confirm forward. EXIT-only, no
sizing, no live enforce, no commits, utf-8 writes.

## Status: COMPLETE

### Done
- [x] Read findings: _sol_winner_behavior.md, _deep_exit_optimization.md, _sol_hot_market.md
- [x] Mapped exit machinery (tick state machine, BotConfig); confirmed barbell(moonbag) exists, min_hold NEW
- [x] Replay harness exit_replay.py -> reproduces bucket table, ranks variants (_replay_out.txt)
- [x] min_hold_floor: bot_evaluator helpers + BotConfig fields + tick() suppression + rug tripwire
- [x] heat_regime.py (NEW) + regime_runner_lift/tp2_pct_hot fields + TP2 lift + dip_scanner fleet feed
- [x] tests/test_min_hold_floor.py (24 passed); regression suites 118 + 18 passed
- [x] 5 paper A/B bots wired (control + minhold + barbell + heatrunner + minhold_heat)
- [x] Deliverable scratchpad/_sol_exit_overhaul.md

### Key result
- TP-side variants (barbell/heatrunner) lift MEAN (+0.4..+0.8pp) but ex-top-2 median UNCHANGED
  (-5.69) -> ex2 is set by the loser cohort. Runner legs are LOWER bounds (MFE truncated).
- min_hold_floor is the ONLY lever that moves ex-top-2 median: -5.8 -> +2.9..+4.5, GREEN 4/4.
- COMBINED (min_hold + heat-runner) = recommended: ex2 +3.49 conservative (min-half +1.43,
  GREEN 4/4), mean +2.76, cat 1.0%. Both axes move.

### Files touched (working tree, NO commits)
- core/bot_config.py, core/bot_evaluator.py, core/heat_regime.py (NEW),
  core/per_bot_position_manager.py, feeds/dip_scanner.py
- config/bots/badday_young_exit_{control,minhold,barbell,heatrunner,minhold_heat}.json (NEW)
- tests/test_min_hold_floor.py (NEW)
- scratchpad/sol_exit_overhaul/exit_replay.py + _replay_out.txt; scratchpad/_sol_exit_overhaul.md

### Env kills
- MIN_HOLD_FLOOR_MODE=off|shadow|enforce (default enforce for opt-in bots; 0 secs = off)
- HEAT_REGIME_MODE=off

### Forward grade
n>=30 closes vs badday_young_exit_control, ex-top-2 token-median AND captured-pp, OOS >=3/4
halves, cat<=1/20. Promote combo to live only on forward-green + AxiS go.
