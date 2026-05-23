# Sub-Project 3: Filter-Focused Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 25 filter-focused bots (6 group-level + 10 individual ablations + 9 threshold sweeps) so the production fleet grows from 24 → 49 bots, enabling attribution of $/tr contribution to individual filters, filter categories, and threshold values.

**Architecture:** Pure JSON configuration additions on top of the Sub-project 2 filter chain restructure. No new code paths, no schema changes to `BotConfig`. Each new bot is `baseline_v1.json` with exactly ONE field changed (either `filters_disabled` for ablations/groups or a numeric threshold for sweeps).

**Tech Stack:** Python 3.11, pytest, JSON configs. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-05-23-sub-project-3-filter-focused-catalog-design.md](../specs/2026-05-23-sub-project-3-filter-focused-catalog-design.md)

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `docs/superpowers/notes/2026-05-23-filter-block-rates.md` | Task 1 output: filters ranked by production block rate |
| `docs/superpowers/notes/2026-05-23-filter-categories.md` | Task 2 output: 40 filters categorized into 6 groups |
| `scripts/mine_filter_block_rates.py` | Task 1 tool: scrape production logs/trade entry_meta for block counts |
| `config/bots/no_macro_filters.json` | Group-test: disable macro filters |
| `config/bots/no_chart_pattern_filters.json` | Group-test: disable chart-pattern filters |
| `config/bots/no_structural_filters.json` | Group-test: disable structural filters |
| `config/bots/no_timing_filters.json` | Group-test: disable timing filters |
| `config/bots/no_flow_filters.json` | Group-test: disable flow filters |
| `config/bots/no_liquidity_filters.json` | Group-test: disable liquidity filters |
| `config/bots/no_filter_<X>.json` (×10) | Individual ablation: disable filter_X. Names determined by Task 1. |
| `config/bots/sol_h6_loose.json` | Sweep: sol_macro_h6_block_threshold=-0.1 |
| `config/bots/sol_h6_tight.json` | Sweep: sol_macro_h6_block_threshold=-0.5 |
| `config/bots/sol_h6_extreme.json` | Sweep: sol_macro_h6_block_threshold=-1.0 |
| `config/bots/psych_h24_50.json` | Sweep: mcap_psych_pc_h24_max=50.0 |
| `config/bots/psych_h24_100.json` | Sweep: mcap_psych_pc_h24_max=100.0 |
| `config/bots/psych_h24_150.json` | Sweep: mcap_psych_pc_h24_max=150.0 |
| `config/bots/vol_min_500.json` | Sweep: vol_h1_min=500.0 |
| `config/bots/vol_min_5k.json` | Sweep: vol_h1_min=5000.0 |
| `config/bots/vol_min_10k.json` | Sweep: vol_h1_min=10000.0 |

### Modified files

| Path | Modification |
|---|---|
| `tests/test_bot_catalog.py` | Change `test_catalog_has_24_bots` → `test_catalog_has_49_bots`. Add ~25 new per-bot assertion tests. |

---

## Task ordering rationale

T1 (block-rate mining) produces the top-10 filter list that T4 (individual ablations) consumes. T2 (categorization) produces the category mapping that T3 (group bots) consumes. T3-T5 can run in any order since they touch disjoint files. T6 (catalog test) must come after all configs are written. T7 (deploy) is the last gate.

---

## Task 1: Mine production block rates → pick top 10 filters

**Files:**
- Create: `scripts/mine_filter_block_rates.py`
- Create: `docs/superpowers/notes/2026-05-23-filter-block-rates.md`

The production scanner emits per-cycle counter dicts like:
```
filter_corpse_block=5 filter_fake_bounce_block=2 filter_topping_block=3 ...
```

These appear in `[DipScanner] Cycle:` log lines on Railway. Also, every recorded buy in `/api/trades?full=1` has `entry_meta.filter_X_verdict` fields showing every filter's BLOCK/PASS verdict at that moment.

The mining strategy: use the `/api/trades?full=1` endpoint (no Railway log scraping needed). For each recent buy's entry_meta, count how many `filter_X_verdict=BLOCK` show up across the full sample. Higher count = more frequent blocker.

### Step 1: Write the mining script

```python
# scripts/mine_filter_block_rates.py
"""Mine production block rates per ENFORCED filter.

Strategy: for each recent buy in /api/trades, count how many candidates
had filter_X_verdict=BLOCK in the entry_meta. This proxies the production
block rate without log scraping.

Note: this counts SHADOW verdicts too. A filter that records BLOCK but
isn't enforced still shows up. The output flags which are ENFORCED vs
SHADOW by cross-referencing the filter inventory.
"""
import json
from collections import Counter
from pathlib import Path

import requests


PROD_URL = "https://gracious-inspiration-production.up.railway.app/api/trades"
INVENTORY_PATH = (
    Path(__file__).parent.parent
    / "docs" / "superpowers" / "notes"
    / "2026-05-23-filter-chain-inventory.md"
)


def load_enforced_filter_names() -> set[str]:
    """Parse the SP2 inventory file for the canonical list of ENFORCED filter names."""
    if not INVENTORY_PATH.exists():
        return set()
    names: set[str] = set()
    for line in INVENTORY_PATH.read_text().splitlines():
        # Lines look like: "| filter_fake_bounce | 2237 | ~2240 |"
        if line.startswith("|") and "filter_" in line:
            cells = [c.strip() for c in line.split("|")]
            for cell in cells:
                if cell.startswith("filter_") and " " not in cell:
                    names.add(cell)
    return names


def fetch_trades(n: int = 500) -> list[dict]:
    resp = requests.get(f"{PROD_URL}?full=1&limit={n}")
    resp.raise_for_status()
    return resp.json()


def count_block_verdicts(trades: list[dict]) -> Counter:
    block_counts: Counter = Counter()
    for t in trades:
        meta = t.get("entry_meta") or {}
        for key, val in meta.items():
            if not key.endswith("_verdict"):
                continue
            if val != "BLOCK":
                continue
            # Strip the "_verdict" suffix to get the filter name
            filter_name = key[: -len("_verdict")]
            if not filter_name.startswith("filter_"):
                filter_name = f"filter_{filter_name}"
            block_counts[filter_name] += 1
    return block_counts


def main():
    enforced = load_enforced_filter_names()
    print(f"Loaded {len(enforced)} ENFORCED filter names from SP2 inventory")

    trades = fetch_trades(500)
    print(f"Fetched {len(trades)} trades from production")

    buys = [t for t in trades if t.get("type") == "buy"]
    print(f"Analyzing {len(buys)} buys")

    blocks = count_block_verdicts(buys)
    print(f"Found {len(blocks)} distinct filter verdicts across all buys")

    out_path = (
        Path(__file__).parent.parent / "docs" / "superpowers" / "notes"
        / "2026-05-23-filter-block-rates.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Filter block rates — mined 2026-05-23", "", ""]
    lines.append(f"Sample: {len(buys)} buys from production /api/trades.")
    lines.append("")
    lines.append("BLOCK count = number of buy candidates where this filter's verdict was BLOCK")
    lines.append("(SHADOW filters block but don't enforce; ENFORCED filters are marked).")
    lines.append("")
    lines.append("| Rank | Filter name | BLOCK count | ENFORCED? |")
    lines.append("|---:|---|---:|:---:|")

    ranked = blocks.most_common()
    for i, (name, count) in enumerate(ranked, start=1):
        is_enforced = "✓" if name in enforced else "shadow"
        lines.append(f"| {i} | {name} | {count} | {is_enforced} |")

    lines.append("")
    lines.append("## Top 10 ENFORCED filters (use for SP3 Block 2 ablations)")
    lines.append("")
    enforced_only = [(n, c) for n, c in ranked if n in enforced][:10]
    for i, (name, count) in enumerate(enforced_only, start=1):
        lines.append(f"{i}. `{name}` — {count} blocks observed")

    out_path.write_text("\n".join(lines))
    print(f"Wrote ranking to {out_path}")
    print("\nTop 10 ENFORCED:")
    for i, (name, count) in enumerate(enforced_only, start=1):
        print(f"  {i}. {name}: {count}")


if __name__ == "__main__":
    main()
```

### Step 2: Run the mining script

```bash
PYTHONPATH=. python scripts/mine_filter_block_rates.py
```

Expected output includes a "Top 10 ENFORCED" listing — these are the filters to ablation in Task 4.

### Step 3: Verify the output file exists and has the top-10 list

```bash
cat docs/superpowers/notes/2026-05-23-filter-block-rates.md | head -50
```

Expected: ranked table + Top 10 ENFORCED section.

### Step 4: Commit

```bash
git add scripts/mine_filter_block_rates.py docs/superpowers/notes/2026-05-23-filter-block-rates.md
git commit -m "docs(filter-rates): rank ENFORCED filters by production block count

Mined from /api/trades?full=1 (500-trade sample). Counts how many
buy candidates had each filter_X_verdict=BLOCK in entry_meta.

Output: top 10 ENFORCED filters by block count. Used by SP3 Task 4
to pick individual ablation bots.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Categorize 40 ENFORCED filters into 6 groups

**Files:**
- Create: `docs/superpowers/notes/2026-05-23-filter-categories.md`

The 40 ENFORCED filters need to be assigned to one of 6 categories: `macro`, `chart_pattern`, `structural`, `timing`, `flow`, `liquidity`. Each filter belongs to exactly one category (no overlap) — this is a one-time research artifact.

### Step 1: Read the filter inventory from SP2 Task 1

```bash
cat docs/superpowers/notes/2026-05-23-filter-chain-inventory.md
```

This lists all 40 filter names.

### Step 2: Categorize each filter

For each filter, read its block-reason text + line context in `feeds/dip_scanner.py` to determine the category. Use the following category definitions:

- **macro**: SOL/BTC macro context, market-wide regime checks
- **chart_pattern**: candle-shape or short-window price-action patterns (corpse, fake bounce, blowoff top)
- **structural**: multi-timeframe alignment, support/resistance levels, V-bottoms, trend topology
- **timing**: 1-minute or sub-minute freshness/staleness checks, sweep recency, candle confirmation
- **flow**: buy/sell ratios, net flow, big trade size, seller imbalance
- **liquidity**: clean-break liquidity profile, LP drain, microcap traps, dev dumping, volatility floor

### Step 3: Write the categorization file

```markdown
# Filter categorization — 2026-05-23

Each ENFORCED filter assigned to exactly one of 6 categories. Used by
SP3 Block 1 group-level filter test bots.

## macro
- filter_sol_macro_down
- filter_macro_panic
- ... (list all filters in this category)

## chart_pattern
- filter_corpse
- filter_fake_bounce
- filter_blowoff_top
- filter_post_pump_corpse
- filter_round_trip
- ... (list all)

## structural
- filter_topping
- filter_falling_knife
- filter_mtf_strong_downtrend
- filter_lower_low
- filter_1h_v_bottom_fake_recovery
- ... (list all)

## timing
- filter_1m
- filter_1m_steep_fall
- filter_1m_dead_vol
- filter_sweep_too_recent
- filter_confirmation_candle
- filter_stale_watch
- ... (list all)

## flow
- filter_bs_m5_low
- filter_bs_m5_weak
- filter_big_trade_size
- filter_negative_net_flow_5m
- filter_seller_imbalance
- filter_quote_asymmetry
- ... (list all)

## liquidity
- filter_clean_break_p90
- filter_lp_drain
- filter_low_volatility
- filter_microcap_trap
- filter_dev_dumping
- filter_dev_rugged
- filter_meteora_dex
- filter_orca_dex
- ... (list all)

## Verification

Sum of all 6 category lists must equal exactly 40 (the ENFORCED count from
SP2 inventory). Each filter must appear in EXACTLY ONE category.
```

Use `Read` tool on the SP2 inventory and `Grep` for filter names in `feeds/dip_scanner.py` to look up context for each filter when categorizing.

### Step 4: Verify coverage

The total filters across all 6 categories must equal 40 (the count from SP2 inventory). Count manually after writing:

```bash
python -c "
import re
text = open('docs/superpowers/notes/2026-05-23-filter-categories.md').read()
# Count filter names — each line that starts with '- filter_'
filters = re.findall(r'^- (filter_\w+)$', text, re.MULTILINE)
print(f'Total filters categorized: {len(filters)}')
print(f'Distinct: {len(set(filters))}')
assert len(filters) == len(set(filters)), 'duplicates found'
"
```

Expected: 40 total, 40 distinct.

### Step 5: Commit

```bash
git add docs/superpowers/notes/2026-05-23-filter-categories.md
git commit -m "docs(filter-categories): 40 ENFORCED filters categorized into 6 groups

One-time research artifact. Each filter belongs to exactly one category:
macro, chart_pattern, structural, timing, flow, or liquidity.

Used by SP3 Block 1 (group-level filter test bots) to populate each
bot's filters_disabled list.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Write 6 group-level filter test bots

**Files:**
- Create: `config/bots/no_macro_filters.json`
- Create: `config/bots/no_chart_pattern_filters.json`
- Create: `config/bots/no_structural_filters.json`
- Create: `config/bots/no_timing_filters.json`
- Create: `config/bots/no_flow_filters.json`
- Create: `config/bots/no_liquidity_filters.json`

Each bot is `baseline_v1.json` content with two fields changed:
- `bot_id` and `display_name` updated
- `filters_disabled` set to the JSON array of filter names in that category (from Task 2)

### Step 1: Read the baseline template

```bash
cat config/bots/baseline_v1.json
```

This is the template. Every bot below is this exact content with the documented changes.

### Step 2: Read the categorization from Task 2

```bash
cat docs/superpowers/notes/2026-05-23-filter-categories.md
```

Extract the filter list under each `## <category>` heading.

### Step 3: Write `config/bots/no_macro_filters.json`

Same as baseline_v1.json with:
- `bot_id`: `"no_macro_filters"`
- `display_name`: `"No macro filters (group test)"`
- `filters_disabled`: JSON array of every filter under `## macro` in the categorization doc

### Step 4: Write `config/bots/no_chart_pattern_filters.json`

Same as baseline_v1.json with:
- `bot_id`: `"no_chart_pattern_filters"`
- `display_name`: `"No chart-pattern filters (group test)"`
- `filters_disabled`: JSON array of every filter under `## chart_pattern` in the categorization doc

### Step 5: Write `config/bots/no_structural_filters.json`

Same as baseline_v1.json with:
- `bot_id`: `"no_structural_filters"`
- `display_name`: `"No structural filters (group test)"`
- `filters_disabled`: JSON array of every filter under `## structural` in the categorization doc

### Step 6: Write `config/bots/no_timing_filters.json`

Same as baseline_v1.json with:
- `bot_id`: `"no_timing_filters"`
- `display_name`: `"No timing filters (group test)"`
- `filters_disabled`: JSON array of every filter under `## timing` in the categorization doc

### Step 7: Write `config/bots/no_flow_filters.json`

Same as baseline_v1.json with:
- `bot_id`: `"no_flow_filters"`
- `display_name`: `"No flow filters (group test)"`
- `filters_disabled`: JSON array of every filter under `## flow` in the categorization doc

### Step 8: Write `config/bots/no_liquidity_filters.json`

Same as baseline_v1.json with:
- `bot_id`: `"no_liquidity_filters"`
- `display_name`: `"No liquidity filters (group test)"`
- `filters_disabled`: JSON array of every filter under `## liquidity` in the categorization doc

### Step 9: Verify all 6 load

```bash
PYTHONPATH=. python -c "
from core.bot_registry import BotRegistry
from pathlib import Path
reg = BotRegistry.from_directory(Path('config/bots'))
group_bots = ['no_macro_filters', 'no_chart_pattern_filters', 'no_structural_filters',
              'no_timing_filters', 'no_flow_filters', 'no_liquidity_filters']
by_id = {c.bot_id: c for c in reg.configs}
for bid in group_bots:
    assert bid in by_id, f'missing {bid}'
    cfg = by_id[bid]
    n = len(cfg.filters_disabled)
    print(f'{bid:30s} disables {n} filters')
"
```

Expected: 6 lines, each with a non-zero filter count.

### Step 10: Commit

```bash
git add config/bots/no_macro_filters.json config/bots/no_chart_pattern_filters.json config/bots/no_structural_filters.json config/bots/no_timing_filters.json config/bots/no_flow_filters.json config/bots/no_liquidity_filters.json
git commit -m "config(bots): 6 group-level filter test bots

Each disables one filter category from the Task-2 categorization:
- no_macro_filters
- no_chart_pattern_filters
- no_structural_filters
- no_timing_filters
- no_flow_filters
- no_liquidity_filters

Tests: which CATEGORY of filters contributes net positive \$/tr?

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Write 10 individual filter ablation bots

**Files:**
- Create: `config/bots/no_filter_<filter_name>.json` (×10)

Each bot disables exactly one filter — the top 10 by production block rate from Task 1.

### Step 1: Read the top-10 list from Task 1

```bash
cat docs/superpowers/notes/2026-05-23-filter-block-rates.md | head -100
```

Find the "Top 10 ENFORCED" section. Use those 10 filter names.

### Step 2: For each of the top 10, write a JSON config

For each filter name `filter_X` in the top 10, write `config/bots/no_filter_<X>.json` where `<X>` is the filter name MINUS the `filter_` prefix (so filename + bot_id stay tidy).

Example for `filter_corpse`:
- Path: `config/bots/no_filter_corpse.json`
- Same as `baseline_v1.json` content with:
  - `bot_id`: `"no_filter_corpse"`
  - `display_name`: `"No filter_corpse enforced"`
  - `filters_disabled`: `["filter_corpse"]`

Apply this pattern for all 10 filters from the Task 1 list.

### Step 3: Verify all 10 load

```bash
PYTHONPATH=. python -c "
from core.bot_registry import BotRegistry
from pathlib import Path
reg = BotRegistry.from_directory(Path('config/bots'))
ablation_bots = [c for c in reg.configs if c.bot_id.startswith('no_filter_')]
print(f'Loaded {len(ablation_bots)} no_filter_* bots:')
for cfg in ablation_bots:
    print(f'  {cfg.bot_id:35s} disables {list(cfg.filters_disabled)}')
"
```

Expected: 10 lines, each disabling exactly one filter.

### Step 4: Commit

```bash
git add config/bots/no_filter_*.json
git commit -m "config(bots): 10 individual filter ablation bots

Each disables exactly ONE filter from the top-10 production block rate
list mined by Task 1. Tests which filters individually contribute
positive vs negative \$/tr.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Write 9 threshold sweep bots

**Files:**
- Create: `config/bots/sol_h6_loose.json`
- Create: `config/bots/sol_h6_tight.json`
- Create: `config/bots/sol_h6_extreme.json`
- Create: `config/bots/psych_h24_50.json`
- Create: `config/bots/psych_h24_100.json`
- Create: `config/bots/psych_h24_150.json`
- Create: `config/bots/vol_min_500.json`
- Create: `config/bots/vol_min_5k.json`
- Create: `config/bots/vol_min_10k.json`

Each sweep bot is `baseline_v1.json` with ONE threshold value changed.

### Step 1: Write the 3 sol_h6 sweep configs

**`config/bots/sol_h6_loose.json`** — same as baseline_v1.json with:
- `bot_id`: `"sol_h6_loose"`
- `display_name`: `"sol_h6 sweep loose (-0.1)"`
- `sol_macro_h6_block_threshold`: `-0.1`

**`config/bots/sol_h6_tight.json`** — same as baseline_v1.json with:
- `bot_id`: `"sol_h6_tight"`
- `display_name`: `"sol_h6 sweep tight (-0.5)"`
- `sol_macro_h6_block_threshold`: `-0.5`

**`config/bots/sol_h6_extreme.json`** — same as baseline_v1.json with:
- `bot_id`: `"sol_h6_extreme"`
- `display_name`: `"sol_h6 sweep extreme (-1.0)"`
- `sol_macro_h6_block_threshold`: `-1.0`

### Step 2: Write the 3 psych_h24 sweep configs

**`config/bots/psych_h24_50.json`** — same as baseline_v1.json with:
- `bot_id`: `"psych_h24_50"`
- `display_name`: `"mcap_psych pc_h24 max 50"`
- `mcap_psych_pc_h24_max`: `50.0`

**`config/bots/psych_h24_100.json`** — same as baseline_v1.json with:
- `bot_id`: `"psych_h24_100"`
- `display_name`: `"mcap_psych pc_h24 max 100"`
- `mcap_psych_pc_h24_max`: `100.0`

**`config/bots/psych_h24_150.json`** — same as baseline_v1.json with:
- `bot_id`: `"psych_h24_150"`
- `display_name`: `"mcap_psych pc_h24 max 150"`
- `mcap_psych_pc_h24_max`: `150.0`

### Step 3: Write the 3 vol_min sweep configs

**`config/bots/vol_min_500.json`** — same as baseline_v1.json with:
- `bot_id`: `"vol_min_500"`
- `display_name`: `"vol_h1_min sweep 500"`
- `vol_h1_min`: `500.0`

**`config/bots/vol_min_5k.json`** — same as baseline_v1.json with:
- `bot_id`: `"vol_min_5k"`
- `display_name`: `"vol_h1_min sweep 5k"`
- `vol_h1_min`: `5000.0`

**`config/bots/vol_min_10k.json`** — same as baseline_v1.json with:
- `bot_id`: `"vol_min_10k"`
- `display_name`: `"vol_h1_min sweep 10k"`
- `vol_h1_min`: `10000.0`

### Step 4: Verify all 9 load

```bash
PYTHONPATH=. python -c "
from core.bot_registry import BotRegistry
from pathlib import Path
reg = BotRegistry.from_directory(Path('config/bots'))
sweep_bots = ['sol_h6_loose', 'sol_h6_tight', 'sol_h6_extreme',
              'psych_h24_50', 'psych_h24_100', 'psych_h24_150',
              'vol_min_500', 'vol_min_5k', 'vol_min_10k']
by_id = {c.bot_id: c for c in reg.configs}
for bid in sweep_bots:
    assert bid in by_id, f'missing {bid}'
    cfg = by_id[bid]
    print(f'{bid:20s} sol_h6={cfg.sol_macro_h6_block_threshold} psych_h24={cfg.mcap_psych_pc_h24_max} vol_min={cfg.vol_h1_min}')
"
```

Expected: 9 lines, each showing exactly one swept value (others at baseline defaults).

### Step 5: Commit

```bash
git add config/bots/sol_h6_loose.json config/bots/sol_h6_tight.json config/bots/sol_h6_extreme.json config/bots/psych_h24_50.json config/bots/psych_h24_100.json config/bots/psych_h24_150.json config/bots/vol_min_500.json config/bots/vol_min_5k.json config/bots/vol_min_10k.json
git commit -m "config(bots): 9 threshold sweep bots

Sweeps three tunable thresholds at 3 values each (loose / mid / tight):
- sol_macro_h6_block_threshold: -0.1, -0.5, -1.0 (baseline -0.3)
- mcap_psych_pc_h24_max: 50, 100, 150 (baseline 80)
- vol_h1_min: 500, 5000, 10000 (baseline 1000)

Tests: is the current threshold value optimal, or should we tune?

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Update catalog test to assert 49 bots

**Files:**
- Modify: `tests/test_bot_catalog.py`

### Step 1: Update the catalog-size test

In `tests/test_bot_catalog.py`, find:

```python
def test_catalog_has_24_bots(catalog):
    assert len(catalog.configs) == 24, (
        f"Expected 24 bots, got {len(catalog.configs)}: "
        f"{[c.bot_id for c in catalog.configs]}"
    )
```

Change to:

```python
def test_catalog_has_49_bots(catalog):
    assert len(catalog.configs) == 49, (
        f"Expected 49 bots, got {len(catalog.configs)}: "
        f"{[c.bot_id for c in catalog.configs]}"
    )
```

### Step 2: Add tests for the 6 group-level bots

Append to `tests/test_bot_catalog.py`:

```python
# SP3 Block 1 — group-level filter tests
def test_no_macro_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_macro_filters"]
    assert len(bot.filters_disabled) > 0, "should disable at least one macro filter"
    assert bot.filters_enforced is None
    # spot check: sol_macro_down should be disabled
    assert "filter_sol_macro_down" in bot.filters_disabled


def test_no_chart_pattern_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_chart_pattern_filters"]
    assert len(bot.filters_disabled) > 0
    assert "filter_corpse" in bot.filters_disabled or "filter_fake_bounce" in bot.filters_disabled


def test_no_structural_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_structural_filters"]
    assert len(bot.filters_disabled) > 0
    assert "filter_topping" in bot.filters_disabled or "filter_falling_knife" in bot.filters_disabled


def test_no_timing_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_timing_filters"]
    assert len(bot.filters_disabled) > 0


def test_no_flow_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_flow_filters"]
    assert len(bot.filters_disabled) > 0


def test_no_liquidity_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_liquidity_filters"]
    assert len(bot.filters_disabled) > 0


def test_group_bots_have_disjoint_categories(catalog):
    """Each filter should appear in exactly ONE group bot's filters_disabled,
    confirming the categorization is partition-style (no overlap)."""
    group_ids = ["no_macro_filters", "no_chart_pattern_filters",
                 "no_structural_filters", "no_timing_filters",
                 "no_flow_filters", "no_liquidity_filters"]
    by_id = _by_id(catalog)
    seen: dict[str, str] = {}
    for gid in group_ids:
        bot = by_id[gid]
        for f in bot.filters_disabled:
            if f in seen:
                pytest.fail(
                    f"filter {f} appears in both {seen[f]} and {gid} — "
                    "groups must be disjoint"
                )
            seen[f] = gid
```

### Step 3: Add tests for the 10 individual filter ablations

Append:

```python
# SP3 Block 2 — individual filter ablations
def test_individual_ablations_exist(catalog):
    """At least 10 no_filter_<X> bots should exist."""
    by_id = _by_id(catalog)
    no_filter_bots = [bid for bid in by_id if bid.startswith("no_filter_")]
    assert len(no_filter_bots) >= 10, (
        f"Expected ≥10 no_filter_* bots, got {len(no_filter_bots)}: {no_filter_bots}"
    )


def test_individual_ablations_disable_exactly_one(catalog):
    """Each no_filter_<X> bot should disable exactly one filter."""
    by_id = _by_id(catalog)
    for bid, cfg in by_id.items():
        if bid.startswith("no_filter_"):
            assert len(cfg.filters_disabled) == 1, (
                f"{bid} disables {len(cfg.filters_disabled)} filters, expected 1"
            )
            # The disabled filter name should match the bot_id suffix
            disabled = cfg.filters_disabled[0]
            expected_suffix = disabled[len("filter_"):]
            assert bid == f"no_filter_{expected_suffix}", (
                f"{bid} should be no_filter_{expected_suffix} (matches disabled filter {disabled})"
            )
```

### Step 4: Add tests for the 9 threshold sweep bots

Append:

```python
# SP3 Block 3 — threshold sweeps
def test_sol_h6_loose_diff(catalog, baseline):
    bot = _by_id(catalog)["sol_h6_loose"]
    assert bot.sol_macro_h6_block_threshold == -0.1
    assert baseline.sol_macro_h6_block_threshold == -0.3


def test_sol_h6_tight_diff(catalog, baseline):
    bot = _by_id(catalog)["sol_h6_tight"]
    assert bot.sol_macro_h6_block_threshold == -0.5


def test_sol_h6_extreme_diff(catalog, baseline):
    bot = _by_id(catalog)["sol_h6_extreme"]
    assert bot.sol_macro_h6_block_threshold == -1.0


def test_psych_h24_50_diff(catalog, baseline):
    bot = _by_id(catalog)["psych_h24_50"]
    assert bot.mcap_psych_pc_h24_max == 50.0
    assert baseline.mcap_psych_pc_h24_max == 80.0


def test_psych_h24_100_diff(catalog, baseline):
    bot = _by_id(catalog)["psych_h24_100"]
    assert bot.mcap_psych_pc_h24_max == 100.0


def test_psych_h24_150_diff(catalog, baseline):
    bot = _by_id(catalog)["psych_h24_150"]
    assert bot.mcap_psych_pc_h24_max == 150.0


def test_vol_min_500_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_500"]
    assert bot.vol_h1_min == 500.0
    assert baseline.vol_h1_min == 1000.0


def test_vol_min_5k_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_5k"]
    assert bot.vol_h1_min == 5000.0


def test_vol_min_10k_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_10k"]
    assert bot.vol_h1_min == 10000.0
```

### Step 5: Run the catalog test

```bash
PYTHONPATH=. pytest tests/test_bot_catalog.py -v 2>&1 | tail -30
```

Expected: ~50 passed (the original 29 from SP2 + ~21 new).

### Step 6: Commit

```bash
git add tests/test_bot_catalog.py
git commit -m "test(catalog): assert 49 bots load with SP3 ablations + sweeps

Updates catalog size assertion (24 → 49) and adds tests for:
- 6 group-level filter test bots (each disables one category)
- Group-disjoint invariant: no filter in two groups
- 10 individual filter ablations (each disables exactly one filter)
- 9 threshold sweep bots (sol_h6, psych_h24, vol_min)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Deploy + verify

**Files:** None (operational)

### Step 1: Run full test suite locally

```bash
PYTHONPATH=. pytest tests/ 2>&1 | tail -5
```

Expected: all new tests pass; pre-existing 7 failures in `test_exhaustion_realtime.py` remain unchanged.

### Step 2: Push to remote

```bash
git push origin master
```

### Step 3: Deploy to Railway

```bash
MSYS_NO_PATHCONV=1 railway up --detach
```

Expected: build initiated, deploy URL returned.

### Step 4: Poll /api/bots until 49 bots return

```bash
until curl -s -f "https://gracious-inspiration-production.up.railway.app/api/bots" 2>/dev/null | python -c "import sys, json; sys.exit(0 if len(json.load(sys.stdin)) == 49 else 1)" 2>/dev/null; do sleep 20; done
echo "ALL 49 BOTS LIVE at $(date -u +%H:%M:%S)"
```

This blocks until the deploy lands and serves 49 bots.

### Step 5: Confirm bot IDs match expected

```bash
curl -s "https://gracious-inspiration-production.up.railway.app/api/bots" | python -c "
import sys, json
bots = json.load(sys.stdin)
ids = sorted(b['bot_id'] for b in bots)

# Expected bot families
sp1_smoke = ['baseline_v1', 'no_sol_gate', 'no_filters']
sp2_ablations = ['no_alpha_sizing', 'no_pc_h24_ceiling', 'wide_concurrent',
                 'narrow_concurrent', 'tight_stop', 'wide_stop']
sp2_thesis = ['strict_alpha_only', 'runner_tilt_aggressive', 'scalp_only',
              'regime_aware_bullish', 'microcap_specialist', 'midcap_specialist',
              'early_token_only', 'mature_token_only']
sp2_trigger_iso = ['whales_only', 'chart_pattern_only', 'one_sec_only',
                   'flow_only', 'deep_dip_only', 'cnn_cluster_only']
sp2_placeholder = ['champion_proposal']
sp3_groups = ['no_macro_filters', 'no_chart_pattern_filters', 'no_structural_filters',
              'no_timing_filters', 'no_flow_filters', 'no_liquidity_filters']
sp3_sweeps = ['sol_h6_loose', 'sol_h6_tight', 'sol_h6_extreme',
              'psych_h24_50', 'psych_h24_100', 'psych_h24_150',
              'vol_min_500', 'vol_min_5k', 'vol_min_10k']

expected_known = set(sp1_smoke + sp2_ablations + sp2_thesis + sp2_trigger_iso
                     + sp2_placeholder + sp3_groups + sp3_sweeps)
# Plus 10 individual ablations whose names depend on Task 1's mining
individual_ablations = [bid for bid in ids if bid.startswith('no_filter_')]

print(f'Total bots live: {len(bots)}')
print(f'Known expected: {len(expected_known)}')
print(f'Individual ablations (no_filter_*): {len(individual_ablations)}')

missing_known = expected_known - set(ids)
if missing_known:
    print(f'MISSING: {sorted(missing_known)}')

assert len(bots) == 49, f'Expected 49 bots, got {len(bots)}'
assert len(individual_ablations) == 10, f'Expected 10 no_filter_* bots, got {len(individual_ablations)}'
assert not missing_known, f'Missing expected bot_ids: {missing_known}'
print('All bot families present, individual ablations count correct')
"
```

Expected: `All bot families present, individual ablations count correct`

### Step 6: Memory check within 1 hour of deploy

Check Railway memory usage:

```bash
MSYS_NO_PATHCONV=1 railway logs --tail 50 | grep -iE "memory|oom|killed" | tail -10
```

If no memory-related errors, memory headroom is fine. If OOM kills appear, disable the 9 sweep bots first (cheapest to remove since they're variations of existing knobs already covered by the ablations).

### Step 7: 48h soak observations

Track these via curl-and-eyeball:

```bash
# Per-bot trade rate divergence
curl -s "https://gracious-inspiration-production.up.railway.app/api/leaderboard?sort=total_trades" | python -m json.tool

# Per-bot capital position
curl -s "https://gracious-inspiration-production.up.railway.app/api/bots" | python -c "
import sys, json
bots = json.load(sys.stdin)
for b in sorted(bots, key=lambda x: -x.get('total_trades', 0)):
    print(f\"{b['bot_id']:30s} trades={b.get('total_trades', 0):3d} pnl=\${b.get('total_pnl_realized', 0):+7.2f}\")
"
```

Look for:
- Group-disabled bots (no_chart_pattern_filters, etc.) should have MORE trades than baseline
- Individual ablations should diverge from baseline proportional to their filter's block rate
- Threshold sweeps should fan out predictably (loose < baseline < tight in block-rate-affected directions)

### Step 8: Mark SP3 complete + handoff

After 48h shows healthy divergence, update handoff doc:

```bash
git add project_bot_handoff.md
git commit -m "docs(handoff): sub-project 3 shipped — 49-bot fleet live

Filter-focused catalog deployed:
- 6 group-level filter tests
- 10 individual filter ablations (top-10 block rate)
- 9 threshold sweeps (sol_h6, psych_h24, vol_min)

Per-bot \$/tr divergence observable. Memory holding under ~2.5GB.

Next: Sub-project 4 — synthesis + attribution tooling. Use the 49-bot
data to identify which configs win and populate champion_proposal.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Plan self-review

### Spec coverage

- 6 group-level filter tests: Task 3 ✅
- 10 individual filter ablations: Task 4 ✅ (data-driven via Task 1)
- 9 threshold sweeps: Task 5 ✅
- 25 total new bots → 49 total: covered by Tasks 3-5 ✅
- Filter categorization: Task 2 ✅
- Block-rate mining: Task 1 ✅
- Catalog test update: Task 6 ✅
- Deploy + verify: Task 7 ✅
- Memory risk mitigation: Task 7 Step 6 ✅
- 48h soak observations: Task 7 Step 7 ✅

### Placeholder scan

No "TBD" / "TODO". Task 1 says "exact list determined empirically" but provides the script that produces the list — that's not a placeholder, it's data-driven. Task 2 says "manually assign each to one of 6 categories" — the categories are defined explicitly, the assignments are research a subagent does using the inventory + grep.

### Type consistency

- `filters_disabled: tuple[str, ...]` consistent across Tasks 3-5.
- `bot_id` naming convention `no_filter_<X>` consistent across Task 4 implementation and Task 6 tests.
- `BotConfig.from_json` (from SP1 Task 2) consumed by all bot configs.

---

## Risks deferred to execution

1. **Filter categorization is somewhat subjective.** Task 2 requires a human-judgment call for each filter. The subagent should bias toward function (what does the filter check?) rather than implementation (where is it in the code?). Edge cases like `filter_dev_dumping` (could be liquidity or flow) — pick one and document.

2. **Task 1's block-rate mining depends on `/api/trades` returning ≥500 trades with full entry_meta.** If the production fleet is too fresh (few trades yet from SP2 24-bot deploy), counts will be sparse. **Mitigation:** the script falls back to whatever sample it can get. Top-10 ranking is robust under reasonable sample sizes.

3. **Memory exceeded.** 49 × ~50MB ≈ 2.45GB. If Railway tier can't handle: drop the 9 sweep bots first (they're swappable variations of existing knobs, while ablations + groups give unique data).

4. **The 7 pre-existing `test_exhaustion_realtime.py` failures.** These are unrelated to SP3 but will appear in pytest output. The full-suite test in Task 7 Step 1 expects them to remain unchanged.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-23-sub-project-3-filter-focused-catalog-plan.md`.

The plan covers 7 tasks. Tasks 1+2 are research (mining + categorization). Tasks 3-5 are pure JSON authoring. Task 6 is test updates. Task 7 is deploy + observe.

Two execution options:

**1. Subagent-Driven (recommended)** — Fresh subagent per task. Best for Task 2 (filter categorization requires careful judgment) and Task 7 (deploy verification).

**2. Inline Execution** — Faster wall-clock but context grows.

**Recommendation: Subagent-Driven.**
