# THREAD T3 — Execution-to-dip mechanisms (how to actually LAND a fill at the dip in LIVE, free tools)

Scope: code audit of the live buy path + Solana-mechanics evaluation of mechanisms to fill AT the
dip price. Ranked by feasibility + latency cut. All proposals must be paper-safe, flag-gated,
winner-safe (live buy path).

## What is already built (audit)

### Detection / price layers
- **`core/onchain_ws_feed.py`** — accountSubscribe WS over pump.fun bonding-curve PDAs, FREE public
  RPC (`wss://api.mainnet-beta.solana.com`, <=90 subs/conn, multi-conn). Decodes price on every push
  (~0.4-1s fresh). Flag `ONCHAIN_WS_MODE` in {off(default), shadow, on}. Dynamic supervisor tracks
  the rotating armed-union-open hot set via a `get_mints` callable.
  - **It IS wired to detection already** (not "shadow only"): `dip_scanner._fast_price_for(addr, jup)`
    returns `(onchain_usd,'onchain')` when `ONCHAIN_WS_MODE=on` AND the cached price is fresh
    (`now-ts <= ONCHAIN_FRESH_SECS`). That price feeds `_fast_samples` → `move_fires` (the dip
    trigger) AND the fill price. So flipping to `on` makes sub-second on-chain price drive BOTH the
    flush detection and the fast-watch fire. shadow = logs onchain-vs-Jupiter delta, never used.
  - **HARD COVERAGE LIMIT**: `core/onchain_price.py` decodes ONLY the pump.fun bonding curve. A
    *migrated* curve (complete + vtr==0) returns `migrated` → no price. Raydium / pump-AMM pool
    decoders are an explicit un-built follow-up. So on-chain WS covers pre-migration fresh grads
    only. That happens to be a big slice of our dip cohort, but post-migration dips get NOTHING from
    this feed (they fall back to Jupiter). Partial coverage, not universal.

- **`core/fast_watch.py`** — the ~3s re-check loop. Polls Jupiter price/v3 (50 ids/call, keyless),
  tiered hot tier (top-50 movers polled every tick ~3s, full armed set every 3rd tick ~9s).
  `move_fires` triggers on rolling dip off window max / rise off min. The fire path uses the Jupiter
  AGGREGATE already in hand (pinned per-pool fetch is DEFAULT OFF — it caused 15-69s executor
  starvation). So fast-watch already fires within a couple seconds of a trigger, off the aggregate.

- **`feeds/price_feed.py`** — Jupiter lite-api price/v3 (free, 50 ids cap, chunked). Polling layer.

### Decision → fire handoff
- `MAIN_SCAN_BUY_MODE=arm_only` + `ARM_INSTANT_FIRE_MODE=on`: a main-scan ARM schedules
  `_arm_fresh_fire` → `_fast_eval_one(cache_only=False)` which re-fetches a FRESH price and runs the
  REAL heavy `_evaluate_pair` (chart/order-flow/MTF entry triggers) before the buy. Every real buy
  serialized under `_buy_fire_lock`.
- **The remaining latency on the fire path is the heavy `_evaluate_pair`** (cold chart fetch / GT
  OHLC / order-flow / MTF). That is the eval cost between "trigger printed" and "order submitted".

### Execution (the swap itself)
- **`core/trader._execute_swap_ultra`** — Jupiter Ultra: GET `/ultra/v1/order` → sign → POST
  `/ultra/v1/execute`. Jupiter BUILDS and LANDS the tx through its own protected infra (RTSE
  slippage, MEV-protected, not public mempool). Free/keyless on `lite-api.jup.ag` (used when no key).
  Buy path uses short 0.3s order-retry backoff (`LIVE_BUY_ORDER_BACKOFF_S`). In-flight fill-quality
  abort (`LIVE_FILL_QUALITY_MODE`) bails pre-sign if priceImpact > ceiling ($0, no MEV exposure).
- **Jito tip / priority fee is NOT tunable on the Ultra path.** Confirmed against current Jupiter
  docs (developers.jup.ag/docs/api-reference/ultra/order, June 2026): the only `/order` params are
  inputMint, outputMint, amount, taker, receiver, payer, closeAuthority, referralAccount/Fee,
  excludeRouters, excludeDexes. **No `prioritizationFeeLamports` / `jitoTipLamports` client
  control** — Ultra chooses the fee/tip autonomously. `feeds/jito_bundle_feed.py` is only a
  *context gauge* (tip floor/p99 for entry_meta), NOT a swap-path tunable. memory's "tip floor in
  entry_meta" = the context gauge, confirmed.
- Legacy `_send_transaction` (raw `sendTransaction` over our `_post_rpc` endpoints) DOES allow a
  Jupiter-swap-API-built tx with `prioritizationFeeLamports.jitoTipLamports` — but that path is the
  sandwich-able quote+swap+send route (no RTSE, no MEV protection) and is force_paper'd / not the
  live fleet route.

## Mechanisms evaluated

### (a) On-chain WS sub-second flush detection → fire instantly  [FEASIBLE, partial coverage]
- Flip `ONCHAIN_WS_MODE=shadow` first (validate delta), then `on`. Already wired end-to-end.
- Latency cut: replaces ~3s Jupiter poll + (worse) ~2-min stale REST snapshot with ~0.4-1s on-chain
  push for the flush itself. Directly attacks the "we detect ~5 bars AFTER the 60s low" problem on
  the curve cohort.
- Free (public RPC WS). Risk: low in `on` (price selection is fresh-gated + fail-open to Jupiter).
  Big caveat = pump.fun-curve-only coverage; post-migration dips unaffected until a Raydium/pump-AMM
  decoder is added.
- Code change: env flip + (later) Raydium/pump-AMM pool decoder in `onchain_price.py` for coverage.

### (b) Fire-then-verify / pre-armed instant fire  [HIGHEST ROI, FEASIBLE]
- Today the heavy `_evaluate_pair` runs ON the fire path (after the trigger prints), adding the
  exact eval latency that lets price bounce. The lever: **make the full ENTRY DECISION at ARM time
  (heavy eval up front), so at FIRE only a cheap, deterministic gate runs** — fresh price + dip-still-
  present confirm + fill-quality — then submit the Ultra order immediately. Heavy rug/structure
  confirmation that doesn't change second-to-second is precomputed; only the price-sensitive checks
  stay on the hot path.
- This is the single biggest controllable latency cut because Ultra's own land time is fixed/opaque —
  the only time WE own is decision→submit, and that is dominated by the cold chart/order-flow eval.
- Free. Risk: must keep the cheap fire-gate strict enough to not fire stale (mitigated: fresh-price
  reprice already exists via BUY_REPRICE + `_fast_price_for`). Winner-safe: precomputed decision is
  the same decision, just moved earlier; flag-gate the "skip-heavy-on-fire" path
  (e.g. `ARM_PRECOMPUTE_DECISION_MODE=shadow/enforce`) and A/B.
- Code change: cache the heavy-eval verdict at arm in `_fast_armed[addr]`; `_arm_fresh_fire` consumes
  the cached verdict + runs only the price/dip/quality gate before `trader.buy`.

### (c) Jito bundle / staked-sender for fast land  [NOT AVAILABLE on our path]
- Ultra gives no client tip control (confirmed). To dial tip we'd abandon Ultra for the legacy
  quote+swap+sendTransaction path → loses RTSE + MEV protection, reintroduces sandwich risk on fresh
  thin tokens. Net negative for our cohort. NOT recommended. Ultra's protected land is already fast.

### (d) Limit-order PRE-PLACEMENT (Jupiter Trigger API)  [EXPLICIT ASSESSMENT — LOW for our cohort]
The idea: pre-place a buy at the expected dip price so the keeper auto-fills with zero detection
latency. Assessed against current Jupiter Trigger docs (June 2026) + our cohort:
- **Keeper is an on-chain PRICE-ORACLE POLLER, not a mempool-speed executor.** Jupiter's own FAQ:
  limit orders fail when "price movements too rapid for keepers." Our target IS the instant flush-low
  wick — exactly the move a poller misses. **High risk of reproducing the same miss.**
- 0-slippage "Exact" mode retries aggressively and may never fill; raising slippage = worse price
  (the thing we're trying to fix).
- `triggerPriceUsd` needs a keeper-trusted USD oracle. Fresh sub-24h memecoins (our cohort) often
  lack a reliable keeper price source → mispriced/unsupported.
- We DON'T know the flush-low ahead of time — selection picks the dip AFTER it prints. Pre-placement
  requires guessing the bottom in advance, which the strategy structurally can't do.
- Capital fragmentation: a trigger order locks capital in a per-order vault. With hundreds of armed
  tokens rotating, you can't pre-fund limits across the universe. Fixed-size sweep model
  (WORKING_CAPITAL_FLOOR) makes this worse.
- Fees: Trigger platform fee + Ultra's **0.5% fresh-token tax (<24h)** still applies to fills.
- **Verdict: pre-placement does NOT solve instant-wick flush dips on fresh memecoins** and adds
  capital fragmentation + guess-the-bottom risk. Could marginally help only for SLOWER, more-liquid,
  predictable-support dips (not our edge). Do not pursue as the primary lever.

### (e) Faster / multiple RPC  [LOW ROI for fill price]
- For Ultra, the RPC is NOT on our buy critical path — Jupiter lands the tx. Our `_post_rpc` multi-
  endpoint + `_await_tx_confirmation` only affect the legacy path and post-fill confirmation, neither
  of which moves the FILL price. Low ROI for reachability. (Confirmation speed matters for capital
  recycling, not for landing the dip.)

## Fee gotcha worth flagging (any mechanism)
Ultra applies a **0.5% fee on tokens <24h old**. Much of the dip cohort is fresh grads → ~0.5%
baked into every live fill on top of slippage. Relevant to the live-vs-paper gap calculus the other
threads are measuring.

## HEADLINE VERDICT
The two highest-ROI, free, paper-safe levers to land fills at the dip in LIVE, ranked:
1. **Fire-then-verify / pre-compute the entry decision at ARM time** — strip the heavy `_evaluate_pair`
   off the fire path so trigger→submit is just a cheap fresh-price+dip-confirm gate. This is the only
   large latency chunk WE control (Ultra's land time is fixed/opaque), and it directly closes the
   arm→fire drift. Flag-gated A/B, winner-safe.
2. **Turn on the on-chain WS feed for detection (`ONCHAIN_WS_MODE`: shadow→on)** — already wired into
   `_fast_price_for`/`_fast_samples`; gives ~0.4-1s flush detection vs 3s/2-min stale, for the
   pump.fun-curve cohort. Add a Raydium/pump-AMM decoder later to extend coverage.

Explicitly: **limit-order pre-placement does NOT eliminate the problem** for our fast-wick fresh-
memecoin dips (keeper is a price poller that misses rapid moves, can't pre-know the bottom, fragments
capital). **Jito-tip tuning is unavailable** on the MEV-protected Ultra path. RPC speed doesn't move
the fill price for Ultra.

Sources: developers.jup.ag/docs/api-reference/ultra/order; developers.jup.ag/docs/trigger/create-order;
support.jup.ag (trigger keeper FAQ); quicknode.com/docs/solana/jupiter-transactions.
