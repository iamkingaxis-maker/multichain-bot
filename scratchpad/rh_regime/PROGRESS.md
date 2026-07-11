# RH Regime System — build progress

STATUS: COMPLETE (2026-07-11). Full report: scratchpad/_rh_regime_system.md.

## Done
- [x] Ground truth read (hour rulebook v0, history decode, lane REGIME_* machinery).
- [x] Mining pass over the full sweep (10.36M swaps, 07-01..11): 39,132 synthetic dip trips +
      31,208 pop events, +20m resolution, 60m rug flag; 30-min feed-composition windows with
      per-window ETH/USD. Outputs: rh_regime/{trips.jsonl.gz, windows.json}.
- [x] Two-window analysis (chrono halves AND odd/even-day parity, x age band):
      rh_regime/{analyze_regimes.py, rulebook_v1_tables.json, analysis_out.txt}.
- [x] Paper-fleet corroboration (all 72 positions young band; hour-only join).
- [x] PASSED 4/4 halves: aged 19-21 UTC bad (GATE shipped); aged 02-10 good; young 02-07 good;
      young bot-era discovery bursts = ~2x rug + lower win (STAMP, live-lane candidate).
- [x] REFUTED: 22-01 dead zone, 19-21 young "prime", v0 human-era-14-23 regime_hours rule
      (removed from the lane), ETH 1h/24h move (all bands).
- [x] Shipped: core/rh_regime.py (stamps + aged_hour_gate_ok); lane stamps `regime` on every
      buy row (CompositionTracker in drain, per-racer expectancy dial w/ persistence);
      regime_hour_ok -> v1; aged racers keep regime_hours=True, scalps stay OFF.
- [x] Tests: tests/test_rh_regime.py new (21 tests); test_rh_aged_racers regime tests
      rewritten to v1. RH suites 275 passed / 2 skipped (incl. concurrent rug-stamp+canary
      workstream sharing the same file — no conflicts).

## Note for the next session
- The aged 19-21 gate keys on POOL band: dormant until RH_FEED_MAX_AGE_H widens past 24h.
- v2: rerun mine_regimes.py + analyze_regimes.py on fresh history (~1 week); grade stamps per
  the pre-registered plan in _rh_regime_system.md (n>=30, tokmed, winner-kill<=5%, AxiS ok).
