# Sub-Project 4: Synthesis & Attribution Tooling — Design Spec

**Status:** Awaiting user spec review
**Date:** 2026-05-23
**Parent project:** Multi-bot fleet (Sub-project 4 of 5)
**Depends on:** Sub-projects 1-3 shipped (49-bot fleet running in production)

---

## Goal

Build the analytics layer that mines the 49-bot fleet's trade data to answer three questions:

1. **Which configs win?** Per-bot leaderboard with $/tr, throughput, drawdown, win rate.
2. **Why?** Attribution of $/tr contribution to individual filters, filter categories, regime conditions.
3. **What's the best-of?** Synthesize a "champion proposal" config from winning field values, written to `config/bots/champion_proposal.json` (left disabled — user enables manually after review).

After this sub-project we have the tools to identify which experimental config beats baseline, with reproducible attribution that survives more data.

---

## Architecture

**Block 1 — Five analytics scripts** that consume `/api/trades?full=1` and produce markdown reports:

| Script | Output(s) | Question answered |
|---|---|---|
| `scripts/sp4_leaderboard.py` | `reports/leaderboard.md` | Which bots are winning right now (by metric)? |
| `scripts/sp4_filter_attribution.py` | `reports/filter_attribution.md` | Per filter: $/tr contribution = baseline_$/tr − no_filter_X_$/tr |
| `scripts/sp4_category_attribution.py` | `reports/category_attribution.md` | Per category: $/tr contribution = baseline − no_<category>_filters |
| `scripts/sp4_regime_stratify.py` | `reports/regime_stratify.md` | Per bot × regime: $/tr in sol-red vs sol-green, day vs night, etc. |
| `scripts/sp4_champion_synthesis.py` | `config/bots/champion_proposal.json` + `reports/champion_synthesis.md` | Which field values win? Synthesize the "best-of" config. |

All scripts: read-only on production via `/api/trades?full=1`. Write markdown + (for champion) JSON. Run on-demand via `python scripts/sp4_<name>.py`.

**Block 2 — Dashboard analytics views** add new endpoints + an ATTRIBUTION tab:

| New endpoint | Returns |
|---|---|
| `GET /api/attribution/filters` | Per-filter contribution table (filter name, baseline n, ablation n, $/tr_delta, confidence) |
| `GET /api/attribution/categories` | Per-category contribution table |
| `GET /api/attribution/regimes?bot_id=X` | Per-regime $/tr for a given bot |
| `GET /api/bots/{bot_id}/details` | Full bot drill-down: config diff vs baseline, recent trades, regime breakdown, WR trend |
| `GET /api/champion_proposal` | Current proposed champion config + reasoning markdown + diff vs baseline |

UI: new "ATTRIBUTION" tab on the dashboard with three sub-panels (filters / categories / regimes) + a "Champion Preview" card.

**Block 3 — Sample-size + significance guards** are embedded in every report and dashboard view:

- Sample size shown next to each metric (e.g., "n=12 trades")
- Warning label when n < 20 ("low confidence — wait for more data")
- Spread metric (stddev or IQR) shown to indicate noise
- No formal hypothesis testing (sample sizes too thin for t-tests in current data window)

**Block 4 — Champion enablement is MANUAL.** The synthesis script writes a config with `enabled=false`. The user reviews the rationale, then either flips to `enabled=true` and deploys, edits the proposal first, or discards and re-runs after more data accumulates.

---

## Components

### Shared helper: `scripts/sp4_common.py`

Provides utility functions used by all five scripts:
- `fetch_all_trades(min_n=10)` — pull `/api/trades?full=1&limit=2000` from production
- `pair_buys_sells(trades) -> list[PairedTrade]` — match buy/sell records by (bot_id, token, entry_price)
- `compute_metrics(paired_trades) -> BotMetrics` — total_pnl, $/tr, win_rate, drawdown, sample_n
- `confidence_label(n) -> str` — returns "OK" / "Low (n<20)" / "Very low (n<5)"
- `BOT_CATEGORIES` — list of the 6 group bot names

### Per-script logic

#### `sp4_leaderboard.py`
1. Fetch trades
2. Group by bot_id
3. Compute metrics per bot
4. Render markdown table sorted by user-selected metric (default `throughput_x_pnl`)
5. Write `reports/leaderboard.md`

#### `sp4_filter_attribution.py`
1. Fetch trades
2. Compute baseline_v1 metrics
3. For each of the 10 `no_<X>` ablation bots: compute metrics, compute delta vs baseline
4. Render markdown table: filter_name | baseline_$/tr (n) | ablation_$/tr (n) | $/tr_delta | confidence
5. Sort by delta descending

#### `sp4_category_attribution.py`
Identical to filter_attribution but for the 6 `no_<category>_filters` bots.

#### `sp4_regime_stratify.py`
1. Fetch trades with entry_meta intact
2. For each bot, bucket trades by:
   - sol_pc_h1 regime: red (<-0.3), flat (-0.3 to 0.3), green (>0.3)
   - pc_h24 bucket: deep_red (<-20), red (-20 to -5), flat (-5 to 5), green (5 to 30), pumped (>30)
   - time-of-day: hour of UTC
3. Per bot × bucket: $/tr + sample_n + confidence
4. Output: nested markdown table (bot rows × regime columns)

#### `sp4_champion_synthesis.py`
The hardest script. Reads attribution + leaderboard outputs and proposes a champion config:

1. **Start from baseline_v1.json**
2. **Filter set:** for each filter where `filter_attribution.delta < -0.05` (filter hurts $/tr), add to champion's `filters_disabled`
3. **Trigger sizing:** if no_alpha_sizing.$/tr ≥ baseline.$/tr, set alpha_multiplier=1.0 in champion
4. **Concurrency:** pick the max_concurrent value with best $/tr from {narrow_concurrent, baseline, wide_concurrent}
5. **Stop:** pick the hard_stop_pct with best $/tr from {tight_stop, baseline, wide_stop}
6. **Exit ladder:** pick the best from {baseline_TP1+5/25, runner_tilt_aggressive_TP1+8/33}
7. **Threshold sweeps:** for each swept knob, pick the value with best $/tr
8. **Always:** `enabled=false`, `display_name="Champion proposal (synthesized 2026-XX-XX)"`
9. Write `config/bots/champion_proposal.json`
10. Write `reports/champion_synthesis.md` with the reasoning (which field came from which bot, why)

The synthesis is **greedy field-by-field** — each field picks the value from the winning bot for that dimension. This is not formally optimal (interactions between fields matter), but it's defensible and reproducible. Sub-project 5 can refine.

### Dashboard additions

In `dashboard/web_dashboard.py`:
- Register 5 new GET endpoints (handlers call the same logic as the scripts but return JSON instead of writing files)
- New HTML/CSS/JS for the ATTRIBUTION tab (sortable tables, regime heatmap, champion preview card)
- Tab is keyed off URL hash `#attribution` so it doesn't auto-load (egress-friendly)

---

## Data Flow

```
Production /api/trades?full=1
       │
       ▼
   sp4_common.fetch_all_trades()
       │
       ▼
   pair_buys_sells() — match buys with their sells
       │
       │  (paired list, per bot)
       │
   ┌───┴───────────────────────────────────────────────────────┐
   ▼                ▼              ▼               ▼            ▼
leaderboard    filter_attr    category_attr    regime_stratify  champion
   │              │               │              │              │
   ▼              ▼               ▼              ▼              ▼
reports/       reports/        reports/       reports/      config/bots/
leaderboard.md filter_attr.md  category_attr.md regime.md   champion_proposal.json
                                                             + reports/champion_synthesis.md
```

Dashboard endpoints call the same `sp4_common` helpers + the same per-script logic, but return JSON instead of writing markdown.

---

## Error Handling

1. **Sparse data:** if a bot has zero trades, its metrics return `None` — markdown shows "—" and confidence "very low". No crash.
2. **API failures:** retry once, then fail loudly. Scripts exit with non-zero status. Dashboard endpoints return 500 + error message.
3. **Malformed entry_meta:** treat as missing regime data — bot's trade is counted but doesn't contribute to regime buckets. Logged.
4. **Champion synthesis on insufficient data:** if `baseline_v1.total_trades < 30`, refuse to write — log "insufficient baseline sample, run again after more data" and exit non-zero. Prevents premature ship.

---

## Testing

### Unit tests (`tests/test_sp4_common.py`)
- `pair_buys_sells` handles edge cases: unpaired buy, multi-sell (TP1 partial + TP2 partial), wrong bot_id
- `compute_metrics` returns correct totals on synthetic input
- `confidence_label` boundaries

### Integration tests
- `tests/test_sp4_scripts.py` — feeds synthetic trades into each script, verifies markdown output structure
- `tests/test_sp4_champion.py` — synthesis with known winning configs produces expected output

### Manual validation post-deploy
After scripts ship, run all 5 against production. Inspect outputs. If baseline_v1 has very few trades (fleet just deployed), the reports will be thin — but they should not crash.

---

## What this sub-project does NOT do

- **Statistical significance testing** — sample sizes too thin to be meaningful. Confidence indicators are enough.
- **Phantom parity for non-baseline bots** — 48 bots × phantom mirror = too much code. Champion bot will get phantom parity in Sub-project 5 once it's selected as the production successor.
- **Champion auto-enable** — manual `enabled=true` flip preserves human review. The synthesis is greedy and may produce a bad config; review is critical.
- **Cross-bot interaction analysis** — e.g., "does disabling filter A make filter B work differently?" — would need combinatorial bots; defer to Sub-project 5+ if needed.
- **Trade-level visualization** — per-trade chart drill-down (existing postmortem covers this for individual tokens).

---

## Risks

1. **Greedy synthesis may produce a worse config than baseline.** Field-by-field optimization ignores interactions. **Mitigation:** synthesis writes `enabled=false`; user reviews the rationale doc; user can edit before flipping. If the proposed config is obviously wrong, easy to discard.

2. **Sparse data in early days.** With 49 bots × ~30 trades/day total, some bots may have 0-2 trades for several days. Reports will be sparse. **Mitigation:** confidence labels prevent over-interpretation. Scripts work fine on sparse data; output just has lots of "low confidence" warnings.

3. **Filter attribution depends on baseline_v1 actually firing on the same candidates as `no_filter_X`.** This is true in principle (shared candidate stream), but if some bots crash silently or skip ticks, the comparison degrades. **Mitigation:** filter the trades to recent N-day window where all bots are known to be running.

4. **Dashboard scope creep.** ATTRIBUTION tab could expand indefinitely. **Mitigation:** spec freezes the 5 endpoints + 3 sub-views above. Anything more = Sub-project 4.5.

---

## Approval gate

Before writing the implementation plan:
1. Are the 5 scripts the right ones (leaderboard + filter_attr + category_attr + regime + champion)?
2. Is the dashboard scope right (5 endpoints + ATTRIBUTION tab)?
3. Is the champion synthesis greedy strategy acceptable as v1, with `enabled=false` as the safety gate?
4. Approval to proceed to writing-plans?
