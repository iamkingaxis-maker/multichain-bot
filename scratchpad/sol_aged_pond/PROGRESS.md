# SOL aged-pond mine — PROGRESS

## Done (2026-07-13)
- Pulled fresh /api/trades?full=1&limit=5000 (07-08..07-13) + combined with the
  older 76MB slice (07-02..09) and prior pulls. Built absorb-family realized
  join: scratchpad/sol_aged_pond/_trips.json = 352 trips / 88 tok, post-scrub.
  Build script: scratchpad/sol_aged_pond/build.py. Analysis:
  scratchpad/sol_aged_pond/{analyze.py,mine.py}.
- Q1 (signature): adolescent's ONLY discriminating axis vs young_pump_dip is
  pc_h6 (-48 post-pump vs +205 mid-launch). Age + deep pc_h1 + live absorption
  + liq floor. adolescent_absorb wider rebuild n=29: ex2 +0.6 (was +4.3 @ n=19),
  OOS FRAGILE.
- Q2 (age band): ABSORB family shows MONOTONIC ex2 gradient by age
  (<2h -5.1 -> 12-24h -0.1 -> 24-48h +2.3). GENERIC young lane (955) is FLAT by
  age. Age alone is not the lever; it pays only under the absorb gate.
- Q3 (green cohorts): best NEW = net_flow_15s_imbal>=0.4 on 6-24h pond -> ex2
  +2.7, n=22, 85 legs, 64% green, 3/4 OOS halves green. Companions: liq>=35k
  (+1.7), entry_vol_h24>=1M (+1.8). Strength-chasing axes invert (buy_pressure,
  pct_above_support, top10<25).

## Shipped (working tree, NO commits, NO live enforce)
- feeds/dip_scanner.py: `aged_pond_absorb_shadow` stamp (FAVOR = 6-24h &
  nf15_imbal>=0.4; records postpump=pc_h6<0). py_compile OK.
- tests/test_aged_pond_absorb_shadow.py: 6 tests PASS. Siblings
  (deep_combo/deep_exit_spec) still PASS.
- Deliverable: scratchpad/_sol_aged_pond_mine.md. Racer spec:
  scratchpad/sol_aged_pond/racer_spec.md (paper only, not built).

## Next (forward, main session)
- Grade aged_pond_absorb_shadow FAVOR side forward to n>=20 tok/side/half; verify
  chrono-late red was noise. If green holds -> build the nf15>=0.4 paper A/B racer
  (racer_spec.md). Then enforce-or-kill. AxiS decides any live promotion.
