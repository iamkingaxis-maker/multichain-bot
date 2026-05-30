# Multi-Bot Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the multichain dip-buy bot to support running N independent bots in a single Railway process, each with its own config, paper capital, positions, and trade history, sharing feature computation but making independent decisions.

**Architecture:** One scanner cycle produces one immutable `FeatureBundle` per token. A `BotManager` fans out each bundle to N `BotEvaluator` instances; each evaluator consults its `BotConfig` and decides independently. Each bot owns a `PerBotCapital` and `PerBotPositionManager`. Trades are persisted to JSON with a `bot_id` field; existing trades default to `baseline_v1`. The dashboard gets per-bot endpoints + a leaderboard.

**Tech Stack:** Python 3.11, dataclasses (frozen for immutability), pytest, aiohttp (existing dashboard), JSON persistence (existing pattern), Railway (existing deploy target).

**Spec:** [docs/superpowers/specs/2026-05-23-multi-bot-harness-design.md](../specs/2026-05-23-multi-bot-harness-design.md)

**Persistence note:** The codebase persists trades to `{DATA_DIR}/trades.json` (line 131 of `core/trader.py`), NOT SQLite. So "DB migration" in the spec means JSON record schema evolution + a one-shot script to backfill `bot_id` on existing records. ALTER TABLE work in the spec is not applicable.

---

## File structure

### New files
| Path | Responsibility |
|---|---|
| `core/bot_config.py` | `BotConfig` frozen dataclass + JSON load/save |
| `core/feature_bundle.py` | `FeatureBundle` frozen dataclass (passed to evaluators) |
| `core/per_bot_capital.py` | Per-bot paper capital tracker (lift from `core/scalp_capital.py`) |
| `core/per_bot_position_manager.py` | Per-bot position state machine |
| `core/bot_evaluator.py` | Per-bot decision engine: evaluate(FeatureBundle) → Optional[BuyDecision] |
| `core/bot_manager.py` | Orchestrator: fans out FeatureBundle to all bots |
| `core/bot_registry.py` | Loads bot configs from `config/bots/*.json` at startup |
| `core/multi_bot_persistence.py` | Bot-aware trades.json reader/writer + `bot_state.json` for per-bot capital/PnL |
| `config/bots/baseline_v1.json` | Current production config |
| `config/bots/no_sol_gate.json` | Smoke variant: baseline minus sol_macro_block |
| `config/bots/no_filters.json` | Smoke variant: baseline minus all filters |
| `scripts/migrate_trades_json_bot_id.py` | One-shot: add bot_id='baseline_v1' to all existing trades.json records |
| `tests/test_bot_config.py` | BotConfig load/save/validation tests |
| `tests/test_feature_bundle.py` | FeatureBundle immutability + field tests |
| `tests/test_per_bot_capital.py` | Capital reserve/release/daily-reset tests |
| `tests/test_per_bot_position_manager.py` | Position lifecycle tests |
| `tests/test_bot_evaluator.py` | Decision logic tests across all gate types |
| `tests/test_bot_manager.py` | Fan-out + isolation tests |
| `tests/test_multi_bot_smoke.py` | 3-bot in-memory integration test |

### Modified files
| Path | Reason for modification |
|---|---|
| `core/trader.py` | Replace inline decision logic with `BotManager.evaluate_all()`; trades.json writes include `bot_id` |
| `core/position_manager.py` | Extract reusable base into `per_bot_position_manager.py`; existing single-bot codepath becomes the `baseline_v1` bot's instance |
| `feeds/dip_scanner.py` | `_scan_cycle` produces a `FeatureBundle` per candidate; calls `BotManager.evaluate_all(bundle)` instead of inline trigger eval |
| `dashboard/web_dashboard.py` | Add `/api/bots`, `/api/leaderboard`, `/api/bots/{id}/trades`, `/api/bots/{id}/positions`; existing endpoints gain `?bot_id=X` filter |
| `main.py` | Instantiate `BotRegistry` + `BotManager` on startup; pass to scanner & dashboard |

---

## Task ordering rationale

The plan is structured so that each task produces working, testable software. Phases 1-2 build data structures (no behavior change). Phases 3-5 build per-bot state + decision logic in isolation. Phase 6 wires it into the existing scanner/trader. Phase 7 adds dashboard visibility. Phase 8 validates with a 3-bot smoke deploy.

You can run unit tests after every phase. Only Phase 6 ("wire into scanner/trader") changes live behavior — and we keep `baseline_v1` config exactly matching today's HEAD so the smoke deploy is a no-op P&L-wise (just adds bot_id to records).

---

## Phase 1: Data structures (no behavior change)

### Task 1: BotConfig dataclass

**Files:**
- Create: `core/bot_config.py`
- Create: `tests/test_bot_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_config.py
import pytest
from dataclasses import FrozenInstanceError
from core.bot_config import BotConfig

def test_botconfig_required_fields():
    cfg = BotConfig(bot_id="b1", display_name="Bot 1")
    assert cfg.bot_id == "b1"
    assert cfg.display_name == "Bot 1"
    assert cfg.enabled is True
    assert cfg.paper_capital_usd == 2000.0
    assert cfg.base_position_usd == 20.0
    assert cfg.max_concurrent_positions == 3

def test_botconfig_is_frozen():
    cfg = BotConfig(bot_id="b1", display_name="Bot 1")
    with pytest.raises(FrozenInstanceError):
        cfg.bot_id = "b2"

def test_botconfig_defaults_match_production():
    cfg = BotConfig(bot_id="baseline_v1", display_name="Baseline")
    # SOL gate matches commit 9fe8366
    assert cfg.sol_macro_h6_block_threshold == -0.3
    assert cfg.sol_macro_h1_block_threshold == -0.7
    # pc_h24 gate matches commit 9840ffe (mcap_psych_level)
    assert cfg.mcap_psych_pc_h24_max == 80.0
    # Exit ladder matches current production
    assert cfg.tp1_pct == 5.0
    assert cfg.tp1_sell_fraction == 0.75
    assert cfg.hard_stop_pct == -15.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_config.py -v`
Expected: `ModuleNotFoundError: No module named 'core.bot_config'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/bot_config.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class BotConfig:
    """Universal config schema for a single bot.

    See docs/superpowers/specs/2026-05-23-multi-bot-harness-design.md for
    semantics of each field. All thresholds are inclusive unless noted.
    """

    bot_id: str
    display_name: str
    enabled: bool = True

    # Capital & sizing
    paper_capital_usd: float = 2000.0
    base_position_usd: float = 20.0
    max_concurrent_positions: int = 3
    alpha_multiplier: float = 1.5
    macro_up_multiplier: float = 1.5
    premium_runner_multiplier: float = 3.0
    marginal_multiplier: float = 0.5

    # Macro gates (None disables)
    sol_macro_h6_block_threshold: Optional[float] = -0.3
    sol_macro_h1_block_threshold: Optional[float] = -0.7
    btc_macro_h1_block_threshold: Optional[float] = None

    # Token regime gates
    pc_h24_max: Optional[float] = None
    pc_h24_min: Optional[float] = None
    pc_h1_max: Optional[float] = None
    age_h_min: Optional[float] = None
    age_h_max: Optional[float] = None
    mcap_min: Optional[float] = None
    mcap_max: Optional[float] = None
    vol_h1_min: Optional[float] = 1000.0

    # Filter set — semantics: if filters_enforced is None, the bot uses
    # the project baseline filter set MINUS anything in filters_disabled.
    # If filters_enforced is a list, that's the EXACT enforced set and
    # filters_disabled is ignored.
    filters_enforced: Optional[tuple[str, ...]] = None
    filters_disabled: tuple[str, ...] = field(default_factory=tuple)

    # Triggers — same semantics as filters
    triggers_allowed: Optional[tuple[str, ...]] = None
    triggers_disabled: tuple[str, ...] = field(default_factory=tuple)
    min_triggers_to_fire: int = 1
    require_alpha_trigger: bool = False

    # Trigger-specific gates (evaluated after universal gates pass)
    mcap_psych_pc_h24_max: Optional[float] = 80.0

    # Exit ladder
    tp1_pct: float = 5.0
    tp1_sell_fraction: float = 0.75
    tp2_pct: float = 10.0
    tp2_sell_fraction: float = 0.25
    trail_pp: float = 3.0
    hard_stop_pct: float = -15.0
    pre_stop_bail_pnl_pct: float = -3.0
    pre_stop_bail_vol_m5_max: float = 500.0
    slow_bleed_minutes: int = 60
    slow_bleed_pnl_threshold: float = -8.0

    # Trading window (UTC hours, half-open: [start, end))
    trading_hour_utc_start: int = 0
    trading_hour_utc_end: int = 24
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_config.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add core/bot_config.py tests/test_bot_config.py
git commit -m "feat(bot_config): BotConfig frozen dataclass

Universal config schema for multi-bot fleet — Sub-project 1 of 5.
All thresholds match current production HEAD (9840ffe) defaults so
baseline_v1 bot will be a no-op replacement of the current single-bot.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: BotConfig JSON load/save

**Files:**
- Modify: `core/bot_config.py` (add classmethod `from_json` + `to_json`)
- Modify: `tests/test_bot_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_config.py  (append)
import json
import tempfile
from pathlib import Path

def test_botconfig_json_roundtrip(tmp_path):
    cfg = BotConfig(
        bot_id="test_v1",
        display_name="Test Bot",
        sol_macro_h6_block_threshold=-0.5,
        filters_disabled=("filter_corpse",),
    )
    p = tmp_path / "test_v1.json"
    cfg.to_json(p)

    loaded = BotConfig.from_json(p)
    assert loaded == cfg
    assert loaded.filters_disabled == ("filter_corpse",)
    assert loaded.sol_macro_h6_block_threshold == -0.5

def test_botconfig_json_unknown_field_rejected(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({
        "bot_id": "x",
        "display_name": "x",
        "unknown_field": 42,
    }))
    with pytest.raises(ValueError, match="unknown_field"):
        BotConfig.from_json(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_config.py::test_botconfig_json_roundtrip -v`
Expected: `AttributeError: type object 'BotConfig' has no attribute 'from_json'`

- [ ] **Step 3: Add JSON load/save methods**

Append to `core/bot_config.py`:

```python
import dataclasses
import json
from pathlib import Path


def _to_json_safe(value):
    """Convert tuples to lists for JSON; recurse into structures."""
    if isinstance(value, tuple):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    return value


def _from_json_safe(field_type, value):
    """Coerce JSON-deserialized lists back to tuples for tuple-typed fields."""
    # tuple[str, ...] becomes list[str] in JSON; coerce back
    if value is None:
        return None
    type_str = str(field_type)
    if "tuple" in type_str and isinstance(value, list):
        return tuple(value)
    return value


def _add_methods(cls):
    def to_json(self, path):
        path = Path(path)
        data = {f.name: _to_json_safe(getattr(self, f.name))
                for f in dataclasses.fields(self)}
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def from_json(cls_, path):
        path = Path(path)
        data = json.loads(path.read_text())
        known = {f.name: f for f in dataclasses.fields(cls_)}
        unknown = set(data.keys()) - set(known.keys())
        if unknown:
            raise ValueError(
                f"Unknown field(s) in {path.name}: {sorted(unknown)}"
            )
        coerced = {
            name: _from_json_safe(known[name].type, val)
            for name, val in data.items()
        }
        return cls_(**coerced)

    cls.to_json = to_json
    cls.from_json = from_json
    return cls


BotConfig = _add_methods(BotConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_config.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add core/bot_config.py tests/test_bot_config.py
git commit -m "feat(bot_config): JSON load/save with unknown-field rejection

Tuple fields serialize as JSON arrays and coerce back on load.
Unknown fields raise ValueError instead of silently ignoring (so a
typo'd field in config/bots/x.json crashes startup, not silently
falls back to default — fail-loud principle).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: FeatureBundle dataclass

**Files:**
- Create: `core/feature_bundle.py`
- Create: `tests/test_feature_bundle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_feature_bundle.py
import pytest
from dataclasses import FrozenInstanceError
from core.feature_bundle import FeatureBundle


def _make_bundle(**overrides):
    defaults = dict(
        token="TEST",
        address="addr1",
        pair_address="pair1",
        chain="solana",
        snapshot_ts=1716480000.0,
        price_usd=0.001,
        mcap_usd=4_000_000.0,
        age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=None, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None,
        sol_pc_h24=None, btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=(),
        triggers_shadow=(),
        filters_block=(),
        filters_pass=(),
        filters_shadow=(),
        raw_meta={},
    )
    defaults.update(overrides)
    return FeatureBundle(**defaults)


def test_feature_bundle_immutable():
    b = _make_bundle()
    with pytest.raises(FrozenInstanceError):
        b.price_usd = 0.002

def test_feature_bundle_fields_accessible():
    b = _make_bundle(
        pc_h24=70.5,
        triggers_fired=("mcap_psych_level", "deep_1h_dip"),
    )
    assert b.token == "TEST"
    assert b.pc_h24 == 70.5
    assert "mcap_psych_level" in b.triggers_fired

def test_feature_bundle_optional_fields_default_none():
    b = _make_bundle()
    assert b.pc_h24 is None
    assert b.sol_pc_h1 is None
    assert b.cnn_cluster_id is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_feature_bundle.py -v`
Expected: `ModuleNotFoundError: No module named 'core.feature_bundle'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/feature_bundle.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FeatureBundle:
    """Immutable snapshot of all features needed to evaluate a token candidate.

    Produced once per token per scan cycle by DipScanner. Passed by reference
    to every BotEvaluator (N bots see the same bundle, decide independently).
    """

    # Identity
    token: str
    address: str
    pair_address: str
    chain: str
    snapshot_ts: float

    # Price / market data
    price_usd: float
    mcap_usd: float
    age_hours: float
    pc_h24: Optional[float]
    pc_h6: Optional[float]
    pc_h1: Optional[float]
    pc_m5: Optional[float]
    vol_h1_usd: Optional[float]
    bs_h1: Optional[float]

    # Macro
    sol_pc_h1: Optional[float]
    sol_pc_h4: Optional[float]
    sol_pc_h6: Optional[float]
    sol_pc_h24: Optional[float]
    btc_pc_h1: Optional[float]
    btc_pc_h6: Optional[float]
    btc_bs_h1: Optional[float]

    # On-chain
    net_flow_15s_usd: Optional[float]
    net_flow_60s_usd: Optional[float]
    net_flow_5m_usd: Optional[float]
    top_buy_makers_n: Optional[int]
    p90_buy_size_usd: Optional[float]

    # Chart / model
    chart_mtf_score: Optional[float]
    chart_score: Optional[float]
    cnn_cluster_id: Optional[int]
    fusion_outcome_prob: Optional[float]

    # Triggers + filters already evaluated by the scanner pipeline
    triggers_fired: tuple[str, ...]
    triggers_shadow: tuple[str, ...]
    filters_block: tuple[str, ...]
    filters_pass: tuple[str, ...]
    filters_shadow: tuple[str, ...]

    # Legacy passthrough for fields not yet promoted to typed slots
    raw_meta: dict = field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_feature_bundle.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add core/feature_bundle.py tests/test_feature_bundle.py
git commit -m "feat(feature_bundle): immutable per-candidate feature snapshot

Replaces the implicit 'candidate dict' currently passed around dip_scanner
with a typed, frozen dataclass. Each bot will receive the same bundle
and decide independently. raw_meta preserves legacy fields not yet
promoted to typed slots.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 2: Per-bot state primitives

### Task 4: PerBotCapital

**Files:**
- Create: `core/per_bot_capital.py`
- Create: `tests/test_per_bot_capital.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_per_bot_capital.py
import pytest
import asyncio
from core.per_bot_capital import PerBotCapital


def test_capital_init_starting_balance():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    assert cap.bot_id == "b1"
    assert cap.balance_usd == 2000.0
    assert cap.in_flight_usd == 0.0
    assert cap.realized_pnl_total_usd == 0.0
    assert cap.daily_pnl_usd == 0.0

def test_capital_reserve_reduces_balance():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    assert cap.balance_usd == 1980.0
    assert cap.in_flight_usd == 20.0

def test_capital_reserve_rejects_when_insufficient():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=20.0)
    with pytest.raises(ValueError, match="insufficient"):
        cap.reserve_for_buy(30.0)

def test_capital_realize_sell_adds_proceeds():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    cap.realize_sell(cost_usd=20.0, proceeds_usd=23.0)
    assert cap.balance_usd == 2003.0
    assert cap.in_flight_usd == 0.0
    assert cap.realized_pnl_total_usd == 3.0
    assert cap.daily_pnl_usd == 3.0

def test_capital_daily_reset_at_utc_midnight_rollover():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    cap.realize_sell(cost_usd=20.0, proceeds_usd=22.0, now_iso="2026-05-22T23:59:59Z")
    assert cap.daily_pnl_usd == 2.0
    # First action on the next UTC day resets daily P&L
    cap.reserve_for_buy(20.0)
    cap.realize_sell(cost_usd=20.0, proceeds_usd=19.0, now_iso="2026-05-23T00:00:01Z")
    assert cap.daily_pnl_usd == -1.0
    assert cap.realized_pnl_total_usd == 1.0  # cumulative still adds up
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_per_bot_capital.py -v`
Expected: `ModuleNotFoundError: No module named 'core.per_bot_capital'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/per_bot_capital.py
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional


def _utc_date_iso(now_iso: Optional[str] = None) -> str:
    """Return the YYYY-MM-DD portion of a UTC ISO timestamp.

    If now_iso is None, uses the current wall clock.
    """
    if now_iso is None:
        return datetime.now(timezone.utc).date().isoformat()
    dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).date().isoformat()


class PerBotCapital:
    """Paper capital tracker for one bot.

    Tracks balance, in-flight (open position cost), cumulative realized P&L,
    and daily P&L (which resets at UTC 00:00). NOT thread-safe — caller must
    serialize via asyncio.Lock per bot.
    """

    def __init__(self, bot_id: str, starting_balance_usd: float) -> None:
        self.bot_id = bot_id
        self.balance_usd = float(starting_balance_usd)
        self.in_flight_usd = 0.0
        self.realized_pnl_total_usd = 0.0
        self.daily_pnl_usd = 0.0
        self._daily_pnl_date = _utc_date_iso()

    def _check_daily_rollover(self, now_iso: Optional[str] = None) -> None:
        today = _utc_date_iso(now_iso)
        if today != self._daily_pnl_date:
            self.daily_pnl_usd = 0.0
            self._daily_pnl_date = today

    def reserve_for_buy(self, size_usd: float, now_iso: Optional[str] = None) -> None:
        self._check_daily_rollover(now_iso)
        if size_usd > self.balance_usd:
            raise ValueError(
                f"bot={self.bot_id} insufficient capital: "
                f"requested={size_usd} balance={self.balance_usd}"
            )
        self.balance_usd -= size_usd
        self.in_flight_usd += size_usd

    def realize_sell(
        self,
        cost_usd: float,
        proceeds_usd: float,
        now_iso: Optional[str] = None,
    ) -> None:
        self._check_daily_rollover(now_iso)
        pnl = proceeds_usd - cost_usd
        self.in_flight_usd -= cost_usd
        self.balance_usd += proceeds_usd
        self.realized_pnl_total_usd += pnl
        self.daily_pnl_usd += pnl

    def to_dict(self) -> dict:
        return {
            "bot_id": self.bot_id,
            "balance_usd": self.balance_usd,
            "in_flight_usd": self.in_flight_usd,
            "realized_pnl_total_usd": self.realized_pnl_total_usd,
            "daily_pnl_usd": self.daily_pnl_usd,
            "daily_pnl_date": self._daily_pnl_date,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PerBotCapital":
        c = cls(bot_id=data["bot_id"], starting_balance_usd=data["balance_usd"])
        c.in_flight_usd = data["in_flight_usd"]
        c.realized_pnl_total_usd = data["realized_pnl_total_usd"]
        c.daily_pnl_usd = data["daily_pnl_usd"]
        c._daily_pnl_date = data["daily_pnl_date"]
        return c
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_per_bot_capital.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add core/per_bot_capital.py tests/test_per_bot_capital.py
git commit -m "feat(per_bot_capital): per-bot paper capital tracker

Each bot maintains its own balance/in_flight/realized_pnl/daily_pnl.
Daily P&L resets on UTC midnight rollover. to_dict/from_dict provide
JSON persistence (used by multi_bot_persistence.py later).

Lifted pattern from core/scalp_capital.py but generalized for any bot_id.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Bot-aware persistence layer

**Files:**
- Create: `core/multi_bot_persistence.py`
- Create: `tests/test_multi_bot_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_multi_bot_persistence.py
import json
import pytest
from pathlib import Path
from core.multi_bot_persistence import MultiBotTradeStore


def test_record_trade_stamps_bot_id(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({
        "type": "buy",
        "token": "SQUIRE",
        "entry_price": 0.001,
        "amount_usd": 20.0,
        "time": "2026-05-23T10:00:00+00:00",
    }, bot_id="baseline_v1")

    trades_file = tmp_path / "trades.json"
    assert trades_file.exists()
    data = json.loads(trades_file.read_text())
    assert len(data) == 1
    assert data[0]["bot_id"] == "baseline_v1"
    assert data[0]["token"] == "SQUIRE"


def test_load_trades_filters_by_bot_id(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({"type": "buy", "token": "A", "time": "t1"}, bot_id="b1")
    store.record_trade({"type": "buy", "token": "B", "time": "t2"}, bot_id="b2")
    store.record_trade({"type": "buy", "token": "C", "time": "t3"}, bot_id="b1")

    b1_trades = store.load_trades(bot_id="b1")
    assert len(b1_trades) == 2
    assert {t["token"] for t in b1_trades} == {"A", "C"}

    b2_trades = store.load_trades(bot_id="b2")
    assert len(b2_trades) == 1
    assert b2_trades[0]["token"] == "B"


def test_load_trades_no_filter_returns_all(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({"type": "buy", "token": "A", "time": "t1"}, bot_id="b1")
    store.record_trade({"type": "buy", "token": "B", "time": "t2"}, bot_id="b2")
    assert len(store.load_trades()) == 2


def test_load_trades_backfills_baseline_v1_for_legacy_records(tmp_path):
    # Simulate pre-multi-bot trades.json: no bot_id field on records
    legacy = [
        {"type": "buy", "token": "OLD", "time": "t0"},
        {"type": "sell", "token": "OLD", "time": "t0.5", "pnl": 1.0},
    ]
    (tmp_path / "trades.json").write_text(json.dumps(legacy))

    store = MultiBotTradeStore(data_dir=tmp_path)
    trades = store.load_trades()
    assert all(t["bot_id"] == "baseline_v1" for t in trades)


def test_bot_state_save_load_roundtrip(tmp_path):
    from core.per_bot_capital import PerBotCapital
    store = MultiBotTradeStore(data_dir=tmp_path)
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    store.save_bot_state("b1", cap.to_dict())

    loaded = store.load_bot_state("b1")
    assert loaded["balance_usd"] == 1980.0
    assert loaded["in_flight_usd"] == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_multi_bot_persistence.py -v`
Expected: `ModuleNotFoundError: No module named 'core.multi_bot_persistence'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/multi_bot_persistence.py
from __future__ import annotations
import json
import threading
from pathlib import Path
from typing import Optional


class MultiBotTradeStore:
    """Bot-aware trade persistence.

    File layout under data_dir:
      trades.json           — append-only list of trade records (all bots)
      bot_state/{id}.json   — per-bot capital + daily P&L snapshot

    Legacy records lacking a 'bot_id' field are implicitly stamped
    'baseline_v1' on read (backfill-on-read). The migration script
    rewrites them on disk explicitly.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "bot_state").mkdir(exist_ok=True)
        self._trades_path = self.data_dir / "trades.json"
        self._lock = threading.Lock()  # file-level write serialization

    def record_trade(self, trade: dict, bot_id: str) -> None:
        record = dict(trade)
        record["bot_id"] = bot_id
        with self._lock:
            existing = []
            if self._trades_path.exists():
                try:
                    existing = json.loads(self._trades_path.read_text())
                except json.JSONDecodeError:
                    existing = []
            existing.append(record)
            self._trades_path.write_text(json.dumps(existing))

    def load_trades(self, bot_id: Optional[str] = None) -> list[dict]:
        if not self._trades_path.exists():
            return []
        data = json.loads(self._trades_path.read_text())
        # Backfill missing bot_id as baseline_v1
        for t in data:
            if "bot_id" not in t:
                t["bot_id"] = "baseline_v1"
        if bot_id is None:
            return data
        return [t for t in data if t["bot_id"] == bot_id]

    def save_bot_state(self, bot_id: str, state: dict) -> None:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        with self._lock:
            path.write_text(json.dumps(state, indent=2))

    def load_bot_state(self, bot_id: str) -> Optional[dict]:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_multi_bot_persistence.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add core/multi_bot_persistence.py tests/test_multi_bot_persistence.py
git commit -m "feat(persistence): MultiBotTradeStore with bot_id stamping

trades.json gains 'bot_id' field on every record. Legacy records
without bot_id implicitly read as 'baseline_v1' (backfill-on-read),
preserving compatibility with pre-multi-bot trade history.

per-bot capital snapshots saved to bot_state/{id}.json (one file
per bot to minimize write contention).

File-level threading.Lock serializes writes — adequate for current
scale (43 bots × ~30 trades/day ~= 0.015 writes/sec).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 3: Per-bot position management

### Task 6: PerBotPositionManager (lifecycle skeleton)

**Files:**
- Create: `core/per_bot_position_manager.py`
- Create: `tests/test_per_bot_position_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_per_bot_position_manager.py
import pytest
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager, OpenPosition


def _cfg(**overrides):
    base = dict(bot_id="b1", display_name="Bot 1")
    base.update(overrides)
    return BotConfig(**base)


def test_open_position_records_entry():
    pm = PerBotPositionManager(_cfg())
    p = pm.open_position(
        token="SQUIRE",
        entry_price=0.001,
        size_usd=20.0,
        entry_time=1716480000.0,
    )
    assert isinstance(p, OpenPosition)
    assert p.token == "SQUIRE"
    assert p.entry_price == 0.001
    assert p.size_usd == 20.0
    assert p.tp1_hit is False
    assert pm.open_count == 1

def test_open_position_rejects_over_max_concurrent():
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=2))
    pm.open_position("A", 0.001, 20.0, entry_time=1.0)
    pm.open_position("B", 0.001, 20.0, entry_time=2.0)
    with pytest.raises(ValueError, match="max_concurrent"):
        pm.open_position("C", 0.001, 20.0, entry_time=3.0)

def test_get_position_returns_open():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    p = pm.get_position("SQUIRE")
    assert p is not None
    assert p.token == "SQUIRE"

def test_close_position_returns_pnl_and_removes():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    result = pm.close_position(token="SQUIRE", exit_price=0.0011, exit_time=2.0, reason="TP1")
    assert result.token == "SQUIRE"
    assert result.cost_usd == 20.0
    # exit_price/entry_price = 1.1 → proceeds = 20 × 1.1 = 22
    assert result.proceeds_usd == pytest.approx(22.0, abs=0.01)
    assert result.realized_pnl_usd == pytest.approx(2.0, abs=0.01)
    assert pm.open_count == 0

def test_close_unknown_position_raises():
    pm = PerBotPositionManager(_cfg())
    with pytest.raises(KeyError):
        pm.close_position("MISSING", 0.001, 2.0, "stop")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_per_bot_position_manager.py -v`
Expected: `ModuleNotFoundError: No module named 'core.per_bot_position_manager'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/per_bot_position_manager.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from core.bot_config import BotConfig


@dataclass
class OpenPosition:
    token: str
    entry_price: float
    size_usd: float
    entry_time: float
    tp1_hit: bool = False
    tp2_hit: bool = False
    peak_pnl_pct: float = 0.0
    peak_pnl_at_secs: int = 0
    state_blob: dict = field(default_factory=dict)


@dataclass
class CloseResult:
    token: str
    cost_usd: float
    proceeds_usd: float
    realized_pnl_usd: float
    pnl_pct: float
    reason: str
    hold_secs: float
    peak_pnl_pct: float


class PerBotPositionManager:
    """Per-bot position state machine.

    Owns the dict of open positions for one bot. Exit logic (TP/trail/stop)
    will be added in Task 7. This task ships the skeleton.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._positions: dict[str, OpenPosition] = {}

    @property
    def open_count(self) -> int:
        return len(self._positions)

    def open_position(
        self,
        token: str,
        entry_price: float,
        size_usd: float,
        entry_time: float,
    ) -> OpenPosition:
        if self.open_count >= self.config.max_concurrent_positions:
            raise ValueError(
                f"bot={self.config.bot_id} max_concurrent reached "
                f"({self.config.max_concurrent_positions})"
            )
        if token in self._positions:
            raise ValueError(
                f"bot={self.config.bot_id} already holds {token}"
            )
        p = OpenPosition(
            token=token,
            entry_price=entry_price,
            size_usd=size_usd,
            entry_time=entry_time,
        )
        self._positions[token] = p
        return p

    def get_position(self, token: str) -> Optional[OpenPosition]:
        return self._positions.get(token)

    def iter_positions(self):
        return list(self._positions.values())

    def close_position(
        self,
        token: str,
        exit_price: float,
        exit_time: float,
        reason: str,
    ) -> CloseResult:
        if token not in self._positions:
            raise KeyError(f"bot={self.config.bot_id} no open position for {token}")
        p = self._positions.pop(token)
        ratio = exit_price / p.entry_price
        proceeds = p.size_usd * ratio
        pnl_usd = proceeds - p.size_usd
        pnl_pct = (ratio - 1.0) * 100.0
        return CloseResult(
            token=token,
            cost_usd=p.size_usd,
            proceeds_usd=proceeds,
            realized_pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            reason=reason,
            hold_secs=exit_time - p.entry_time,
            peak_pnl_pct=p.peak_pnl_pct,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_per_bot_position_manager.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add core/per_bot_position_manager.py tests/test_per_bot_position_manager.py
git commit -m "feat(position_mgr): per-bot position state machine skeleton

PerBotPositionManager owns open positions for ONE bot, parameterized
by its BotConfig (max_concurrent etc.). Each bot has its own instance.

Exit logic (TP/trail/stop tick) follows in Task 7.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7: PerBotPositionManager exit ladder logic

**Files:**
- Modify: `core/per_bot_position_manager.py`
- Modify: `tests/test_per_bot_position_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_per_bot_position_manager.py (append)

def test_tick_emits_tp1_when_peak_hits_threshold():
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, tp1_sell_fraction=0.75))
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    # Price up 5.0%
    decisions = pm.tick(token="SQUIRE", current_price=0.00105, now=2.0)
    assert any(d.kind == "TP1" for d in decisions)


def test_tick_emits_hard_stop_when_pnl_below_threshold():
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-15.0))
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    # Price down 16%
    decisions = pm.tick(token="SQUIRE", current_price=0.00084, now=2.0)
    assert any(d.kind == "HARD_STOP" for d in decisions)


def test_tick_emits_post_tp1_trail_when_pulled_back_pp():
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, trail_pp=3.0))
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    # First push to peak +10%
    pm.tick(token="SQUIRE", current_price=0.0011, now=2.0)
    # peak_pnl_pct should now reflect +10
    p = pm.get_position("SQUIRE")
    assert p.peak_pnl_pct >= 9.9
    assert p.tp1_hit is True
    # Pull back to +6% (3pp below peak +10 = +7; +6 is below)
    decisions = pm.tick(token="SQUIRE", current_price=0.00106, now=3.0)
    assert any(d.kind == "POST_TP1_TRAIL" for d in decisions)


def test_tick_no_decision_when_within_normal_band():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="SQUIRE", current_price=0.00102, now=2.0)
    assert decisions == []


def test_tick_unknown_token_returns_empty():
    pm = PerBotPositionManager(_cfg())
    assert pm.tick(token="MISSING", current_price=0.001, now=1.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_per_bot_position_manager.py::test_tick_emits_tp1_when_peak_hits_threshold -v`
Expected: `AttributeError: 'PerBotPositionManager' object has no attribute 'tick'`

- [ ] **Step 3: Add tick() method + ExitDecision**

Add to `core/per_bot_position_manager.py`:

```python
from typing import Literal


@dataclass
class ExitDecision:
    token: str
    kind: Literal["TP1", "TP2", "POST_TP1_TRAIL", "HARD_STOP", "PRE_STOP_BAIL"]
    reason: str
    sell_fraction: float  # 0.0 to 1.0; full exit = 1.0


# Add to PerBotPositionManager class:

    def tick(self, token: str, current_price: float, now: float) -> list:
        """Evaluate exit decisions for one position at this price tick.

        Returns a list of ExitDecision objects (may be multi-stage, e.g.
        TP1 partial + later TP2). Caller is responsible for invoking
        close_position() or partial-sell logic.
        """
        p = self._positions.get(token)
        if p is None:
            return []

        pnl_pct = (current_price / p.entry_price - 1.0) * 100.0
        # Track peak
        if pnl_pct > p.peak_pnl_pct:
            p.peak_pnl_pct = pnl_pct
            p.peak_pnl_at_secs = int(now - p.entry_time)

        decisions: list[ExitDecision] = []

        # Hard stop
        if pnl_pct <= self.config.hard_stop_pct:
            decisions.append(ExitDecision(
                token=token,
                kind="HARD_STOP",
                reason=f"hard stop pnl={pnl_pct:.2f}% <= {self.config.hard_stop_pct}",
                sell_fraction=1.0,
            ))
            return decisions

        # TP1 (only fires once)
        if not p.tp1_hit and pnl_pct >= self.config.tp1_pct:
            p.tp1_hit = True
            decisions.append(ExitDecision(
                token=token,
                kind="TP1",
                reason=f"TP1 pnl={pnl_pct:.2f}% >= {self.config.tp1_pct}",
                sell_fraction=self.config.tp1_sell_fraction,
            ))

        # TP2 (only fires once, must be after TP1)
        if p.tp1_hit and not p.tp2_hit and pnl_pct >= self.config.tp2_pct:
            p.tp2_hit = True
            decisions.append(ExitDecision(
                token=token,
                kind="TP2",
                reason=f"TP2 pnl={pnl_pct:.2f}% >= {self.config.tp2_pct}",
                sell_fraction=self.config.tp2_sell_fraction,
            ))

        # Post-TP1 trail: once TP1 hit, exit remainder if pulled back trail_pp
        # below the peak. (Decision is to exit the residual, not partial.)
        if p.tp1_hit and not decisions:
            trail_threshold = p.peak_pnl_pct - self.config.trail_pp
            if pnl_pct <= trail_threshold:
                decisions.append(ExitDecision(
                    token=token,
                    kind="POST_TP1_TRAIL",
                    reason=(
                        f"trail pnl={pnl_pct:.2f}% <= peak({p.peak_pnl_pct:.2f}%)"
                        f" - {self.config.trail_pp}pp"
                    ),
                    sell_fraction=1.0,
                ))

        return decisions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_per_bot_position_manager.py -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add core/per_bot_position_manager.py tests/test_per_bot_position_manager.py
git commit -m "feat(position_mgr): exit ladder (TP1/TP2/trail/hard_stop)

Implements the core exit decisions parameterized by BotConfig:
- TP1 partial sell at config.tp1_pct (default 5%, sell 75%)
- TP2 partial sell at config.tp2_pct (default 10%, sell 25%)
- Post-TP1 trail exits residual if pulled back config.trail_pp from peak
- Hard stop at config.hard_stop_pct (default -15%)

Pre-stop bail + slow-bleed logic deferred to Task 8 (they need vol
data not yet in OpenPosition state).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8: PerBotPositionManager pre-stop bail + slow bleed

**Files:**
- Modify: `core/per_bot_position_manager.py`
- Modify: `tests/test_per_bot_position_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_per_bot_position_manager.py (append)

def test_pre_stop_bail_fires_at_threshold_with_low_vol():
    pm = PerBotPositionManager(_cfg(
        pre_stop_bail_pnl_pct=-3.0,
        pre_stop_bail_vol_m5_max=500.0,
    ))
    pm.open_position("CHUD", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(
        token="CHUD",
        current_price=0.00097,  # -3% pnl
        now=2.0,
        vol_m5_usd=367.0,
    )
    assert any(d.kind == "PRE_STOP_BAIL" for d in decisions)


def test_pre_stop_bail_does_NOT_fire_at_threshold_with_healthy_vol():
    pm = PerBotPositionManager(_cfg(
        pre_stop_bail_pnl_pct=-3.0,
        pre_stop_bail_vol_m5_max=500.0,
    ))
    pm.open_position("CHUD", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(
        token="CHUD",
        current_price=0.00097,
        now=2.0,
        vol_m5_usd=5000.0,  # vol is healthy → no bail
    )
    assert not any(d.kind == "PRE_STOP_BAIL" for d in decisions)


def test_slow_bleed_fires_after_hold_min_at_loss():
    pm = PerBotPositionManager(_cfg(
        slow_bleed_minutes=60,
        slow_bleed_pnl_threshold=-8.0,
        hard_stop_pct=-15.0,  # must stay above hard stop to test slow bleed
    ))
    pm.open_position("VIRL", 0.001, 20.0, entry_time=1.0)
    # 60min later at -10%
    decisions = pm.tick(
        token="VIRL",
        current_price=0.00090,  # -10%
        now=1.0 + 3600.0,
    )
    assert any(d.reason.startswith("slow_bleed") for d in decisions)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_per_bot_position_manager.py::test_pre_stop_bail_fires_at_threshold_with_low_vol -v`
Expected: `TypeError: tick() got an unexpected keyword argument 'vol_m5_usd'`

- [ ] **Step 3: Extend tick() to accept vol + slow-bleed logic**

Modify `PerBotPositionManager.tick` in `core/per_bot_position_manager.py`:

```python
    def tick(
        self,
        token: str,
        current_price: float,
        now: float,
        vol_m5_usd: float | None = None,
    ) -> list:
        """As above but with optional volume context for pre-stop bail."""
        p = self._positions.get(token)
        if p is None:
            return []

        pnl_pct = (current_price / p.entry_price - 1.0) * 100.0
        if pnl_pct > p.peak_pnl_pct:
            p.peak_pnl_pct = pnl_pct
            p.peak_pnl_at_secs = int(now - p.entry_time)

        decisions: list[ExitDecision] = []

        # 1. Hard stop (highest priority)
        if pnl_pct <= self.config.hard_stop_pct:
            decisions.append(ExitDecision(
                token=token, kind="HARD_STOP",
                reason=f"hard stop pnl={pnl_pct:.2f}% <= {self.config.hard_stop_pct}",
                sell_fraction=1.0,
            ))
            return decisions

        # 2. Pre-stop bail (volume-aware, only fires before TP1)
        if (
            not p.tp1_hit
            and vol_m5_usd is not None
            and pnl_pct <= self.config.pre_stop_bail_pnl_pct
            and vol_m5_usd <= self.config.pre_stop_bail_vol_m5_max
        ):
            decisions.append(ExitDecision(
                token=token, kind="PRE_STOP_BAIL",
                reason=(
                    f"pre-stop bail pnl={pnl_pct:.2f}% vol_m5=${vol_m5_usd:.0f}"
                    f" <= {self.config.pre_stop_bail_vol_m5_max}"
                ),
                sell_fraction=1.0,
            ))
            return decisions

        # 3. Slow bleed (held too long at a loss)
        hold_minutes = (now - p.entry_time) / 60.0
        if (
            hold_minutes >= self.config.slow_bleed_minutes
            and pnl_pct <= self.config.slow_bleed_pnl_threshold
            and not p.tp1_hit
        ):
            decisions.append(ExitDecision(
                token=token, kind="HARD_STOP",  # reuse kind; tag in reason
                reason=(
                    f"slow_bleed hold={hold_minutes:.0f}min pnl={pnl_pct:.2f}%"
                ),
                sell_fraction=1.0,
            ))
            return decisions

        # 4. TP1 / TP2 / trail (same as before)
        if not p.tp1_hit and pnl_pct >= self.config.tp1_pct:
            p.tp1_hit = True
            decisions.append(ExitDecision(
                token=token, kind="TP1",
                reason=f"TP1 pnl={pnl_pct:.2f}% >= {self.config.tp1_pct}",
                sell_fraction=self.config.tp1_sell_fraction,
            ))
        if p.tp1_hit and not p.tp2_hit and pnl_pct >= self.config.tp2_pct:
            p.tp2_hit = True
            decisions.append(ExitDecision(
                token=token, kind="TP2",
                reason=f"TP2 pnl={pnl_pct:.2f}% >= {self.config.tp2_pct}",
                sell_fraction=self.config.tp2_sell_fraction,
            ))
        if p.tp1_hit and not decisions:
            trail_threshold = p.peak_pnl_pct - self.config.trail_pp
            if pnl_pct <= trail_threshold:
                decisions.append(ExitDecision(
                    token=token, kind="POST_TP1_TRAIL",
                    reason=(
                        f"trail pnl={pnl_pct:.2f}% <= peak({p.peak_pnl_pct:.2f}%)"
                        f" - {self.config.trail_pp}pp"
                    ),
                    sell_fraction=1.0,
                ))
        return decisions
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_per_bot_position_manager.py -v`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add core/per_bot_position_manager.py tests/test_per_bot_position_manager.py
git commit -m "feat(position_mgr): pre-stop bail + slow-bleed exits

Pre-stop bail: when pnl <= pre_stop_bail_pnl_pct AND vol_m5 is dead
(<= pre_stop_bail_vol_m5_max), exit early to cap loss before hitting
the hard stop. Only fires pre-TP1.

Slow bleed: held longer than slow_bleed_minutes at a loss <=
slow_bleed_pnl_threshold → exit. Catches positions stuck mid-bleed
that won't reach the hard stop in reasonable time.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 4: Decision logic (BotEvaluator)

### Task 9: BotEvaluator skeleton with macro gates

**Files:**
- Create: `core/bot_evaluator.py`
- Create: `tests/test_bot_evaluator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_evaluator.py
import pytest
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator, BuyDecision


def _bundle(**overrides):
    defaults = dict(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1716480000.0, price_usd=0.001, mcap_usd=4_000_000.0,
        age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("deep_1h_dip",),
        triggers_shadow=(), filters_block=(), filters_pass=(), filters_shadow=(),
        raw_meta={},
    )
    defaults.update(overrides)
    return FeatureBundle(**defaults)


def _cfg(**overrides):
    base = dict(bot_id="b1", display_name="Bot 1")
    base.update(overrides)
    return BotConfig(**base)


def test_evaluator_returns_buy_when_triggers_fire():
    ev = BotEvaluator(_cfg())
    d = ev.evaluate(_bundle())
    assert d is not None
    assert d.token == "TEST"
    assert d.size_usd == 20.0


def test_evaluator_skips_when_no_triggers_fire():
    ev = BotEvaluator(_cfg())
    d = ev.evaluate(_bundle(triggers_fired=()))
    assert d is None


def test_evaluator_sol_macro_blocks_when_h6_below_threshold():
    ev = BotEvaluator(_cfg(sol_macro_h6_block_threshold=-0.3))
    d = ev.evaluate(_bundle(sol_pc_h6=-0.5))
    assert d is None


def test_evaluator_sol_macro_allows_when_h6_above_threshold():
    ev = BotEvaluator(_cfg(sol_macro_h6_block_threshold=-0.3))
    d = ev.evaluate(_bundle(sol_pc_h6=-0.1))
    assert d is not None


def test_evaluator_sol_macro_disabled_when_threshold_None():
    ev = BotEvaluator(_cfg(sol_macro_h6_block_threshold=None,
                            sol_macro_h1_block_threshold=None))
    d = ev.evaluate(_bundle(sol_pc_h6=-5.0))  # would normally block
    assert d is not None


def test_evaluator_blocks_when_pc_h24_above_max():
    ev = BotEvaluator(_cfg(pc_h24_max=80.0))
    d = ev.evaluate(_bundle(pc_h24=90.0))
    assert d is None


def test_evaluator_allows_when_pc_h24_under_max():
    ev = BotEvaluator(_cfg(pc_h24_max=80.0))
    d = ev.evaluate(_bundle(pc_h24=50.0))
    assert d is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_evaluator.py -v`
Expected: `ModuleNotFoundError: No module named 'core.bot_evaluator'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/bot_evaluator.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle


# Alpha-tier triggers that warrant 1.5x sizing.
# Lifted from current dip_scanner.py:12937 (commit 9840ffe).
ALPHA_TRIGGERS = frozenset({
    "1s_capit_reversal",
    "deep_1h_dip",
    "concurrent_alpha",
    "whale_concentrated_demand",
    "whale_recent_burst",
    "whale_p90_size",
    "textbook_pullback_vol_accel",
    "textbook_pullback_big_buyer",
})


@dataclass
class BuyDecision:
    bot_id: str
    token: str
    address: str
    pair_address: str
    entry_price: float
    size_usd: float
    size_tier: str  # "standard" | "alpha_trigger" | ...
    triggers_fired: tuple[str, ...]
    reason_summary: str


class BotEvaluator:
    """Per-bot decision engine.

    Pure function of (BotConfig, FeatureBundle) → Optional[BuyDecision].
    No I/O, no external lookups — all data must already be in the bundle.

    This lets us call evaluate() N times per cycle (one per bot) for cheap.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def evaluate(self, b: FeatureBundle) -> Optional[BuyDecision]:
        # 1. Macro gates
        if self._sol_macro_blocks(b):
            return None
        if self._btc_macro_blocks(b):
            return None

        # 2. Token regime gates
        if not self._token_regime_passes(b):
            return None

        # 3. Trigger requirements
        effective_triggers = self._effective_triggers(b)
        if len(effective_triggers) < self.config.min_triggers_to_fire:
            return None
        if self.config.require_alpha_trigger:
            if not (set(effective_triggers) & ALPHA_TRIGGERS):
                return None

        # 4. Sizing
        size_usd, size_tier = self._size_for(effective_triggers, b)

        return BuyDecision(
            bot_id=self.config.bot_id,
            token=b.token,
            address=b.address,
            pair_address=b.pair_address,
            entry_price=b.price_usd,
            size_usd=size_usd,
            size_tier=size_tier,
            triggers_fired=effective_triggers,
            reason_summary=f"triggers={','.join(effective_triggers)} tier={size_tier}",
        )

    def _sol_macro_blocks(self, b: FeatureBundle) -> bool:
        if (
            self.config.sol_macro_h6_block_threshold is not None
            and b.sol_pc_h6 is not None
            and b.sol_pc_h6 < self.config.sol_macro_h6_block_threshold
        ):
            return True
        if (
            self.config.sol_macro_h1_block_threshold is not None
            and b.sol_pc_h1 is not None
            and b.sol_pc_h1 < self.config.sol_macro_h1_block_threshold
        ):
            return True
        return False

    def _btc_macro_blocks(self, b: FeatureBundle) -> bool:
        if (
            self.config.btc_macro_h1_block_threshold is not None
            and b.btc_pc_h1 is not None
            and b.btc_pc_h1 < self.config.btc_macro_h1_block_threshold
        ):
            return True
        return False

    def _token_regime_passes(self, b: FeatureBundle) -> bool:
        c = self.config
        if c.pc_h24_max is not None and b.pc_h24 is not None and b.pc_h24 > c.pc_h24_max:
            return False
        if c.pc_h24_min is not None and b.pc_h24 is not None and b.pc_h24 < c.pc_h24_min:
            return False
        if c.pc_h1_max is not None and b.pc_h1 is not None and b.pc_h1 > c.pc_h1_max:
            return False
        if c.age_h_min is not None and b.age_hours < c.age_h_min:
            return False
        if c.age_h_max is not None and b.age_hours > c.age_h_max:
            return False
        if c.mcap_min is not None and b.mcap_usd < c.mcap_min:
            return False
        if c.mcap_max is not None and b.mcap_usd > c.mcap_max:
            return False
        if c.vol_h1_min is not None and (b.vol_h1_usd or 0) < c.vol_h1_min:
            return False
        return True

    def _effective_triggers(self, b: FeatureBundle) -> tuple[str, ...]:
        """Apply triggers_allowed allowlist + triggers_disabled blocklist +
        trigger-specific gates (e.g. mcap_psych_pc_h24_max)."""
        c = self.config
        result = list(b.triggers_fired)

        # Apply mcap_psych_pc_h24_max gate
        if (
            c.mcap_psych_pc_h24_max is not None
            and "mcap_psych_level" in result
            and b.pc_h24 is not None
            and b.pc_h24 >= c.mcap_psych_pc_h24_max
        ):
            result = [t for t in result if t != "mcap_psych_level"]

        # Allowlist (None = baseline = all firing triggers permitted)
        if c.triggers_allowed is not None:
            allow = set(c.triggers_allowed)
            result = [t for t in result if t in allow]

        # Blocklist
        if c.triggers_disabled:
            block = set(c.triggers_disabled)
            result = [t for t in result if t not in block]

        return tuple(result)

    def _size_for(
        self, triggers: tuple[str, ...], b: FeatureBundle
    ) -> tuple[float, str]:
        c = self.config
        # 1s_capit_reversal is demoted from alpha when pc_h24 >= 80
        # (matches commit 9840ffe).
        is_alpha = bool(set(triggers) & ALPHA_TRIGGERS)
        if (
            "1s_capit_reversal" in triggers
            and b.pc_h24 is not None
            and b.pc_h24 >= 80.0
            and not (set(triggers) - {"1s_capit_reversal"}) & ALPHA_TRIGGERS
        ):
            is_alpha = False  # the only alpha source was demoted

        if is_alpha:
            return c.base_position_usd * c.alpha_multiplier, "alpha_trigger"
        return c.base_position_usd, "standard"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_evaluator.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add core/bot_evaluator.py tests/test_bot_evaluator.py
git commit -m "feat(bot_evaluator): decision engine with macro + regime gates

Pure function of (BotConfig, FeatureBundle) -> Optional[BuyDecision].
No I/O — designed to be called N times per scan cycle (one per bot)
without rate-limit explosion.

Implements:
- SOL macro gate (h6 + h1 thresholds, both must pass)
- BTC macro gate
- Token regime gates (pc_h24/h1, age, mcap, vol_h1)
- Trigger allowlist + blocklist
- mcap_psych_level pc_h24 gate (matches commit 9840ffe)
- Alpha-tier sizing (matches dip_scanner.py:12937)
- 1s_capit_reversal alpha demotion at pc_h24>=80 (commit 9840ffe)

Filter logic deferred to Task 10 (needs filter_block/filter_pass
fields populated upstream by dip_scanner).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 10: BotEvaluator filter handling

**Files:**
- Modify: `core/bot_evaluator.py`
- Modify: `tests/test_bot_evaluator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_evaluator.py (append)

def test_evaluator_blocks_when_baseline_filter_blocks():
    # Default config = baseline (filters_enforced=None)
    # If the bundle has filter_corpse in filters_block, baseline blocks.
    ev = BotEvaluator(_cfg())
    d = ev.evaluate(_bundle(filters_block=("filter_corpse",)))
    assert d is None


def test_evaluator_allows_when_filter_disabled():
    ev = BotEvaluator(_cfg(filters_disabled=("filter_corpse",)))
    d = ev.evaluate(_bundle(filters_block=("filter_corpse",)))
    assert d is not None


def test_evaluator_allows_when_filter_not_in_enforced_list():
    ev = BotEvaluator(_cfg(filters_enforced=("filter_fake_bounce",)))
    # filter_corpse blocks the candidate, but bot only enforces fake_bounce
    d = ev.evaluate(_bundle(filters_block=("filter_corpse",)))
    assert d is not None


def test_evaluator_blocks_when_filter_in_enforced_list():
    ev = BotEvaluator(_cfg(filters_enforced=("filter_corpse",)))
    d = ev.evaluate(_bundle(filters_block=("filter_corpse",)))
    assert d is None


def test_evaluator_no_filters_config_ignores_all_filter_blocks():
    ev = BotEvaluator(_cfg(filters_enforced=()))
    d = ev.evaluate(_bundle(filters_block=("filter_corpse", "filter_fake_bounce")))
    assert d is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_evaluator.py::test_evaluator_blocks_when_baseline_filter_blocks -v`
Expected: PASS unexpectedly (no filter check yet) or FAIL because the bundle has filters_block but the evaluator ignores it. We want a fresh failure: add an early check on `b.filters_block` THEN run.

Actually — first add a stub assertion that we DO honor filters. The test as written should currently FAIL because the evaluator returns a BuyDecision instead of None.

Expected (without code changes): `assert d is None` fails because `d` is a BuyDecision.

- [ ] **Step 3: Add filter handling to evaluate()**

Modify `core/bot_evaluator.py` — replace `evaluate` to insert filter check after macro gates (before triggers):

```python
    def evaluate(self, b: FeatureBundle) -> Optional[BuyDecision]:
        # 1. Macro gates
        if self._sol_macro_blocks(b):
            return None
        if self._btc_macro_blocks(b):
            return None

        # 2. Token regime gates
        if not self._token_regime_passes(b):
            return None

        # 3. Filter set
        if self._effective_filter_blocks(b):
            return None

        # 4. Trigger requirements
        effective_triggers = self._effective_triggers(b)
        if len(effective_triggers) < self.config.min_triggers_to_fire:
            return None
        if self.config.require_alpha_trigger:
            if not (set(effective_triggers) & ALPHA_TRIGGERS):
                return None

        # 5. Sizing
        size_usd, size_tier = self._size_for(effective_triggers, b)

        return BuyDecision(
            bot_id=self.config.bot_id,
            token=b.token,
            address=b.address,
            pair_address=b.pair_address,
            entry_price=b.price_usd,
            size_usd=size_usd,
            size_tier=size_tier,
            triggers_fired=effective_triggers,
            reason_summary=f"triggers={','.join(effective_triggers)} tier={size_tier}",
        )

    def _effective_filter_blocks(self, b: FeatureBundle) -> bool:
        """Determine if this bot's filter config blocks the candidate.

        Semantics per BotConfig docstring:
          - filters_enforced=None  → all of b.filters_block are honored
                                     EXCEPT those in filters_disabled
          - filters_enforced=tuple → only those filters block (filters_disabled ignored)
        """
        c = self.config
        if c.filters_enforced is None:
            disabled = set(c.filters_disabled)
            return any(f not in disabled for f in b.filters_block)
        enforced = set(c.filters_enforced)
        return any(f in enforced for f in b.filters_block)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_evaluator.py -v`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add core/bot_evaluator.py tests/test_bot_evaluator.py
git commit -m "feat(bot_evaluator): filter allowlist + blocklist semantics

Implements the filter-set logic per BotConfig docstring:
- filters_enforced=None  -> baseline (all firing filters honored
                            minus filters_disabled)
- filters_enforced=tuple -> only those filters block

The 'no_filters' bot can pass filters_enforced=() to disable all
filters. The 'baseline' bot leaves filters_enforced=None.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 5: Orchestration

### Task 11: BotManager fan-out + isolation

**Files:**
- Create: `core/bot_manager.py`
- Create: `tests/test_bot_manager.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_manager.py
import pytest
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator, BuyDecision
from core.bot_manager import BotManager


def _bundle():
    return FeatureBundle(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1.0, price_usd=0.001, mcap_usd=4_000_000.0, age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("deep_1h_dip",),
        triggers_shadow=(), filters_block=(), filters_pass=(), filters_shadow=(),
        raw_meta={},
    )


def test_bot_manager_fans_out_to_all_bots():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1"),
        BotConfig(bot_id="b2", display_name="B2"),
        BotConfig(bot_id="b3", display_name="B3"),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 3
    assert {d.bot_id for d in decisions} == {"b1", "b2", "b3"}


def test_bot_manager_isolates_exceptions_in_one_bot():
    class _BoomEvaluator(BotEvaluator):
        def evaluate(self, b):
            raise RuntimeError("boom")

    cfgs = [
        BotConfig(bot_id="ok", display_name="OK"),
        BotConfig(bot_id="boom", display_name="Boom"),
        BotConfig(bot_id="also_ok", display_name="Also OK"),
    ]
    evaluators = [
        BotEvaluator(cfgs[0]),
        _BoomEvaluator(cfgs[1]),
        BotEvaluator(cfgs[2]),
    ]
    mgr = BotManager(evaluators=evaluators)
    decisions = mgr.evaluate_all(_bundle())
    # ok + also_ok proceed; boom is logged + swallowed
    assert len(decisions) == 2
    assert {d.bot_id for d in decisions} == {"ok", "also_ok"}


def test_bot_manager_skips_disabled_bots():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1", enabled=True),
        BotConfig(bot_id="b2", display_name="B2", enabled=False),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 1
    assert decisions[0].bot_id == "b1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_manager.py -v`
Expected: `ModuleNotFoundError: No module named 'core.bot_manager'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/bot_manager.py
from __future__ import annotations
import logging
from typing import Iterable
from core.bot_evaluator import BotEvaluator, BuyDecision
from core.feature_bundle import FeatureBundle


logger = logging.getLogger(__name__)


class BotManager:
    """Orchestrates fan-out of a FeatureBundle to all bot evaluators.

    Per-bot exceptions are caught, logged, and swallowed — one bot
    crashing must never affect the others.
    """

    def __init__(self, evaluators: Iterable[BotEvaluator]) -> None:
        self.evaluators: list[BotEvaluator] = list(evaluators)

    def evaluate_all(self, bundle: FeatureBundle) -> list[BuyDecision]:
        decisions: list[BuyDecision] = []
        for ev in self.evaluators:
            if not ev.config.enabled:
                continue
            try:
                d = ev.evaluate(bundle)
                if d is not None:
                    decisions.append(d)
            except Exception as e:
                logger.error(
                    "[BotManager] bot=%s evaluate failed: %s",
                    ev.config.bot_id, e,
                    exc_info=True,
                )
                continue
        return decisions

    def enabled_bot_ids(self) -> list[str]:
        return [e.config.bot_id for e in self.evaluators if e.config.enabled]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_manager.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add core/bot_manager.py tests/test_bot_manager.py
git commit -m "feat(bot_manager): orchestrator with per-bot exception isolation

One bot's evaluate() raising must not affect any other bot. Exceptions
are logged with bot_id + stack trace and swallowed. Disabled bots are
skipped.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 12: BotRegistry — load configs from disk

**Files:**
- Create: `core/bot_registry.py`
- Create: `tests/test_bot_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_registry.py
import json
import pytest
from core.bot_config import BotConfig
from core.bot_registry import BotRegistry


def test_registry_loads_all_bots_from_directory(tmp_path):
    (tmp_path / "b1.json").write_text(json.dumps({
        "bot_id": "b1", "display_name": "Bot 1"
    }))
    (tmp_path / "b2.json").write_text(json.dumps({
        "bot_id": "b2", "display_name": "Bot 2", "enabled": False
    }))
    reg = BotRegistry.from_directory(tmp_path)
    assert len(reg.configs) == 2
    by_id = {c.bot_id: c for c in reg.configs}
    assert by_id["b1"].enabled is True
    assert by_id["b2"].enabled is False


def test_registry_skips_malformed_config_files(tmp_path, caplog):
    (tmp_path / "ok.json").write_text(json.dumps({
        "bot_id": "ok", "display_name": "OK"
    }))
    (tmp_path / "bad.json").write_text("not json")
    reg = BotRegistry.from_directory(tmp_path)
    assert len(reg.configs) == 1
    assert reg.configs[0].bot_id == "ok"
    assert any("bad.json" in r.message for r in caplog.records)


def test_registry_rejects_duplicate_bot_ids(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({
        "bot_id": "dup", "display_name": "First"
    }))
    (tmp_path / "b.json").write_text(json.dumps({
        "bot_id": "dup", "display_name": "Second"
    }))
    with pytest.raises(ValueError, match="duplicate bot_id"):
        BotRegistry.from_directory(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_registry.py -v`
Expected: `ModuleNotFoundError: No module named 'core.bot_registry'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/bot_registry.py
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from core.bot_config import BotConfig


logger = logging.getLogger(__name__)


@dataclass
class BotRegistry:
    configs: list[BotConfig] = field(default_factory=list)

    @classmethod
    def from_directory(cls, dir_path) -> "BotRegistry":
        dir_path = Path(dir_path)
        if not dir_path.exists():
            return cls(configs=[])

        configs: list[BotConfig] = []
        seen: set[str] = set()
        for path in sorted(dir_path.glob("*.json")):
            try:
                cfg = BotConfig.from_json(path)
            except Exception as e:
                logger.warning(
                    "[BotRegistry] skipped malformed config %s: %s",
                    path.name, e,
                )
                continue
            if cfg.bot_id in seen:
                raise ValueError(
                    f"duplicate bot_id={cfg.bot_id} (file: {path.name})"
                )
            seen.add(cfg.bot_id)
            configs.append(cfg)
        return cls(configs=configs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_registry.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add core/bot_registry.py tests/test_bot_registry.py
git commit -m "feat(bot_registry): load BotConfigs from config/bots/*.json

Startup-time scanner that ingests every .json in config/bots/. Malformed
files are logged + skipped (so one bad file doesn't take down the
process). Duplicate bot_ids raise — they would corrupt per-bot accounting.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 6: Initial bot configs + 3-bot in-memory smoke test

### Task 13: Define baseline_v1, no_sol_gate, no_filters config files

**Files:**
- Create: `config/bots/baseline_v1.json`
- Create: `config/bots/no_sol_gate.json`
- Create: `config/bots/no_filters.json`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_registry.py (append)
from pathlib import Path

def test_smoke_configs_present_and_loadable():
    config_dir = Path(__file__).parent.parent / "config" / "bots"
    reg = BotRegistry.from_directory(config_dir)
    by_id = {c.bot_id: c for c in reg.configs}
    assert "baseline_v1" in by_id
    assert "no_sol_gate" in by_id
    assert "no_filters" in by_id

    # baseline_v1 matches production HEAD defaults
    base = by_id["baseline_v1"]
    assert base.sol_macro_h6_block_threshold == -0.3
    assert base.mcap_psych_pc_h24_max == 80.0
    assert base.hard_stop_pct == -15.0

    # no_sol_gate disables sol macro gate
    nsg = by_id["no_sol_gate"]
    assert nsg.sol_macro_h6_block_threshold is None
    assert nsg.sol_macro_h1_block_threshold is None

    # no_filters disables all filters
    nf = by_id["no_filters"]
    assert nf.filters_enforced == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bot_registry.py::test_smoke_configs_present_and_loadable -v`
Expected: `AssertionError: 'baseline_v1' in ...` (configs don't exist yet)

- [ ] **Step 3: Write the 3 config files**

```bash
mkdir -p config/bots
```

`config/bots/baseline_v1.json`:
```json
{
  "bot_id": "baseline_v1",
  "display_name": "Baseline (current production)",
  "enabled": true,
  "paper_capital_usd": 2000.0,
  "base_position_usd": 20.0,
  "max_concurrent_positions": 3,
  "alpha_multiplier": 1.5,
  "macro_up_multiplier": 1.5,
  "premium_runner_multiplier": 3.0,
  "marginal_multiplier": 0.5,
  "sol_macro_h6_block_threshold": -0.3,
  "sol_macro_h1_block_threshold": -0.7,
  "btc_macro_h1_block_threshold": null,
  "pc_h24_max": null,
  "pc_h24_min": null,
  "pc_h1_max": null,
  "age_h_min": null,
  "age_h_max": null,
  "mcap_min": null,
  "mcap_max": null,
  "vol_h1_min": 1000.0,
  "filters_enforced": null,
  "filters_disabled": [],
  "triggers_allowed": null,
  "triggers_disabled": [],
  "min_triggers_to_fire": 1,
  "require_alpha_trigger": false,
  "mcap_psych_pc_h24_max": 80.0,
  "tp1_pct": 5.0,
  "tp1_sell_fraction": 0.75,
  "tp2_pct": 10.0,
  "tp2_sell_fraction": 0.25,
  "trail_pp": 3.0,
  "hard_stop_pct": -15.0,
  "pre_stop_bail_pnl_pct": -3.0,
  "pre_stop_bail_vol_m5_max": 500.0,
  "slow_bleed_minutes": 60,
  "slow_bleed_pnl_threshold": -8.0,
  "trading_hour_utc_start": 0,
  "trading_hour_utc_end": 24
}
```

`config/bots/no_sol_gate.json`:
```json
{
  "bot_id": "no_sol_gate",
  "display_name": "No SOL macro gate",
  "enabled": true,
  "paper_capital_usd": 2000.0,
  "base_position_usd": 20.0,
  "max_concurrent_positions": 3,
  "alpha_multiplier": 1.5,
  "macro_up_multiplier": 1.5,
  "premium_runner_multiplier": 3.0,
  "marginal_multiplier": 0.5,
  "sol_macro_h6_block_threshold": null,
  "sol_macro_h1_block_threshold": null,
  "btc_macro_h1_block_threshold": null,
  "pc_h24_max": null,
  "pc_h24_min": null,
  "pc_h1_max": null,
  "age_h_min": null,
  "age_h_max": null,
  "mcap_min": null,
  "mcap_max": null,
  "vol_h1_min": 1000.0,
  "filters_enforced": null,
  "filters_disabled": [],
  "triggers_allowed": null,
  "triggers_disabled": [],
  "min_triggers_to_fire": 1,
  "require_alpha_trigger": false,
  "mcap_psych_pc_h24_max": 80.0,
  "tp1_pct": 5.0,
  "tp1_sell_fraction": 0.75,
  "tp2_pct": 10.0,
  "tp2_sell_fraction": 0.25,
  "trail_pp": 3.0,
  "hard_stop_pct": -15.0,
  "pre_stop_bail_pnl_pct": -3.0,
  "pre_stop_bail_vol_m5_max": 500.0,
  "slow_bleed_minutes": 60,
  "slow_bleed_pnl_threshold": -8.0,
  "trading_hour_utc_start": 0,
  "trading_hour_utc_end": 24
}
```

`config/bots/no_filters.json`:
```json
{
  "bot_id": "no_filters",
  "display_name": "No filters enforced",
  "enabled": true,
  "paper_capital_usd": 2000.0,
  "base_position_usd": 20.0,
  "max_concurrent_positions": 3,
  "alpha_multiplier": 1.5,
  "macro_up_multiplier": 1.5,
  "premium_runner_multiplier": 3.0,
  "marginal_multiplier": 0.5,
  "sol_macro_h6_block_threshold": -0.3,
  "sol_macro_h1_block_threshold": -0.7,
  "btc_macro_h1_block_threshold": null,
  "pc_h24_max": null,
  "pc_h24_min": null,
  "pc_h1_max": null,
  "age_h_min": null,
  "age_h_max": null,
  "mcap_min": null,
  "mcap_max": null,
  "vol_h1_min": 1000.0,
  "filters_enforced": [],
  "filters_disabled": [],
  "triggers_allowed": null,
  "triggers_disabled": [],
  "min_triggers_to_fire": 1,
  "require_alpha_trigger": false,
  "mcap_psych_pc_h24_max": 80.0,
  "tp1_pct": 5.0,
  "tp1_sell_fraction": 0.75,
  "tp2_pct": 10.0,
  "tp2_sell_fraction": 0.25,
  "trail_pp": 3.0,
  "hard_stop_pct": -15.0,
  "pre_stop_bail_pnl_pct": -3.0,
  "pre_stop_bail_vol_m5_max": 500.0,
  "slow_bleed_minutes": 60,
  "slow_bleed_pnl_threshold": -8.0,
  "trading_hour_utc_start": 0,
  "trading_hour_utc_end": 24
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bot_registry.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add config/bots/ tests/test_bot_registry.py
git commit -m "config: define baseline_v1, no_sol_gate, no_filters smoke bots

3-bot smoke fleet for Sub-project 1 harness validation:
- baseline_v1: exactly current production HEAD config
- no_sol_gate: baseline minus sol macro gate
- no_filters: baseline minus all enforced filters

Each gets its own \$2000 paper capital pool. Sub-project 2 will add
the ~18 thesis/ablation bot catalog; Sub-project 3 the ~25 filter-
focused bots. These 3 only validate the harness works.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 14: 3-bot in-memory smoke test

**Files:**
- Create: `tests/test_multi_bot_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_multi_bot_smoke.py
"""Integration smoke: 3 bots running in-memory against a stream of
FeatureBundles. Verifies independent state, isolation, and persistence.
"""
from pathlib import Path
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator
from core.bot_manager import BotManager
from core.bot_registry import BotRegistry
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager
from core.multi_bot_persistence import MultiBotTradeStore


def _bundle(token, pc_h24=None, sol_pc_h6=None,
            filters_block=(), triggers=("deep_1h_dip",)):
    return FeatureBundle(
        token=token, address=f"addr_{token}", pair_address=f"pair_{token}",
        chain="solana", snapshot_ts=1.0, price_usd=0.001, mcap_usd=4_000_000.0,
        age_hours=240.0,
        pc_h24=pc_h24, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=sol_pc_h6, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=triggers,
        triggers_shadow=(),
        filters_block=filters_block, filters_pass=(), filters_shadow=(),
        raw_meta={},
    )


def test_3bot_smoke_independent_state(tmp_path):
    config_dir = Path(__file__).parent.parent / "config" / "bots"
    reg = BotRegistry.from_directory(config_dir)
    evaluators = [BotEvaluator(c) for c in reg.configs]
    mgr = BotManager(evaluators=evaluators)

    capitals = {c.bot_id: PerBotCapital(c.bot_id, c.paper_capital_usd)
                for c in reg.configs}
    position_mgrs = {c.bot_id: PerBotPositionManager(c) for c in reg.configs}
    store = MultiBotTradeStore(data_dir=tmp_path)

    # SCENARIO 1: SOL macro down candidate.
    # baseline_v1 + no_filters BLOCK (both have sol gate enabled).
    # no_sol_gate enters.
    b1 = _bundle(token="A", sol_pc_h6=-1.0)
    decisions = mgr.evaluate_all(b1)
    assert {d.bot_id for d in decisions} == {"no_sol_gate"}

    # Apply the decision
    for d in decisions:
        capitals[d.bot_id].reserve_for_buy(d.size_usd)
        position_mgrs[d.bot_id].open_position(
            d.token, d.entry_price, d.size_usd, entry_time=1.0
        )
        store.record_trade({
            "type": "buy", "token": d.token, "entry_price": d.entry_price,
            "amount_usd": d.size_usd, "time": "2026-05-23T10:00:00+00:00",
        }, bot_id=d.bot_id)

    # SCENARIO 2: filter_corpse blocks candidate.
    # baseline_v1 + no_sol_gate BLOCK (both enforce baseline filters).
    # no_filters enters.
    b2 = _bundle(token="B", filters_block=("filter_corpse",))
    decisions = mgr.evaluate_all(b2)
    assert {d.bot_id for d in decisions} == {"no_filters"}

    for d in decisions:
        capitals[d.bot_id].reserve_for_buy(d.size_usd)
        position_mgrs[d.bot_id].open_position(
            d.token, d.entry_price, d.size_usd, entry_time=2.0
        )
        store.record_trade({
            "type": "buy", "token": d.token, "entry_price": d.entry_price,
            "amount_usd": d.size_usd, "time": "2026-05-23T10:01:00+00:00",
        }, bot_id=d.bot_id)

    # SCENARIO 3: clean candidate, all 3 bots enter.
    b3 = _bundle(token="C")
    decisions = mgr.evaluate_all(b3)
    assert {d.bot_id for d in decisions} == {"baseline_v1", "no_sol_gate", "no_filters"}

    for d in decisions:
        capitals[d.bot_id].reserve_for_buy(d.size_usd)
        position_mgrs[d.bot_id].open_position(
            d.token, d.entry_price, d.size_usd, entry_time=3.0
        )
        store.record_trade({
            "type": "buy", "token": d.token, "entry_price": d.entry_price,
            "amount_usd": d.size_usd, "time": "2026-05-23T10:02:00+00:00",
        }, bot_id=d.bot_id)

    # ASSERTIONS — independent state
    assert capitals["baseline_v1"].in_flight_usd == 20.0  # only b3
    assert capitals["no_sol_gate"].in_flight_usd == 40.0  # b1 + b3
    assert capitals["no_filters"].in_flight_usd == 40.0   # b2 + b3

    assert position_mgrs["baseline_v1"].open_count == 1
    assert position_mgrs["no_sol_gate"].open_count == 2
    assert position_mgrs["no_filters"].open_count == 2

    # Persistence — each bot's trades visible separately
    assert len(store.load_trades(bot_id="baseline_v1")) == 1
    assert len(store.load_trades(bot_id="no_sol_gate")) == 2
    assert len(store.load_trades(bot_id="no_filters")) == 2
    assert len(store.load_trades()) == 5  # all bots combined
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_multi_bot_smoke.py -v`
Expected: PASS — all components built in prior tasks work together end-to-end.

- [ ] **Step 3: Commit**

```bash
git add tests/test_multi_bot_smoke.py
git commit -m "test(multi_bot): in-memory smoke covering 3 bots end-to-end

Validates:
- Fan-out: same FeatureBundle, different decisions per bot
- Isolation: per-bot capital + positions track independently
- Filter semantics: filters_enforced=[] truly disables all filters
- SOL gate semantics: threshold=None truly disables the gate
- Persistence: trades.json stamped with bot_id, loadable per-bot

This is the harness-validation test for Sub-project 1. If it passes,
the data structures + decision logic + orchestrator + persistence
work together as designed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 7: Wire into existing scanner + dashboard

### Task 15: dip_scanner produces FeatureBundle

This is the most invasive task. The current `DipScanner._scan_cycle` mixes feature computation, trigger evaluation, filter evaluation, and decision-making into one ~2000-line loop body. The refactor extracts the FeatureBundle construction so the BotManager can take over decision-making.

**Files:**
- Modify: `feeds/dip_scanner.py` (around lines 4900-8800 — the per-token loop body)
- The strategy: after the existing per-token feature computation completes, construct a `FeatureBundle` from local variables, then pass it to `bot_manager.evaluate_all()`. Keep the existing single-bot decision path active behind a feature flag (`MULTI_BOT_ENABLED`) so we can rollback.

- [ ] **Step 1: Add the import + flag**

In `feeds/dip_scanner.py`, near the top with other imports:

```python
from core.feature_bundle import FeatureBundle
from core.bot_manager import BotManager
import os

MULTI_BOT_ENABLED = os.getenv("MULTI_BOT_ENABLED", "false").lower() == "true"
```

- [ ] **Step 2: Add bot_manager param to DipScanner.__init__**

Find the `class DipScanner:` definition and the `__init__` signature. Add an optional `bot_manager` parameter:

```python
def __init__(
    self,
    # ... existing params ...
    bot_manager: BotManager | None = None,
):
    # ... existing init body ...
    self.bot_manager = bot_manager
```

- [ ] **Step 3: Find the trigger/filter eval block exit point and emit FeatureBundle**

In `_scan_cycle`, after the per-token block populates `_triggers_fired`, `_alt_reasons`, etc., but before the existing buy-decision logic (around line 12900-12950 where `_is_alpha_trigger` is computed):

Insert this block:

```python
# 2026-05-23 — Multi-bot fan-out (Sub-project 1). When MULTI_BOT_ENABLED
# is set, build a FeatureBundle from the local vars and dispatch to every
# bot. When unset, the existing single-bot path continues unchanged.
if MULTI_BOT_ENABLED and self.bot_manager is not None:
    bundle = FeatureBundle(
        token=token_symbol,
        address=address,
        pair_address=pair_address,
        chain=chain,
        snapshot_ts=time.time(),
        price_usd=current_price or 0.0,
        mcap_usd=mcap or 0.0,
        age_hours=age_h or 0.0,
        pc_h24=pc_h24, pc_h6=pc_h6, pc_h1=pc_h1, pc_m5=pc_m5,
        vol_h1_usd=vol_h1 if 'vol_h1' in dir() else None,
        bs_h1=bs_h1 if 'bs_h1' in dir() else None,
        sol_pc_h1=sol_features.get("sol_pc_h1") if sol_features else None,
        sol_pc_h4=sol_features.get("sol_pc_h4") if sol_features else None,
        sol_pc_h6=sol_features.get("sol_pc_h6") if sol_features else None,
        sol_pc_h24=sol_features.get("sol_pc_h24") if sol_features else None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,  # populate from btc_features when wired
        net_flow_15s_usd=(_tier3_net_flow or {}).get("net_flow_15s_usd")
            if '_tier3_net_flow' in dir() else None,
        net_flow_60s_usd=(_tier3_net_flow or {}).get("net_flow_60s_usd")
            if '_tier3_net_flow' in dir() else None,
        net_flow_5m_usd=(_tier3_net_flow or {}).get("net_flow_5m_usd")
            if '_tier3_net_flow' in dir() else None,
        top_buy_makers_n=(_tier2_features or {}).get("top_buy_makers_n")
            if '_tier2_features' in dir() else None,
        p90_buy_size_usd=None,  # populate when wired
        chart_mtf_score=(_chart_data_dict or {}).get("chart_mtf_score")
            if '_chart_data_dict' in dir() else None,
        chart_score=(_chart_data_dict or {}).get("chart_score")
            if '_chart_data_dict' in dir() else None,
        cnn_cluster_id=cnn_cluster_id if 'cnn_cluster_id' in dir() else None,
        fusion_outcome_prob=fusion_prob if 'fusion_prob' in dir() else None,
        triggers_fired=tuple(_triggers_fired),
        triggers_shadow=tuple(_triggers_shadow if '_triggers_shadow' in dir() else []),
        filters_block=tuple(blocked_filters if 'blocked_filters' in dir() else []),
        filters_pass=(),  # baseline computes blocks only
        filters_shadow=(),
        raw_meta=entry_meta_dict if 'entry_meta_dict' in dir() else {},
    )
    decisions = self.bot_manager.evaluate_all(bundle)
    # Hand off to the trader for each decision (Task 16 wires this).
    for d in decisions:
        await self._execute_bot_buy(d, bundle)
    # Skip the legacy single-bot path when multi-bot is active for this token
    continue
```

Note: the `# ... existing params ...` and `'foo' in dir()` defensive patterns are because the per-token loop body has many optional locals that may not always be defined depending on which feature-fetch succeeded. The defensive `in dir()` checks prevent NameError when a fetch fails.

- [ ] **Step 4: Run existing tests + new harness tests**

Run: `pytest tests/ -v -x`
Expected: All existing tests still pass; multi-bot tests pass; dip_scanner imports successfully.

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py
git commit -m "feat(scanner): wire FeatureBundle production + BotManager fan-out

Behind MULTI_BOT_ENABLED env flag (default false). When set, the per-token
loop emits a FeatureBundle and dispatches to BotManager.evaluate_all()
instead of running the legacy single-bot decision path.

Existing tests pass unchanged because MULTI_BOT_ENABLED defaults to
false. Production smoke deploy will set MULTI_BOT_ENABLED=true with
3 bot configs to validate end-to-end.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 16: Trader executes per-bot BuyDecisions

**Files:**
- Modify: `feeds/dip_scanner.py` (add `_execute_bot_buy` method)
- Modify: `core/trader.py` (factor out the existing buy execution into a callable that takes (token, entry_price, size_usd, bot_id))

- [ ] **Step 1: Write a failing test**

Add to `tests/test_multi_bot_smoke.py`:

```python
def test_3bot_smoke_persists_each_bot_state_to_disk(tmp_path):
    """Verify per-bot state persists across a save/load cycle."""
    config_dir = Path(__file__).parent.parent / "config" / "bots"
    reg = BotRegistry.from_directory(config_dir)
    capitals = {c.bot_id: PerBotCapital(c.bot_id, c.paper_capital_usd)
                for c in reg.configs}
    capitals["baseline_v1"].reserve_for_buy(20.0)
    capitals["no_sol_gate"].reserve_for_buy(40.0)

    store = MultiBotTradeStore(data_dir=tmp_path)
    for c in reg.configs:
        store.save_bot_state(c.bot_id, capitals[c.bot_id].to_dict())

    # Simulate process restart: load all state
    loaded = {
        c.bot_id: PerBotCapital.from_dict(store.load_bot_state(c.bot_id))
        for c in reg.configs
    }
    assert loaded["baseline_v1"].in_flight_usd == 20.0
    assert loaded["baseline_v1"].balance_usd == 1980.0
    assert loaded["no_sol_gate"].in_flight_usd == 40.0
    assert loaded["no_sol_gate"].balance_usd == 1960.0
    assert loaded["no_filters"].in_flight_usd == 0.0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_multi_bot_smoke.py::test_3bot_smoke_persists_each_bot_state_to_disk -v`
Expected: PASS (already supported by Tasks 4 + 5).

- [ ] **Step 3: Add `_execute_bot_buy` to DipScanner**

In `feeds/dip_scanner.py`, add a method:

```python
async def _execute_bot_buy(self, decision, bundle):
    """Execute a BuyDecision from a single bot.

    Reserves capital, opens a position in that bot's position manager,
    and persists the trade with bot_id stamped.
    """
    bot_id = decision.bot_id
    capital = self.bot_capitals.get(bot_id)
    pm = self.bot_position_managers.get(bot_id)
    if capital is None or pm is None:
        logger.error("[DipScanner] missing capital/pm for bot=%s", bot_id)
        return
    try:
        capital.reserve_for_buy(decision.size_usd)
    except ValueError as e:
        logger.info("[DipScanner] bot=%s buy rejected: %s", bot_id, e)
        return
    try:
        pm.open_position(
            token=decision.token,
            entry_price=decision.entry_price,
            size_usd=decision.size_usd,
            entry_time=time.time(),
        )
    except ValueError as e:
        # max_concurrent or duplicate token; refund capital
        capital.balance_usd += decision.size_usd
        capital.in_flight_usd -= decision.size_usd
        logger.info("[DipScanner] bot=%s open_position rejected: %s", bot_id, e)
        return
    # Persist
    self.trade_store.record_trade({
        "type": "buy",
        "token": decision.token,
        "address": decision.address,
        "pair_address": decision.pair_address,
        "entry_price": decision.entry_price,
        "amount_usd": decision.size_usd,
        "size_tier": decision.size_tier,
        "time": datetime.now(timezone.utc).isoformat(),
        "triggers_fired": list(decision.triggers_fired),
        "entry_meta": bundle.raw_meta,
    }, bot_id=bot_id)
    # Save bot state snapshot
    self.trade_store.save_bot_state(bot_id, capital.to_dict())
    logger.info(
        "[DipScanner] BUY bot=%s token=%s size=$%.2f tier=%s",
        bot_id, decision.token, decision.size_usd, decision.size_tier,
    )
```

- [ ] **Step 4: Wire `bot_capitals`, `bot_position_managers`, `trade_store` into `DipScanner.__init__`**

```python
# In DipScanner.__init__, after self.bot_manager = bot_manager:
self.bot_capitals: dict[str, PerBotCapital] = {}
self.bot_position_managers: dict[str, PerBotPositionManager] = {}
self.trade_store = trade_store  # passed in by caller (main.py)
if bot_manager is not None:
    for ev in bot_manager.evaluators:
        c = ev.config
        # Try to restore state from disk
        existing = trade_store.load_bot_state(c.bot_id) if trade_store else None
        if existing:
            self.bot_capitals[c.bot_id] = PerBotCapital.from_dict(existing)
        else:
            self.bot_capitals[c.bot_id] = PerBotCapital(
                c.bot_id, c.paper_capital_usd,
            )
        self.bot_position_managers[c.bot_id] = PerBotPositionManager(c)
```

Add the required imports:

```python
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager
from core.multi_bot_persistence import MultiBotTradeStore
from datetime import datetime, timezone
```

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_multi_bot_smoke.py
git commit -m "feat(scanner): _execute_bot_buy + per-bot state restore on init

DipScanner now owns:
- bot_capitals: dict[bot_id -> PerBotCapital] (restored from disk if present)
- bot_position_managers: dict[bot_id -> PerBotPositionManager]
- trade_store: MultiBotTradeStore for persistence

When MULTI_BOT_ENABLED and a BotManager is wired, each scan cycle emits
a FeatureBundle, fans out to all bots, and any BuyDecision is executed
through _execute_bot_buy (reserve capital, open position, persist).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 17: Position tick loop (per-bot exit decisions)

**Files:**
- Modify: `feeds/dip_scanner.py` or wherever the existing price-tick loop lives
- Modify: `core/position_manager.py` if needed

- [ ] **Step 1: Locate the existing tick loop**

Run: `grep -n "def _tick_positions\|async def position_tick\|asyncio.sleep.*price" feeds/dip_scanner.py core/position_manager.py`

The existing PositionManager has a tick loop that polls `PoolPriceFeed` for each open position's price and calls exit logic. We need to add a parallel loop that does the same for each bot's PerBotPositionManager.

- [ ] **Step 2: Add `tick_all_bots_positions` to DipScanner**

```python
async def _tick_all_bots_positions(self):
    """Per-bot exit-decision loop. Runs in parallel with main scan loop.

    For each bot, for each open position, fetch the current price + vol,
    call PerBotPositionManager.tick(), and execute any ExitDecisions.
    """
    for bot_id, pm in self.bot_position_managers.items():
        for position in pm.iter_positions():
            try:
                # Fetch current price from PoolPriceFeed (shared across bots)
                price = await self.pool_price_feed.get_price(position.token)
                vol_m5 = await self.pool_price_feed.get_vol_m5(position.token)
                if price is None:
                    continue
                now = time.time()
                decisions = pm.tick(
                    token=position.token,
                    current_price=price,
                    now=now,
                    vol_m5_usd=vol_m5,
                )
                for d in decisions:
                    await self._execute_bot_sell(bot_id, position.token, d, price, now)
            except Exception as e:
                logger.error(
                    "[DipScanner] tick failed bot=%s token=%s: %s",
                    bot_id, position.token, e,
                )
```

- [ ] **Step 3: Add `_execute_bot_sell`**

```python
async def _execute_bot_sell(self, bot_id, token, exit_decision, current_price, now):
    """Execute an ExitDecision from a single bot's position tick.

    Currently treats ALL exit_decisions as full close (sell_fraction=1.0
    behavior). Partial sells (TP1 selling 75%) is a Sub-project 2 line
    item — for now the harness ships with full-close semantics, mimicking
    the simpler exit ladder for the smoke test.
    """
    capital = self.bot_capitals[bot_id]
    pm = self.bot_position_managers[bot_id]
    try:
        result = pm.close_position(
            token=token,
            exit_price=current_price,
            exit_time=now,
            reason=exit_decision.reason,
        )
    except KeyError:
        return  # already closed
    capital.realize_sell(
        cost_usd=result.cost_usd,
        proceeds_usd=result.proceeds_usd,
    )
    self.trade_store.record_trade({
        "type": "sell",
        "token": token,
        "exit_price": current_price,
        "pnl": result.realized_pnl_usd,
        "pnl_pct": result.pnl_pct,
        "peak_pnl_pct": result.peak_pnl_pct,
        "hold_secs": result.hold_secs,
        "reason": exit_decision.reason,
        "kind": exit_decision.kind,
        "time": datetime.now(timezone.utc).isoformat(),
    }, bot_id=bot_id)
    self.trade_store.save_bot_state(bot_id, capital.to_dict())
    logger.info(
        "[DipScanner] SELL bot=%s token=%s pnl=$%.2f reason=%s",
        bot_id, token, result.realized_pnl_usd, exit_decision.reason,
    )
```

- [ ] **Step 4: Schedule `_tick_all_bots_positions` in the main loop**

In the main async run loop, alongside the existing scan/position-tick code:

```python
# Run scan + position-tick concurrently
if MULTI_BOT_ENABLED:
    await asyncio.gather(
        self._scan_cycle(),
        self._tick_all_bots_positions(),
    )
else:
    await self._scan_cycle()
```

- [ ] **Step 5: Smoke run + commit**

Run: `pytest tests/ -v -x`
Expected: All tests still pass (this task doesn't break existing flow because MULTI_BOT_ENABLED defaults to false).

```bash
git add feeds/dip_scanner.py
git commit -m "feat(scanner): _tick_all_bots_positions exit loop

Each scan iteration also runs per-bot exit ticks in parallel. For each
open position, fetches price from PoolPriceFeed (shared), calls the
bot's PerBotPositionManager.tick(), and executes any ExitDecision via
_execute_bot_sell. Sells persist with bot_id stamped.

Partial sells (TP1 selling 75%) currently approximated as full close
for the smoke test — proper partial-sell handling is a Sub-project 2
deliverable.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 8: Dashboard

### Task 18: /api/bots and /api/leaderboard endpoints

**Files:**
- Modify: `dashboard/web_dashboard.py`

- [ ] **Step 1: Write a failing test**

```python
# tests/test_dashboard_bot_endpoints.py
import json
import pytest
from pathlib import Path
from core.bot_config import BotConfig
from core.multi_bot_persistence import MultiBotTradeStore
from core.per_bot_capital import PerBotCapital


@pytest.fixture
def dashboard_with_bots(tmp_path):
    """Yield a configured aiohttp app + test client with 3 bots' data populated."""
    from dashboard.web_dashboard import WebDashboard
    from aiohttp.test_utils import TestClient, TestServer
    import asyncio

    store = MultiBotTradeStore(data_dir=tmp_path)
    for bid in ["baseline_v1", "no_sol_gate", "no_filters"]:
        cap = PerBotCapital(bid, 2000.0)
        if bid == "baseline_v1":
            cap.reserve_for_buy(20.0)
            cap.realize_sell(20.0, 22.0)
        store.save_bot_state(bid, cap.to_dict())
        store.record_trade({
            "type": "buy", "token": f"T{bid}", "time": "2026-05-23T10:00:00+00:00",
            "amount_usd": 20.0,
        }, bot_id=bid)

    dash = WebDashboard(trade_store=store)
    return dash


@pytest.mark.asyncio
async def test_api_bots_returns_all_bots(dashboard_with_bots, aiohttp_client):
    client = await aiohttp_client(dashboard_with_bots.app)
    resp = await client.get("/api/bots")
    assert resp.status == 200
    data = await resp.json()
    assert len(data) == 3
    ids = {b["bot_id"] for b in data}
    assert ids == {"baseline_v1", "no_sol_gate", "no_filters"}


@pytest.mark.asyncio
async def test_api_leaderboard_sorts_by_throughput_x_pnl(dashboard_with_bots, aiohttp_client):
    client = await aiohttp_client(dashboard_with_bots.app)
    resp = await client.get("/api/leaderboard?sort=throughput_x_pnl")
    assert resp.status == 200
    data = await resp.json()
    # baseline_v1 has 1 trade with +$2 P&L = throughput×$/tr = 1×2 = 2.0
    # others have 0 closed trades = 0
    assert data[0]["bot_id"] == "baseline_v1"
```

- [ ] **Step 2: Add the endpoints**

In `dashboard/web_dashboard.py`, locate the route registration block and add:

```python
self.app.router.add_get("/api/bots", self._handle_api_bots)
self.app.router.add_get("/api/leaderboard", self._handle_api_leaderboard)
self.app.router.add_get("/api/bots/{bot_id}/trades", self._handle_api_bot_trades)
self.app.router.add_get("/api/bots/{bot_id}/positions", self._handle_api_bot_positions)
```

Add the handlers:

```python
async def _handle_api_bots(self, request):
    if self.trade_store is None:
        return web.json_response([], status=200)
    bots = []
    # Walk bot_state/*.json
    state_dir = self.trade_store.data_dir / "bot_state"
    if state_dir.exists():
        for path in sorted(state_dir.glob("*.json")):
            try:
                state = json.loads(path.read_text())
                trades = self.trade_store.load_trades(bot_id=state["bot_id"])
                buys = [t for t in trades if t.get("type") == "buy"]
                sells = [t for t in trades if t.get("type") == "sell"]
                total_pnl = sum(s.get("pnl", 0) for s in sells)
                bots.append({
                    "bot_id": state["bot_id"],
                    "balance_usd": state["balance_usd"],
                    "in_flight_usd": state["in_flight_usd"],
                    "realized_pnl_total_usd": state["realized_pnl_total_usd"],
                    "daily_pnl_usd": state["daily_pnl_usd"],
                    "open_position_count": len(buys) - len(sells),
                    "total_trades": len(sells),
                    "wins": sum(1 for s in sells if s.get("pnl", 0) > 0),
                    "total_pnl_realized": total_pnl,
                })
            except Exception as e:
                logger.warning("api/bots skipped %s: %s", path, e)
                continue
    return web.json_response(bots)


async def _handle_api_leaderboard(self, request):
    sort = request.query.get("sort", "total_pnl_realized")
    bots = await (await self._handle_api_bots(request)).json()
    if sort == "throughput_x_pnl":
        def key(b):
            n = b["total_trades"]
            if n == 0:
                return 0.0
            per = b["total_pnl_realized"] / n
            return n * per  # = total_pnl_realized, but explicit form
        bots.sort(key=key, reverse=True)
    elif sort == "pnl_per_trade":
        def key(b):
            n = b["total_trades"]
            return b["total_pnl_realized"] / n if n > 0 else 0.0
        bots.sort(key=key, reverse=True)
    else:
        bots.sort(key=lambda b: b.get(sort, 0), reverse=True)
    return web.json_response(bots)


async def _handle_api_bot_trades(self, request):
    bot_id = request.match_info["bot_id"]
    limit = int(request.query.get("limit", 50))
    trades = self.trade_store.load_trades(bot_id=bot_id)
    return web.json_response(trades[-limit:])


async def _handle_api_bot_positions(self, request):
    bot_id = request.match_info["bot_id"]
    trades = self.trade_store.load_trades(bot_id=bot_id)
    buys_by_token = {}
    for t in trades:
        if t.get("type") == "buy":
            buys_by_token[t["token"]] = t
        elif t.get("type") == "sell":
            buys_by_token.pop(t["token"], None)
    return web.json_response(list(buys_by_token.values()))
```

Also add WebDashboard.__init__ accepting trade_store:

```python
def __init__(self, ..., trade_store=None):
    ...
    self.trade_store = trade_store
```

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_dashboard_bot_endpoints.py -v`
Expected: `2 passed`

- [ ] **Step 4: Commit**

```bash
git add dashboard/web_dashboard.py tests/test_dashboard_bot_endpoints.py
git commit -m "feat(dashboard): /api/bots + /api/leaderboard endpoints

New endpoints for multi-bot fleet visibility:
- GET /api/bots                       — list all bots with balance/pnl/open count
- GET /api/leaderboard?sort=X         — sortable by total_pnl/pnl_per_trade/throughput
- GET /api/bots/{bot_id}/trades       — per-bot trade history
- GET /api/bots/{bot_id}/positions    — per-bot open positions

Reads from MultiBotTradeStore. UI panel (Task 19) will consume these.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 19: Dashboard FLEET panel UI

**Files:**
- Modify: `dashboard/web_dashboard.py` (HTML/JS in the rendered page)

- [ ] **Step 1: Inspect existing dashboard HTML structure**

Run: `grep -n "header-bar\|dashboard-grid\|<style>" dashboard/web_dashboard.py | head -10`

Identify where stat-card panels are rendered (existing layout has cards for AVAILABLE / TOTAL P&L / etc. on the homepage).

- [ ] **Step 2: Add FLEET panel above existing stats**

Locate the homepage HTML template string. Add a new panel HTML block:

```html
<div class="fleet-panel">
  <h2>FLEET</h2>
  <table id="fleet-table">
    <thead>
      <tr>
        <th>Bot</th>
        <th>Balance</th>
        <th>Open</th>
        <th>Trades</th>
        <th>Win Rate</th>
        <th>P&amp;L</th>
        <th>$/tr</th>
        <th>Tput × $/tr</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</div>
```

CSS:
```css
.fleet-panel { background: #1a1a1a; padding: 1rem; margin: 1rem 0; border-radius: 8px; }
.fleet-panel table { width: 100%; border-collapse: collapse; }
.fleet-panel th, .fleet-panel td { padding: 0.4rem 0.6rem; text-align: right; }
.fleet-panel th:first-child, .fleet-panel td:first-child { text-align: left; }
.fleet-panel tbody tr:nth-child(odd) { background: #222; }
.fleet-panel .pnl-pos { color: #4caf50; }
.fleet-panel .pnl-neg { color: #f44336; }
```

JS poller:
```javascript
async function updateFleet() {
  try {
    const resp = await fetch("/api/leaderboard?sort=throughput_x_pnl");
    if (!resp.ok) return;
    const bots = await resp.json();
    const tbody = document.querySelector("#fleet-table tbody");
    tbody.innerHTML = "";
    for (const b of bots) {
      const wr = b.total_trades > 0 ? (100 * b.wins / b.total_trades).toFixed(0) : "—";
      const perTr = b.total_trades > 0 ? (b.total_pnl_realized / b.total_trades).toFixed(2) : "—";
      const tputXPnl = (b.total_trades * (b.total_trades > 0 ? b.total_pnl_realized / b.total_trades : 0)).toFixed(2);
      const pnlClass = b.total_pnl_realized > 0 ? "pnl-pos" : (b.total_pnl_realized < 0 ? "pnl-neg" : "");
      const row = `<tr>
        <td>${b.bot_id}</td>
        <td>$${b.balance_usd.toFixed(2)}</td>
        <td>${b.open_position_count}</td>
        <td>${b.total_trades}</td>
        <td>${wr}%</td>
        <td class="${pnlClass}">$${b.total_pnl_realized.toFixed(2)}</td>
        <td>$${perTr}</td>
        <td>$${tputXPnl}</td>
      </tr>`;
      tbody.insertAdjacentHTML("beforeend", row);
    }
  } catch (e) {
    console.error("updateFleet failed", e);
  }
}
setInterval(updateFleet, 15000);
updateFleet();
```

- [ ] **Step 3: Manually verify the panel renders**

Run dashboard locally:
```bash
python -m dashboard.web_dashboard --port 5001 &
sleep 2
curl -s http://localhost:5001/ | head -50
```

Confirm the HTML contains `fleet-panel` and the JS block.

- [ ] **Step 4: Commit**

```bash
git add dashboard/web_dashboard.py
git commit -m "feat(dashboard): FLEET panel UI for multi-bot leaderboard

Adds a sortable table at the top of the dashboard showing per-bot:
- Balance, open positions, total trades, win rate, total P&L, $/tr,
  throughput × $/tr (the success metric for the fleet test).

Polls /api/leaderboard?sort=throughput_x_pnl every 15s. Color-codes
P&L green/red.

Phase 1 UI is minimal — full multi-bot analytics (regime breakdowns,
filter contribution attribution) is Sub-project 4.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 9: Migration + production smoke deploy

### Task 20: Migration script for existing trades.json

**Files:**
- Create: `scripts/migrate_trades_json_bot_id.py`

- [ ] **Step 1: Write a failing test**

```python
# tests/test_migration.py
import json
import subprocess
import sys
from pathlib import Path


def test_migration_adds_bot_id_to_legacy_records(tmp_path):
    legacy = [
        {"type": "buy", "token": "A", "time": "t1"},
        {"type": "sell", "token": "A", "time": "t2", "pnl": 1.0},
    ]
    trades_file = tmp_path / "trades.json"
    trades_file.write_text(json.dumps(legacy))

    script = Path(__file__).parent.parent / "scripts" / "migrate_trades_json_bot_id.py"
    result = subprocess.run(
        [sys.executable, str(script), "--data-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(trades_file.read_text())
    assert all(t["bot_id"] == "baseline_v1" for t in data)


def test_migration_is_idempotent(tmp_path):
    records = [{"type": "buy", "token": "A", "bot_id": "custom"}]
    (tmp_path / "trades.json").write_text(json.dumps(records))
    script = Path(__file__).parent.parent / "scripts" / "migrate_trades_json_bot_id.py"
    subprocess.run([sys.executable, str(script), "--data-dir", str(tmp_path)], check=True)
    subprocess.run([sys.executable, str(script), "--data-dir", str(tmp_path)], check=True)
    data = json.loads((tmp_path / "trades.json").read_text())
    assert data[0]["bot_id"] == "custom"  # not overwritten
```

- [ ] **Step 2: Write the migration script**

```python
# scripts/migrate_trades_json_bot_id.py
"""Backfill bot_id='baseline_v1' on legacy trades.json records.

Idempotent: re-running is safe (records that already have a bot_id are
left untouched).
"""
import argparse
import json
from pathlib import Path


def migrate(data_dir: Path) -> int:
    trades_path = data_dir / "trades.json"
    if not trades_path.exists():
        print(f"No trades.json in {data_dir}; nothing to do.")
        return 0
    data = json.loads(trades_path.read_text())
    updated = 0
    for t in data:
        if "bot_id" not in t:
            t["bot_id"] = "baseline_v1"
            updated += 1
    if updated:
        # Backup first
        backup = data_dir / "trades.json.pre-migrate"
        if not backup.exists():
            backup.write_text(trades_path.read_text())
            print(f"Backup saved to {backup}")
        trades_path.write_text(json.dumps(data))
    print(f"Migration complete: {updated} records updated, {len(data)} total")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="/data")
    args = p.parse_args()
    raise SystemExit(migrate(Path(args.data_dir)))
```

- [ ] **Step 3: Run test**

Run: `pytest tests/test_migration.py -v`
Expected: `2 passed`

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_trades_json_bot_id.py tests/test_migration.py
git commit -m "feat(migrate): backfill bot_id='baseline_v1' on legacy trades.json

Idempotent one-shot migration. Backs up trades.json -> trades.json.pre-migrate
on first run. Subsequent runs are no-ops.

Will run automatically on first MULTI_BOT_ENABLED deploy via the main.py
startup sequence (Task 21).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 21: Wire BotRegistry + BotManager into main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Identify main.py structure**

Run: `grep -n "DipScanner\|WebDashboard\|asyncio.run\|async def main\|if __name__" main.py | head -20`

Identify where the DipScanner and WebDashboard are instantiated.

- [ ] **Step 2: Add bot harness instantiation at startup**

Near the top of `main.py`, add imports:

```python
import os
from core.bot_registry import BotRegistry
from core.bot_evaluator import BotEvaluator
from core.bot_manager import BotManager
from core.multi_bot_persistence import MultiBotTradeStore
from pathlib import Path

MULTI_BOT_ENABLED = os.getenv("MULTI_BOT_ENABLED", "false").lower() == "true"
```

In the startup sequence (before `DipScanner(...)` is constructed):

```python
data_dir = Path(os.environ.get("DATA_DIR", "/data"))
trade_store = MultiBotTradeStore(data_dir=data_dir)

bot_manager = None
if MULTI_BOT_ENABLED:
    # Run migration first (idempotent)
    from scripts.migrate_trades_json_bot_id import migrate
    migrate(data_dir)

    config_dir = Path(__file__).parent / "config" / "bots"
    registry = BotRegistry.from_directory(config_dir)
    evaluators = [BotEvaluator(c) for c in registry.configs]
    bot_manager = BotManager(evaluators=evaluators)
    logger.info(
        "[main] MULTI_BOT_ENABLED — loaded %d bots: %s",
        len(registry.configs),
        [c.bot_id for c in registry.configs],
    )

# Pass to scanner and dashboard
scanner = DipScanner(..., bot_manager=bot_manager, trade_store=trade_store)
dashboard = WebDashboard(..., trade_store=trade_store)
```

(Adjust the `...` to match the existing constructor signatures.)

- [ ] **Step 3: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(main): wire BotRegistry + BotManager startup behind feature flag

When MULTI_BOT_ENABLED=true:
1. Migration runs (idempotent backfill of legacy trades.json)
2. BotRegistry loads config/bots/*.json
3. BotManager is constructed with one evaluator per config
4. DipScanner and WebDashboard receive bot_manager + trade_store

When unset (default): legacy single-bot path unchanged.

This is the final wiring task. Setting MULTI_BOT_ENABLED=true on the
Railway env + redeploying activates the 3-bot smoke fleet.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 22: Production smoke deploy + 24h verification

**Files:** None (operational)

- [ ] **Step 1: Run full test suite locally**

Run: `pytest tests/ -v`
Expected: All tests pass with no errors.

- [ ] **Step 2: Set MULTI_BOT_ENABLED on Railway**

Run:
```bash
MSYS_NO_PATHCONV=1 railway variables --set MULTI_BOT_ENABLED=true
```

Expected output: confirmation that the env var is set.

- [ ] **Step 3: Push + deploy**

```bash
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

Expected: Build succeeds, deployment completes.

- [ ] **Step 4: Verify dashboard shows 3 bots within 5 min of deploy**

```bash
curl -s https://gracious-inspiration-production.up.railway.app/api/bots | python -m json.tool
```

Expected: Array of 3 bot objects with bot_ids baseline_v1, no_sol_gate, no_filters, all with `balance_usd: 2000.0` initially.

- [ ] **Step 5: Verify migration ran (existing trades have bot_id)**

```bash
curl -s "https://gracious-inspiration-production.up.railway.app/api/trades?limit=5" | python -m json.tool
```

Expected: Each trade has a `"bot_id": "baseline_v1"` field.

- [ ] **Step 6: Verify first new trades have correct bot_id**

Wait until at least 1 buy fires (could be 5-30 min depending on candidate flow). Then:

```bash
curl -s "https://gracious-inspiration-production.up.railway.app/api/bots/baseline_v1/trades?limit=5" | python -m json.tool
```

Expected: Most recent trades show `bot_id: "baseline_v1"`.

Also check the variant bots:
```bash
curl -s "https://gracious-inspiration-production.up.railway.app/api/bots/no_sol_gate/trades?limit=5"
curl -s "https://gracious-inspiration-production.up.railway.app/api/bots/no_filters/trades?limit=5"
```

- [ ] **Step 7: 24h soak check**

After 24 hours, verify:
1. All 3 bots still present in `/api/bots`
2. Per-bot trade counts differ (validating different filter/gate behavior)
3. No error spike in Railway logs:
   ```bash
   MSYS_NO_PATHCONV=1 railway logs --tail 500 | grep -i "BotManager\|evaluate failed\|migration"
   ```
4. Dashboard FLEET panel renders without errors

- [ ] **Step 8: Mark sub-project 1 complete + commit summary**

Update `project_bot_handoff.md` with:
- Sub-project 1 status: SHIPPED
- 3-bot smoke deploy: PASS / dates / per-bot stats
- Next: Sub-project 2 spec (catalog of ~18 thesis/ablation bots)

```bash
git add project_bot_handoff.md
git commit -m "docs(handoff): sub-project 1 shipped — multi-bot harness live

3-bot smoke fleet (baseline_v1, no_sol_gate, no_filters) running
in production for 24h+. Per-bot accounting verified independent.

Next: Sub-project 2 — define full ~18 thesis/ablation catalog.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Plan self-review

After writing the plan above, here is my fresh-eyes review:

**Spec coverage:** ✅
- BotConfig: Task 1
- FeatureBundle: Task 3
- PerBotCapital: Task 4
- MultiBotTradeStore (persistence): Task 5
- PerBotPositionManager: Tasks 6, 7, 8
- BotEvaluator: Tasks 9, 10
- BotManager: Task 11
- BotRegistry: Task 12
- 3 smoke configs: Task 13
- In-memory smoke test: Task 14
- Scanner wire-up (FeatureBundle): Task 15
- Buy execution (per-bot): Task 16
- Sell tick loop (per-bot): Task 17
- Dashboard endpoints: Task 18
- Dashboard UI panel: Task 19
- Migration script: Task 20
- main.py wiring: Task 21
- Production smoke deploy: Task 22

**Placeholder scan:** No "TBD", "TODO", or "fill in details" remain. Each step has the actual code to write.

**Type consistency:**
- `BotConfig.filters_enforced: Optional[tuple[str, ...]]` consistent across Tasks 1, 10, 13.
- `BotEvaluator.evaluate(b: FeatureBundle) -> Optional[BuyDecision]` signature consistent across Tasks 9, 10, 11.
- `BotManager.evaluate_all(bundle) -> list[BuyDecision]` consistent.
- `PerBotPositionManager.tick(token, current_price, now, vol_m5_usd=None)` consistent across Tasks 7, 8, 17.
- `MultiBotTradeStore.record_trade(trade: dict, bot_id: str)` consistent across Tasks 5, 16, 17.

**Scope:** Sub-project 1 only. Bot catalog beyond 3 is Sub-project 2. Synthesis is Sub-project 4. Live mode is Sub-project 5.

**Gaps found and addressed inline:** Two minor adjustments made during writing — `_execute_bot_buy` and `_execute_bot_sell` were not in the spec but emerged as natural extraction points; added to Tasks 16, 17.

---

## Open risks deferred to execution

1. **dip_scanner.py refactor in Task 15 is the biggest unknown.** The per-token loop is ~3000 lines with many optional locals. The `'foo' in dir()` defensive pattern in the FeatureBundle construction may need tuning during actual implementation. If construction fails for too many candidates, the bot fleet will be quiet and we'll need to debug — keep the legacy single-bot path active (MULTI_BOT_ENABLED=false fallback) until the bundle is reliably populated.

2. **Partial sells (TP1 sells 75%).** Currently implemented as full close in Task 17. For Sub-project 1's smoke test this is acceptable (we're validating the harness, not optimizing exit ladders). Sub-project 2 should add proper partial-sell semantics to PerBotPositionManager.

3. **Phantom parity for the 2 variant bots.** `live_forward_test.py` only mirrors the baseline. The 2 variant bots' behavior won't show in phantom. Acceptable for Sub-project 1; explicit Sub-project 4 line item.

4. **Memory footprint at scale.** With only 3 bots in smoke deploy, this is fine. Sub-projects 2+3 (scaling to ~43 bots) will need to verify Railway tier handles 2GB+.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-05-23-multi-bot-harness-plan.md`.

The plan covers 22 tasks across 9 phases. Each task is TDD-disciplined (failing test → minimal impl → passing test → commit) and self-contained (an engineer can pick up any task without reading the others).

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks for spec compliance + code quality. Fast iteration, isolates context per task, catches regressions early.

2. **Inline Execution** — Execute tasks in this session using executing-plans. Batch execution with checkpoints. Faster wall-clock but my context window grows quickly across 22 tasks.

**Recommendation: Subagent-Driven** — at 22 tasks and ~3500 lines of code across multiple files, subagent isolation will keep each task's reasoning focused and catch issues earlier.
