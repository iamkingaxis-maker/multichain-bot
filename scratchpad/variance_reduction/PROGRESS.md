# Variance-reduction mine — PROGRESS

## DONE
- Reconstructed 457 RH closed trips from partial-sell log (rh_trips.py -> _rh_trips.json).
- Scored 4 levers on BOTH chains' realized data (rh_levers.py, sol_levers.py):
  1. Catastrophe cap (early de-risk / floor) — WINNER: 100% volume, edge UP both
     chains, biggest clean per-trip stdev cut (RH -20.0%, Sol -7.4%).
  2. Hold-time box (600s) — #2: ~92% volume, edge UP, stdev -5.5/-5.6%.
  3. Per-token daily cap — biggest DAY-variance cut (Sol -42% @ K5) but heavy
     MEASURED volume/edge cost (clustering was into winners); redirection reframe
     restores fleet volume; cross-sibling form already live (exclusion_group,
     core/fleet_token_cap.py).
  4. Earlier TP1 slice — weakest; marginal stdev cut + edge cost; the win in
     aged_derisk is its EXPOSURE cap, not the bigger slice.
- REFUTED: rug-signal stamp as an entry gate (rug-stamped RH pools had HIGHER mean).
- Live microcap stops GAP THROUGH => catastrophe defense must be EXPOSURE de-risk
  (bank to 25% early), not just a tighter price stop.

## WIRED (working tree, no commits, no Solana-live enforce)
- RH: rh_lowvar_catstop (5min derisk->25% + -12 stop) + rh_lowvar_box (10min box),
  both exclusion_group="lowvar". Config-only in scripts/rh_paper_lane.py; entry size
  untouched. Roster now 21 racers; both load & pre-registered vs rh_young_v1.
- Solana: VARIANCE_SHADOW stamp (block 0d) in core/per_bot_position_manager.py —
  stamp-only, non-enforcing, VARIANCE_SHADOW=off disables.
- tests/test_variance_shadow.py (4 pass). 212 passed across the regression sweep.

## Deliverable
- scratchpad/_variance_reduction.md — full ranking table + top rec + what's wired.

## NEXT (forward)
- Grade rh_lowvar_* at n>=30 closes vs young_v1 control (stdev + worst-trip, mean
  not worse). Grade VARIANCE_SHADOW forward: how many Sol exits the cap/box would
  change and the pnl at those moments. Then enforce-or-kill per chain.
- Consider extending fleet_token_cap shadow to the young lane (day-variance leg).
