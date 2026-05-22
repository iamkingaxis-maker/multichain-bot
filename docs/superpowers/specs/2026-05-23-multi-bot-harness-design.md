# Multi-Bot Harness — Sub-Project 1 Design Spec

**Status:** Awaiting approval
**Date:** 2026-05-23
**Author:** Claude (Opus 4.7) with jcoleman-droid
**Parent project:** Multi-bot fleet production fleet (5 sub-projects total)

---

## Goal

Refactor the dip-buy bot to support running **N virtual bots in a single Railway process**, each with its own config, paper capital pool, positions, and trade history. The bots share feature computation (one scan cycle services all) but make independent decisions and maintain independent accounting.

This is the foundation for the broader fleet test (~43 bots) that will identify which configs/features/filters actually work in production conditions.

---

## Architecture

**Today:** one `DipScanner` produces candidates → one `Trader` → one `PositionManager` → one paper balance → one `trades` table.

**After Sub-project 1:** one `DipScanner` produces a `FeatureBundle` per token per cycle → `BotManager` fans out to N `BotEvaluator` instances → each `BotEvaluator` consults its own `BotConfig` and decides independently → each maintains its own `PerBotPositionManager` and `PerBotCapital` → all trades land in a shared `trades` table with a `bot_id` column.

**Key invariant:** feature computation happens exactly once per token per cycle (no rate-limit explosion). Decision-making and position management are per-bot.

---

## Components

### `core/bot_config.py` (new)
A frozen dataclass defining the universal config schema. Every knob the strategy uses becomes a field:

```python
@dataclass(frozen=True)
class BotConfig:
    bot_id: str                      # unique identifier, e.g., "baseline_v1"
    display_name: str                # for dashboard
    enabled: bool = True

    # Capital & sizing
    paper_capital_usd: float = 2000.0
    base_position_usd: float = 20.0
    max_concurrent_positions: int = 3
    alpha_multiplier: float = 1.5
    macro_up_multiplier: float = 1.5
    premium_runner_multiplier: float = 3.0
    marginal_multiplier: float = 0.5

    # Macro gates (set to None to disable)
    sol_macro_h6_block_threshold: Optional[float] = -0.3
    sol_macro_h1_block_threshold: Optional[float] = -0.7
    btc_macro_h1_block_threshold: Optional[float] = None

    # Token regime gates
    pc_h24_max: Optional[float] = None          # ceiling
    pc_h24_min: Optional[float] = None          # floor
    pc_h1_max: Optional[float] = None
    age_h_min: Optional[float] = None
    age_h_max: Optional[float] = None
    mcap_min: Optional[float] = None
    mcap_max: Optional[float] = None
    vol_h1_min: Optional[float] = 1000.0

    # Filter set
    # Semantics: if filters_enforced is None, the bot uses the project baseline
    #   filter set MINUS anything listed in filters_disabled.
    # If filters_enforced is a list, that's the EXACT enforced set and
    #   filters_disabled is ignored (use [] then).
    filters_enforced: Optional[list[str]] = None
    filters_disabled: list[str] = field(default_factory=list)

    # Triggers (same semantics as filters)
    triggers_allowed: Optional[list[str]] = None
    triggers_disabled: list[str] = field(default_factory=list)
    min_triggers_to_fire: int = 1
    require_alpha_trigger: bool = False

    # Trigger-specific gates (apply only when the named trigger would fire).
    # These are evaluated AFTER the universal token-regime gates (pc_h24_max
    # etc.) — if a universal gate already blocked the candidate, these are
    # never reached. They're for narrowing a specific trigger, not for
    # gating the whole entry.
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

    # Trading window (UTC hours)
    trading_hour_utc_start: int = 0
    trading_hour_utc_end: int = 24
```

Configs are loaded from `config/bots/{bot_id}.json` (JSON serialization of dataclass). Each bot has one file.

### `core/bot_evaluator.py` (new)
Per-bot decision engine. One `BotEvaluator` instance per bot. Holds a `BotConfig` + a `PerBotState` (capital, positions, daily P&L). Has one main method:

```python
def evaluate(self, candidate: FeatureBundle) -> Optional[BuyDecision]:
    # 1. Apply macro gates (sol, btc, time-of-day)
    # 2. Apply token regime gates (pc_h24, age, mcap, vol)
    # 3. Apply filter set per config
    # 4. Apply trigger requirements per config
    # 5. Check capital availability + position limits
    # 6. Compute sizing tier
    # 7. Return BuyDecision or None
```

The evaluator pulls all its inputs from `candidate` (the FeatureBundle) — no I/O, no external lookups. This makes it cheap to call N times per cycle.

### `core/bot_manager.py` (new)
Orchestrator. Owns the list of `BotEvaluator` instances. Receives a `FeatureBundle` from the scanner and fans out:

```python
async def evaluate_all(self, candidate: FeatureBundle) -> list[BuyDecision]:
    decisions = []
    for bot in self.bots:
        try:
            d = bot.evaluate(candidate)
            if d:
                decisions.append(d)
        except Exception as e:
            logger.error(f"[BotManager] bot={bot.config.bot_id} eval failed: {e}")
            # CRITICAL: one bot failing must not kill others
            continue
    return decisions
```

Also owns:
- `tick_positions()` — called each cycle to update per-bot open positions (TP/trail/stop checks)
- `bot_state_for_dashboard()` — returns serialized state for API

### `core/per_bot_capital.py` (new)
Per-bot paper capital tracker. Lifted from existing `scalp_capital.py` pattern but generalized. Tracks:
- `paper_balance_usd` (starts at config.paper_capital_usd)
- `in_flight_usd` (open positions × size)
- `realized_pnl_usd`
- `daily_pnl_usd` (resets at UTC 00:00)

### `core/per_bot_position_manager.py` (new)
Per-bot position state machine. Tracks open positions for this bot only. Existing `PositionManager` logic moves here, parameterized by config (exit ladder values come from `BotConfig`).

### `core/feature_bundle.py` (new)
Frozen dataclass holding everything `evaluate()` needs:

```python
@dataclass(frozen=True)
class FeatureBundle:
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

    # Triggers fired (from existing trigger evaluator)
    triggers_fired: tuple[str, ...]
    triggers_shadow: tuple[str, ...]

    # Filter verdicts (from existing filter evaluator)
    filters_block: tuple[str, ...]
    filters_pass: tuple[str, ...]
    filters_shadow: tuple[str, ...]

    # Raw entry_meta (legacy passthrough)
    raw_meta: dict
```

The current `DipScanner._scan_cycle` already computes most of these. The refactor wraps them in this immutable bundle and pushes the bundle to BotManager.

### Database schema changes
```sql
ALTER TABLE trades ADD COLUMN bot_id TEXT NOT NULL DEFAULT 'baseline_v1';
CREATE INDEX idx_trades_bot_id_time ON trades(bot_id, time);

CREATE TABLE bot_state (
    bot_id TEXT PRIMARY KEY,
    paper_balance_usd REAL NOT NULL,
    in_flight_usd REAL NOT NULL,
    realized_pnl_total_usd REAL NOT NULL,
    daily_pnl_usd REAL NOT NULL,
    daily_pnl_date TEXT NOT NULL,
    last_updated_at REAL NOT NULL
);

CREATE TABLE bot_positions (
    bot_id TEXT NOT NULL,
    token TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_time REAL NOT NULL,
    size_usd REAL NOT NULL,
    peak_pnl_pct REAL NOT NULL DEFAULT 0,
    tp1_hit INTEGER NOT NULL DEFAULT 0,
    state_blob TEXT NOT NULL,
    PRIMARY KEY (bot_id, token, entry_price)
);
```

Migration script handles ALTER TABLE on the live `/data/trades.db`.

### Dashboard changes (`dashboard/web_dashboard.py`)
New endpoints:
- `GET /api/bots` — fleet status: list of bots with current paper_balance, open_position_count, daily_pnl, total_pnl
- `GET /api/bots/{bot_id}/trades?limit=N` — per-bot trade history
- `GET /api/bots/{bot_id}/positions` — per-bot open positions
- `GET /api/leaderboard?sort=throughput_x_pnl` — sortable by total_pnl, $/tr, throughput, throughput×$/tr, drawdown
- Existing endpoints (`/api/trades`, `/api/stats`) gain `?bot_id=X` query param; default = all bots aggregated

New UI: a small "FLEET" panel showing leaderboard + filter dropdown to view single-bot stats. Phase 1 keeps UI minimal — full multi-bot analytics is Sub-project 4.

---

## Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│  Scanner cycle (every 5s)                                     │
│  1. DipScanner fetches tokens (DexScreener, Axiom, GeckoT.)  │
│  2. For each token, compute features ONCE → FeatureBundle    │
│  3. Push bundle → BotManager.evaluate_all(bundle)             │
│                                                                │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  BotManager.evaluate_all(bundle):                    │    │
│  │    For each bot in self.bots:                        │    │
│  │      decision = bot.evaluate(bundle)                 │    │
│  │      if decision:                                    │    │
│  │        bot.capital.reserve(decision.size_usd)        │    │
│  │        bot.position_mgr.open_position(decision)      │    │
│  │        db.record_trade(decision, bot_id=bot.id)      │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                                │
│  Position tick (every 5s, parallel to scan):                  │
│  4. For each bot:                                              │
│      bot.position_mgr.tick_all(current_prices)                │
│        → may emit SELL → close position → record sell        │
└──────────────────────────────────────────────────────────────┘
```

**Critical:** The "FeatureBundle once per token" is the rate-limit savings mechanism. Currently the scanner makes ~30-50 GT/DS calls per cycle. With 43 bots, naive implementation would multiply that by 43 — instant rate limit ban. Sharing the bundle keeps it at ~30-50 calls regardless of bot count.

---

## Error Handling

1. **Per-bot isolation:** every `bot.evaluate()` and `bot.position_mgr.tick()` call is wrapped in try/except. One bot's exception is logged + swallowed; other bots continue normally.

2. **Capital corruption protection:** all capital updates go through `PerBotCapital.atomic_update()`. Concurrent writes are guarded by per-bot `asyncio.Lock`.

3. **DB write isolation:** trades insert with `bot_id` always set. Foreign-key-like check at write time (bot_id must match an active bot config); if mismatch, log + skip rather than corrupt.

4. **Config validation at startup:** if a bot config is malformed (unknown filter name, invalid threshold), the bot is disabled and logged. The process does NOT crash.

5. **Migration safety:** the `bot_id` column has a DEFAULT of `'baseline_v1'` so existing rows survive. The migration runs idempotently (checks if column exists first).

---

## Testing

### Unit tests
- `test_bot_config.py` — config loading from JSON, field defaults, frozen dataclass invariants
- `test_bot_evaluator.py` — given a FeatureBundle + config, evaluator returns expected BuyDecision
- `test_bot_manager.py` — fans out to all bots, exception in one bot doesn't kill others
- `test_per_bot_capital.py` — capital reserves correctly, daily reset, in_flight accounting
- `test_per_bot_position_manager.py` — position lifecycle (open → TP1 → trail → close)

### Integration / smoke
- `tests/test_multi_bot_smoke.py` — spin up 3 bots in-memory (baseline + 2 variants), feed mock FeatureBundles, verify trades land in DB with correct bot_id, capital accounting per bot is independent.

### Production smoke deployment
After unit + integration pass, deploy with 3 bots (defined in Sub-project 2, but the harness ships with these as initial smoke configs):
1. `baseline_v1` — exact current production config
2. `no_sol_gate` — baseline with sol_macro_block disabled
3. `no_filters` — baseline with all filters_enforced = []

Run 24h, then verify:
- Dashboard shows 3 bots with independent stats
- Trades table has bot_id populated correctly
- Per-bot capital pools update independently
- No bot crashes another bot

---

## Build Sequence

1. **BotConfig dataclass + JSON loader** (~150 lines)
2. **FeatureBundle dataclass** (~100 lines)
3. **PerBotCapital + tests** (~150 lines + tests)
4. **PerBotPositionManager + tests** (~400 lines — most logic lifts from existing PositionManager)
5. **BotEvaluator + tests** (~300 lines — lifts trigger/filter eval from existing dip_scanner)
6. **BotManager + tests** (~150 lines)
7. **DB migration script** (~50 lines + idempotent ALTER)
8. **Refactor dip_scanner._scan_cycle** to produce FeatureBundle and call BotManager (~200 lines diff)
9. **Refactor trader.py** to be bot-aware (or replace with BotManager logic) (~100 lines diff)
10. **Dashboard endpoints** (~250 lines)
11. **Dashboard UI panel** (~150 lines)
12. **Config files: baseline_v1.json, no_sol_gate.json, no_filters.json** (~3 small JSON files)
13. **Smoke test deploy + 24h verification**

Estimated total: ~2000 lines of new code, ~500 lines of refactoring, ~1000 lines of tests. 2-3 sessions of focused work.

---

## What this sub-project does NOT do

Deferred to later sub-projects:
- **Bot catalog beyond smoke (3 bots).** Sub-project 2 defines the full ~18 thesis/ablation catalog. Sub-project 3 adds the ~25 filter-focused bots.
- **Cross-bot analytics / attribution.** Sub-project 4 builds the synthesis tooling.
- **Choosing the "winner" config.** Sub-project 5 builds the production successor.
- **Phantom parity for new bots.** Existing phantom continues tracking `baseline_v1`. Other bots are forward-only initially; phantom parity gets retrofitted in Sub-project 4 if needed.
- **Live mode.** All bots are paper-mode for this sub-project. Live cutover happens after synthesis (Sub-project 5).

---

## Risks & open questions

1. **Memory footprint:** 43 bots × ~50MB each ≈ 2GB. Railway free tier is 0.5GB; current paid tier should handle 2GB but worth checking. Mitigation: lazy-load + share immutable feature data.

2. **DB contention:** SQLite with 43 bots writing concurrently could be slow. Mitigation: use WAL mode (already on), batch writes per scan cycle.

3. **Dashboard egress:** loading all 43 bots' last 50 trades = ~$10/mo egress cost based on prior incident. Mitigation: paginate, default to summary-only, expand on demand.

4. **Existing position manager has scalp + dip logic intertwined.** Refactoring into per-bot risks regressions. Mitigation: keep existing single-bot codepath as `baseline_v1`; only refactor what's needed for the multi-bot fan-out.

5. **Phantom parity drift.** With 42 new bots not mirrored in phantom, the phantom validation framework only covers baseline. Acceptable trade-off for sub-project 1 — phantom catch-up is a Sub-project 4 line item.

---

## Approval gate

Before I write the implementation plan and start building:
1. Does the architecture match what you want?
2. Is `BotConfig` capturing every knob you care about? Anything missing?
3. Are the 3 smoke bots (baseline, no_sol_gate, no_filters) the right initial test set?
4. Any of the deferred items you'd want pulled into this sub-project?

**Once approved, I move directly to writing-plans (the implementation plan) and then execute without further check-ins until sub-project 1 is shipped + deployed + 24h smoke-verified.**
