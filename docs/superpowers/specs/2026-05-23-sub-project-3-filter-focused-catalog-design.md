# Sub-Project 3: Filter-Focused Catalog — Design Spec

**Status:** Awaiting user spec review
**Date:** 2026-05-23
**Parent project:** Multi-bot fleet (Sub-project 3 of 5)
**Depends on:** Sub-project 2 (filter chain restructure + 24-bot catalog) shipped

---

## Goal

Add **25 filter-focused bots** to the production fleet so we can attribute $/tr contribution to individual filters, filter groups, and threshold-sweep values. After this sub-project the fleet has **49 bots** running on the same shared candidate stream.

The new bots leverage the Sub-project 2 filter-chain restructure: each filter now populates `_filters_block` instead of using early-`continue`, so per-bot configurations to disable individual filters (or entire groups) actually differentiate buy/skip behavior in production.

---

## Architecture

No new code paths or BotConfig schema fields. Pure JSON-configuration additions, plus a small inventory + categorization research step.

Each SP3 bot is `baseline_v1.json` content with ONE of these changes:
- Group-level: `filters_disabled` = list of filter names in that category (5-10 entries)
- Individual ablation: `filters_disabled` = exactly one filter name
- Threshold sweep: one numeric threshold field set to a swept value

All other fields stay at baseline defaults. This keeps each bot a clean "1-knob change vs baseline" so attribution is unambiguous: any $/tr divergence from baseline is causally attributable to that single config delta.

---

## The 25 new bots

### Block 1: 6 group-level filter tests

Each disables an entire category of filters. The category definitions are produced as a one-time research artifact in Task 2 (`docs/superpowers/notes/filter-categories.md`).

| Bot ID | Category disabled (representative members) |
|---|---|
| `no_macro_filters` | filter_sol_macro_down, filter_macro_panic, filter_macro_panic_premium_rescue, btc-related |
| `no_chart_pattern_filters` | filter_corpse, filter_fake_bounce, filter_blowoff_top, filter_post_pump_corpse, filter_round_trip |
| `no_structural_filters` | filter_topping, filter_falling_knife, filter_mtf_strong_downtrend, filter_lower_low, filter_1h_v_bottom_fake_recovery |
| `no_timing_filters` | filter_1m, filter_1m_steep_fall, filter_1m_dead_vol, filter_sweep_too_recent, filter_confirmation_candle, filter_stale_watch |
| `no_flow_filters` | filter_bs_m5_low, filter_bs_m5_weak, filter_big_trade_size, filter_negative_net_flow_5m, filter_seller_imbalance, filter_quote_asymmetry |
| `no_liquidity_filters` | filter_clean_break_p90, filter_lp_drain, filter_low_volatility, filter_microcap_trap, filter_dev_dumping, filter_dev_rugged |

**Tests:** which CATEGORY of filters contributes net positive $/tr in aggregate? Answers questions like "are macro filters helping or are they over-cautious right now?"

### Block 2: 10 individual filter ablations

Each disables ONE filter — the top 10 by production block rate. The exact list is determined empirically in Task 1 (mine recent Railway logs for `BLOCKED by filter_X` counts).

Format for each bot:
- `bot_id`: `no_filter_X`
- `display_name`: `No filter_X enforced`
- `filters_disabled`: `["filter_X"]`
- All other fields = baseline

**Tests:** which INDIVIDUAL filter contributes the most positive (or negative) $/tr? Some filters may be net negative — turning them off improves the bot.

### Block 3: 9 threshold sweep bots

Sweep tunable threshold values to find optima.

| Knob | Baseline | Sweep values | Bots |
|---|---|---|---|
| `sol_macro_h6_block_threshold` | -0.3 | -0.1, -0.5, -1.0 | `sol_h6_loose` (-0.1), `sol_h6_tight` (-0.5), `sol_h6_extreme` (-1.0) |
| `mcap_psych_pc_h24_max` | 80.0 | 50, 100, 150 | `psych_h24_50`, `psych_h24_100`, `psych_h24_150` |
| `vol_h1_min` | 1000 | 500, 5000, 10000 | `vol_min_500`, `vol_min_5k`, `vol_min_10k` |

**Tests:** is the current threshold value optimal? E.g. sol_h6=-0.3 is the baseline; if `sol_h6_loose` at -0.1 earns more $/tr, we're being too cautious on SOL macro.

---

## Implementation tasks

### Task 1: Mine top-10 filters by production block rate
- Scrape recent Railway log cycles for `BLOCKED by filter_X` line counts
- Aggregate per filter name
- Produce `docs/superpowers/notes/2026-05-23-filter-block-rates.md` ranked list
- Pick top 10 for Block 2 ablations

### Task 2: Define filter categories
- Read all ~40 ENFORCED filters in `feeds/dip_scanner.py`
- Manually assign each to one of 6 categories (macro, chart_pattern, structural, timing, flow, liquidity)
- Produce `docs/superpowers/notes/2026-05-23-filter-categories.md` reference doc
- Used by Block 1 group bot configs

### Tasks 3-5: Write the 25 JSON configs
- T3: 6 group-level bot JSONs
- T4: 10 individual ablation JSONs
- T5: 9 threshold sweep JSONs

### Task 6: Update catalog test to assert 49 bots
- Modify `tests/test_bot_catalog.py` — change `test_catalog_has_24_bots` → `test_catalog_has_49_bots`
- Add assertion tests for each new bot's specific divergence from baseline
- All 49 bots must load cleanly

### Task 7: Deploy + verify
- Push + `railway up --detach`
- Poll `/api/bots` until 49 entries return
- Verify per-bot configurations match expected
- 48-hour soak observations: memory < ~3GB, no error spikes, per-bot trade rate divergence visible

---

## Validation criteria (post-deploy)

Within 48 hours:

1. **All 49 bots load and appear in `/api/bots`** — startup smoke
2. **Group-disabled bots have higher trade rates than baseline** — e.g. `no_chart_pattern_filters` should accept more candidates than baseline since 4-5 filters that were blocking now don't
3. **Individual ablations differentiate** — each `no_filter_X` bot's trade count should differ from baseline proportional to that filter's block rate
4. **Threshold sweeps fan out** — `sol_h6_loose` (-0.1, more strict) blocks more than baseline; `sol_h6_extreme` (-1.0, more lenient) blocks fewer
5. **Memory stays under ~3GB** — measured via Railway memory metrics
6. **No `[BotManager]` error lines in production logs**

After 7 days:
- Each bot should have accumulated ≥10 trades — sample sufficient for visible per-bot $/tr trend (not yet statistically significant for tiny effects, but directional)

---

## Risks

1. **Memory headroom** — 49 × ~50MB ≈ 2.45GB. If we hit OOM, we either upgrade Railway tier (cost concern) or cap the fleet. **Mitigation:** measure within 1 hour of deploy; if memory climbs past 2.5GB, disable the lowest-priority bots (probably sweep ones — easy to re-enable later).

2. **Category boundaries are subjective** — some filters could plausibly belong to multiple categories (e.g. `filter_post_pump_corpse` is "chart pattern" or "structural"?). **Mitigation:** Task 2 picks one category per filter and documents the rationale. The categorization is one-time research, not a runtime structure.

3. **Block-rate ranking may surface filters that look high-volume but are duplicative.** E.g. `filter_real_dip_3` and `filter_real_dip_5` may both fire on the same candidates. **Mitigation:** Task 1 reports overlap so we can pick non-redundant filters.

4. **Threshold sweeps interact with each other in ways the ablations don't.** A bot with `vol_h1_min=10000` (much stricter) may trade so rarely that it has no statistical power within 7 days. **Mitigation:** acceptable trade-off — even 5 trades' worth of $/tr divergence is informative when paired with the baseline reference.

---

## What this sub-project does NOT do

- Pairwise/combo filter tests (e.g. "what if we disable sol_gate AND chart_pattern_filters together?") — combinatorial explosion; defer to Sub-project 4 attribution analytics if interactions matter
- New BotConfig schema fields — all 25 bots use the existing schema
- Phantom parity for new bots — Sub-project 4 deliverable
- Cross-bot attribution analytics — Sub-project 4 deliverable
- Live trading — Sub-project 5 deliverable

---

## Approval gate

Before writing the implementation plan:
1. Does the 49-bot fleet size feel right, or do we want to scale back?
2. Are the 6 group categorizations the right axis (macro/chart_pattern/structural/timing/flow/liquidity)?
3. Are the threshold sweep knobs (sol_h6, pc_h24, vol_h1) the right ones to test first, or do you want different sweeps?
4. Approval to proceed to writing-plans?
