# Real-Time Dip Detection Redesign — Design Spec

**Date:** 2026-06-21
**Status:** Approved design (pre-implementation)
**Author:** Claude (Opus) + AxiS

---

## Problem

Our dip-buy fleet detects price flushes **~5 minutes late**, so it enters on the recovery instead of the dip. Proven this session by two Opus code/data agents on a real incident (token HERALD): the strategy decided to buy at HERALD's ~$0.15 flush-low, but that price was a **~2-minute-stale DexScreener snapshot**; by the time the order fired the live price had recovered to ~$0.20. The live probe paid $0.2045 and lost ~$17; paper "fills" at the stale $0.15 that no longer existed.

### Root causes (all verified with file:line)

1. **Stale trigger source.** The deep-flush triggers read `pc_h1`/`pc_m5` from the DexScreener pair snapshot (`feeds/dip_scanner.py:4440-4443`), refreshed on a 30s scan + 60s client cache + ~150s sweep walk. The decision price is `pair["priceUsd"]` (`core/bot_evaluator.py:335`, `feeds/dip_scanner.py:18655/18664`). Even the fast-watch path injects only the fresh `priceUsd` (`:4072-4076`) — **not** `priceChange` — so the authoritative dip gate inside `_evaluate_pair` still reads stale `pc_h1`.
2. **Arming gated on the slow sweep.** A token is armed for fast-watch only if it is in `_cycle_pair_by_addr` or the sticky watchlist (`feeds/dip_scanner.py:3705-3716`, `_fast_arm_subset` `:3673`), both produced by the ~150s sweep. A fresh token flushing is not watched until a later cycle picks it up.
3. **Shared event loop blocks ~34s.** The main-scan sync sweep blocks the single event loop in bursts (live `[loop-lag] blocked ~34.2s` observed), starving fast-watch ticks (3s cadence → 27s gaps when a full sweep runs). Even the real-time loop cannot tick on time.

We already have the real-time feeds (Jupiter batch in `feeds/price_feed.py`, on-chain `accountSubscribe` WS in `core/onchain_ws_feed.py`) and even compute a real-time `rolling_dip_pct` (`core/fast_watch.py:365`) — but these only **reprice the fill** or act as a **loose wake-up**; they never drive the authoritative dip trigger or the demand-turn.

## Goal

Detect flushes in real-time so the bot enters on the **demand-turn (~$0.16–0.17, early bounce)** instead of the **5-minute-late recovery (~$0.20)**. Keep the *validated* edge (`net_flow_15s_imbalance >= 0` demand-turn) intact — just compute it on fresh data.

## Non-goals

- Not changing the strategy philosophy: we still wait for the demand-turn (don't catch a falling knife). We only make that decision on fresh data.
- Not replacing DexScreener for slow/heavy features (rugcheck, liquidity, holders) — those don't move in seconds; reading them slightly stale is fine.
- Not building new paid infrastructure — free tools only (no paid RPC / Jupiter key).

## Constraints

- **Free tools only** (no paid RPC/Jupiter key).
- **Fast fill = fidelity, not a P&L lever** — always fill as fast as possible.
- **Never flip `PAPER_MODE` without explicit approval.** Currently `PAPER_MODE=true` (paused after the HERALD live probe).
- `BUY_REPRICE_MODE=enforce` stays on as a backstop for any live trade.
- Every new behavior ships behind an env flag with `off`/`shadow`/`enforce` semantics so it can be flipped/reverted without a code change.

---

## Decisions (locked during brainstorming)

- **Trigger target:** *staged* — a real-time price-flush ARMS the candidate instantly, then the fastest available demand-turn CONFIRMS within seconds.
- **Validation:** *capped live A/B* — two tiny live bots identical except the trigger (legacy vs real-time), compared on real fills. (Paper P&L is not a valid metric — it fills at stale prices.)
- **Loop block:** *in scope* — unblocking the event loop is part of this redesign.
- **Architecture:** *Approach 1 — rewire in place*. Reuse the proven fast-watch loop; smallest blast radius; preserves the validated `net_flow_15s` edge. On-chain WS push can be layered later as a latency optimization.

---

## Architecture

The slow main scan is demoted from *trigger authority* to a *universe + heavy-feature provider*. The fast loop becomes the authoritative two-stage trigger.

```
MAIN SCAN (30s, slow)            FAST LOOP (~3s, authoritative trigger)
─────────────────────            ────────────────────────────────────
discovers tokens,         ──►    STAGE 0: arm — any watched token polling-fresh,
heavy features (rugcheck,          PLUS real-time arm: a token whose fresh samples
liq, holders) → universe           show a flush even if this cycle has not enriched it
+ sticky watchlist                         │
                                           ▼
                                  STAGE 1: price-flush detect (arms the buy)
                                   recompute fresh_pc_h1 = fresh_price / high_ref − 1
                                   inject into bundle → _evaluate_pair gates on the
                                   LIVE move, not stale pair["priceChange"]["h1"]
                                           │ (candidate is "hot")
                                           ▼
                                  STAGE 2: demand-turn confirm (fires the buy)
                                   fresh fetch_recent_trades on the armed token
                                   → real-time net_flow_15s >= 0  (validated edge)
                                           │
                                           ▼
                                  BUY at the fresh price (decision_mid = fresh)
```

**Data-flow inversions vs today:**
1. Decision price = fresh feed value, not `pair["priceUsd"]` → paper and live decide on the same reality (kills the stale-price illusion at the source).
2. Dip trigger reads a fresh-derived dip, not stale `pc_h1`.
3. Demand-turn reads a fresh trade-flow poll, not 60s-cached trades.
4. The fast loop never starves because the sync sweep is offloaded/chunked.

---

## Components

### Component A — Real-time dip trigger
**Where:** `feeds/dip_scanner.py` `_eval_one_survivor` (~`:4003`, injection at `:4072-4076`), trigger reads at `:4440-4443`; `core/bot_evaluator.py:335`.

**What:** The deep-flush triggers compute `pc_h1 = (current − ref) / ref`. The *reference* (90m/1h high) is slow-moving — stale is acceptable. Only the *current* must be fresh. In `_eval_one_survivor`, where we already inject `_pair["priceUsd"]` with the fresh price, **also recompute the short-horizon dip metrics against the existing high reference using the fresh price**:

```
fresh_pc_h1 = (fresh_price / high_1h_ref) - 1
fresh_pc_m5 = (fresh_price / high_5m_ref) - 1   # if a 5m ref is available; else leave m5 as-is
```

Write these into the bundle so `_evaluate_pair`'s triggers gate on the fresh values instead of `pair["priceChange"]`. Also set `decision_mid_price`/`entry_price` to the fresh price (not `pair["priceUsd"]`).

**High reference source:** the existing slow high reference already used to compute `pc_h1` (DexScreener-derived 1h/90m high). If a clean 1h high is not directly available in the bundle, derive it from `current_price / (1 + pc_h1_snapshot)` (invert the snapshot to recover the reference), which is stale-safe because the high moves slowly.

**Flag:** `RT_TRIGGER_MODE` ∈ {`off`, `shadow`, `enforce`}. `shadow` logs `fresh_pc_h1` vs snapshot `pc_h1` per candidate without changing the buy decision; `enforce` makes the trigger gate on the fresh value.

### Component B — Real-time arming
**Where:** `feeds/dip_scanner.py` `_fast_arm_subset` (`:3673`), candidate build (`:3705-3716`), `in_band` (`:3726`); `core/fast_watch.py` `arm_subset` (`:322-337`).

**What:** Add a real-time arm path: any token in the *polled universe* whose fresh sample shows a flush (rolling_dip beyond `RT_ARM_DIP_PCT`, default to match the deep-flush threshold) is armed **this tick**, independent of whether the ~150s sweep has reached it. Heavy-feature gates (rug/liq/mcap floors) still apply downstream from whatever scan data exists; tokens too fresh to have them are rejected by the existing fail-closed antirug floors — **no new rug exposure**.

**Flag:** `RT_ARM_MODE` ∈ {`off`, `shadow`, `enforce`}. `shadow` logs which tokens *would* have been armed early (and how much sooner) without arming them.

### Component C — Staged demand-turn confirm
**Where:** reuse `dexs_client.fetch_recent_trades(pair, limit=30)` (`feeds/dip_scanner.py:5724`); `net_flow_15s` derivation (`:5741-5757`, `:1967`).

**What:** When Stage 1 marks a candidate hot, Stage 2 fires a **fresh `fetch_recent_trades` for just that token** (armed set is small → bounded egress), recomputes `net_flow_15s` live, and requires `>= 0` to buy — the proven signal, seconds-fresh instead of 60s-cached. The fetch must bypass/short-circuit the 60s client cache for armed tokens (e.g. a dedicated short-TTL call). On fetch failure, **fall back to the cached value** (fail-toward-current-behavior, never fail-open-to-buy).

**Flag:** `RT_DEMAND_TURN_MODE` ∈ {`off`, `shadow`, `enforce`}.

### Component D — Loop unblock
**Where:** main-scan sync sweep (`feeds/dip_scanner.py`, builds on `SCAN_YIELD_EVERY` from e17025b and `to_thread`/`LEDGER_WRITE_OFFLOAD` from 1d37398).

**What:** Offload/chunk the CPU-bound per-pair evaluation sweep off the event loop (extend the existing `to_thread`/`evaluate_all` offload) and/or tighten the cooperative-yield interval, targeting **max loop-block < ~2s**. Measured by the existing `[loop-lag]` instrumentation.

**Pass/fail gate:** real-time detection cannot function on a loop that freezes; sustained loop-block < ~2s is a prerequisite for trusting Components A–C.

**Flag:** reuse/extend existing `SCAN_YIELD_EVERY` and the offload toggle; no new behavior flag required beyond what exists.

### Component E — Live A/B + telemetry
**Where:** `core/live_swap_log.py` record (`feeds/dip_scanner.py:2193`); two bot configs under `config/bots/`.

**What:**
- Add a `trigger_source` field (`legacy` | `realtime`) to the live-swap record.
- Run **two tiny capped live bots, identical except the trigger**:
  - **A (control):** current stale trigger (`RT_*_MODE=off` for this bot).
  - **B (treatment):** real-time trigger (`RT_*_MODE=enforce` for this bot).
  - Both: flat $100, caps $120 inflight / $50 daily-kill / $60 bot, same entry gate otherwise, `BUY_REPRICE_MODE=enforce` backstop.
- The existing telemetry already logs `decision_mid_price`, `reprice_price`, `reprice_runup_pct`, `real_fill_price`, latency, `tx_signature`. The A/B falls out directly: **B should show near-zero `reprice_runup`** (decided on fresh price) and entries far closer to the flush low than A.

**Per-bot flag scoping:** the `RT_*_MODE` flags must be resolvable per-bot (config override) so A and B can run different trigger modes on the same deploy. If per-bot resolution is not already supported for these flags, add it (config key on the bot, falling back to the env default).

---

## Rollout sequence

1. **Component D first** (loop unblock) — without it, real-time ticks don't happen. Verify loop-block < ~2s via `[loop-lag]` logs in paper.
2. **Components A–C in `shadow`** — deploy with `RT_*_MODE=shadow`, paper mode. Confirm the shadow logs show fresh-vs-stale divergence (fresh_pc_h1 catching flushes the stale path misses) and early-arm timing, with no behavior change. This is a *sanity* check that the fresh signal is sane — not the validation.
3. **Capped live A/B** — set up bots A and B, get explicit AxiS approval to flip `PAPER_MODE=false` (with the go-live runbook: reprice-enforce on, daily P&L clear at cutover, capital floor ≤ wallet, do NOT redeploy while holding a live position). Compare real fills.
4. **Decision** — if B's entries are materially closer to the flush low at comparable/better realized P&L, promote the real-time trigger fleet-wide (flip the env default to `enforce`); else iterate from the A/B data.

## Testing

- **Component A:** unit-test `fresh_pc_h1` reconstruction (given fresh_price + snapshot pc_h1, recover ref and recompute) — including the inversion fallback. Property: when fresh_price == snapshot current, fresh_pc_h1 == snapshot pc_h1.
- **Component B:** unit-test the real-time arm predicate (a token with a fresh flush sample arms; one without does not; fail-closed antirug still rejects).
- **Component C:** unit-test demand-turn confirm (fresh net_flow_15s >= 0 → buy; < 0 → block; fetch failure → cached fallback, never fail-open).
- **Component D:** measure loop-block before/after via `[loop-lag]`; assert sustained < ~2s in a paper soak.
- **Component E:** verify `trigger_source` is stamped on every live-swap record; verify A and B resolve different `RT_*_MODE` on the same deploy.
- **Pre-live invariants:** run the existing `test_pre_live_invariants.py` suite before any `PAPER_MODE=false`.

## Risks & mitigations

- **Fresh-but-wrong price (single-source spike).** Mitigation: `BUY_REPRICE_MODE=enforce` backstop already aborts buys that drift >5% at fire; the fresh trigger reduces, not increases, the drift.
- **Early arming → more rug exposure.** Mitigation: heavy antirug floors remain fail-closed; early-arm only changes *when* a token is evaluated, not *whether* the rug gates apply.
- **Trade-poll egress on armed subset.** Mitigation: armed set is small (only currently-flushing tokens); bounded; reuses the existing fetch. Monitor egress in the paper soak.
- **Demand-turn signal divergence if we ever swap the source.** Mitigation: Component C keeps the *exact* `net_flow_15s` computation; only the data freshness changes.
- **Loop offload regressions.** Mitigation: builds on already-shipped offload patterns; gated behind the existing toggles; paper soak before live.

## Success criterion

On the capped live A/B, the real-time bot (B) enters materially closer to the flush low than the legacy bot (A) — concretely, **B's `reprice_runup_pct` near zero** and **B's entry price meaningfully below A's** on the same tokens — at comparable or better realized P&L. That is the honest, fill-based proof immune to the paper stale-price illusion.
