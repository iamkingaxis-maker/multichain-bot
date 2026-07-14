# hoodlana_class_gate PROGRESS

Mission: grade entry-time HOLDER-STRUCTURE gate for HOODLANA-class hidden-supply dumps.
Bar: winner-kill <= 5%, negligible universe block, must catch HOODLANA-at-entry (reconstructed).

- [x] Step 0: dir created, resources located (holder_features.py has all features already; rpc_lib.py present; death_split.json alive=119 dead=105)
- [x] Step 1: winner cohort from _full_trades.json — cohorts.json + winners_anybot.json
      * strict (net across ALL bots > 0 per mint): 55 winners, 27 alive / 28 dead
      * anybot (ANY bot closed net-positive): 92 winners, 45 alive / 47 dead
      * grade winner-kill on BOTH; alive-only fetched (current-state ~ entry-state approximation)
- [x] Step 2: universe sample = 50 most recent distinct buy mints (2026-07-08T13:13 .. 07-09T02:04)
- [x] Step 3: rugcheck fetch DONE (80 mints -> features.jsonl + raw/). KEY FINDING: rugcheck
      topHolders now carry NO tags -> production pool_topholder_pct (tag-based) reads 0 for
      everything; pool vault counts as a "real" holder. Pool identifiable via topHolders.owner ==
      pool-state pubkey (our pair_address, or markets[].pubkey) or Raydium V4 authority 5Q544f...
      First trim dropped markets[].pubkey -> corrective refetch pass running (_refetch.log).
- [x] Step 4: HOODLANA recon DONE (hoodlana_recon.json). F6Kmx = pool-state PDA that OWNS both
      vaults. Entry window: pool token vault 12.45-12.78% of 1B supply, WSOL side 154-165 SOL.
      DUMP 02:30->02:35 UTC: vault 12.78% -> 96.36%, WSOL 154.7 -> 20.6 (-87%).
      Entry-time derived: outside-pool share ~87.4%; recorded top10=14.71 -> hidden rank-11+
      share ~72.6%.
- [x] Step 5: grade DONE -> scratchpad/_hoodlana_class_gate_grade.md + grade_results.json.
      BEST RULE: hidden_share >= 60 AND total_holders < 1000
      (hidden = 100 - pool_pct - top10_real - insider_pct, pool via owner-join not dead tags)
      -> HOODLANA caught (hidden 72.84 chain-verified); winner-kill 4.4% anybot / 3.7% strict;
      universe block 6% (9.4% alive-universe). Tighter y=70 variant: 2.2%/0%/2% but only 2.8pp margin.
      shoulder_11_20 NOT discriminative; insider flag dead in API; graphInsiders non-discriminative.
      SHIP PREREQ: holder_features.py pool_topholder_pct tag filter is dead (no tags in reports) —
      needs owner==markets[].pubkey join.
ALL STEPS COMPLETE.
