# Universe-Recorder Enrichment — Design Spec (2026-05-27)

**Goal:** Add `chart_score`, dev-wallet, and holder-concentration features to the
universe recorder so we can (1) validate the **rug shadow** and **chart_score<40**
gate on forward data, and (2) build the **>80%-precision rug blocker** the
2026-05-27 live-loser audit pointed to — all **without breaching the $25/mo
Railway cost cap** (the hard constraint that shapes this entire design).

**Status:** spec for review. NOT implementing until approved. Shadow→validate→enforce.

---

## Why this exists
The 7-hr mining run found three things the recorder can't currently validate forward:
- **Rug archetype** (`dev_pct_dumped∈[2,5%]` + `dev_baseline_pct∈[8,12%]` + fake-LP
  injection) caused the TROLL/GIGA-class correlated losses, but `top10_holder_pct`,
  `dev_*`, `lp_locked_pct`, `rugcheck_score` are populated on **<1% of trades** →
  can't mine a reliable blocker.
- **chart_score<40** was the cleanest single real-loss discriminator (89.7% precision)
  but isn't in the recorder → can't validate held-out.
- The recorder records only `pair_features` + `candle_features_at` (pc/bs/vol/liq/
  age/cum) → momentum is already mineable, but rug/chart_score are blind.

## The cost constraint (drives every decision)
The recorder (`scripts/universe_dip_recorder.py::cycle_universe`) already does ~1
DexScreener pair fetch + 1 candle fetch per universe token, gated by liq≥20k/vol≥50k
and deduped to NEW dips only. Adding features must not multiply egress:

- **chart_score → FREE.** The recorder already holds the 1m candles. Compute
  chart_score from those candles in-process. Zero new fetches.
- **dev + holder → BOUNDED RPC.** These need on-chain data (`feeds/dev_wallet.py::
  fetch_dev_features`, async) + a holder-concentration source. Bound it by:
  1. **Tighter gate:** only fetch dev/holder for NEW dips with `liq≥40k` (the
     tradeable set) — not the full liq≥20k universe.
  2. **Per-token cache** (TTL ~6–12h): dev baseline + holder concentration change
     slowly; fetch once per token, reuse on re-detections.
  3. **Per-cycle cap:** `RECORDER_DEV_MAX_PER_CYCLE` (default ~15) hard-limits fetches.
  4. **Env kill-switch:** `RECORDER_DEV_FETCH_ENABLED` (default true) to disable
     instantly if egress spikes.
  - **Estimated added egress:** ~50–150 dev/holder fetches/day (event-driven on new
    liq≥40k dips, cached) — trivial vs the cap, and *bounded* not continuous. Must be
    measured post-deploy (success metric below).

## Architecture / components
1. **`chart_score` helper (new or extracted).** Find where the scanner computes
   chart_score (the `_chart_ctx_dict` pipeline) and extract a **candle-only** scoring
   function the recorder can call. RISK: chart_score may depend on more than candles
   (e.g. MTF context). If so, scope chart_score to what's computable from 1m candles
   and name the field `chart_score_1m` to be honest about the difference. Verify
   during impl; if not cleanly extractable, drop chart_score from Phase 1 and keep
   dev/holder.
2. **Bounded dev/holder fetch in the recorder.** New helper
   `_maybe_fetch_rug_features(token_addr, liq_usd, cache)` →
   `{dev_pct_dumped, dev_baseline_pct, dev_balance_change_pct, top10_holder_pct,
   top1_holder_pct, lp_locked_pct, rugcheck_score}` or `{}` on
   skip/failure. Reuses `fetch_dev_features`; finds the holder/rugcheck source the
   scanner already uses (grep `top10_holder_pct` producer). Gated + cached + capped
   per above. Wrapped in try/except → null features on any error, NEVER breaks the
   recording loop (it's research infra).
3. **Record-schema additions** (in the `ev = {...}` dict, `cycle_universe` ~L302):
   `chart_score` (or `chart_score_1m`), the rug feature block, and two derived shadow
   flags: `shadow_rug_risk = (dev_pct_dumped≥2 AND dev_baseline_pct≥8)`,
   `shadow_chart_score_low = (chart_score < 40)`. Backward compatible (new optional keys).
4. **Coverage metric.** Log per cycle: `% of liq≥40k dips with non-null rug features`.
   Target ≥80% (else the RPC source is unreliable → fix before trusting Phase 2).

## Phasing
- **Phase 1 — enrich (observational, this build):** ship the recorder changes;
  forward outcomes already scheduled by the existing `resolve_outcomes`. No live
  trading change. Watch egress + coverage for 48h.
- **Phase 2 — validate (~1–2 weeks out):** mine the enriched recorder. Does
  `shadow_rug_risk` (or a compound) hit ≥80% loser-precision held-out? Does
  `chart_score<40` separate losers held-out + token-deduped? Report $/blocked-trade.
- **Phase 3 — enforce (only if validated):** promote the validated predicate(s) from
  shadow to ENFORCED filter(s) in the live entry path, with phantom parity in
  `scripts/live_forward_test.py` per [[feedback_phantom_parity]]. Carve-outs audited
  (don't block confirmed winners).

## Testing
- Unit: `_maybe_fetch_rug_features` honors the liq gate, cache (no double-fetch),
  per-cycle cap, and returns `{}` (not raise) on fetch error.
- Unit: shadow-flag predicates (`shadow_rug_risk`, `shadow_chart_score_low`) on
  representative feature dicts.
- Unit: chart_score helper on a candle fixture (if extracted).
- Smoke: recorder cycle runs end-to-end with enrichment disabled AND enabled (mocked
  fetch) — record still written, loop never breaks on a fetch exception.

## Risks
- **Cost cap (primary):** mitigated by gate+cache+cap+kill-switch; MUST measure
  post-deploy egress (the success gate). If it climbs, flip `RECORDER_DEV_FETCH_ENABLED=false`.
- **chart_score extractability:** may not be candle-only → fall back to `chart_score_1m`
  or drop from Phase 1.
- **RPC reliability:** `fetch_dev_features` failures = null coverage (the current
  problem). Retry-once + cache + the coverage metric; if coverage <80%, the holder
  source needs fixing before Phase 2.
- **Recorder is research infra:** any bug poisons all forward research → all
  enrichment wrapped in try/except, feature-flagged, null-on-failure.

## Open questions (resolve in review or impl)
1. Holder-concentration source: what does the scanner use to populate
   `top10_holder_pct`? (grep the producer; reuse it, don't add a new provider.)
2. chart_score: candle-only or needs MTF? (determines Phase-1 inclusion.)
3. Cache TTL + per-cycle cap exact values (start 6h / 15; tune to egress).

## Success criteria
Phase 1: recorder ships, runs 48h, rug-feature coverage ≥80% on liq≥40k dips, **no
measurable Railway egress increase beyond a few %**. Phase 2: a held-out-validated
rug blocker (precision ≥80%) and/or chart_score gate, or an honest "didn't validate."
