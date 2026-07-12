# SOL DEEP+X combo gate — PROGRESS

## Done
- COMBO HUNT: DEEP (pc_h1<=-45) AND each of {liq, entry_vol_h24, rt_buys, unique_buyers,
  net_flow_15s/60s, bs_h1, buy_pressure, top10_holder, hour 03-08/13-22} — graded 4 halves,
  ex2 token-median, volume%deep/%all, winner-kill (p90 vs deep-alone).
  Scripts: scratchpad/sol_selection/combo_hunt.py + combo_verify.py (dataset _trips.json).
- WINNER: DEEP + liquidity_usd>=30k. ex2 tokmed +4.6 (deep-alone -3.0, baseline -5.8),
  wr 56, 3/4 halves green (ODD -2.2 only, shallow), winner-CONCENTRATING (p90 +32.5 > deep
  +28.3). GENUINE INTERACTION: liq alone -5.0, deep alone -3.0, combo +4.6, shallow+liq -6.3.
  Neighborhood robust (30-35k plateau all green; deep -40..-50 all green at liq>=30k).
- VOLUME COST: keeps 68% of deep = ~19% of ALL young fills (deep is only 28% of fills). Hard
  gate guts lane ~5x -> recommend SHADOW FAVOR + soft-preference / dedicated-sleeve, not
  blanket block. Deep-alone stays size-neutral tilt for the volume lane.
- WIRED shadow-only: deep_combo_shadow (FAVOR/SKIP) + deep_combo_liq in feeds/dip_scanner.py
  next to deep_capitulation_shadow. Fail-open. py_compile OK. NO commit, NO enforce.
- Contract test tests/test_deep_combo_shadow.py (4 cases) PASS. Ran young_tape+nf60 shadow
  (17 pass), pre_live_invariants (8 pass).
- Deliverable: scratchpad/_sol_deep_gate.md (full grid + interaction table + neighborhood +
  volume cost + enforce spec DEEP_COMBO_MODE default off).

## Enable prereqs (unchanged)
- n>=20 deep+liq tokens/side/half on fresh tape (CH2=9, EVEN=12 still <20) + green holds +
  explicit AxiS go for live enforce.
