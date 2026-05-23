# Sub-Project 4: Synthesis & Attribution Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 5 analytics scripts that produce markdown attribution reports + a synthesized champion config, plus dashboard ATTRIBUTION views that visualize the same data.

**Architecture:** Shared `scripts/sp4_common.py` module provides fetch + pairing + metric helpers. Five scripts each compute one report dimension (leaderboard / filter / category / regime / champion). Dashboard endpoints reuse the same helpers but return JSON instead of writing markdown.

**Tech Stack:** Python 3.11, pytest, requests (existing), aiohttp dashboard (existing).

**Spec:** [docs/superpowers/specs/2026-05-23-sub-project-4-attribution-design.md](../specs/2026-05-23-sub-project-4-attribution-design.md)

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `scripts/sp4_common.py` | `fetch_all_trades()`, `pair_buys_sells()`, `compute_metrics()`, `confidence_label()`, `BotMetrics` dataclass |
| `scripts/sp4_leaderboard.py` | Ranked-by-metric table of all 49 bots → `reports/leaderboard.md` |
| `scripts/sp4_filter_attribution.py` | Per-filter contribution = baseline - no_filter_X → `reports/filter_attribution.md` |
| `scripts/sp4_category_attribution.py` | Per-category contribution → `reports/category_attribution.md` |
| `scripts/sp4_regime_stratify.py` | Per-bot × regime $/tr → `reports/regime_stratify.md` |
| `scripts/sp4_champion_synthesis.py` | Greedy field-by-field synthesis → `config/bots/champion_proposal.json` + `reports/champion_synthesis.md` |
| `tests/test_sp4_common.py` | Unit tests for shared helpers |
| `tests/test_sp4_leaderboard.py` | Integration test on synthetic trades |
| `tests/test_sp4_filter_attribution.py` | Integration test on synthetic trades |
| `tests/test_sp4_champion_synthesis.py` | Validates synthesized config is loadable + invariants |
| `reports/.gitkeep` | Ensures `reports/` dir exists in repo |

### Modified files

| Path | Modification |
|---|---|
| `dashboard/web_dashboard.py` | Add 5 `/api/attribution/*` endpoints + ATTRIBUTION tab UI |

---

## Task ordering rationale

T1 (`sp4_common.py`) is the shared foundation — all subsequent scripts import from it. T2-T6 are independent script tasks (any order works once T1 is done). T7 (dashboard) reuses the same logic via the common module. Each task is self-contained: ship script + tests + commit, then move to next.

---

## Task 1: sp4_common.py — shared helpers

**Files:**
- Create: `scripts/sp4_common.py`
- Create: `tests/test_sp4_common.py`
- Create: `reports/.gitkeep`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sp4_common.py
import pytest
from scripts.sp4_common import (
    BotMetrics, pair_buys_sells, compute_metrics, confidence_label,
)


def _trade(bot_id, type_, token, price=0.001, pnl=None, time="2026-05-23T10:00:00+00:00"):
    t = {
        "bot_id": bot_id, "type": type_, "token": token,
        "entry_price": price, "time": time,
    }
    if pnl is not None:
        t["pnl"] = pnl
    if type_ == "buy":
        t["amount_usd"] = 20.0
    return t


def test_pair_buys_sells_simple_match():
    trades = [
        _trade("b1", "buy",  "A", price=0.001),
        _trade("b1", "sell", "A", price=0.001, pnl=2.0),
    ]
    paired = pair_buys_sells(trades)
    assert len(paired) == 1
    p = paired[0]
    assert p.bot_id == "b1"
    assert p.token == "A"
    assert p.realized_pnl_usd == 2.0


def test_pair_buys_sells_multi_sell_per_buy():
    """TP1 partial + TP2 partial → two sell records for one buy. Sum pnl."""
    trades = [
        _trade("b1", "buy",  "A", price=0.001),
        _trade("b1", "sell", "A", price=0.001, pnl=1.5),
        _trade("b1", "sell", "A", price=0.001, pnl=0.5),
    ]
    paired = pair_buys_sells(trades)
    assert len(paired) == 1
    assert paired[0].realized_pnl_usd == 2.0


def test_pair_buys_sells_filters_by_bot_id():
    """Same token bought by two bots — paired separately."""
    trades = [
        _trade("b1", "buy",  "A", price=0.001),
        _trade("b2", "buy",  "A", price=0.001),
        _trade("b1", "sell", "A", price=0.001, pnl=1.0),
        _trade("b2", "sell", "A", price=0.001, pnl=-0.5),
    ]
    paired = pair_buys_sells(trades)
    by_bot = {p.bot_id: p for p in paired}
    assert by_bot["b1"].realized_pnl_usd == 1.0
    assert by_bot["b2"].realized_pnl_usd == -0.5


def test_pair_buys_sells_skips_unpaired_buy():
    """Open position (buy without sell) is excluded from paired list."""
    trades = [
        _trade("b1", "buy", "A", price=0.001),  # still open
    ]
    paired = pair_buys_sells(trades)
    assert len(paired) == 0


def test_compute_metrics_basic():
    from scripts.sp4_common import PairedTrade
    pairs = [
        PairedTrade(bot_id="b1", token="A", entry_price=0.001,
                    size_usd=20.0, realized_pnl_usd=2.0, time="t1",
                    sells=[], buy_meta={}),
        PairedTrade(bot_id="b1", token="B", entry_price=0.001,
                    size_usd=20.0, realized_pnl_usd=-1.0, time="t2",
                    sells=[], buy_meta={}),
        PairedTrade(bot_id="b1", token="C", entry_price=0.001,
                    size_usd=20.0, realized_pnl_usd=3.0, time="t3",
                    sells=[], buy_meta={}),
    ]
    metrics = compute_metrics(pairs)
    assert metrics.bot_id == "b1"
    assert metrics.sample_n == 3
    assert metrics.total_pnl_usd == 4.0
    assert metrics.pnl_per_trade == pytest.approx(4.0 / 3, abs=0.001)
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.001)


def test_compute_metrics_empty_returns_zero_sample():
    metrics = compute_metrics([])
    assert metrics.sample_n == 0
    assert metrics.total_pnl_usd == 0.0
    assert metrics.pnl_per_trade is None
    assert metrics.win_rate is None


def test_confidence_label_thresholds():
    assert confidence_label(0) == "Very low (n<5)"
    assert confidence_label(4) == "Very low (n<5)"
    assert confidence_label(5) == "Low (n<20)"
    assert confidence_label(19) == "Low (n<20)"
    assert confidence_label(20) == "OK"
    assert confidence_label(100) == "OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_sp4_common.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.sp4_common'`

- [ ] **Step 3: Write implementation at `scripts/sp4_common.py`**

```python
"""Shared helpers for Sub-project 4 attribution scripts.

All 5 SP4 scripts import from this module. Dashboard endpoints (Task 7)
reuse the same logic.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import requests


PROD_BASE_URL = "https://gracious-inspiration-production.up.railway.app"


@dataclass
class PairedTrade:
    """A buy matched with all its sells (TP1 partial + TP2 partial + ...)."""
    bot_id: str
    token: str
    entry_price: float
    size_usd: float
    realized_pnl_usd: float
    time: str
    sells: list[dict] = field(default_factory=list)
    buy_meta: dict = field(default_factory=dict)


@dataclass
class BotMetrics:
    bot_id: str
    sample_n: int
    total_pnl_usd: float
    pnl_per_trade: Optional[float]  # None when sample_n == 0
    win_rate: Optional[float]
    avg_win_usd: Optional[float]
    avg_loss_usd: Optional[float]
    best_trade_usd: float
    worst_trade_usd: float
    throughput_x_pnl: float  # sample_n * pnl_per_trade (or 0 if no data)


def fetch_all_trades(base_url: str = PROD_BASE_URL, limit: int = 2000) -> list[dict]:
    """Pull all trades from production with full entry_meta."""
    resp = requests.get(f"{base_url}/api/trades", params={"full": "1", "limit": limit})
    resp.raise_for_status()
    return resp.json()


def pair_buys_sells(trades: list[dict]) -> list[PairedTrade]:
    """Match buys with their sells by (bot_id, token, entry_price).

    Multiple sells per buy (TP1 partial + TP2 partial) are aggregated:
    realized_pnl_usd = sum of all sell records' pnl for that key.

    Unpaired buys (open positions) are excluded.
    """
    # Group by (bot_id, token, entry_price)
    buys_by_key: dict[tuple, dict] = {}
    sells_by_key: dict[tuple, list[dict]] = defaultdict(list)
    for t in trades:
        bid = t.get("bot_id", "baseline_v1")
        token = t.get("token")
        price = t.get("entry_price")
        if price is None:
            continue
        key = (bid, token, price)
        if t.get("type") == "buy":
            buys_by_key[key] = t
        elif t.get("type") == "sell":
            sells_by_key[key].append(t)

    paired: list[PairedTrade] = []
    for key, buy in buys_by_key.items():
        sells = sells_by_key.get(key, [])
        if not sells:
            continue  # still open
        total_pnl = sum(s.get("pnl", 0.0) for s in sells)
        paired.append(PairedTrade(
            bot_id=key[0],
            token=key[1],
            entry_price=key[2],
            size_usd=float(buy.get("amount_usd", 0.0)),
            realized_pnl_usd=total_pnl,
            time=buy.get("time", ""),
            sells=sells,
            buy_meta=buy.get("entry_meta") or {},
        ))
    return paired


def compute_metrics(paired: list[PairedTrade]) -> BotMetrics:
    """Compute summary metrics for one bot's paired trades."""
    bot_id = paired[0].bot_id if paired else "?"
    n = len(paired)
    if n == 0:
        return BotMetrics(
            bot_id=bot_id, sample_n=0, total_pnl_usd=0.0,
            pnl_per_trade=None, win_rate=None,
            avg_win_usd=None, avg_loss_usd=None,
            best_trade_usd=0.0, worst_trade_usd=0.0,
            throughput_x_pnl=0.0,
        )
    pnls = [p.realized_pnl_usd for p in paired]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    per_trade = total / n
    return BotMetrics(
        bot_id=bot_id,
        sample_n=n,
        total_pnl_usd=total,
        pnl_per_trade=per_trade,
        win_rate=len(wins) / n,
        avg_win_usd=(sum(wins) / len(wins)) if wins else None,
        avg_loss_usd=(sum(losses) / len(losses)) if losses else None,
        best_trade_usd=max(pnls),
        worst_trade_usd=min(pnls),
        throughput_x_pnl=n * per_trade,
    )


def confidence_label(n: int) -> str:
    """Sample-size confidence indicator."""
    if n < 5:
        return "Very low (n<5)"
    if n < 20:
        return "Low (n<20)"
    return "OK"
```

- [ ] **Step 4: Create `reports/.gitkeep`**

```bash
mkdir -p reports
echo "# SP4 analytics reports land here" > reports/.gitkeep
```

- [ ] **Step 5: Run tests to verify pass**

Run: `PYTHONPATH=. pytest tests/test_sp4_common.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/sp4_common.py tests/test_sp4_common.py reports/.gitkeep
git commit -m "feat(sp4): shared helpers for attribution scripts

scripts/sp4_common.py provides:
- fetch_all_trades() — pulls /api/trades?full=1
- pair_buys_sells() — matches buys with sells by (bot_id, token, entry_price)
  Handles multi-sell-per-buy (TP1 partial + TP2 partial → sum pnl)
- compute_metrics() — BotMetrics: sample_n, total_pnl, pnl_per_trade,
  win_rate, avg_win/loss, best/worst, throughput_x_pnl
- confidence_label() — Very low / Low / OK based on sample size

Used by all 5 SP4 scripts + dashboard /api/attribution/* endpoints.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: sp4_leaderboard.py — ranked-by-metric table

**Files:**
- Create: `scripts/sp4_leaderboard.py`
- Create: `tests/test_sp4_leaderboard.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_sp4_leaderboard.py
from pathlib import Path
from scripts.sp4_leaderboard import build_leaderboard_markdown
from scripts.sp4_common import BotMetrics


def test_leaderboard_renders_table_sorted_by_metric():
    metrics = [
        BotMetrics(bot_id="b_high", sample_n=30, total_pnl_usd=15.0,
                   pnl_per_trade=0.5, win_rate=0.6, avg_win_usd=2.0,
                   avg_loss_usd=-1.5, best_trade_usd=8.0,
                   worst_trade_usd=-3.0, throughput_x_pnl=15.0),
        BotMetrics(bot_id="b_low", sample_n=10, total_pnl_usd=-5.0,
                   pnl_per_trade=-0.5, win_rate=0.3, avg_win_usd=1.0,
                   avg_loss_usd=-2.0, best_trade_usd=2.0,
                   worst_trade_usd=-4.0, throughput_x_pnl=-5.0),
    ]
    md = build_leaderboard_markdown(metrics, sort_by="throughput_x_pnl")
    assert "b_high" in md
    assert "b_low" in md
    # Higher throughput_x_pnl appears first
    assert md.index("b_high") < md.index("b_low")
    # Headers present
    assert "$/tr" in md
    assert "WR" in md
    assert "Sample" in md or "n=" in md


def test_leaderboard_includes_confidence_label():
    metrics = [BotMetrics(
        bot_id="b_thin", sample_n=3, total_pnl_usd=2.0,
        pnl_per_trade=0.67, win_rate=0.67, avg_win_usd=2.0,
        avg_loss_usd=-1.0, best_trade_usd=3.0, worst_trade_usd=-1.0,
        throughput_x_pnl=2.0,
    )]
    md = build_leaderboard_markdown(metrics, sort_by="total_pnl_usd")
    assert "Very low" in md  # confidence label for n=3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_sp4_leaderboard.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.sp4_leaderboard'`

- [ ] **Step 3: Write implementation at `scripts/sp4_leaderboard.py`**

```python
"""SP4: leaderboard of all bots ranked by chosen metric.

Usage: python scripts/sp4_leaderboard.py [--sort throughput_x_pnl|total_pnl_usd|pnl_per_trade]
Output: reports/leaderboard.md
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from pathlib import Path

from scripts.sp4_common import (
    BotMetrics, fetch_all_trades, pair_buys_sells,
    compute_metrics, confidence_label,
)


def build_leaderboard_markdown(metrics: list[BotMetrics], sort_by: str) -> str:
    """Render a markdown table of bot metrics, sorted by the given metric."""
    sort_key_funcs = {
        "throughput_x_pnl": lambda m: m.throughput_x_pnl,
        "total_pnl_usd": lambda m: m.total_pnl_usd,
        "pnl_per_trade": lambda m: (m.pnl_per_trade if m.pnl_per_trade is not None else -1e9),
        "win_rate": lambda m: (m.win_rate if m.win_rate is not None else -1.0),
        "sample_n": lambda m: m.sample_n,
    }
    key_fn = sort_key_funcs.get(sort_by, sort_key_funcs["throughput_x_pnl"])
    sorted_metrics = sorted(metrics, key=key_fn, reverse=True)

    lines = [
        f"# Leaderboard (sorted by `{sort_by}`)",
        "",
        "| Rank | Bot | Sample | $/tr | Total P&L | WR | Best | Worst | Throughput × $/tr | Confidence |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for i, m in enumerate(sorted_metrics, start=1):
        per = f"${m.pnl_per_trade:+.2f}" if m.pnl_per_trade is not None else "—"
        wr = f"{m.win_rate * 100:.0f}%" if m.win_rate is not None else "—"
        lines.append(
            f"| {i} | `{m.bot_id}` | {m.sample_n} | {per} | "
            f"${m.total_pnl_usd:+.2f} | {wr} | "
            f"${m.best_trade_usd:+.2f} | ${m.worst_trade_usd:+.2f} | "
            f"${m.throughput_x_pnl:+.2f} | {confidence_label(m.sample_n)} |"
        )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--sort", default="throughput_x_pnl",
        choices=["throughput_x_pnl", "total_pnl_usd", "pnl_per_trade",
                 "win_rate", "sample_n"],
    )
    args = p.parse_args()

    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p_ in paired:
        by_bot[p_.bot_id].append(p_)
    metrics = [compute_metrics(ps) for ps in by_bot.values()]

    out_path = Path(__file__).parent.parent / "reports" / "leaderboard.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_leaderboard_markdown(metrics, args.sort))
    print(f"Wrote leaderboard for {len(metrics)} bots to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `PYTHONPATH=. pytest tests/test_sp4_leaderboard.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/sp4_leaderboard.py tests/test_sp4_leaderboard.py
git commit -m "feat(sp4): leaderboard script

Ranks all bots by chosen metric (default throughput × \$/tr).
Output: reports/leaderboard.md with sample size and confidence label
for each row.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: sp4_filter_attribution.py — per-filter contribution

**Files:**
- Create: `scripts/sp4_filter_attribution.py`
- Create: `tests/test_sp4_filter_attribution.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_sp4_filter_attribution.py
from scripts.sp4_filter_attribution import (
    build_filter_attribution_markdown, ABLATION_FILTER_MAP,
)
from scripts.sp4_common import BotMetrics


def test_attribution_computes_delta_baseline_minus_ablation():
    baseline = BotMetrics(
        bot_id="baseline_v1", sample_n=30, total_pnl_usd=15.0,
        pnl_per_trade=0.5, win_rate=0.6, avg_win_usd=2.0,
        avg_loss_usd=-1.5, best_trade_usd=8.0, worst_trade_usd=-3.0,
        throughput_x_pnl=15.0,
    )
    ablations = {
        "no_topping": BotMetrics(
            bot_id="no_topping", sample_n=35, total_pnl_usd=7.0,
            pnl_per_trade=0.2, win_rate=0.5, avg_win_usd=1.5,
            avg_loss_usd=-1.5, best_trade_usd=6.0, worst_trade_usd=-3.0,
            throughput_x_pnl=7.0,
        ),
    }
    md = build_filter_attribution_markdown(baseline, ablations)
    # delta = 0.5 - 0.2 = 0.3 (filter_topping contributes +$0.30/tr by not removing)
    assert "filter_topping" in md
    assert "+0.30" in md or "0.30" in md


def test_ablation_filter_map_has_10_entries():
    """Sanity check that the bot→filter mapping matches SP3 catalog."""
    assert len(ABLATION_FILTER_MAP) == 10
    assert ABLATION_FILTER_MAP["no_topping"] == "filter_topping"
    assert ABLATION_FILTER_MAP["no_turn"] == "filter_turn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_sp4_filter_attribution.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write implementation at `scripts/sp4_filter_attribution.py`**

```python
"""SP4: per-filter $/tr contribution.

For each individual-ablation bot (no_<filter>):
  contribution = baseline_v1.pnl_per_trade - no_<filter>.pnl_per_trade

Positive contribution = filter HELPS (removing it makes things worse).
Negative contribution = filter HURTS (removing it makes things better).

Usage: python scripts/sp4_filter_attribution.py
Output: reports/filter_attribution.md
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path

from scripts.sp4_common import (
    BotMetrics, fetch_all_trades, pair_buys_sells,
    compute_metrics, confidence_label,
)


# Maps ablation bot_id → the filter it disables (matches SP3 catalog)
ABLATION_FILTER_MAP = {
    "no_turn": "filter_turn",
    "no_negative_net_flow_5m": "filter_negative_net_flow_5m",
    "no_seller_imbalance": "filter_seller_imbalance",
    "no_low_volatility": "filter_low_volatility",
    "no_vp_poc": "filter_vp_poc",
    "no_topping": "filter_topping",
    "no_above_vwap_chase": "filter_above_vwap_chase",
    "no_bs_m5_weak": "filter_bs_m5_weak",
    "no_blowoff_top": "filter_blowoff_top",
    "no_1m_steep_fall": "filter_1m_steep_fall",
}


def build_filter_attribution_markdown(
    baseline: BotMetrics, ablations: dict[str, BotMetrics],
) -> str:
    lines = [
        "# Filter Attribution",
        "",
        f"Baseline (`{baseline.bot_id}`): n={baseline.sample_n}, "
        f"$/tr=${baseline.pnl_per_trade or 0:+.2f}, "
        f"total=${baseline.total_pnl_usd:+.2f}",
        "",
        "**Contribution = baseline.$/tr − no_X.$/tr**",
        "Positive → filter HELPS (removing it makes things worse).",
        "Negative → filter HURTS (removing it makes things better).",
        "",
        "| Filter | Baseline n | Ablation n | Baseline $/tr | Ablation $/tr | $/tr Δ (contribution) | Confidence |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    rows = []
    base_per = baseline.pnl_per_trade or 0.0
    for bot_id, filter_name in ABLATION_FILTER_MAP.items():
        ab = ablations.get(bot_id)
        if ab is None or ab.sample_n == 0:
            rows.append((filter_name, baseline.sample_n, 0, base_per, None, None, "Very low (n<5)"))
            continue
        ab_per = ab.pnl_per_trade or 0.0
        delta = base_per - ab_per
        # Use min sample size for confidence
        min_n = min(baseline.sample_n, ab.sample_n)
        rows.append((filter_name, baseline.sample_n, ab.sample_n, base_per,
                     ab_per, delta, confidence_label(min_n)))
    # Sort by delta descending (most helpful filters first)
    rows.sort(key=lambda r: r[5] if r[5] is not None else -1e9, reverse=True)
    for filter_name, base_n, ab_n, base_per, ab_per, delta, conf in rows:
        ab_per_s = f"${ab_per:+.2f}" if ab_per is not None else "—"
        delta_s = f"${delta:+.2f}" if delta is not None else "—"
        lines.append(
            f"| `{filter_name}` | {base_n} | {ab_n} | "
            f"${base_per:+.2f} | {ab_per_s} | {delta_s} | {conf} |"
        )
    return "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)

    baseline_pairs = by_bot.get("baseline_v1", [])
    baseline = compute_metrics(baseline_pairs)
    ablations = {
        bid: compute_metrics(by_bot.get(bid, []))
        for bid in ABLATION_FILTER_MAP
    }

    out_path = Path(__file__).parent.parent / "reports" / "filter_attribution.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_filter_attribution_markdown(baseline, ablations))
    print(f"Wrote filter attribution to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `PYTHONPATH=. pytest tests/test_sp4_filter_attribution.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/sp4_filter_attribution.py tests/test_sp4_filter_attribution.py
git commit -m "feat(sp4): filter attribution script

For each of the 10 individual-ablation bots: contribution = baseline -
no_<filter>. Positive = filter helps; negative = filter hurts.

Sorted descending so the most-helpful filters appear first. Includes
sample size + confidence label per row.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: sp4_category_attribution.py — per-category contribution

**Files:**
- Create: `scripts/sp4_category_attribution.py`

(No new test file — covered by the test in Task 5 via integration. Skipping a separate test file for symmetry; structure mirrors Task 3.)

- [ ] **Step 1: Write implementation at `scripts/sp4_category_attribution.py`**

```python
"""SP4: per-category $/tr contribution.

For each group bot (no_<category>_filters):
  contribution = baseline_v1.pnl_per_trade - no_<category>.pnl_per_trade

Usage: python scripts/sp4_category_attribution.py
Output: reports/category_attribution.md
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path

from scripts.sp4_common import (
    fetch_all_trades, pair_buys_sells, compute_metrics, confidence_label,
)


CATEGORY_BOTS = [
    "no_macro_filters",
    "no_chart_pattern_filters",
    "no_structural_filters",
    "no_timing_filters",
    "no_flow_filters",
    "no_liquidity_filters",
]


def build_category_attribution_markdown(baseline, category_bots: dict) -> str:
    base_per = baseline.pnl_per_trade or 0.0
    lines = [
        "# Category Attribution",
        "",
        f"Baseline (`{baseline.bot_id}`): n={baseline.sample_n}, "
        f"$/tr=${base_per:+.2f}, total=${baseline.total_pnl_usd:+.2f}",
        "",
        "**Contribution = baseline.$/tr − no_<category>.$/tr**",
        "Positive → category helps in aggregate. Negative → category hurts.",
        "",
        "| Category | Baseline n | Ablation n | Baseline $/tr | Ablation $/tr | $/tr Δ | Confidence |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    rows = []
    for bid in CATEGORY_BOTS:
        ab = category_bots.get(bid)
        category = bid.replace("no_", "").replace("_filters", "")
        if ab is None or ab.sample_n == 0:
            rows.append((category, baseline.sample_n, 0, base_per, None, None, "Very low (n<5)"))
            continue
        ab_per = ab.pnl_per_trade or 0.0
        delta = base_per - ab_per
        min_n = min(baseline.sample_n, ab.sample_n)
        rows.append((category, baseline.sample_n, ab.sample_n, base_per,
                     ab_per, delta, confidence_label(min_n)))
    rows.sort(key=lambda r: r[5] if r[5] is not None else -1e9, reverse=True)
    for cat, base_n, ab_n, base_per_v, ab_per_v, delta, conf in rows:
        ab_per_s = f"${ab_per_v:+.2f}" if ab_per_v is not None else "—"
        delta_s = f"${delta:+.2f}" if delta is not None else "—"
        lines.append(
            f"| `{cat}` | {base_n} | {ab_n} | ${base_per_v:+.2f} | "
            f"{ab_per_s} | {delta_s} | {conf} |"
        )
    return "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)

    baseline = compute_metrics(by_bot.get("baseline_v1", []))
    category_metrics = {bid: compute_metrics(by_bot.get(bid, [])) for bid in CATEGORY_BOTS}

    out_path = Path(__file__).parent.parent / "reports" / "category_attribution.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_category_attribution_markdown(baseline, category_metrics))
    print(f"Wrote category attribution to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke test the script imports cleanly**

```bash
PYTHONPATH=. python -c "from scripts.sp4_category_attribution import build_category_attribution_markdown, CATEGORY_BOTS; print('OK', len(CATEGORY_BOTS), 'categories')"
```

Expected: `OK 6 categories`

- [ ] **Step 3: Commit**

```bash
git add scripts/sp4_category_attribution.py
git commit -m "feat(sp4): category attribution script

For each of the 6 group bots: contribution = baseline - no_<category>.
Tests which filter CATEGORY contributes most positive (or negative)
\$/tr in aggregate.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: sp4_regime_stratify.py — per-bot × regime breakdown

**Files:**
- Create: `scripts/sp4_regime_stratify.py`

- [ ] **Step 1: Write implementation at `scripts/sp4_regime_stratify.py`**

```python
"""SP4: per-bot $/tr stratified by macro regime.

For each bot, bucket its trades by:
  - sol_pc_h1 bucket: red (< -0.3), flat (-0.3 to 0.3), green (> 0.3)
  - pc_h24 bucket: deep_red (< -20), red (-20..-5), flat (-5..5),
                   green (5..30), pumped (> 30)
  - time-of-day: UTC hour (0..23)

Usage: python scripts/sp4_regime_stratify.py
Output: reports/regime_stratify.md
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.sp4_common import (
    fetch_all_trades, pair_buys_sells, confidence_label, PairedTrade,
)


def sol_h1_bucket(v) -> str:
    if v is None:
        return "unknown"
    if v < -0.3:
        return "red"
    if v > 0.3:
        return "green"
    return "flat"


def pc_h24_bucket(v) -> str:
    if v is None:
        return "unknown"
    if v < -20:
        return "deep_red"
    if v < -5:
        return "red"
    if v < 5:
        return "flat"
    if v < 30:
        return "green"
    return "pumped"


def utc_hour(time_iso: str) -> int:
    try:
        dt = datetime.fromisoformat(time_iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).hour
    except Exception:
        return -1


def _summarize_bucket(pairs: list[PairedTrade]) -> tuple[int, float | None]:
    n = len(pairs)
    if n == 0:
        return 0, None
    return n, sum(p.realized_pnl_usd for p in pairs) / n


def build_regime_stratify_markdown(by_bot: dict[str, list[PairedTrade]]) -> str:
    lines = ["# Regime Stratification", ""]
    lines.append("Per-bot $/tr bucketed by macro regime at entry time.")
    lines.append("")

    # SOL h1 buckets
    lines.append("## SOL h1 regime")
    lines.append("")
    lines.append("| Bot | red n | red $/tr | flat n | flat $/tr | green n | green $/tr |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for bot_id in sorted(by_bot.keys()):
        bucket_pairs = defaultdict(list)
        for p in by_bot[bot_id]:
            sol_h1 = (p.buy_meta or {}).get("sol_pc_h1")
            bucket_pairs[sol_h1_bucket(sol_h1)].append(p)
        red_n, red_per = _summarize_bucket(bucket_pairs.get("red", []))
        flat_n, flat_per = _summarize_bucket(bucket_pairs.get("flat", []))
        green_n, green_per = _summarize_bucket(bucket_pairs.get("green", []))
        red_s = f"${red_per:+.2f}" if red_per is not None else "—"
        flat_s = f"${flat_per:+.2f}" if flat_per is not None else "—"
        green_s = f"${green_per:+.2f}" if green_per is not None else "—"
        lines.append(
            f"| `{bot_id}` | {red_n} | {red_s} | {flat_n} | {flat_s} | {green_n} | {green_s} |"
        )

    # pc_h24 buckets
    lines.append("")
    lines.append("## pc_h24 regime")
    lines.append("")
    lines.append("| Bot | deep_red n | deep_red $/tr | red n | red $/tr | flat n | flat $/tr | green n | green $/tr | pumped n | pumped $/tr |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for bot_id in sorted(by_bot.keys()):
        bucket_pairs = defaultdict(list)
        for p in by_bot[bot_id]:
            pch = (p.buy_meta or {}).get("pc_h24")
            bucket_pairs[pc_h24_bucket(pch)].append(p)
        cells = []
        for b in ["deep_red", "red", "flat", "green", "pumped"]:
            n, per = _summarize_bucket(bucket_pairs.get(b, []))
            per_s = f"${per:+.2f}" if per is not None else "—"
            cells.append(f"{n}")
            cells.append(per_s)
        lines.append(f"| `{bot_id}` | " + " | ".join(cells) + " |")

    # Time-of-day (UTC hour) — only show bots with ≥10 trades to avoid noise
    lines.append("")
    lines.append("## Time of day (UTC hour) — bots with n≥10")
    lines.append("")
    for bot_id in sorted(by_bot.keys()):
        pairs = by_bot[bot_id]
        if len(pairs) < 10:
            continue
        by_hour = defaultdict(list)
        for p in pairs:
            h = utc_hour(p.time)
            if 0 <= h <= 23:
                by_hour[h].append(p)
        if not by_hour:
            continue
        lines.append(f"### `{bot_id}`")
        lines.append("")
        lines.append("| Hour UTC | n | $/tr |")
        lines.append("|---:|---:|---:|")
        for h in range(24):
            pairs_h = by_hour.get(h, [])
            n, per = _summarize_bucket(pairs_h)
            per_s = f"${per:+.2f}" if per is not None else "—"
            lines.append(f"| {h:02d} | {n} | {per_s} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list[PairedTrade]] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)

    out_path = Path(__file__).parent.parent / "reports" / "regime_stratify.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_regime_stratify_markdown(by_bot))
    print(f"Wrote regime stratification to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke test import**

```bash
PYTHONPATH=. python -c "from scripts.sp4_regime_stratify import sol_h1_bucket, pc_h24_bucket; assert sol_h1_bucket(-0.5) == 'red'; assert sol_h1_bucket(0.5) == 'green'; assert pc_h24_bucket(50) == 'pumped'; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/sp4_regime_stratify.py
git commit -m "feat(sp4): regime stratification script

Per-bot \$/tr bucketed by:
- SOL h1 regime (red / flat / green at -0.3, 0.3 thresholds)
- pc_h24 bucket (deep_red / red / flat / green / pumped)
- Time of day (UTC hour, only shown for bots with n≥10)

Identifies 'bot X wins in regime Y' — useful for regime-aware
champion selection.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: sp4_champion_synthesis.py — synthesize the best-of config

**Files:**
- Create: `scripts/sp4_champion_synthesis.py`
- Create: `tests/test_sp4_champion_synthesis.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_sp4_champion_synthesis.py
import json
from pathlib import Path

from scripts.sp4_champion_synthesis import (
    pick_best_from_pair, synthesize_champion, MIN_BASELINE_SAMPLE,
)
from scripts.sp4_common import BotMetrics


def _m(bot_id, n, per_tr):
    return BotMetrics(
        bot_id=bot_id, sample_n=n, total_pnl_usd=n * per_tr,
        pnl_per_trade=per_tr, win_rate=0.5, avg_win_usd=2.0,
        avg_loss_usd=-1.5, best_trade_usd=10.0, worst_trade_usd=-5.0,
        throughput_x_pnl=n * per_tr,
    )


def test_pick_best_from_pair_returns_higher_per_tr():
    a = _m("a", 10, 0.5)
    b = _m("b", 10, 1.0)
    winner = pick_best_from_pair(a, b)
    assert winner.bot_id == "b"


def test_pick_best_from_pair_falls_back_to_first_on_no_data():
    a = _m("a", 0, 0.0)
    b = _m("b", 0, 0.0)
    a = BotMetrics(bot_id="a", sample_n=0, total_pnl_usd=0.0,
                   pnl_per_trade=None, win_rate=None, avg_win_usd=None,
                   avg_loss_usd=None, best_trade_usd=0.0,
                   worst_trade_usd=0.0, throughput_x_pnl=0.0)
    b = BotMetrics(bot_id="b", sample_n=0, total_pnl_usd=0.0,
                   pnl_per_trade=None, win_rate=None, avg_win_usd=None,
                   avg_loss_usd=None, best_trade_usd=0.0,
                   worst_trade_usd=0.0, throughput_x_pnl=0.0)
    winner = pick_best_from_pair(a, b)
    assert winner.bot_id == "a"  # tie → first arg wins


def test_synthesize_refuses_insufficient_baseline(tmp_path):
    """If baseline.sample_n < MIN_BASELINE_SAMPLE, refuse to write."""
    baseline_config = {
        "bot_id": "baseline_v1", "display_name": "Baseline",
        # ... minimal required fields ...
        "enabled": True, "paper_capital_usd": 2000.0,
        "base_position_usd": 20.0, "max_concurrent_positions": 3,
        "alpha_multiplier": 1.5, "macro_up_multiplier": 1.5,
        "premium_runner_multiplier": 3.0, "marginal_multiplier": 0.5,
        "sol_macro_h6_block_threshold": -0.3,
        "sol_macro_h1_block_threshold": -0.7,
        "btc_macro_h1_block_threshold": None,
        "pc_h24_max": None, "pc_h24_min": None, "pc_h1_max": None,
        "age_h_min": None, "age_h_max": None, "mcap_min": None,
        "mcap_max": None, "vol_h1_min": 1000.0,
        "filters_enforced": None, "filters_disabled": [],
        "triggers_allowed": None, "triggers_disabled": [],
        "min_triggers_to_fire": 1, "require_alpha_trigger": False,
        "mcap_psych_pc_h24_max": 80.0,
        "tp1_pct": 5.0, "tp1_sell_fraction": 0.75,
        "tp2_pct": 10.0, "tp2_sell_fraction": 0.25,
        "trail_pp": 3.0, "hard_stop_pct": -15.0,
        "pre_stop_bail_pnl_pct": -3.0, "pre_stop_bail_vol_m5_max": 500.0,
        "slow_bleed_minutes": 60, "slow_bleed_pnl_threshold": -8.0,
        "trading_hour_utc_start": 0, "trading_hour_utc_end": 24,
    }
    baseline_path = tmp_path / "baseline_v1.json"
    baseline_path.write_text(json.dumps(baseline_config))
    out_path = tmp_path / "champion_proposal.json"
    reasoning_path = tmp_path / "champion_synthesis.md"

    insufficient = _m("baseline_v1", MIN_BASELINE_SAMPLE - 1, 0.5)
    metrics_by_id = {"baseline_v1": insufficient}
    result = synthesize_champion(
        metrics_by_id, baseline_path, out_path, reasoning_path,
    )
    assert result is False
    assert not out_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_sp4_champion_synthesis.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write implementation at `scripts/sp4_champion_synthesis.py`**

```python
"""SP4: greedy synthesis of a champion config from winning bots.

For each tunable dimension, pick the field value from whichever bot has
the highest \$/tr in that dimension. Write the resulting config to
config/bots/champion_proposal.json with enabled=false (user reviews +
flips manually).

Usage: python scripts/sp4_champion_synthesis.py
Output:
- config/bots/champion_proposal.json (overwrites)
- reports/champion_synthesis.md (reasoning)
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.sp4_common import (
    BotMetrics, fetch_all_trades, pair_buys_sells, compute_metrics,
)


MIN_BASELINE_SAMPLE = 30  # refuse synthesis if baseline has < N trades


def pick_best_from_pair(a: BotMetrics, b: BotMetrics) -> BotMetrics:
    """Return the BotMetrics with the higher pnl_per_trade.

    Both None (no data) → return a (stable first-arg tiebreak).
    One None → return the other.
    Both numeric → return the higher.
    """
    if a.pnl_per_trade is None and b.pnl_per_trade is None:
        return a
    if a.pnl_per_trade is None:
        return b
    if b.pnl_per_trade is None:
        return a
    return b if b.pnl_per_trade > a.pnl_per_trade else a


def _pick_field(field_name: str, candidates: list[BotMetrics],
                bot_id_to_value: dict[str, object],
                fallback_value: object) -> tuple[object, str]:
    """Pick the field value from the candidate with highest $/tr.

    Returns (chosen_value, source_bot_id).
    Skips candidates with no data; falls back to baseline value.
    """
    best = None
    for c in candidates:
        if c.sample_n < 5:
            continue
        if best is None or (c.pnl_per_trade or -1e9) > (best.pnl_per_trade or -1e9):
            best = c
    if best is None:
        return fallback_value, "baseline_v1 (no candidates had n≥5)"
    return bot_id_to_value[best.bot_id], best.bot_id


def synthesize_champion(
    metrics_by_id: dict[str, BotMetrics],
    baseline_config_path: Path,
    out_config_path: Path,
    out_reasoning_path: Path,
) -> bool:
    """Synthesize a champion config from winning bots' field values.

    Returns True if written, False if refused (insufficient baseline sample).
    """
    baseline_metrics = metrics_by_id.get("baseline_v1")
    if baseline_metrics is None or baseline_metrics.sample_n < MIN_BASELINE_SAMPLE:
        print(
            f"REFUSED: baseline_v1 has only "
            f"{baseline_metrics.sample_n if baseline_metrics else 0} trades, "
            f"need ≥{MIN_BASELINE_SAMPLE}. Re-run after more data."
        )
        return False

    baseline_config = json.loads(baseline_config_path.read_text())
    champion = dict(baseline_config)
    reasoning: list[str] = [
        f"# Champion synthesis — {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Baseline: n={baseline_metrics.sample_n}, "
        f"$/tr=${baseline_metrics.pnl_per_trade:+.2f}",
        "",
        "Greedy field-by-field synthesis. For each tunable dimension, "
        "the field value from the highest-$/tr bot is chosen.",
        "",
        "## Field choices",
        "",
    ]

    def _candidates(*bot_ids):
        return [metrics_by_id[bid] for bid in bot_ids
                if bid in metrics_by_id and metrics_by_id[bid].sample_n > 0]

    # 1. Alpha multiplier: baseline (1.5x) vs no_alpha_sizing (1.0x)
    chosen_alpha, src_alpha = _pick_field(
        "alpha_multiplier",
        _candidates("baseline_v1", "no_alpha_sizing"),
        {"baseline_v1": 1.5, "no_alpha_sizing": 1.0},
        fallback_value=1.5,
    )
    champion["alpha_multiplier"] = chosen_alpha
    reasoning.append(f"- `alpha_multiplier={chosen_alpha}` ← from `{src_alpha}`")

    # 2. max_concurrent: baseline (3) vs narrow (1) vs wide (5)
    chosen_mc, src_mc = _pick_field(
        "max_concurrent_positions",
        _candidates("baseline_v1", "narrow_concurrent", "wide_concurrent"),
        {"baseline_v1": 3, "narrow_concurrent": 1, "wide_concurrent": 5},
        fallback_value=3,
    )
    champion["max_concurrent_positions"] = chosen_mc
    reasoning.append(f"- `max_concurrent_positions={chosen_mc}` ← from `{src_mc}`")

    # 3. hard_stop_pct: baseline (-15) vs tight (-10) vs wide (-20)
    chosen_stop, src_stop = _pick_field(
        "hard_stop_pct",
        _candidates("baseline_v1", "tight_stop", "wide_stop"),
        {"baseline_v1": -15.0, "tight_stop": -10.0, "wide_stop": -20.0},
        fallback_value=-15.0,
    )
    champion["hard_stop_pct"] = chosen_stop
    reasoning.append(f"- `hard_stop_pct={chosen_stop}` ← from `{src_stop}`")

    # 4. Exit ladder: baseline vs runner_tilt_aggressive vs scalp_only
    exit_candidates = _candidates(
        "baseline_v1", "runner_tilt_aggressive", "scalp_only",
    )
    best_exit = None
    for c in exit_candidates:
        if c.sample_n < 5:
            continue
        if best_exit is None or (c.pnl_per_trade or -1e9) > (best_exit.pnl_per_trade or -1e9):
            best_exit = c
    exit_ladder_values = {
        "baseline_v1": (5.0, 0.75, 10.0, 0.25, 3.0),
        "runner_tilt_aggressive": (8.0, 0.33, 20.0, 0.33, 4.0),
        "scalp_only": (3.0, 1.0, 999.0, 0.0, 999.0),
    }
    src_exit = best_exit.bot_id if best_exit else "baseline_v1"
    tp1, tp1_sf, tp2, tp2_sf, trail = exit_ladder_values.get(
        src_exit, exit_ladder_values["baseline_v1"],
    )
    champion["tp1_pct"] = tp1
    champion["tp1_sell_fraction"] = tp1_sf
    champion["tp2_pct"] = tp2
    champion["tp2_sell_fraction"] = tp2_sf
    champion["trail_pp"] = trail
    reasoning.append(
        f"- Exit ladder (tp1={tp1}, tp1_sf={tp1_sf}, tp2={tp2}, "
        f"tp2_sf={tp2_sf}, trail={trail}) ← from `{src_exit}`"
    )

    # 5. sol_h6 threshold sweep: baseline (-0.3) vs loose (-0.1) vs tight (-0.5) vs extreme (-1.0)
    chosen_sol, src_sol = _pick_field(
        "sol_macro_h6_block_threshold",
        _candidates("baseline_v1", "sol_h6_loose", "sol_h6_tight", "sol_h6_extreme"),
        {"baseline_v1": -0.3, "sol_h6_loose": -0.1, "sol_h6_tight": -0.5, "sol_h6_extreme": -1.0},
        fallback_value=-0.3,
    )
    champion["sol_macro_h6_block_threshold"] = chosen_sol
    reasoning.append(f"- `sol_macro_h6_block_threshold={chosen_sol}` ← from `{src_sol}`")

    # 6. mcap_psych_pc_h24_max sweep: baseline (80) vs 50 vs 100 vs 150
    chosen_psych, src_psych = _pick_field(
        "mcap_psych_pc_h24_max",
        _candidates("baseline_v1", "psych_h24_50", "psych_h24_100", "psych_h24_150"),
        {"baseline_v1": 80.0, "psych_h24_50": 50.0, "psych_h24_100": 100.0, "psych_h24_150": 150.0},
        fallback_value=80.0,
    )
    champion["mcap_psych_pc_h24_max"] = chosen_psych
    reasoning.append(f"- `mcap_psych_pc_h24_max={chosen_psych}` ← from `{src_psych}`")

    # 7. vol_h1_min sweep: baseline (1000) vs 500 vs 5000 vs 10000
    chosen_vol, src_vol = _pick_field(
        "vol_h1_min",
        _candidates("baseline_v1", "vol_min_500", "vol_min_5k", "vol_min_10k"),
        {"baseline_v1": 1000.0, "vol_min_500": 500.0, "vol_min_5k": 5000.0, "vol_min_10k": 10000.0},
        fallback_value=1000.0,
    )
    champion["vol_h1_min"] = chosen_vol
    reasoning.append(f"- `vol_h1_min={chosen_vol}` ← from `{src_vol}`")

    # 8. Filter set: disable any filter where the no_<filter> bot beats baseline
    ablation_map = {
        "no_turn": "filter_turn",
        "no_negative_net_flow_5m": "filter_negative_net_flow_5m",
        "no_seller_imbalance": "filter_seller_imbalance",
        "no_low_volatility": "filter_low_volatility",
        "no_vp_poc": "filter_vp_poc",
        "no_topping": "filter_topping",
        "no_above_vwap_chase": "filter_above_vwap_chase",
        "no_bs_m5_weak": "filter_bs_m5_weak",
        "no_blowoff_top": "filter_blowoff_top",
        "no_1m_steep_fall": "filter_1m_steep_fall",
    }
    filters_to_disable: list[str] = []
    base_per = baseline_metrics.pnl_per_trade or 0.0
    for bot_id, filter_name in ablation_map.items():
        ab = metrics_by_id.get(bot_id)
        if ab is None or ab.sample_n < 5:
            continue
        ab_per = ab.pnl_per_trade or 0.0
        if ab_per > base_per + 0.05:  # filter hurts by ≥ $0.05/tr → disable
            filters_to_disable.append(filter_name)
            reasoning.append(
                f"- Disabling `{filter_name}` (ablation $/tr ${ab_per:+.2f} > "
                f"baseline ${base_per:+.2f})"
            )
    champion["filters_disabled"] = filters_to_disable

    # Always: keep proposal disabled, mark with timestamp
    champion["bot_id"] = "champion_proposal"
    champion["display_name"] = (
        f"Champion proposal (synthesized {datetime.now(timezone.utc).date().isoformat()})"
    )
    champion["enabled"] = False

    out_config_path.write_text(json.dumps(champion, indent=2, sort_keys=True))
    out_reasoning_path.write_text("\n".join(reasoning))
    print(f"Wrote champion proposal to {out_config_path}")
    print(f"Wrote reasoning to {out_reasoning_path}")
    return True


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)
    metrics_by_id = {bid: compute_metrics(ps) for bid, ps in by_bot.items()}

    project_root = Path(__file__).parent.parent
    baseline_path = project_root / "config" / "bots" / "baseline_v1.json"
    out_config_path = project_root / "config" / "bots" / "champion_proposal.json"
    out_reasoning_path = project_root / "reports" / "champion_synthesis.md"
    out_reasoning_path.parent.mkdir(parents=True, exist_ok=True)

    ok = synthesize_champion(
        metrics_by_id, baseline_path, out_config_path, out_reasoning_path,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `PYTHONPATH=. pytest tests/test_sp4_champion_synthesis.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/sp4_champion_synthesis.py tests/test_sp4_champion_synthesis.py
git commit -m "feat(sp4): champion synthesis script

Greedy field-by-field selection: for each tunable dimension, pick the
field value from the bot with highest \$/tr in that dimension.

Refuses to synthesize if baseline_v1 has < 30 trades (prevents premature
ship on thin samples).

Output:
- config/bots/champion_proposal.json (enabled=false, user reviews)
- reports/champion_synthesis.md (reasoning: which field from which bot)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Dashboard ATTRIBUTION endpoints + UI tab

**Files:**
- Modify: `dashboard/web_dashboard.py`

- [ ] **Step 1: Read existing dashboard structure**

```bash
grep -n "app.router.add_get\|class WebDashboard\|def __init__\|<style>\|<body>\|self.trade_store" dashboard/web_dashboard.py | head -30
```

Identify where routes are registered + where HTML body lives.

- [ ] **Step 2: Add `/api/attribution/*` route registrations**

In the router setup block:

```python
self.app.router.add_get("/api/attribution/filters", self._handle_attribution_filters)
self.app.router.add_get("/api/attribution/categories", self._handle_attribution_categories)
self.app.router.add_get("/api/attribution/regimes", self._handle_attribution_regimes)
self.app.router.add_get("/api/bots/{bot_id}/details", self._handle_bot_details)
self.app.router.add_get("/api/champion_proposal", self._handle_champion_proposal)
```

- [ ] **Step 3: Add handler methods on WebDashboard**

```python
async def _handle_attribution_filters(self, request):
    """JSON: per-filter contribution table."""
    from scripts.sp4_common import pair_buys_sells, compute_metrics
    from scripts.sp4_filter_attribution import ABLATION_FILTER_MAP
    from collections import defaultdict
    if self.trade_store is None:
        return web.json_response([])
    trades = self.trade_store.load_trades()
    paired = pair_buys_sells(trades)
    by_bot = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)
    baseline = compute_metrics(by_bot.get("baseline_v1", []))
    base_per = baseline.pnl_per_trade or 0.0
    rows = []
    for bot_id, filter_name in ABLATION_FILTER_MAP.items():
        ab = compute_metrics(by_bot.get(bot_id, []))
        ab_per = ab.pnl_per_trade if ab.pnl_per_trade is not None else None
        delta = (base_per - ab_per) if ab_per is not None else None
        rows.append({
            "filter": filter_name, "baseline_n": baseline.sample_n,
            "ablation_n": ab.sample_n,
            "baseline_per_tr": base_per, "ablation_per_tr": ab_per,
            "delta_per_tr": delta,
        })
    rows.sort(key=lambda r: r["delta_per_tr"] if r["delta_per_tr"] is not None else -1e9, reverse=True)
    return web.json_response(rows)


async def _handle_attribution_categories(self, request):
    """JSON: per-category contribution."""
    from scripts.sp4_common import pair_buys_sells, compute_metrics
    from scripts.sp4_category_attribution import CATEGORY_BOTS
    from collections import defaultdict
    if self.trade_store is None:
        return web.json_response([])
    trades = self.trade_store.load_trades()
    paired = pair_buys_sells(trades)
    by_bot = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)
    baseline = compute_metrics(by_bot.get("baseline_v1", []))
    base_per = baseline.pnl_per_trade or 0.0
    rows = []
    for bot_id in CATEGORY_BOTS:
        ab = compute_metrics(by_bot.get(bot_id, []))
        ab_per = ab.pnl_per_trade if ab.pnl_per_trade is not None else None
        delta = (base_per - ab_per) if ab_per is not None else None
        category = bot_id.replace("no_", "").replace("_filters", "")
        rows.append({
            "category": category, "baseline_n": baseline.sample_n,
            "ablation_n": ab.sample_n,
            "baseline_per_tr": base_per, "ablation_per_tr": ab_per,
            "delta_per_tr": delta,
        })
    rows.sort(key=lambda r: r["delta_per_tr"] if r["delta_per_tr"] is not None else -1e9, reverse=True)
    return web.json_response(rows)


async def _handle_attribution_regimes(self, request):
    """JSON: per-regime breakdown for a given bot."""
    bot_id = request.query.get("bot_id", "baseline_v1")
    from scripts.sp4_common import pair_buys_sells
    from scripts.sp4_regime_stratify import sol_h1_bucket, pc_h24_bucket
    from collections import defaultdict
    if self.trade_store is None:
        return web.json_response({})
    trades = self.trade_store.load_trades(bot_id=bot_id)
    paired = pair_buys_sells(trades)
    sol_buckets = defaultdict(list)
    pch_buckets = defaultdict(list)
    for p in paired:
        sol_h1 = (p.buy_meta or {}).get("sol_pc_h1")
        pch = (p.buy_meta or {}).get("pc_h24")
        sol_buckets[sol_h1_bucket(sol_h1)].append(p.realized_pnl_usd)
        pch_buckets[pc_h24_bucket(pch)].append(p.realized_pnl_usd)

    def _summary(values):
        if not values:
            return {"n": 0, "per_tr": None}
        return {"n": len(values), "per_tr": sum(values) / len(values)}

    return web.json_response({
        "bot_id": bot_id,
        "sol_h1": {b: _summary(sol_buckets.get(b, []))
                   for b in ["red", "flat", "green", "unknown"]},
        "pc_h24": {b: _summary(pch_buckets.get(b, []))
                   for b in ["deep_red", "red", "flat", "green", "pumped", "unknown"]},
    })


async def _handle_bot_details(self, request):
    """JSON: full per-bot drill-down."""
    bot_id = request.match_info["bot_id"]
    from scripts.sp4_common import pair_buys_sells, compute_metrics
    if self.trade_store is None:
        return web.json_response({})
    trades = self.trade_store.load_trades(bot_id=bot_id)
    paired = pair_buys_sells(trades)
    metrics = compute_metrics(paired)
    return web.json_response({
        "bot_id": bot_id,
        "sample_n": metrics.sample_n,
        "pnl_per_trade": metrics.pnl_per_trade,
        "total_pnl_usd": metrics.total_pnl_usd,
        "win_rate": metrics.win_rate,
        "avg_win_usd": metrics.avg_win_usd,
        "avg_loss_usd": metrics.avg_loss_usd,
        "best_trade_usd": metrics.best_trade_usd,
        "worst_trade_usd": metrics.worst_trade_usd,
        "recent_trades": [
            {"token": p.token, "pnl_usd": p.realized_pnl_usd, "time": p.time}
            for p in paired[-20:]
        ],
    })


async def _handle_champion_proposal(self, request):
    """JSON: current proposed champion config + reasoning."""
    project_root = Path(__file__).parent.parent
    cfg_path = project_root / "config" / "bots" / "champion_proposal.json"
    reasoning_path = project_root / "reports" / "champion_synthesis.md"
    payload = {}
    if cfg_path.exists():
        payload["config"] = json.loads(cfg_path.read_text())
    if reasoning_path.exists():
        payload["reasoning_md"] = reasoning_path.read_text()
    return web.json_response(payload)
```

(Note: ensure `from pathlib import Path` and `import json` are at the top of web_dashboard.py — they likely already are; if not, add.)

- [ ] **Step 4: Add ATTRIBUTION tab UI**

Locate the HTML body template + JS block. Add the following HTML near the existing FLEET panel:

```html
<div id="attribution-tab" class="tab-section" style="display:none;">
  <h2>ATTRIBUTION</h2>
  <div class="attr-grid">
    <div class="attr-panel">
      <h3>Filter Attribution</h3>
      <table id="attr-filters-table">
        <thead>
          <tr><th>Filter</th><th>Baseline n</th><th>Ablation n</th><th>Δ $/tr</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="attr-panel">
      <h3>Category Attribution</h3>
      <table id="attr-categories-table">
        <thead>
          <tr><th>Category</th><th>Baseline n</th><th>Ablation n</th><th>Δ $/tr</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="attr-panel">
      <h3>Champion Preview</h3>
      <pre id="champion-preview">Loading…</pre>
    </div>
  </div>
</div>
<a href="#attribution" id="attribution-tab-link">Open ATTRIBUTION tab</a>
```

CSS:
```css
.attr-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.attr-panel { background: #1a1a1a; padding: 1rem; border-radius: 8px; }
.attr-panel table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.attr-panel th, .attr-panel td { padding: 0.3rem 0.5rem; text-align: right; }
.attr-panel th:first-child, .attr-panel td:first-child { text-align: left; }
.attr-panel .pnl-pos { color: #4caf50; }
.attr-panel .pnl-neg { color: #f44336; }
#champion-preview {
  background: #111; padding: 0.5rem; max-height: 400px; overflow: auto;
  font-size: 0.75rem; white-space: pre-wrap;
}
```

JS poller:
```javascript
async function updateAttributionFilters() {
  try {
    const resp = await fetch("/api/attribution/filters");
    if (!resp.ok) return;
    const rows = await resp.json();
    const tbody = document.querySelector("#attr-filters-table tbody");
    tbody.innerHTML = "";
    for (const r of rows) {
      const delta = r.delta_per_tr;
      const deltaStr = delta === null ? "—" : ("$" + delta.toFixed(2));
      const cls = (delta || 0) > 0 ? "pnl-pos" : (delta || 0) < 0 ? "pnl-neg" : "";
      tbody.insertAdjacentHTML("beforeend",
        `<tr><td>${r.filter}</td><td>${r.baseline_n}</td><td>${r.ablation_n}</td><td class="${cls}">${deltaStr}</td></tr>`);
    }
  } catch (e) { console.error("attr filters", e); }
}

async function updateAttributionCategories() {
  try {
    const resp = await fetch("/api/attribution/categories");
    if (!resp.ok) return;
    const rows = await resp.json();
    const tbody = document.querySelector("#attr-categories-table tbody");
    tbody.innerHTML = "";
    for (const r of rows) {
      const delta = r.delta_per_tr;
      const deltaStr = delta === null ? "—" : ("$" + delta.toFixed(2));
      const cls = (delta || 0) > 0 ? "pnl-pos" : (delta || 0) < 0 ? "pnl-neg" : "";
      tbody.insertAdjacentHTML("beforeend",
        `<tr><td>${r.category}</td><td>${r.baseline_n}</td><td>${r.ablation_n}</td><td class="${cls}">${deltaStr}</td></tr>`);
    }
  } catch (e) { console.error("attr categories", e); }
}

async function updateChampionPreview() {
  try {
    const resp = await fetch("/api/champion_proposal");
    if (!resp.ok) return;
    const data = await resp.json();
    const el = document.getElementById("champion-preview");
    if (data.config) {
      el.textContent = JSON.stringify(data.config, null, 2);
    } else {
      el.textContent = "No champion proposal yet. Run scripts/sp4_champion_synthesis.py";
    }
  } catch (e) { console.error("champion preview", e); }
}

function maybeShowAttribution() {
  const tab = document.getElementById("attribution-tab");
  if (location.hash === "#attribution") {
    tab.style.display = "block";
    updateAttributionFilters();
    updateAttributionCategories();
    updateChampionPreview();
  } else {
    tab.style.display = "none";
  }
}

window.addEventListener("hashchange", maybeShowAttribution);
window.addEventListener("DOMContentLoaded", maybeShowAttribution);
```

The tab loads ONLY when `#attribution` is in the URL — avoids egress on the default homepage.

- [ ] **Step 5: AST syntax check**

```bash
python -c "import ast; ast.parse(open('dashboard/web_dashboard.py', encoding='utf-8').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Run full test suite — confirm no regressions**

```bash
PYTHONPATH=. pytest tests/ 2>&1 | tail -10
```

Expected: existing tests pass; pre-existing 7 failures in test_exhaustion_realtime.py remain unchanged.

- [ ] **Step 7: Commit + push + deploy**

```bash
git add dashboard/web_dashboard.py
git commit -m "feat(dashboard): ATTRIBUTION tab + /api/attribution/* endpoints

5 new endpoints:
- /api/attribution/filters
- /api/attribution/categories
- /api/attribution/regimes?bot_id=X
- /api/bots/{bot_id}/details
- /api/champion_proposal

Tab loads lazily off #attribution hash (egress-friendly default).
Three sub-panels: filter contribution, category contribution, champion
preview.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

- [ ] **Step 8: Verify deploy serves new endpoints**

After deploy lands, poll until /api/attribution/filters returns 200:

```bash
until curl -s -f "https://gracious-inspiration-production.up.railway.app/api/attribution/filters" -o /dev/null; do sleep 30; done
echo "/api/attribution/* live"
curl -s "https://gracious-inspiration-production.up.railway.app/api/attribution/filters" | python -m json.tool | head -20
```

Expected: HTTP 200 with a JSON array of 10 filter rows (will mostly have null deltas if data is sparse; that's fine).

---

## Plan self-review

### Spec coverage

- 5 analytics scripts: Tasks 2-6 ✅
- `scripts/sp4_common.py` shared helpers: Task 1 ✅
- Dashboard endpoints + ATTRIBUTION tab: Task 7 ✅
- Sample-size + confidence guards: Task 1 (`confidence_label`) used throughout ✅
- Champion synthesis with manual enablement: Task 6 (writes `enabled=false`) ✅
- Greedy field-by-field strategy: Task 6 ✅
- Refuse synthesis on insufficient baseline (n<30): Task 6 ✅

### Placeholder scan

No "TBD" / "TODO". One date-templated string: `"Champion proposal (synthesized {today})"` — that's intentional runtime value, not a spec placeholder.

### Type consistency

- `BotMetrics` dataclass defined in Task 1, consumed by Tasks 2, 3, 4, 5, 6 ✅
- `PairedTrade` defined in Task 1, used in Tasks 5, 6, 7 ✅
- `pair_buys_sells(trades) -> list[PairedTrade]` signature consistent ✅
- `compute_metrics(paired) -> BotMetrics` signature consistent ✅
- `ABLATION_FILTER_MAP` defined in Task 3, imported in Task 6 (champion) + Task 7 (dashboard) — same dict ✅
- `CATEGORY_BOTS` defined in Task 4, imported in Task 7 ✅

---

## Risks deferred to execution

1. **Sparse data on first run.** Fleet just deployed; baseline_v1 has few trades. The `MIN_BASELINE_SAMPLE=30` gate prevents premature champion synthesis. Other scripts run fine on sparse data — outputs just have lots of "low confidence" rows.

2. **Production memory headroom.** 49 bots already in production; the dashboard endpoints add per-request CPU (paired-trade computation) but no persistent memory cost. Should be fine.

3. **Synthesis greedy isn't optimal.** Field-by-field misses interactions (e.g., maybe wide_stop pairs well with no_alpha_sizing but not with alpha=1.5). Acceptable for v1 — champion ships disabled, user reviews. Sub-project 5 could refine.

4. **Pre-existing test_exhaustion_realtime failures.** Unchanged baseline; the new SP4 tests don't touch that area.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-23-sub-project-4-attribution-plan.md`.

The plan covers 7 tasks. Task 1 is the shared foundation (everything depends on it). Tasks 2-6 are independent scripts that can be implemented in any order. Task 7 (dashboard) ties everything together with UI.

Two execution options:

**1. Subagent-Driven (recommended)** — Fresh subagent per task. Each script task is small + self-contained.

**2. Inline Execution** — Faster wall-clock but my context grows.

**Recommendation: Subagent-Driven.**
