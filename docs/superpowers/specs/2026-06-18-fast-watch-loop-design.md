# Fast-Watch Loop — Design Spec (Revision 2: armed-subset + DexScreener batch)

**Date:** 2026-06-18
**Status:** Approved design (Rev 2 supersedes Rev 1's Axiom-tick approach)
**Author:** Claude (with AxiS)

## Problem (unchanged)

The main scanner loop (`DipScanner._scan_cycle`, `feeds/dip_scanner.py`) is compute-bound at
**~150–165s** effective cadence (serial per-token ML/rug/decision over ~150–230 survivors; proven not
fetch-bound — see `_mission_latency_2026_06_18.md`). When an **already-watched** token dips, we may not
re-evaluate it for up to ~160s — far too slow for memecoin entries. Tokens we want to buy are almost
always already on the sticky watchlist, so the fix is a **separate fast loop that re-checks watched
tokens** and front-runs the slow sweep.

## Why Rev 1 (Axiom tick buffers) was abandoned

Rev 1 read `AxiomPriceFeed.get_tick_trend()` over the whole ~415-token sticky cohort. Shadow deployment
(2026-06-18) proved this dead: `[fast-watch] tick cohort=415 live_ticks=0` on every tick. Root cause:
the Axiom price WebSocket (Cloudflare-proxy, `target=socket8`) connects and accepts our room-joins but
streams **zero messages back** (0 heartbeats / 0 raw messages over 130s while `sub_manager` stayed
alive). Axiom's price socket went silent industry-wide (the code already documents the legacy `<addr>`
room dying; the `t:/s:/td:` rooms are silent too; direct `api4.axiom.trade` fails DNS). Axiom was
originally scoped to **open positions only** (a handful), never the full cohort — so the cohort-wide
real-time premise was never sound. **We will not depend on Axiom.**

Critically, the rate-limit math also kills a naive "poll all 415 via DexScreener": even via the 30-token
batch endpoint that is ~14 calls/tick → ~280/min, over DexScreener's limits. **Neither feed gives cheap
real-time prices for 415 tokens.** That is the real constraint Rev 2 designs around.

## Goal (unchanged)

Catch a dip on an already-watched token within **~3–5s** instead of up to ~160s, triggering the
**existing** `_evaluate_pair` decision sooner — without changing what we buy, which gates apply, or which
bots fire (live pool + dip-entry, allowlisted).

## The Rev 2 idea: arm a small subset, batch-poll only that

Don't watch all 415. Each main scan cycle, **arm** the small subset of watchlist tokens that are
plausibly *close to a buy* (cheap synchronous gates on cached `pair` data — no fetch). Then a fast loop
**batch-polls only the armed subset** for fresh prices via DexScreener's multi-token JSON endpoint
(≤30 addresses per call) every ~3s, detects a fresh dip from its own price samples, and escalates the
0–N survivors into the existing `_evaluate_pair`.

At ≤30 armed tokens that is **1 DexScreener call / 3s = ~20 calls/min** against a ~300/min public limit —
huge headroom, no dead-socket dependency, and it reuses the HTTP price path that already prices open
positions today.

## What's reused vs new

**Reused as-is (already merged, feed-agnostic — Rev 1 commits stay):**
- `core/fast_watch.py`: `dip_trigger()`, `FastWatchDedup`, `shortlist()` (its `get_trend` callback is an
  injected abstraction — Rev 2 passes a DexScreener-sample-based dip function instead of an Axiom one).
- `core/bot_manager.evaluate_all(bot_allowlist=...)`.
- `_evaluate_pair` allowlist+shadow threading (`_fast_path_allowlist` / `_fast_path_shadow`) +
  `_fast_route_decisions`. Escalation + buy-safety model are **unchanged**.
- `DipScanner.run()` spawn of `_fast_watch_loop` (no-op when `FAST_WATCH_MODE=off`).
- The startup-window `__init__` initialization of the per-cycle attrs.

**New / changed in Rev 2:**
- `FastWatchConfig` gains `armed_max` and `sample_window`; drops `trend_secs` (Axiom-specific).
- New arming step (cheap-gate subset selection over the sticky watchlist).
- New DexScreener batch price source + per-armed-token rolling price-sample buffers.
- `_fast_watch_tick` rewritten to: arm → batch-poll → dip-from-samples → escalate. **No Axiom calls.**

## Architecture (Rev 2)

A background coroutine on the `DipScanner` instance (unchanged spawn). Three tiers:

### Tier 0 — Arm the subset (each main scan cycle, no fetch)
After `_scan_cycle` refreshes the watchlist, compute the armed set from `_sticky_watchlist` using only
cached `pair` fields + existing scanner thresholds (`self.min_mcap`, `self.max_mcap`, `self.min_age_ms`,
the anti-rug liq floor): keep tokens in the mcap/liq/age band, rank by "closeness to a dip" (most
negative cached `priceChange.h1`/`m5`), and cap to `FAST_WATCH_ARMED_MAX` (default 30). Store the armed
set as `self._fast_armed: dict[addr -> pair]`. This is pure in-memory selection — no network, no
side effects. Re-armed every cycle so the set tracks the freshest scan data and rotates as tokens age out.

### Tier 1 — Batch-poll the armed subset (every `FAST_WATCH_INTERVAL_SECS`, default 3s)
One DexScreener call per ≤30 armed tokens:
`GET https://api.dexscreener.com/latest/dex/tokens/{comma-joined armed addresses}` (chunk into
`ceil(n/30)` calls if armed > 30). Parse `pairs[]`, map each to its token by `baseToken.address`
(lowercased), take `priceUsd`. Append `(ts, price)` to a per-token rolling sample deque
(`maxlen=FAST_WATCH_SAMPLE_WINDOW`, default 40 ≈ 2 min at 3s). This deque is the loop's *own* fresh
price history — no external feed, no rolling-high to maintain elsewhere.

### Tier 1 (cont.) — Dip detection from samples
For each armed token with ≥2 samples, the injected `get_trend` computes the drop off the recent rolling
high: `dip_pct = (current / max(window) - 1) * 100`. `shortlist()` keeps tokens where `dip_trigger(dip,
FAST_WATCH_DIP_PCT)` is true AND `FastWatchDedup.should_eval` (TTL `FAST_WATCH_EVAL_COOLDOWN_SECS`, 60s)
AND not held/blocked for the allowlisted bots. Expect 0–N survivors. This is true *fresh sub-minute* dip
detection built from our own 3s polls (DexScreener's `priceChange.m5` is also available from the same
payload as a coarse corroborating signal, but the rolling-high-from-samples is primary).

### Tier 2 — Escalate (unchanged)
For each survivor: refresh the cached `pair["priceUsd"]` with the fresh batch price, then call
`_evaluate_pair(pair, ctx)` with `_fast_path_allowlist`=cfg.bot_allowlist and
`_fast_path_shadow`=(mode=="shadow"). The existing filters/ML/rug/dip gates decide; fires (or shadow-logs)
go through `_buy_fire_lock` + the durable in-`_execute_bot_buy` guards. No double-buy (same model as Rev 1,
already reviewed SHIP).

## Data flow (Rev 2)

```
_scan_cycle (every ~150s)
   └─> Tier 0 arm: cheap-gate subset of _sticky_watchlist (mcap/liq/age band, ranked, cap 30)
                   -> self._fast_armed {addr: pair}

_fast_watch_tick (every ~3s)
   └─> Tier 1: DexScreener batch GET /latest/dex/tokens/{<=30 armed addrs}   (1 call)
               -> append (ts, price) to per-token sample deque
   └─> dip = (price / rolling_high - 1)*100 ; shortlist(dip<=-X%, not-deduped, not-held)
   └─> Tier 2: for survivor -> pair["priceUsd"]=fresh ; _evaluate_pair(pair, ctx[allowlist,shadow])
                shadow: log "would-fire"   enforce: fire scoped bots under _buy_fire_lock
```

## Safety (unchanged from Rev 1, re-confirmed)

- Buy fires only inside `_evaluate_pair` via `_fast_route_decisions` under the process-lifetime
  `_buy_fire_lock`; durable cross-loop guards (exclusion pool `is_blocked`, `open_positions_ref`,
  `capital.reserve_for_buy`, re-entry cooldown) re-checked inside the lock. No double-buy.
- `_fast_path_allowlist=None` on the main path ⇒ byte-identical to today (proven; the 16-test pre-live
  invariants + parallel-scan regression remain the guard).
- Shadow mode moves no money (logs only). Default `FAST_WATCH_MODE=off` ⇒ loop returns immediately.
- Tier 0/1 are side-effect-free reads + idempotent dict writes; iterate snapshots, never live dicts.

## Error handling & degradation

- **DexScreener batch failure** (timeout/429/non-200): the tick logs and skips this round; armed tokens
  simply don't get a fresh sample this tick — the main sweep remains the safety net. No exception escapes
  the loop (whole-tick try/except, mirrors Rev 1).
- **Rate-limit self-defense:** armed set is capped (`FAST_WATCH_ARMED_MAX`) so calls/min stay far under
  the public limit; the batch call uses a short timeout (5s) and a per-tick budget. If armed > cap, the
  lowest-ranked tokens are dropped and the count is logged (no silent truncation).
- **Coverage health log** each tick: `[fast-watch] tick armed=N polled=M dipped=K mode=X` (replaces the
  Axiom `live_ticks` metric; `polled` = tokens that got a fresh price this tick).
- **Stale/missing price:** a token with <2 samples or no fresh price is skipped (not triggered); it
  re-accumulates samples over subsequent ticks.

## Components / files

- **Modify `core/fast_watch.py`** — add `armed_max` + `sample_window` to `FastWatchConfig.from_env`
  (drop `trend_secs`); add a tiny `rolling_dip_pct(samples, current)` helper (pure) used by the loop's
  `get_trend` callback. `dip_trigger`/`FastWatchDedup`/`shortlist` unchanged.
- **Modify `feeds/dip_scanner.py`** — add `_fast_arm_subset()` (Tier 0, called at end of `_scan_cycle`);
  add `_fast_batch_prices(addrs)` (DexScreener batch fetch); rewrite `_fast_watch_tick` for arm→poll→
  dip→escalate; add `self._fast_armed` + `self._fast_samples` (dict[addr -> deque]) init in `__init__`.
  Remove the Axiom subscribe + `_fast_trend`/`get_tick_trend` usage from the loop. `_fast_held_or_blocked`,
  `_fast_route_decisions`, `_fast_watch_loop` skeleton, and the `run()` spawn stay.
- **Modify `tests/test_fast_watch.py`** — replace the Axiom-tick tick tests with armed-subset tests
  (arming selects the right band/cap; batch-parse maps by baseToken.address; rolling-dip triggers; dedup;
  shadow no-fire; exception-survival). Keep the Tasks 1–3 tests (allowlist/shadow/route — still valid).
- **No new external dependency.** Uses `aiohttp` + the public DexScreener JSON endpoint already in use.

### Config (env)
| Flag | Default | Meaning |
|---|---|---|
| `FAST_WATCH_MODE` | `off` | `off` / `shadow` / `enforce` |
| `FAST_WATCH_INTERVAL_SECS` | `3` | Tier-1 batch-poll cadence |
| `FAST_WATCH_ARMED_MAX` | `30` | max armed tokens (= 1 DexScreener batch call) |
| `FAST_WATCH_SAMPLE_WINDOW` | `40` | per-token rolling price samples (~2 min at 3s) |
| `FAST_WATCH_DIP_PCT` | `3` | dip threshold off the rolling high (loose superset trigger) |
| `FAST_WATCH_EVAL_COOLDOWN_SECS` | `60` | per-token fast-eval TTL dedup |
| `FAST_WATCH_BOT_ALLOWLIST` | live pool + dip-entry bot_ids | scoped bots the fast path may fire |

## Phasing (unchanged)

1. **Shadow** (default once shipped): arm + poll + log `would-fire bot=X token=Y dip=Z% ~Ns ahead`. No
   money. Validate: (a) `polled` ≈ `armed` (DexScreener coverage is healthy, unlike Axiom's 0), (b)
   would-fire entries lead the main loop, (c) zero double-decisions.
2. **Enforce** (explicit flip): fire scoped bots. **Paper first**; live is a separate AxiS decision
   (`PAPER_MODE` unchanged by this work).

## Testing

Unit (`tests/test_fast_watch.py`):
- Tasks 1–3 tests unchanged (allowlist filter, shadow no-fire, route order, byte-identical-off).
- Arming: selects only in-band tokens (mcap/liq/age), ranks by dip-closeness, caps to `armed_max`.
- Batch parse: maps `pairs[]` to tokens by lowercased `baseToken.address`; ignores unknown/extra pairs;
  missing token → no sample.
- `rolling_dip_pct`: correct % off the window max; <2 samples → None (no trigger).
- Shortlist integration: a token dropping ≥ threshold off its rolling high is shortlisted; held/blocked
  and recently-evaluated are excluded.
- Shadow mode calls no fire path; enforce fires only allowlisted bots; double-fire guard via shared lock.
- Batch fetch failure → tick logs + continues, no exception escapes; token with no fresh price skipped.

Shadow validation (pre-enforce, deployed): `polled≈armed`, would-fire lead-time recorded, zero
double-decisions across real cycles.

## Acceptance criteria

- `FAST_WATCH_MODE=off` ⇒ byte-identical to today (loop returns immediately).
- In shadow: `[fast-watch] tick armed=N polled=M dipped=K` shows `M≈N` (healthy DexScreener coverage,
  not 0), would-fire lines lead the main loop, zero double-decisions.
- DexScreener call rate stays ≪ limit (≤ a few calls per tick).
- In enforce (paper): buys fire only for allowlisted bots; no double-buys vs the main loop; loop survives
  DexScreener errors (skips a tick, recovers).
- Pre-live invariants (`tests/test_pre_live_invariants.py`) still pass.
