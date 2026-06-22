# On-Chain WS as Authoritative Price — Design Spec

**Date:** 2026-06-22
**Status:** spec → (shadow-validate) → enforce
**Owner:** AxiS

## Problem (the root cause, finally named)

The bot decides to buy off a **~2-minute-stale DexScreener snapshot** (`pair["priceUsd"]` + `priceChange`). Paper books its entry AT that stale price → it "buys the flush low." Live can't reach a 2-min-old low (deep dips are V-shaped and have already bounced), so live fills at the recovered price. Result: paper overstates the edge by the stale-low gap, and live's entries skew to near-sure losses on the same signals. The `BUY_REPRICE` gate is a **band-aid** — it decides on the stale price then aborts/reprices at fill. The correct fix is to **never decide on a stale price**: know the current price immediately, so the dip we detect IS the dip we can buy. No reprice needed.

## What already exists

`core/onchain_ws_feed.py` (`OnchainWsFeed`) is a built-but-dormant **`accountSubscribe` WebSocket feed** over pump.fun bonding-curve PDAs on the free Solana RPC. It is PUSHED new reserves on every on-chain swap and derives price via `core/onchain_price.py` (`decode_bonding_curve` → `price_sol_from_curve`). Interface: `get_price(mint) -> (price_usd, ts)`. A shadow comparison vs Jupiter already exists at `feeds/dip_scanner.py:3641`. It is gated by `ONCHAIN_WS_MODE ∈ {off, shadow, on}`, currently **off** (true no-op).

## Architecture: three feeds, correct roles

| Feed | Latency | Role |
|---|---|---|
| DexScreener snapshot | ~2 min | **Arm-selection only** — the cheap universe-wide pre-filter that decides WHICH tokens to watch. NEVER the decision/entry price. |
| Jupiter poll (fast-watch) | ~2 s | **Backstop / cross-check** + the route/liquidity reference for fillability. |
| **On-chain WS (accountSubscribe)** | **sub-second push** | **Authoritative decision + entry price** for the armed/hot subset. |

**Data flow (target):**
1. Universe scan (DexScreener) flags candidate dips → ARM them.
2. Armed mints get an `accountSubscribe` WS subscription → live curve price in `price_cache`.
3. The dip TRIGGER and the recorded ENTRY price both read `feed.get_price(mint)` (fresh on-chain price), not the snapshot.
4. Fill executes against that same live price. **No reprice gate** — there is no stale price to guard against; only true ~1–2s execution slippage remains (real, small, symmetric paper/live).

## Components / changes

1. **WS feed lifecycle** — `ONCHAIN_WS_MODE=shadow` then `on`; ensure armed-set subscription churn is wired (subscribe on arm, drop on disarm) and SOL/USD is fed.
2. **Authoritative price resolver** — a single helper `authoritative_price(mint)`:
   - return fresh WS price if `ts` within `ONCHAIN_WS_MAX_AGE_SECS` (e.g. 5s);
   - else fall back to Jupiter fast-watch price (also fresh);
   - else (no fresh price) → **no-trade** (this is the `no-fast-price` gate, flipped to enforce).
3. **Wire it as the decision + entry basis** — `decision.entry_price` and the trigger gate consume `authoritative_price`, for BOTH paper and live (so paper books the reachable price → paper and live converge).
4. **Delete/retire BUY_REPRICE** — once the decision price is live, the reprice abort is obsolete. Keep a thin execution-slippage allowance only.
5. **Coverage handling** — pump.fun curve covers most memecoins; migrated/Raydium pools use `resolve_price_account`. Mints with no resolvable on-chain price = not traded live (and paper skips them too → honest).

## Rollout (enforce directly — it's paper, AxiS 2026-06-22)

Live is paused (wallet drained), so this is 100% paper — a wrong curve price costs nothing but garbage paper data, which the accuracy logs surface immediately and we revert. So we **enforce directly** and validate live in-place rather than a separate shadow soak.

- **Step 1 — `ONCHAIN_WS_MODE=on`:** the feed spawns, subscribes the armed hot subset, and the price resolver (`dip_scanner.py:3594`) returns the on-chain price for the fresh-price path (RT trigger + reprice). Watch the `[onchain] delta=` logs (WS vs Jupiter) — this IS the accuracy validation, live.
- **Step 2 — entry-basis wiring:** wire the recorded ENTRY price (paper especially) to the fresh on-chain price (mirror the live reprice rebase), so paper books the reachable price, not the snapshot. This is what actually makes paper honest.
- **Step 3 — retire reprice / demote snapshot** once the on-chain price is the decision basis.
- **Re-fund live** only after paper-on-WS visibly tracks reachable prices.

MONITOR THROUGHOUT: `[onchain] delta=` distribution (accuracy), armed-mint WS coverage %, feed health. If the curve price is garbage vs Jupiter → revert `ONCHAIN_WS_MODE=shadow` instantly.

## Monitoring

- `[onchain] WS-vs-fill delta_pct` distribution (the accuracy gate).
- Armed-mint WS coverage % (how many armed tokens have a fresh on-chain price).
- WS connection health (reconnects, dropped subs, RPC WS rate-limit).
- Paper entry-basis source histogram (ws / jupiter / no-trade).
- After enforce: paper vs live entry-price agreement on shared tokens (target: ~0 phantom-low gap).

## Risks & mitigations

- **Wrong curve math on some pool types** → Stage-0 accuracy gate; fall back to Jupiter when WS missing/stale; never authoritative until validated.
- **WS coverage < universe** → expected; snapshot still arms, but we only TRADE mints with a fresh on-chain price (volume drops — accepted, every trade is real).
- **Free RPC WS limits** → cap subscriptions to the hot subset; backstop on Jupiter.
- **Volume cut** → accepted by AxiS: "only trade tokens with a live price." Honest > high-volume.

## Success criteria

1. Decision/entry price for armed tokens is the live on-chain price, not the snapshot.
2. BUY_REPRICE retired (no stale price to reprice).
3. Paper and live, on the same token, record entries within execution-slippage (~1–2%) of each other — no phantom low.
4. Paper P&L becomes a faithful predictor of live-reachable P&L (validated before any re-funding).
