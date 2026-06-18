# Fast-Watch Loop — Design Spec

**Date:** 2026-06-18
**Status:** Approved design (pending implementation plan)
**Author:** Claude (with AxiS)

## Problem

The main scanner loop (`DipScanner._scan_cycle` in `feeds/dip_scanner.py`) sweeps the full ~485-token
universe each cycle. Its effective cadence is **~150–165s** because the per-token decision work
(ML fusion model + rug-bundle + trigger features + decision logic) over the ~150–230 surviving tokens
is **serial and CPU/GIL-bound**. The 2026-06-18 latency investigation proved this is *compute*-bound,
not fetch-bound: parallelizing the read-only fetches (`PARALLEL_SCAN_DECISION_MODE`) produced **zero**
cadence improvement and was reverted (see `_mission_latency_2026_06_18.md`).

Consequence: when an **already-watched** token dips, we may not re-evaluate it for up to ~160s. For
memecoin entries that is far too slow — the target is to act within a few seconds of the dip.

Key insight: the tokens we actually want to buy are almost always **already on the sticky watchlist**.
So entry timing does not require speeding up the full universe sweep — it requires a **separate fast
loop that re-checks the already-watched cohort** and front-runs the slow sweep's cadence.

## Goal

Catch a dip on an already-watched token within **~3–5s** instead of up to ~160s, by re-checking the
sticky cohort in a cheap in-memory loop and triggering the **existing** entry evaluation sooner —
**without changing what we buy or which gates apply**.

Non-goals (explicitly out of scope):
- New entry criteria / a tighter "fast-entry" gate. The fast loop reuses the existing `_evaluate_pair`
  decision verbatim. (AxiS decision: "Reuse existing gates.")
- Firing the whole fleet. Scoped to the live pool + dip-entry bots. (AxiS decision: "Live pool +
  dip-entry bots.")
- Speeding up the main sweep or its per-token compute (separate future work).
- Replacing the main sweep. The main loop remains the discovery path and the safety net.

## Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| Entry logic | Reuse existing `_evaluate_pair` gates | No new buy-path logic to validate; lowest risk; the real ML/rug/dip filters still judge. The fast loop only changes *when* the existing decision runs. |
| Bot scope | Live pool + dip-entry bots | Entry timing matters most for real money + the dip-buy cohort; smaller blast radius; less buy-lock contention than the full ~70-bot fleet. |
| Rollout | Shadow → enforce, default off | Project convention (shadow-before-enforce) and this is the buy-firing path. |

## Architecture

A new long-lived background coroutine on the **same `DipScanner` instance** (so it can reach
`_sticky_watchlist`, `axiom_price_feed`, `_buy_fire_lock`, and the buy path). Spawned via
`asyncio.create_task` inside `DipScanner.run()` before the `while True` sweep loop.

Three tiers, designed so the per-tick cost is dominated by cheap in-memory reads and the expensive
`_evaluate_pair` runs only for the 0–3 tokens that actually tripped a dip trigger.

### Tier 0 — Subscribe the cohort
On startup and refreshed each main cycle, push every `_sticky_watchlist` address into
`AxiomPriceFeed.subscribe_token(addr)` so `price_cache` and `_tick_buffers` populate. Subscription is
idempotent and safe before the WS connects (`feeds/axiom_price_feed.py:135`).

### Tier 1 — Cheap shortlist (every `FAST_WATCH_INTERVAL_SECS`, default 3s)
Iterate a **snapshot** (`list(self._sticky_watchlist.items())`) to avoid dict-mutation races with
`_prune_sticky`/`_fetch_candidates` (which reassign the dict). For each token, read **in-memory only**:
- `trend = axiom_price_feed.get_tick_trend(addr, FAST_WATCH_TREND_SECS)` — % change over last N
  seconds from the buffered ticks (`feeds/axiom_price_feed.py:90`); `None` if <2 ticks.

Shortlist a token when **all** hold:
1. `trend is not None and trend <= -FAST_WATCH_DIP_PCT` (default −3%). Deliberately **looser** than the
   real entry gates — it is only a "worth a full evaluation now" superset signal.
2. Token is not already held by, or blocked for, the scoped bots (quick pre-check; re-verified inside
   the lock at fire time — see Safety).
3. Token was not fast-evaluated within `FAST_WATCH_EVAL_COOLDOWN_SECS` (default 60s) — the fast loop's
   own TTL dedup, so it does not hammer the same token every 3s.

Expected survivors per tick: 0–3.

### Tier 2 — Real evaluation (shortlisted tokens only)
For each shortlisted token, call the existing `_evaluate_pair(pair, _eval_ctx)` with the sticky entry's
`pair` dict, **restricted to the scoped bots** (live pool + dip-entry, via `FAST_WATCH_BOT_ALLOWLIST`).
`_evaluate_pair` applies all real filters and, on a pass, fires under `_buy_fire_lock`. The looseness of
the Tier-1 trigger is intentional: `_evaluate_pair`'s gates make the actual buy decision.

Restriction mechanism: pass a bot-allowlist through the eval context so only the scoped bots are
evaluated/fired on the fast path. (Exact wiring — an allowlist param on `bot_manager.evaluate_all` or an
`_eval_ctx` field consulted in the fan-out — is an implementation-plan detail; the **requirement** is
that the fast path evaluates and can fire only the configured scoped bots.)

## Data flow

```
AxiomPriceFeed (WS, push)  ──updates──>  price_cache / _tick_buffers   (in-memory)
                                                  │
        ┌─────────────────────────────────────────┘
        ▼
Tier 1 cheap scan (every 3s) over snapshot(_sticky_watchlist)
        │  get_tick_trend(addr, 90s) <= -3%  AND not-held  AND not-recently-evaluated
        ▼
shortlist (0–3 tokens)
        │
        ▼
Tier 2: _evaluate_pair(pair, ctx, scoped-bot allowlist)
        │   (existing filters/ML/rug/dip gates decide)
        ▼
  async with _buy_fire_lock:
        re-check durable guards (exclusion pool, open_positions, capital, cooldown)
        shadow:  log "would-fire bot=X token=Y dip=Z% ~Ns ahead of main loop"
        enforce: fire scoped bot(s)
```

## Safety — why it cannot double-buy

The buy path already serializes through the **process-lifetime** `self._buy_fire_lock`
(`feeds/dip_scanner.py:16950`), the exact primitive the existing parallel-scan mode uses to prevent two
concurrent tasks racing the exclusion pool / caps / double-buying. The fast loop reuses it:

1. **Hold `_buy_fire_lock` across the entire decide-and-fire.** Never cache a "this token is buyable"
   decision across an `await`.
2. **Re-check the durable cross-loop guards inside the lock, immediately before firing:** exclusion pool
   `self._token_registry.is_blocked(bot_id, token)` (`:997`), `open_positions_ref` membership,
   `capital.reserve_for_buy` (`:1210`, raises on insufficient), `pm.in_reentry_cooldown` (`:986`).
   These — not the per-cycle `_cycle_bought_addrs` (reset every cycle, main-loop only) — are what protect
   against the fast loop and the main loop both trying the same token.
3. **Lock-creation guard:** `_buy_fire_lock` is lazily created inside `_scan_cycle`. The fast loop must
   create it if `None` before first use (it may start before the first main cycle).
4. **Snapshot iteration:** iterate `list(...items())`, never the live dict, to avoid mutation races with
   `_prune_sticky` (reassigns the dict at `:17418`).
5. **Fast-loop TTL dedup:** an internal `addr -> last_eval_ts` map (TTL `FAST_WATCH_EVAL_COOLDOWN_SECS`)
   stops repeated same-token evaluation between cycles.

## Error handling & degradation

- **Axiom WS unreliability** (it disconnects/reconnects; some rooms have gone silent): the fast loop is
  **best-effort**. If `get_tick_trend` returns `None` (no buffered ticks) the token simply isn't
  triggered — the 30s main sweep still covers it. If Axiom is fully down, the fast loop is inert; no harm.
- **Coverage health log:** each tick (throttled) logs the % of the sticky cohort that currently has live
  ticks, so we know how much of the cohort the fast loop actually accelerates.
- **Fail-open per token:** every tier is wrapped so one token's exception cannot kill the loop; the loop
  catches and continues, and never crashes the process (mirrors `DipScanner.run`'s cycle try/except).
- **Stale sticky price:** the stored `pair["priceUsd"]` can be up to ~150s stale; the **dip trigger uses
  the live Axiom tick trend**, not the stale pair price. `_evaluate_pair` re-fetches what it needs.

## Components / files

- **New `core/fast_watch.py`** — pure, unit-testable logic:
  - `dip_trigger(trend_pct, threshold_pct) -> bool`
  - `FastWatchDedup(ttl_secs)` — `should_eval(addr, now)` / `mark(addr, now)`
  - `shortlist(snapshot, get_trend, dedup, held_or_blocked, cfg, now) -> list[addr]`
- **Modify `feeds/dip_scanner.py`** — `_fast_watch_loop` coroutine; spawn it in `run()`; subscribe the
  cohort each cycle; scoped-eval call into `_evaluate_pair`; coverage health log.
- **New `tests/test_fast_watch.py`**.
- **No change to `feeds/axiom_price_feed.py`** — `get_tick_trend` + `subscribe_token` + `price_cache`
  already provide everything.

### Config (env)
| Flag | Default | Meaning |
|---|---|---|
| `FAST_WATCH_MODE` | `off` | `off` / `shadow` / `enforce` |
| `FAST_WATCH_INTERVAL_SECS` | `3` | Tier-1 cadence |
| `FAST_WATCH_TREND_SECS` | `90` | tick-trend window for the dip signal |
| `FAST_WATCH_DIP_PCT` | `3` | dip threshold (loose superset trigger) |
| `FAST_WATCH_EVAL_COOLDOWN_SECS` | `60` | per-token fast-eval TTL dedup |
| `FAST_WATCH_BOT_ALLOWLIST` | live pool + dip-entry bot_ids | scoped bots the fast path may fire |

## Phasing

1. **Shadow (`FAST_WATCH_MODE=shadow`, the default once shipped):** all three tiers run; Tier 2 **logs**
   `fast-watch would-fire bot=X token=Y dip=Z% main-lag~Ns` instead of firing. Validate over real cycles:
   (a) it would catch entries meaningfully earlier than the main loop, and (b) **zero** double-decisions
   vs the main loop. No money moves.
2. **Enforce (`FAST_WATCH_MODE=enforce`, explicit flip):** actually fire the scoped bots. **Paper first**;
   live is a separate AxiS decision (`PAPER_MODE` unchanged by this work).

## Testing

Unit (`tests/test_fast_watch.py`):
- `dip_trigger` fires at/below threshold, not above; `None` trend never triggers.
- `FastWatchDedup` suppresses within TTL, allows after expiry.
- `shortlist` excludes held/blocked tokens and recently-evaluated tokens; includes a fresh dip.
- Shadow mode never calls the fire path (assert no `_execute_bot_buy` / `trader.buy`).
- Enforce mode evaluates/fires **only** allowlisted bots.
- **Double-fire guard:** fast loop and a simulated main-loop fire on the same token → exactly one buy
  (shared `_buy_fire_lock` + durable re-check).
- Snapshot iteration is safe while the sticky dict is reassigned mid-iteration.
- Loop never raises out: an injected per-token exception is caught and the loop continues.

Shadow validation (pre-enforce, on the deployed bot): measured would-fire timing advantage + zero
double-decisions across real cycles before flipping to enforce.

## Acceptance criteria

- With `FAST_WATCH_MODE=off`, behavior is byte-identical to today (loop not spawned).
- In shadow, logs show fast-watch detecting dips on watched tokens seconds ahead of the main loop, with
  zero double-decisions.
- In enforce (paper), buys fire only for allowlisted bots; no double-buys vs the main loop across a
  sustained run; the loop survives Axiom WS disconnects (goes inert, recovers).
- Pre-live invariants (`tests/test_pre_live_invariants.py`) still pass.
