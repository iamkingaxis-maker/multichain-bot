# RH Winner Behavior Decode — PROGRESS

Task: decode EXIT / re-entry / breadth behavior of the 91 audited day-robust winners
(un-decoded axes beyond the entry signature in _rh_candidate_factory.md).

## Status — COMPLETE
- [x] Read prior artifacts (_rh_candidate_factory, _rh_history_decode, _rh_deep_decode)
- [x] Understood tape format (hist_*.jsonl + recorder tape_*.jsonl) + winner selection logic
- [x] Wrote winner_behavior.py (trip reconstruction: buys+sells, units-based)
- [x] Ran: exit / re-entry / breadth on 93 audited winners (846 closed + 412 reentry trips)
- [x] Wrote scratchpad/_rh_winner_behavior.md deliverable

## HEADLINE
#1 miss = EXIT SHAPE. 55.4% of winner trips never peak past +6% -> our fixed +6 TP1 sits
ABOVE the median RH mover (MFE p50 +3.6%). Winners exit ALL-OUT single sell (n_sells p50=1),
into rising price (74.2%), at 97.4% of trip peak. Realized p50 +3.7% / p90 +57%.
Spec = rh_strength_trail: all-out peak-trail armed from +2% (not +6), 3pp gap, stop -15,
bite cap 2, entry = verbatim rh_deep_only clone. Paper shadow only; confirm n>=30 vs scalp control.
Re-entry = secondary fat-tail (24% of profit, median breakeven). Breadth NOT the lever (median 1 tok/day).

## BUILD (2026-07-12, coordinator follow-up) — rh_strength_trail WIRED (paper, no live, no commit)
Files changed (all utf-8 via tools):
- core/bot_config.py: +strength_trail_exit/arm_pct/gap_pp fields (default OFF = byte-identical).
- core/per_bot_position_manager.py: new "1s" exit branch (after hard stop + time box, before
  moonbag); when on, OWNS the ladder — all-out single leg once peak>=arm and pnl<=peak-gap;
  bypasses TP1/TP2/pre-bail/slow-bleed/trail; hard stop + time_stop still fire first.
  +STRENGTH_TRAIL in ExitDecision Literal.
- scripts/rh_paper_lane.py: LaneBot fields + bot_config() wiring + ROSTER entry rh_strength_trail
  (verbatim rh_deep_only entry clone: dip -25, age 24h, demand $50; exit arm +2/gap 3pp/stop -15;
  bites 2; exclusion_group="strengthexit").
- tests/test_rh_factory_racers.py: +TestStrengthTrailRacer (10) +TestStrengthTrailInertForOtherBots (1).
- tests/test_rh_paper_fleet.py: roster count 24->25 + strengthexit group assert.
Tests (exit codes checked directly, no pipe): factory 67/0; fleet+endpoint+pm+config 136/0;
full -k "rh or position_manager or bot_config" 637 passed / 2 skipped / exit 0.
Pre-registered confirm: n>=30 closed vs rh_deep_only control, tokmed ex-top2 green + beats control
+ cat<=1/20, else retire (no re-tune on same tape). RH probe held OFF; left in working tree.

## Data
- 91 audited pure-ontape day-robust winners (decode_results.json)
- Tapes: scratchpad/rh_history/hist_*.jsonl (25) + robinhood_tapes/tape_*.jsonl (481)
- Caveat: captured set = 506 pools (481 from 07-10 only) -> breadth is a LOWER BOUND

## Notes
- Winner OWN trips = upper bound (we enter later). Report ex-top-2 token-median for cohort.
- px = price units (ratios only); units = volume_usd / px for position tracking.
