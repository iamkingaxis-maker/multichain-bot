# Winner Behavior Decode — PROGRESS (COMPLETE)

Deliverable: scratchpad/_sol_winner_behavior.md  (written, utf-8)

## Findings (union-counted, distinct tokens, ex-top-2 honest metric)
- WR: winners 46.2% ≈ us 45.9% -> selection is NOT the gap (confirms harvester prior).
- Size: winners $168 avg-cost vs us $100 -> not the lever. Breadth: ~comparable.
- HOLD: winners ~535 min avg vs us 134s median -> THE GAP.
- Our hold buckets: 0-60s WR25%/-6.4, 60-120s WR47%/-4.0, 120-300s WR56%/+4.5 (sweet spot).
- 48% of trips cut <2min, red. Fast cuts DON'T avoid rugs (rug tail is 600s+, 11.7% cat).
- 49% of fast cuts are shallow (ret>-8%) = panic on noise.
- Exit-on-winners already good: capture 0.83, only 6.9% round-trip-to-red. NOT the leak.
- DECISIVE same-token union: 61 tokens with both fast-cut & >=120s hold ->
  holding beat cutting 72%, med -7.9% -> +0.4% (+10pp), 51% held-green.
- Panic-cut cohort = 292 trips / 31% vol / med -8.1% / 99 tokens = biggest drag.

## #1 LEVER (shadow hypothesis min_hold_no_panic_floor)
Min-hold ~120s floor gating soft pre-TP1 cutters (velocity-bail peak<2&pnl<=-4,
in-flight -7 floor, -9 fast-bail, pre-stop) EXCEPT a hard-rug tripwire (liq pull /
top1 dump / price<=-25%). Code lever: core/per_bot_position_manager.py.
Upper bound: ex2 -5.8 -> +4.5 (haircut). Robust: +10pp on 72% of tokens.
Guardrail: FLOOR not longer target (600s+ is worst bucket). No commit, no live change.

STATUS: DONE.
