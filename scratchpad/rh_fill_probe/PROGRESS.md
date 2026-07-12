# RH LIVE FILL PROBE — COMPLETE (2026-07-12 subagent)

Status: BUILT + TESTED, working tree only, NOT committed (main session
reviews/ships). No Railway changes, running lanes untouched, key never
required at import. Coexists cleanly with the concurrent factory no-fire
fix (session-anchor gate) that landed in the same file mid-build.

## What's wired
1. `scripts/rh_paper_lane.py`
   - ROSTER +1: `rh_fill_probe` — $7.50 (RH_PROBE_SIZE_USD), <=4 buys/UTC
     day (RH_PROBE_MAX_BUYS_DAY, BotState.day_buys persisted same-day),
     max_concurrent 1, min_liq 30k, standard dip trigger, NO factory
     extras, full normal exit ladder, exclusion_group "fill_probe".
     LaneBot fields entry_usd / max_buys_per_day (None = pre-existing
     racers byte-identical; verified by tests).
   - Routing glue: `live_probe_bots()` / `live_route_open(bot_id)` — FOUR
     conditions (triple gate + RH_LIVE_PROBE_BOTS opt-in), env at CALL
     time. `_live_buy_leg` (single gated call site) replaces the paper
     fill with RhLiveExecutor.live_buy; `_paper_sell` routes live-bought
     positions (meta["live"]) through live_sell — partial = exact atomic,
     full close = "all" (dust sweep). Paper ledger row still books, marked
     live=true + "fill" telemetry.
   - FAIL-SAFE: `classify_live_error` -> pre_send / reverted /
     unknown_spend (E1b). Buys: any failure books NOTHING + ledger event
     `ev=rh_live_exec_error`; unknown_spend/undecodable-fill = LOUD
     MANUAL RECONCILE. Sells: pre_send/reverted keep the position (60s
     retry cooldown, LIVE_SELL_RETRY_COOLDOWN_S); unknown_spend books the
     quote ESTIMATE with live_unconfirmed+manual_reconcile flags.
     Gate closed mid-hold: position survives, never paper-closed.
   - Telemetry: `fill_telemetry()` — decision_ts/quote_ts/order_sent_ts/
     landed_wall_ts/tx_landed_ts (receipt BLOCK ts via `_tx_landed_ts`,
     fail-open)/decision_to_landed_ms/fill_vs_quote_pct/gas_cost_eth/tx.
     On the ledger row AND scratchpad/robinhood_tapes/rh_live_fills.jsonl.
     Live closes feed `record_realized` (the executor's $25 daily stop);
     live pnl uses REAL gas (buy leg amortized by frac + sell leg).
2. `scripts/rh_dust_test.py` — sell-path-first go-live step: gate check ->
   deepest-liq WETH pool (backfill + batched WETH.balanceOf, honeypot
   sim; or --token) -> wallet-truth before -> $2 buy -> SELL ALL ->
   wallet-truth after + round-trip cost; per-leg timings printed +
   appended (dry rows flagged dry_run). Exit codes 0/2/3/4/5/6/7.
   --dry-run = mocked, offline, suite-driven.
3. `scripts/rh_make_wallet.py` — keypair -> rh_wallet_key.txt (gitignore
   line ADDED to .gitignore + verified via git check-ignore; script fails
   closed if uncovered); prints ONLY address + funding notes. Refuses to
   overwrite an existing key file.
4. Tests: `tests/test_rh_fill_probe.py` NEW (57 tests: config, 16 gate
   combos, dormancy/byte-shape, live buy/sell booking + telemetry, error
   classes, sell fail-safety, daily cap + persistence, dust dry-run,
   wallet helper). Updated: test_rh_pre_live_invariants::test_lane_never_
   swaps + test_rh_live_execution static dormancy -> gate-wrapped
   assertions (one call site each, behind live_route_open / meta live);
   test_rh_paper_fleet roster 18->19 + probe-enters expectations.

## Test evidence (2026-07-12, direct exit codes, no pipes)
- Full RH regression (15 suites incl. new): **486 passed, 2 skipped**
  (same 2 pre-existing skips), PYTEST_EXIT=0.
- `python tests/test_rh_pre_live_invariants.py` -> **15/15 PASS, exit 0**
  (includes real-RPC reachability, keyless).
- `python scripts/rh_dust_test.py --dry-run` -> exit 0, both legs printed.

## GO-LIVE SEQUENCE (main session; local lane session, NOT Railway)
1. `python scripts/rh_make_wallet.py` -> fund printed address with ETH on
   chain 4663 (~$40-50). Verify keyless:
   `RH_WALLET_ADDRESS=<addr>` -> rh_wallet_truth() ok:true.
2. `python tests/test_rh_pre_live_invariants.py` must exit 0.
3. Env in the lane shell: RH_PRIVATE_KEY=<key file contents>,
   RH_LIVE_CONFIRMED=true, RH_PAPER_MODE=false (RH_LIVE_STATE_DIR default
   scratchpad/robinhood_tapes is fine locally).
4. `python scripts/rh_dust_test.py` MUST exit 0 (sell-path-first, 07-10
   rule). Nonzero -> stop; 6 = dust stuck = sell path broken.
5. AxiS approval recorded, then: RH_LIVE_PROBE_BOTS=rh_fill_probe and
   start `python scripts/rh_paper_lane.py [minutes]`. Canary auto-ON.
6. Verify: rh_live_fills.jsonl rows per leg; ledger buys/sells live:true;
   rh_wallet_truth.json delta = the honest P&L; ANY ev=rh_live_exec_error
   with manual_reconcile:true -> halt + reconcile.
7. Kill switch: unset RH_LIVE_PROBE_BOTS (buys stop, live exits continue).
   Do NOT close the triple gate while a live position is open — exits
   would refuse (position is kept + logged, but the bag sits).
