# Sub-Project 2: Core Bot Catalog + Filter-Layer Restructure — Design Spec

**Status:** DRAFT — awaiting Sub-project 1 smoke validation
**Date:** 2026-05-23
**Parent project:** Multi-bot fleet production fleet (Sub-project 2 of 5)
**Depends on:** Sub-project 1 (multi-bot harness) shipped and smoke-validated

---

## Goal

Ship the ~18-bot thesis/ablation catalog AND restructure the scanner's filter-evaluation layer so that filter ablations actually differentiate between bots in production. The Sub-project 1 harness has a known limitation: filters use early-`continue` control flow, so a bot whose config disables filter X still never sees candidates filter X blocked. This sub-project fixes that, then ships the full catalog that exercises both filter and trigger variation.

After this sub-project, we have a working fleet that meaningfully tests:
- Whether each individual filter contributes positive $/tr (single-filter ablations)
- Whether each "thesis" (runner-tilt, scalp-only, regime-aware, etc.) outperforms baseline
- Whether trigger compositions matter (alpha-only, no-alpha-sizing, etc.)

---

## Architecture changes

### Filter evaluation layer restructure (the prerequisite)

**Today (post-Sub-project 1):**
```
for pair in pairs:
    ... compute features ...
    if filter_corpse_match: continue  # blocked here, never reaches multi-bot block
    if filter_fake_bounce_match: continue
    ... 39 more filters ...
    ... compute triggers ...
    # Multi-bot block here — never sees filter-blocked candidates
    if MULTI_BOT_ENABLED:
        bundle = FeatureBundle(filters_block=())  # always empty
        bot_manager.evaluate_all(bundle)
```

**After Sub-project 2:**
```
for pair in pairs:
    ... compute features ...
    # First pass: COLLECT filter verdicts (don't continue)
    filters_block: list[str] = []
    if filter_corpse_match: filters_block.append("filter_corpse")
    if filter_fake_bounce_match: filters_block.append("filter_fake_bounce")
    ... etc ...
    # Triggers ALWAYS computed (regardless of filter verdicts)
    ... compute triggers ...
    # Build bundle with FULL filter info
    bundle = FeatureBundle(filters_block=tuple(filters_block), ...)
    # Legacy single-bot decision: skip if any filter blocks
    if MULTI_BOT_ENABLED:
        bot_manager.evaluate_all(bundle)
        # Run legacy too (for parity check during catalog rollout)
    # Legacy path
    if filters_block:
        continue  # legacy honors all filters
    ... single-bot buy decision ...
```

**Key change:** filter checks become **observational** (record verdict, keep going) instead of **control-flow** (early-continue). The legacy single-bot path then honors them via a single combined check at the end. This is a pattern-preserving refactor — same behavior, different decomposition.

### New BotEvaluator field: trigger-specific gates expanded

Today only `mcap_psych_pc_h24_max` exists as a trigger-specific gate. Sub-project 2 adds similar gates for the other ENFORCED triggers where audit data identified per-trigger regime sensitivities (e.g. `deep_1h_dip_pc_h1_max`). New fields on `BotConfig`:

- `deep_1h_dip_pc_h1_min: Optional[float]` (default None — preserves current behavior)
- `whale_conviction_pc_h24_max: Optional[float]` (default None)
- `concurrent_alpha_sol_h4_min: Optional[float]` (default -0.15, matches current behavior)
- (full list TBD based on which triggers each thesis bot wants to override)

### Removal of "run-both-paths" parity mode

After Sub-project 1 smoke validates that `baseline_v1` bot decisions match the legacy single-bot path for ≥24h, the legacy decision path is **removed**. All trades go through the BotManager fan-out. The "main production" bot becomes `baseline_v1`. This simplifies the scanner and confirms the harness is the single source of truth.

---

## The 18-bot catalog

### 1. Control: baseline_v1
Exact current production HEAD config. The reference for all comparisons.

### 2-9. Single-knob ablations (each one differs from baseline by ONE field)

| Bot ID | Differs from baseline by | What it tests |
|---|---|---|
| `no_sol_gate` | `sol_macro_h*_block_threshold=None` | Does the SOL gate help or hurt? |
| `no_filters` | `filters_enforced=()` | Do filters in aggregate help? |
| `no_alpha_sizing` | `alpha_multiplier=1.0` | Does 1.5x alpha sizing pay? |
| `no_pc_h24_ceiling` | `mcap_psych_pc_h24_max=None` | Was the 9840ffe ceiling a good call? |
| `wide_concurrent` | `max_concurrent_positions=5` | Does the concurrent_alpha thesis (memory) hold up at 5x? |
| `narrow_concurrent` | `max_concurrent_positions=1` | Does narrowing reduce drawdown more than it costs in upside? |
| `tight_stop` | `hard_stop_pct=-10.0` | Does a tighter stop improve $/tr? |
| `wide_stop` | `hard_stop_pct=-20.0` | Does a wider stop let more winners breathe? |

### 10-17. Thesis bots (coherent alternative philosophies)

| Bot ID | Config differences | Thesis being tested |
|---|---|---|
| `strict_alpha_only` | `require_alpha_trigger=True`, `alpha_multiplier=1.5` (matches baseline) | Only alpha triggers fire — do we earn more by being more selective? |
| `runner_tilt_aggressive` | `tp1_pct=8.0`, `tp1_sell_fraction=0.33`, `tp2_pct=20.0`, `tp2_sell_fraction=0.33`, `trail_pp=4.0` | Captures more upside on runners — does the tail justify giving up early-TP1 dollars? |
| `scalp_only` | `tp1_pct=3.0`, `tp1_sell_fraction=1.0`, `tp2_pct=999`, `trail_pp=999` (effectively disabled) | Take 3% and run. Tests whether quick-out scalping beats the full ladder. |
| `regime_aware_bullish` | `sol_macro_h1_block_threshold=0.0`, `btc_macro_h1_block_threshold=0.0` | Only trades when both SOL and BTC are flat-to-up. |
| `microcap_specialist` | `mcap_min=500_000.0`, `mcap_max=3_000_000.0` | Sub-$3M only. Tests the micro-cap upside thesis. |
| `midcap_specialist` | `mcap_min=5_000_000.0`, `mcap_max=25_000_000.0` | $5–25M only. The "mature" zone. |
| `early_token_only` | `age_h_max=24.0` | Tokens <24h old only — pure first-day momentum |
| `mature_token_only` | `age_h_min=168.0` | Tokens >1 week old — established projects |

### 18. Final: champion_proposal
Built after Sub-projects 2+3 generate enough data. Default-disabled at creation; enabled once attribution analytics (Sub-project 4) identify the best config. Placeholder for now.

---

## Files to create/modify

### New files
| Path | Responsibility |
|---|---|
| `config/bots/no_sol_gate.json` | Already exists from Sub-project 1 |
| `config/bots/no_filters.json` | Already exists from Sub-project 1 |
| `config/bots/no_alpha_sizing.json` | NEW |
| `config/bots/no_pc_h24_ceiling.json` | NEW |
| `config/bots/wide_concurrent.json` | NEW |
| `config/bots/narrow_concurrent.json` | NEW |
| `config/bots/tight_stop.json` | NEW |
| `config/bots/wide_stop.json` | NEW |
| `config/bots/strict_alpha_only.json` | NEW |
| `config/bots/runner_tilt_aggressive.json` | NEW |
| `config/bots/scalp_only.json` | NEW |
| `config/bots/regime_aware_bullish.json` | NEW |
| `config/bots/microcap_specialist.json` | NEW |
| `config/bots/midcap_specialist.json` | NEW |
| `config/bots/early_token_only.json` | NEW |
| `config/bots/mature_token_only.json` | NEW |
| `config/bots/champion_proposal.json` | NEW (disabled=true initially) |
| `tests/test_filter_layer_restructure.py` | Test that all 39 ENFORCED filters now report their verdict via `filters_block` field on the FeatureBundle |
| `tests/test_bot_catalog.py` | Each catalog bot loads cleanly, and each ablation differs from baseline by exactly the expected fields |

### Modified files
| Path | Modification |
|---|---|
| `feeds/dip_scanner.py` | The per-token filter chain refactor — each filter populates `filters_block` list instead of early-continue. Legacy decision path gates on `if filters_block` at the END (single check). |
| `core/bot_config.py` | Add new trigger-specific gate fields (e.g. `deep_1h_dip_pc_h1_min`). Update `__post_init__` if any new invariants emerge. |
| `core/bot_evaluator.py` | Honor new trigger-specific gates in `_effective_triggers`. |
| `tests/test_bot_evaluator.py` | New tests for the new gate fields. |
| `tests/test_bot_config.py` | New tests for the new fields. |

---

## Validation criteria

After deploy, within 48 hours:

1. **All 18 bots load on startup** — `/api/bots` returns 18 entries.
2. **Filter ablations differentiate** — `no_filters` bot has materially more total_trades than `baseline_v1` (because it accepts candidates filtered out for everyone else).
3. **No infrastructure regressions** — Railway memory < 1.5GB, egress < $5/day, zero crash loops in 48h.
4. **Per-bot $/tr divergence visible** — at least 3 bots have $/tr distinct from baseline by ≥ $0.10 after ≥ 20 trades each. (If everyone tracks baseline within $0.05, something is wrong with the differentiation.)

---

## Risks

1. **Filter layer restructure could regress legacy single-bot behavior.** Mitigation: extensive integration testing that the rewritten loop produces identical buy/skip decisions for the legacy code path on a fixture of 100 candidates from production trades.

2. **18 bots × $20 base = $360 max in-flight per cycle.** All paper, but high paper exposure means more API calls if any bot's exit logic re-fetches prices. Mitigation: PoolPriceFeed is already shared across bots (Sub-project 1 design).

3. **Trigger-specific gates may not capture intent across configs.** If two thesis bots want to override the same trigger differently, the schema's per-field design might force one to win. Mitigation: revisit schema if conflicts arise during catalog definition.

4. **Memory growth.** 18 bots × ~50MB each ≈ 1GB additional. Current Railway tier should handle but worth measuring before adding Sub-project 3's 25 more bots.

---

## What this sub-project does NOT do

- Add the ~25 filter-focused bots (deferred to Sub-project 3 — filter ablations + threshold sweeps)
- Build cross-bot synthesis / attribution analytics (deferred to Sub-project 4)
- Phantom parity for non-baseline bots (deferred to Sub-project 4 — too much code to wire 17 phantom mirrors)
- Cut over to live trading mode (Sub-project 5)

---

## Open questions for review

1. **Should `champion_proposal` exist as a placeholder in this sub-project, or wait until Sub-project 4 generates the data to populate it?** Leaning towards "wait" — but having the slot reserved makes deployment smoother.

2. **Is 18 the right number for sub-project 2, or should we stage to ~10 first (baseline + 5 ablations + 4 theses) and add more after 1 week of soak?** Leaning towards "ship 18" — config files are cheap; the runtime cost is identical.

3. **Should the filter restructure preserve the early-continue performance optimization at all?** Current early-continue saves a few microseconds per candidate by skipping later filters. With ~30 candidates per cycle × ~40 filters that's measurable but not load-bearing. The restructure makes everything observational, which is cleaner. Leaning towards "yes, refactor fully" — clarity > microseconds.

---

## Approval gate (after Sub-project 1 smoke validates)

Before writing the implementation plan:
1. Is the 18-bot catalog the right list? Any bots to add/remove?
2. Are the filter layer restructure semantics correct (observational filters + single end-of-loop gate for legacy)?
3. Are the validation criteria the right success metrics?
4. Approval to proceed to writing-plans?
