# RH Live Execution Infrastructure — COMPLETE (2026-07-11 subagent)

Status: BUILT, TESTED, PARKED DORMANT. Working tree only, NOT committed
(main-session review). Nothing live: no key exists, every money path refuses
unless the triple gate is open. Running lane process / Railway / Solana paths
untouched.

## Router / contract provenance (VERIFIED, not assumed)

Constants live in `core/rh_execution.py` (originally LIVE-VERIFIED 2026-07-09
via Blockscout source + eth_call cross-checks). RE-VERIFIED on-chain
2026-07-11 by this build (read-only eth_call, paced, procedure repeatable):

- `eth_chainId` -> 0x1237 (4663) on https://rpc.mainnet.chain.robinhood.com
- SwapRouter02 `0xCaf681a66D020601342297493863E78C959E5cb2`:
  `WETH9()` == 0x0Bd7…AD73 and `factory()` == 0x1F7D…2eFA (both == module
  consts). Code present (24,497 bytes).
- QuoterV2 `0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7`: SAME `WETH9()` and
  `factory()` — quoter and router provably one deployment (8,273 bytes code).
- WETH9: `symbol()`=="WETH", `decimals()`==18, code present.
- Factory `0x1F7D…2eFA`: code present (24,535 bytes), `getPool()` answers.
- CLOSED LOOP: `scripts/rh_chain_feed.py` V3_FACTORY == SwapRouter02.factory()
  — the pools the lane discovers/trades ARE the pools this router routes.
- NOT verified / explicitly out of scope: Universal Router (Permit2 flow, we
  don't use it), v4 PoolManager, Uniswap V2 routing (Robinfun V5 graduations
  — known follow-up in rh_execution docstring).

## What was built (all in working tree)

1. `core/rh_live_execution.py` (NEW, ~640 lines) — live POLICY layer over
   RhExecutor:
   - TRIPLE GATE (Solana mirror): RH_LIVE_CONFIRMED=true AND
     RH_PAPER_MODE=false AND RH_PRIVATE_KEY present. Env read at CALL time;
     any missing leg -> RhLiveGateError with all missing legs named; injected
     paper-only executor also refused (belt-and-braces).
   - `RhLiveExecutor.live_buy/live_sell`: buy containment = position cap
     (RH_LIVE_MAX_POSITION_USD=25) + daily loss halt (RH_LIVE_DAILY_STOP_USD
     =25, persisted UTC-day store `RhDailyPnl`, unreadable state FAILS
     CLOSED) + canary halt + slippage bps bound (default 300, hard ceiling
     1000) + gas-cost cap (RH_LIVE_MAX_GAS_COST_ETH=0.0005 via
     `GasCappedExecutor`, pre-sign, rh_execution untouched). SELLS gated by
     the triple gate ONLY — never by canary/caps/stop.
   - Sell-path canary analog (`RhSellCanary` + `probe_exit_quotes` +
     `rh_canary_entry_block`): exit-quote probe through the exact sell-path
     code (quote_sell -> batch quoter) on every open position; transport
     probe when flat (well-formed revert IS a pass). N consecutive fails
     (RH_CANARY_MAX_FAILS=3), only-failures, wedged loop, garbage/missing
     state past grace -> halt. Cross-process FILE flag storing STATE (readers
     re-evaluate healthy(now) — a stale writer can never pin healthy).
   - Wallet-truth (`rh_wallet_truth` / `rh_wallet_rebase`): native ETH + WETH
     vs persisted baseline; baseline arms only while the gate is OPEN
     (mirrors live_wallet_baseline.json); read errors report {ok:false},
     never fabricate, never touch the baseline; status JSON written for the
     uploader (`rh_wallet_truth.json`).
   - Revert decoding: Error(string)/Panic(uint256) decode + eth_call replay
     (`fetch_revert_reason`) + RhSwapError enrichment (FAIL-OPEN).
   - Nonce/receipt/chain-id: inherited from RhExecutor (send lock, pending
     nonce, wait_for_receipt timeout, chain_id==4663 FAIL-CLOSED connect).
2. `scripts/rh_paper_lane.py` (3 small edits): `_canary_tick` probe in
   strategy_loop + halt-flag read at the TOP of `_consider_entries` (buys
   only; `_manage_exits` untouched). Canary mode OFF in paper default ->
   lane byte-identical; RH_SELL_CANARY=auto flips ON when RH_PAPER_MODE=false.
3. `tests/test_rh_live_execution.py` (NEW, 64 tests) — dormancy FIRST
   (gate combos, no-network refusal, canary-off byte-identity incl. red-flag
   ignore, lane quotes-only static), then revert decode / gas cap / canary
   state machine + probe / daily store / containment / wallet-truth / lane
   wiring. All offline (MagicMock executors, never a real tx).
4. `tests/test_rh_pre_live_invariants.py` (NEW, 15 checks) — Solana-style
   standalone runner + pytest-compatible. Standalone run also does the
   REAL-RPC reachability checks (wallet-truth read + canary quote pipe,
   read-only, keyless).

## Test evidence (2026-07-11)
- `pytest tests/test_rh_live_execution.py tests/test_rh_pre_live_invariants.py`
  -> 79 passed.
- `python tests/test_rh_pre_live_invariants.py` -> 15/15 PASS including the
  two live-RPC reachability checks.
- Full RH regression (9 pre-existing suites: lane/fleet/aged/exec/exit-impact/
  honeypot/chain-feed/firehose/endpoint) -> 249 passed, 2 skipped (same
  skips as before; zero regressions from the lane wiring).

## GO-LIVE FLIP SEQUENCE (future session — copy into the runbook)
1. `python tests/test_rh_pre_live_invariants.py` must exit 0.
2. Fund the RH hot wallet; set RH_PRIVATE_KEY (env only, never a file).
3. Set RH_LIVE_CONFIRMED=true, RH_PAPER_MODE=false; point RH_LIVE_STATE_DIR
   at durable storage ($DATA_DIR on Railway; default scratchpad/robinhood_tapes).
4. `rh_wallet_truth()` once — proves balance reads AND arms the baseline.
5. Sell path END-TO-END with a dust position BEFORE any strategy buy
   (canary green is necessary, not sufficient — 07-10 rule).
6. Explicit AxiS approval recorded, then start the lane live. Canary is
   auto-ON; caps default $25/$25.
