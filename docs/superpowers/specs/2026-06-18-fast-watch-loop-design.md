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

## The Rev 2 idea: arm the near-miss subset, batch-poll only that

Don't watch all 415. Each main scan cycle, **arm** the small subset of watchlist tokens that are *one
nudge from a buy* — measured by the scan's own **distance-to-fire** (see "Arming" below), not a crude
price rank. Then a fast loop **batch-polls only the armed subset** for fresh prices via DexScreener's
multi-token JSON endpoint (≤30 addresses per call) every ~3s, detects a fresh dip from its own price
samples, and escalates the 0–N survivors into the existing `_evaluate_pair`.

**Arming correctly is the entire value proposition.** If we arm the wrong tokens, the fast loop adds no
speed (the ~150s sweep still catches the entry — no worse than today, just no win). So arming is driven
by the richest signal available and validated empirically in shadow before enforce.

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
- `FastWatchConfig` gains `armed_max`, `sample_window`, `volatility_reserve`; drops `trend_secs` (Axiom).
- New arming by **proxy distance-to-fire**: `_fast_arm_subset()` (end of `_scan_cycle`) computes
  `distance = cached pc_h1 − dip-zone-edge` per watchlist token and arms the smallest-distance cusp tokens
  + a volatility reserve. **No `_evaluate_pair` change** (money path untouched beyond Tasks 1–3).
- New DexScreener batch price source + per-armed-token rolling price-sample buffers.
- `_fast_watch_tick` rewritten to: (read armed set) → batch-poll → dip-from-samples → escalate. **No
  Axiom calls.**
- New shadow instrumentation: **armed-set hit-rate** — every main-loop buy logs whether its token was
  armed and the fast-loop lead time, so arming correctness is measured, not assumed.

## Architecture (Rev 2)

A background coroutine on the `DipScanner` instance (unchanged spawn). Three tiers:

### Tier 0 — Arm the in-play subset (each scan cycle, no fetch, no money-path changes)
**Rev 2.1 (after the first shadow read).** The first arming proxy was dip-only (`distance to the deep-dip
edge`), which scored **0/10 armed-hit-rate** in shadow: the allowlist mixes **dip** bots (badday family,
deepflush) and a **momentum** bot (`timebox_probe_5mgreen`), and the tokens that actually fire buys are
the *active/in-play* ones — a dip-only signal structurally misses momentum entries. So arming is now
**entry-type-agnostic**: arm the in-band tokens that are *in play* (near a threshold on either side) and
**most active**, since recent activity is what precedes a buy.

`_fast_arm_subset()` runs at the end of `_scan_cycle` and builds `self._fast_armed` (dict addr→pair) from
`_sticky_watchlist`, in-band only (`self.min_mcap`/`max_mcap`/`min_age_ms` + liq > 0):
1. keep tokens that are **in play**: `abs(cached priceChange.h1) ≤ FAST_WATCH_ARM_BAND_PP` (default 15pp
   — not already far gone up *or* down; a token at ±20% has likely already made its move);
2. rank the in-play set by **recent volume** (`volume.h1` desc) — the most-traded tokens are the ones the
   fleet is most likely to buy — and take the top `FAST_WATCH_ARMED_MAX` (default 30).
Pure in-memory selection (no network, no `_evaluate_pair` change). Re-armed every cycle. Still validated
by the armed-hit-rate; if volume-ranking underperforms we iterate (e.g. blend in the dip cusp or cache
the 90m shape) — but this directly targets "tokens about to be bought" across both entry types.

### Tier 1 — Batch-poll the armed subset (every `FAST_WATCH_INTERVAL_SECS`, default 3s)
One DexScreener call per ≤30 armed tokens:
`GET https://api.dexscreener.com/latest/dex/tokens/{comma-joined armed addresses}` (chunk into
`ceil(n/30)` calls if armed > 30). Parse `pairs[]`, map each to its token by `baseToken.address`
(lowercased), take `priceUsd`. Append `(ts, price)` to a per-token rolling sample deque
(`maxlen=FAST_WATCH_SAMPLE_WINDOW`, default 40 ≈ 2 min at 3s). This deque is the loop's *own* fresh
price history — no external feed, no rolling-high to maintain elsewhere.

### Tier 1 (cont.) — Bidirectional move detection from samples
**Rev 2.1.** For each armed token with ≥2 samples, the loop computes a fresh move in **either direction**
off the rolling window: a dip `(current/max − 1)*100 ≤ −FAST_WATCH_DIP_PCT` (serves dip bots) OR a rise
`(current/min − 1)*100 ≥ +FAST_WATCH_RISE_PCT` (serves momentum bots). A token fires the shortlist if
**either** triggers AND `FastWatchDedup.should_eval` (TTL `FAST_WATCH_EVAL_COOLDOWN_SECS`, 60s) AND it's
not held/blocked. Expect 0–N survivors. The threshold is a loose superset — `_evaluate_pair`'s per-bot
gates make the actual buy decision, so a rise that no momentum bot wants simply gets rejected downstream.

### Tier 2 — Escalate (unchanged)
For each survivor: refresh the cached `pair["priceUsd"]` with the fresh batch price, then call
`_evaluate_pair(pair, ctx)` with `_fast_path_allowlist`=cfg.bot_allowlist and
`_fast_path_shadow`=(mode=="shadow"). The existing filters/ML/rug/dip gates decide; fires (or shadow-logs)
go through `_buy_fire_lock` + the durable in-`_execute_bot_buy` guards. No double-buy (same model as Rev 1,
already reviewed SHIP).

## Data flow (Rev 2)

```
_scan_cycle (every ~150s)
   └─> Tier 0 _fast_arm_subset(): from cached pair, distance = pc_h1 − DIP_ZONE_EDGE ;
       keep 0<distance<=band, smallest-first + volatility reserve, cap 30
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
- **Modify `feeds/dip_scanner.py`** —
  - Add `_fast_arm_subset()` (called at the END of `_scan_cycle`): pure in-memory selection over
    `_sticky_watchlist` → `self._fast_armed` (cusp by proxy distance from cached `pc_h1` + volatility
    reserve, in-band, cap `armed_max`). No `_evaluate_pair` change.
  - Add `_fast_batch_prices(addrs)` (DexScreener `/latest/dex/tokens/{≤30 csv}` fetch, map by
    `baseToken.address`); rewrite `_fast_watch_tick` for read-armed → batch-poll → dip-from-samples →
    escalate; add `self._fast_armed`, `self._fast_samples` (dict[addr→deque]) init in `__init__`.
  - Add the **armed-hit-rate** log: where a buy actually fires (main fan-out + legacy path), emit
    `[fast-watch] hit-rate buy bot=X token=Y armed=<bool> last_fast_sample_age=<s>` so shadow can compute
    coverage of real entries. (`armed` reads `addr in self._fast_armed`; additive log, decision-neutral.)
  - Remove the Axiom subscribe + `_fast_trend`/`get_tick_trend` usage from the loop. `_fast_held_or_blocked`,
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
| `FAST_WATCH_ARM_BAND_PP` | `15` | in-play band: arm in-band tokens with `abs(pc_h1) ≤ this` (not already gone either way) |
| `FAST_WATCH_SAMPLE_WINDOW` | `40` | per-token rolling price samples (~2 min at 3s) |
| `FAST_WATCH_DIP_PCT` | `3` | dip trigger: drop off the rolling high (serves dip bots) |
| `FAST_WATCH_RISE_PCT` | `3` | rise trigger: gain off the rolling low (serves momentum bots) |
| `FAST_WATCH_EVAL_COOLDOWN_SECS` | `60` | per-token fast-eval TTL dedup |
| `FAST_WATCH_BOT_ALLOWLIST` | live pool + dip-entry bot_ids | scoped bots the fast path may fire |

## Phasing (unchanged)

1. **Shadow** (default once shipped): arm + poll + log `would-fire bot=X token=Y dip=Z% ~Ns ahead`. No
   money. Validate: (a) `polled` ≈ `armed` (DexScreener coverage is healthy, unlike Axiom's 0), (b)
   would-fire entries lead the main loop, (c) zero double-decisions, **and (d) the ARMED-HIT-RATE gate —
   of the entries the main loop actually fired, the fraction whose token was armed at fire time (with the
   median lead time)**. This is the direct test that we are arming the *correct* tokens. A low hit-rate
   means the arming signal is wrong → tune `distance`/`volatility_reserve`/`armed_max` and re-measure
   BEFORE enforce. Do not flip to enforce until the hit-rate is satisfactory.
2. **Enforce** (explicit flip): fire scoped bots. **Paper first**; live is a separate AxiS decision
   (`PAPER_MODE` unchanged by this work).

## Testing

Unit (`tests/test_fast_watch.py`):
- Tasks 1–3 tests unchanged (allowlist filter, shadow no-fire, route order, byte-identical-off).
- Arming (`_fast_arm_subset`): from a synthetic watchlist with cached `pc_h1`/`volume.h1`/band fields,
  selects only in-band tokens with `0 < distance ≤ ARM_BAND_PP` (distance = `pc_h1 − DIP_ZONE_EDGE`),
  smallest-distance first for the cusp slots, fills `volatility_reserve` with highest-`volume.h1` in-band
  tokens; respects `armed_max`; excludes tokens already past the edge (distance ≤ 0) and out-of-band;
  deterministic for a given input.
- Armed-hit-rate log: a fired buy emits the hit-rate line with `armed=<bool>` reflecting whether the
  token was in `self._fast_armed`.
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
  not 0), would-fire lines lead the main loop, zero double-decisions, and the **armed-hit-rate** of actual
  fired entries is high enough to justify enforce (the explicit go/no-go on whether arming is correct).
- DexScreener call rate stays ≪ limit (≤ a few calls per tick).
- In enforce (paper): buys fire only for allowlisted bots; no double-buys vs the main loop; loop survives
  DexScreener errors (skips a tick, recovers).
- Pre-live invariants (`tests/test_pre_live_invariants.py`) still pass.
