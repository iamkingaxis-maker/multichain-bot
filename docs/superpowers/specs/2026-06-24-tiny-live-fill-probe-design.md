# Tiny Live Fill Probe — Design Spec

**Date:** 2026-06-24
**Status:** SPEC — awaiting AxiS approval. Touches REAL money; nothing builds/runs until explicit go.

## Goal
Measure the **last ~5% of fill accuracy that only a real landed transaction can reveal** — landing latency (price drift between submit and on-chain confirm), MEV/sandwich, and true gap-through — by firing **minimal real swaps** and comparing decision price → actual landed fill. This closes the fill-accuracy question the free quote/sim probe (already live, the ~95%) physically cannot answer, because those effects only exist when a real tx is in a real block.

**It is a MEASUREMENT INSTRUMENT, not a strategy.** Its job is to capture real-fill ground truth, not to make money. Success = a confident answer to "does paper's modeled fill match reality?", at trivial, hard-capped cost.

## What it measures (per probe)
Reuses the existing `live_swap_log` telemetry (already captures all of this):
- `decision_mid_price` → `reprice_price` → `real_fill_price` (the landed fill from balance-delta)
- `fill_vs_mid_slippage_pct`, `reprice_runup_pct`, `ultra_reported_slippage_bps`
- `total_latency_ms` (decision→confirm), `execute_duration_ms`, `priority_fee_lamports`, `sol_spent`
- `tx_signature`, `success`/`failure_reason`, `429`s

**The new derived metric (the whole point):** compare the probe's `real_fill_price` against the **quote-probe's predicted fill** for the same conditions. That delta = the **MEV + landing residual** — the part the free quote couldn't see. If it's ~0, the quote probe (and thus paper) is trustworthy. If it's large/variable, that's the real-execution risk we must price in before going live at size.

## Architecture (reuse, don't rebuild)
- **Execution:** the existing live path — `trader.buy()`/`sell()` via the `live_probe` allowlist + `_execute_swap` (Ultra). The hot key signs; `force_paper` neutralizes everything not on the allowlist (proven C1-C6 fail-closed guards).
- **Config:** a dedicated bot config `fill_probe_live` — `live_probe: true`, **tiny size**, the badday entry gate (so it fires on representative deep-flush dips), on the live allowlist.
- **Round-trip for both legs:** tiny BUY → record landed buy fill → **immediate SELL** (within seconds) → record landed sell fill. Measures entry AND exit fill accuracy with minimal hold/exposure. The round-trip cost (2× slippage + 2× fee on $5) is the "tuition" — cents.
- **Data:** flows to `live_swap_log` (existing) tagged `probe=fill_probe_live`, then into the **fill-calibration scaffold** (already built, ingests `live_swaps`) → calibrates the paper model with REAL landed fills. Plus a comparison vs the quote-probe predictions.

## Blast radius / safety (the heart of this — real money)
Hard, layered caps. This is the only live component while the strategy stays paper-paused:
- `FILL_PROBE_LIVE_SIZE_USD` — default **$5**, hard max **$25** (clamped in code; AxiS tunes representativeness vs risk).
- **One probe inflight at a time** (sequential) — immediate round-trip means exposure ≈ one $5 position for seconds.
- `FILL_PROBE_LIVE_MAX_PER_DAY` — default **20 probes/day**.
- `FILL_PROBE_LIVE_DAILY_LOSS_KILL_USD` — default **$10**; cumulative probe loss past this → stop for the day (enforced, re-derives across restart per the deploy-amnesia fix).
- **Sampling:** 1-in-N badday decisions (`FILL_PROBE_LIVE_SAMPLE`) so it doesn't fire on every signal.
- **Kill switches (any one stops it):** `PAPER_MODE=true` (the global), `FILL_PROBE_LIVE_MODE=off`, removing it from the live allowlist, `enabled:false`.
- **Isolation:** runs on the existing hot wallet (the single live key) under the caps; its accounting is **separate** from the real strategy (it must NOT count toward or interfere with strategy live-trading — and the strategy stays paused, so no conflict).
- **Pre-flight:** `test_pre_live_invariants.py` must pass; `LIVE_CONFIRMED=true` required (existing go-live guard).

## What it fires on
Sampled **badday deep-flush decisions** (the exact trade class we care about) — so the measured fill accuracy is representative of what the strategy actually trades. NOT standalone synthetic decisions.

## Sizing honesty (important)
Slippage/impact is size-dependent; a $5 probe under-measures the impact a $100 trade faces. **That's fine** — impact at the real size is already captured by the quote probe. The tiny probe measures the **size-INDEPENDENT residual** (landing latency, MEV existence, quote-vs-real gap), which is exactly what the quote can't see. Quote (real size, impact) + tiny probe (landing/MEV residual) = the complete picture. Optionally bump size toward $25 for more representative MEV exposure.

## Output / analysis
- `/api/fill-probe-live` (or extend `/api/live-swaps`): n, success rate, median/p90 `total_latency_ms`, real `fill_vs_mid_slippage_pct` dist, and **the residual = real_fill vs quote-predicted** (median/p90), buy-leg and sell-leg.
- Feeds `core/fill_calibration.py` (already built) so the paper slippage model auto-calibrates from real landed fills.

## Validation / exit criteria (it's a campaign, not permanent)
- Accrue **n ≥ 30–50 round-trips** across ≥2 days.
- Produce: the real landing/MEV residual distribution + the paper-model error vs ground truth.
- **Conclusion:** either "quote/paper fill model is accurate to within X% → go-live confidence earned" OR "real fills diverge by Y% → correct the model / reconsider size before live."
- Then the probe **stops** (turn off) — it's measured what it needed; no reason to keep bleeding tuition.

## Open decisions for AxiS (sign-off needed — money)
1. **Size:** $5 default ok, or go to $10–25 for more representative MEV exposure?
2. **Daily caps:** 20 probes/day + $10 daily-loss-kill ok?
3. **Round-trip vs hold:** immediate sell (cleanest, both legs, min exposure) vs hold to the strategy's normal exit (more realistic sell timing, but holds tiny money longer + P&L noise)?
4. **When:** run it now (re-enables a $5 sliver of live while strategy stays paused), or hold until you're closer to a full go-live?

## Non-goals
- Not a profit strategy. Not the strategy at size. Not a permanent live component. Not a replacement for the eventual full go-live decision — it's the instrument that *informs* it.
