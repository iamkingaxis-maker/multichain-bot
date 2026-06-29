# Real-Time Dip Detection Rebuild — Design Spec

**Date:** 2026-06-29
**Status:** Approved design → ready for implementation plan
**Scope:** Subsystem #1 of the real-time discovery rebuild — real-time **dip detection on the watched universe**. (Real-time new-pool *discovery* via on-chain `logsSubscribe` is a deferred follow-on spec.)

## Goal

Detect memecoin flushes on the watched universe in **seconds, not ~2 minutes**, by computing the dip signal against a **real-time rolling reference** instead of the ~2-min-stale DexScreener snapshot anchor — so the fleet enters on the demand-turn, not the recovered price 2 minutes later.

## Problem

The fleet's entry decision gates on `pair["priceChange"]` (`pc_m5/h1/h6/h24`). Today:

- The **public REST discovery feed** (`api.dexscreener.com/latest/dex/tokens/…`) is ~2-min cached. `_SCAN_INTERVAL=30s` but the *data* is ~2 min old.
- `RT_TRIGGER_MODE=enforce` (`core/fast_watch.py::reprice_all`) refreshes the **numerator** (current price, via Jupiter) but reconstructs the dip **anchor** from the same stale snapshot `priceChange`. Its own comment concedes: *"the repriced m5 is a directional signal, trustworthy in sign but not in exact magnitude … anchor staleness (the ~2-min-old snapshot reference)."*

The stale **anchor** — the reference high the dip is measured against — is the defect. A token that flushed in the last ~2 min isn't reflected, so we appraise the dip late (or miss it).

## Architecture

A new **real-time dip reference** layer computes `pc_m5/h1/h6/h24` from a live rolling OHLC window. Two data layers, by freshness:

- **Inner (seconds-fresh): in-memory rolling buffer.** Each fast-watch tick already polls fresh Jupiter prices on the watched universe. We append every `(ts, price)` into a per-token, age- and count-capped ring buffer. The rolling high/low over any horizon comes from this with **zero new fetches**.
- **Outer (authoritative depth): io.dexscreener bars.** The buffer is only as deep as we've watched a token. To get a true h1/h6/h24 high immediately on first sight (and to correct drift), fetch io.dexscreener **1m bars** (back ~24h) via the proven `curl_cffi impersonate=chrome` chart path, plus **1S bars** for the hot/dipping subset. Broad coverage (per the "whatever it takes" cost decision), paced off-loop via the existing `run_ds_fetch` executor.

The dip per horizon is `(fresh_price / rolling_high[h] − 1) × 100` — real-time on **both** ends. This **replaces** the `reprice_all`-off-stale-anchor reconstruction and writes into the *same* `priceChange` dict the gate chain already consumes, so **no gate logic changes** — the gates simply receive a real-time-anchored number.

Gated by **`RT_DIP_MODE` off/shadow/enforce** (default `off` = byte-identical), resolved via the existing `rt_mode()` per-bot/env helper.

## Components

1. **`core/realtime_dip.py` (new, pure):**
   - `RollingPriceWindow` — append `(ts, price)`, evict by age + count cap, `window_high(secs)` / `window_low(secs)`.
   - `compute_rt_price_change(buffer, bars, fresh_price, now, horizons=("h1","m5","h6","h24"))` → `(price_change_dict, coverage)` where `coverage ∈ {BARS+BUFFER, BUFFER_ONLY, NONE}`. Pure; never raises; returns `({}, NONE)` when unusable. Horizon→window seconds: `m5=300, h1=3600, h6=21600, h24=86400`. Dip % is measured off the window **high** (`(fresh_price/window_high − 1)×100`), matching the dip-buy semantics of the existing `priceChange`.
2. **`feeds/dexscreener_chart_format.py` (existing):** reuse `parse_chart_bars`; add `rolling_high_from_bars(bars, window_secs)` if not trivially derivable.
3. **`feeds/dip_scanner.py` (modify, minimal):** at the fast-watch reprice site (~6524, where `reprice_all` runs), branch on `RT_DIP_MODE` — update the buffer from the fresh price, fetch+cache io.dexscreener bars, compute the real-time `priceChange`, and in `enforce` write it into `_pair["priceChange"]` instead of the stale-anchor reprice; `shadow` logs divergence only.
4. **Per-token bar cache** (on `DipScanner`): `{bars, fetched_ts}` with a short TTL (~60s) so we don't refetch every 3s tick — the inner buffer covers the gap between bar refreshes.

## Data flow (per fast-watch tick, `RT_DIP_MODE != off`)

1. **Append** the fresh Jupiter price (already fetched this tick) into the token's `RollingPriceWindow` with `now`.
2. **Bar refresh (cache-gated):** if cached bars are older than TTL (60s) or absent, fetch 1m bars (~24h) off-loop via `run_ds_fetch`; cache on success. Hot/dipping subset also pulls 1S bars.
3. **Compute reference:** `rolling_high[h] = max(bar highs in window) ∪ (buffer maxima in window)` — bars give historical depth, the buffer gives the last-~60s freshness bars lack. `compute_rt_price_change` returns the `{h1,m5,h6,h24}` dict + coverage stamp.
4. **Apply:**
   - `enforce`: overwrite `_pair["priceChange"]` with the real-time dict → unchanged gate chain decides on a real-time-anchored dip.
   - `shadow`: leave `priceChange` as-is; log `rt_pc` vs `stale_pc` vs `reprice_all_pc` + would-fire delta.
   - `off`: skip entirely (byte-identical).

## Error handling & fallbacks (strict freshness ladder — never fail-open into a buy)

- **io.dexscreener throttle/403/timeout:** fall back to **buffer-only** reference (`coverage=BUFFER_ONLY`) — still real-time for the recent window, shallower history.
- **Buffer too shallow AND bars unavailable** (`coverage=NONE` — token just discovered + io.dx down): in `enforce`, **do not fabricate a dip** — fall back to the existing `reprice_all` path. Guarantees the rebuild is never worse than today: worst case = status quo.
- **Staleness guard:** each reference carries its newest-sample age; if the freshest input exceeds max-age (90s), mark the horizon stale and fall back rather than gate on a frozen number.
- **Bad/empty bars, non-positive prices, parse failure:** `compute_rt_price_change` returns `({}, NONE)` → caller falls back. Pure, never raises.
- **Loop safety:** all io.dexscreener fetches stay off the event loop (`run_ds_fetch`/`to_thread`), matching the existing chart path — no new loop-block risk.

## Live-mode behavior

This subsystem works in **live exactly as in paper** — both data sources (Jupiter buffer, io.dexscreener bars) are external market data fetched identically regardless of `PAPER_MODE`, and the real-time `priceChange` feeds the same gate chain in both modes. There is no paper-only crutch.

- **Detection vs execution — they compose.** This fixes detection (seeing the dip in real time). The live execution leg (build swap → sign → RPC → on-chain confirm ~2–3s, with `BUY_REPRICE_MODE=enforce` aborting on >5% decision→fill drift) is already built. Real-time detection makes that leg **better**: a fresher, real-time-anchored decision price sits closer to the actual fill, so `BUY_REPRICE` aborts less and more intended entries land live. This is the missing front half of the live entry path whose back half already exists.
- **Safe degradation in live.** If io.dexscreener throttles/403s mid-session, or on a fresh deploy when the in-memory buffer is cold, the freshness ladder degrades (buffer-only → `reprice_all` → stale) and **never fabricates a dip**. Worst case in live = today's behavior, never a phantom real-money buy.

## Shadow validation & rollout

`RT_DIP_MODE=shadow` accrues a divergence log per candidate: `{rt_pc_h1, stale_pc_h1, reprice_pc_h1, coverage, would_fire_rt, would_fire_stale}`. Promote to `enforce` only when shadow shows (a) the real-time anchor catches materially deeper/fresher dips than the stale one (catastrophic-miss-rate metric, as RT_TRIGGER used), and (b) `coverage=BARS+BUFFER` on the large majority of candidates (io.dx breadth holds at scale).

Default ships `off`. **Paper-enforce first**; live-enforce only via the go-live runbook (live is paused for cost). Design is live-correct from day one.

## Testing (TDD, per component)

- **`core/realtime_dip.py` (pure, the bulk):** ring-buffer append/evict by age + count; `window_high`/`window_low` correctness; `compute_rt_price_change` per horizon; coverage stamping (BARS+BUFFER / BUFFER_ONLY / NONE); shallow-buffer→fallback; non-positive/empty→`({}, NONE)`; staleness→stale-mark. No network.
- **`rolling_high_from_bars`:** window slicing, empty bars, malformed bars.
- **Integration (mode dispatch):** `off` = byte-identical (priceChange untouched); `shadow` = priceChange untouched + divergence recorded; `enforce` = priceChange overwritten with rt dict, and `coverage=NONE` falls back to `reprice_all`.
- **`test_pre_live_invariants.py`** runs green before any deploy.

## Global Constraints

- **Free tools only** — no paid RPC/Jupiter/DexScreener key. io.dexscreener via keyless `curl_cffi impersonate=chrome` (Origin/Referer headers), Jupiter keyless batch.
- **Every new behavior behind `RT_DIP_MODE` off/shadow/enforce**, per-bot resolvable via `rt_mode()`; default `off` = byte-identical.
- **Paper-validate in shadow then paper-enforce before any live enable.**
- **Never fail-open into a buy** — `coverage=NONE` falls back to existing behavior, never fabricates a dip.
- **All external fetches off the event loop** (`run_ds_fetch`/`to_thread`).
- **Never flip `PAPER_MODE` without approval;** live enable follows the go-live runbook.
- **`test_pre_live_invariants.py` green before any deploy.**
