# ScalpQueue Strategy Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A high-volume scalping strategy running independently alongside existing strategies, targeting 5% TP with a 2.5% hard stop on $200 positions from a dedicated $2000 capital pool.

**Architecture:** Two feeders (DexScreener dip candidates + Axiom trending) push tokens into a shared watch queue. The Axiom tick gate decides exact entry timing. Position management is a clean in/out scalp branch — no trailing stops, no pyramids.

**Tech Stack:** Python asyncio, Axiom WebSocket price feed (socket8), DexScreener REST, existing `trader.buy()` / `PositionManager` infrastructure.

---

## Economics

| Metric | Value |
|--------|-------|
| Position size | $200 fixed |
| Take profit 1 | 3% → sell 50% |
| Take profit 2 | 5% → sell remaining 50% |
| Hard stop | 2.5% |
| Max hold | 45 minutes |
| Max concurrent | 10 positions |
| Capital pool | $2000 (independent) |
| Daily loss limit | $400 (20% of pool) |

**EV per trade at 70% win rate:**
- Win (full 5%): $200 × 5% = $10 gross, ~$3 slippage = +$7 net
- Partial win (TP1 only, then stopped): bank $3 on first 50%, lose ~$2.50 on second 50% = ~+$0.50
- Loss: $200 × 2.5% stop = -$5, ~$3 slippage = -$8 net
- EV: (0.70 × $7) − (0.30 × $8) ≈ **+$2.50 per trade**
- 20 trades/day ≈ $50/day. 40 trades/day ≈ $100/day.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `feeds/scalp_queue.py` | Create | Candidate intake, watch set management, Axiom tick gate, entry trigger |
| `core/scalp_capital.py` | Create | $2000 pool tracker — deployed capital, concurrent count, daily loss limit |
| `core/position_manager.py` | Modify | Add `strategy=="scalp"` branch: TP1 3%/50%, TP2 5%/100%, stop 2.5%, 45min time stop |
| `utils/config.py` | Modify | `scalp_*` config fields with env overrides |
| `main.py` | Modify | Instantiate ScalpQueue, ScalpCapitalManager, wire to existing feeds |

---

## Component Design

### ScalpQueue (`feeds/scalp_queue.py`)

**Candidate intake — runs every 90s (Feeder A) and on Axiom events (Feeder B):**

Quality gates (both feeders):
- `mcap >= 1_000_000`
- `pair_age_days >= 7`
- `volume_h24 >= 200_000`
- `price_change_h24 > 0` (uptrend intact)
- Token NOT already in `trader.open_positions` (any strategy)
- Token NOT in scalp stop-loss cooldown (30-min block after a scalp stop-out)
- `ScalpCapitalManager.has_capacity()` (< 10 open scalp positions)
- Watch set not full (cap at 25 candidates)

On intake: subscribe token to Axiom price feed, record `watch_entry_price` and `watch_entry_time`.

**Watch set maintenance:**
- Drop candidates that have been watched > 30 minutes without firing
- Drop candidates where price has already moved > 3% from `watch_entry_price` (avoid chasing)
- Unsubscribe Axiom feed on drop

**Axiom tick gate — fires entry when ALL true:**
1. 3+ consecutive price upticks in the last 15 seconds (`_tick_buffers` comparison)
2. Buy/sell ratio > 0.65 over the last 30 seconds
3. `axiom_price_feed.get_tick_trend(addr, 30) > 0`
4. Price movement since watch entry ≤ 3% (still within entry window)
5. `ScalpCapitalManager.has_capacity()`

On gate firing:
```python
await trader.buy(
    token_address=addr,
    token_symbol=symbol,
    strategy="scalp",
    override_usd=200.0,
    reason=f"scalp: tick_trend={trend:.3f} ratio={ratio:.2f}"
)
scalp_capital.record_open(addr, 200.0)
_stop_cooldowns.pop(addr, None)  # clear any old cooldown
```

**Stop-loss cooldown tracking:**
```python
_stop_cooldowns: dict[str, float]  # addr -> monotonic expiry time (30 min)
```
Populated by `on_scalp_close(addr, reason)` callback from PositionManager when `reason == "stop_loss"`.

---

### ScalpCapitalManager (`core/scalp_capital.py`)

```python
@dataclass
class ScalpCapitalManager:
    total_capital: float = 2000.0
    max_position_usd: float = 200.0
    max_concurrent: int = 10
    daily_loss_limit: float = 400.0

    _open: dict[str, float]     # addr -> usd deployed
    _daily_pnl: float           # resets at midnight UTC
    _daily_loss_hit: bool

    def has_capacity(self) -> bool
    def record_open(self, addr: str, usd: float)
    def record_close(self, addr: str, pnl_usd: float)
    def deployed_usd(self) -> float
    def available_usd(self) -> float
```

Completely independent from the main `RiskManager`. Does not share capital with any other strategy.

---

### PositionManager — scalp branch

Added between `dip_buy` and standard branches:

```python
elif state.strategy == "scalp":
    hold_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()

    # Time stop — 45 minutes
    if hold_seconds >= 2700:
        await self._sell(addr, 1.0, "scalp_time_stop")
        return

    # Hard stop — 2.5%
    if pnl_pct <= -2.5:
        await self._sell(addr, 1.0, "scalp_stop_loss")
        scalp_queue.on_scalp_close(addr, "stop_loss")
        return

    # TP2 — 5%, sell remaining 50%
    if state.tp1_hit and pnl_pct >= 5.0:
        await self._sell(addr, 1.0, "scalp_tp2")
        return

    # TP1 — 3%, sell 50%
    if not state.tp1_hit and pnl_pct >= 3.0:
        await self._sell(addr, 0.5, "scalp_tp1")
        state.tp1_hit = True
        return
```

Stop applies to the full remaining position regardless of whether TP1 has fired.

---

### Config (`utils/config.py`)

```python
# ── Scalp Queue ──────────────────────────────────────────────────
scalp_enabled: bool = True
scalp_capital: float = 2000.0
scalp_position_usd: float = 200.0
scalp_tp1_pct: float = 3.0
scalp_tp2_pct: float = 5.0
scalp_stop_pct: float = 2.5
scalp_max_concurrent: int = 10
scalp_max_hold_minutes: float = 45.0
scalp_daily_loss_limit: float = 400.0
scalp_min_mcap: float = 1_000_000
scalp_min_age_days: float = 7.0
scalp_min_volume_h24: float = 200_000
scalp_max_watch_candidates: int = 25
scalp_watch_expiry_minutes: float = 30.0
scalp_max_entry_move_pct: float = 3.0
scalp_tick_ratio_min: float = 0.65
scalp_tick_consecutive_min: int = 3
scalp_stop_cooldown_minutes: float = 30.0
```

Env overrides: `SCALP_ENABLED`, `SCALP_CAPITAL`, `SCALP_POSITION_USD`, `SCALP_STOP_PCT`, `SCALP_MAX_CONCURRENT`.

---

### main.py wiring

```python
if config.scalp_enabled:
    scalp_capital = ScalpCapitalManager(
        total_capital=config.scalp_capital,
        max_position_usd=config.scalp_position_usd,
        max_concurrent=config.scalp_max_concurrent,
        daily_loss_limit=config.scalp_daily_loss_limit,
    )
    scalp_queue = ScalpQueue(
        trader=sol_trader,
        axiom_price_feed=axiom.price_feed,
        open_positions_ref=sol_trader.open_positions,
        scalp_capital=scalp_capital,
        config=config,
    )
    sol_position_mgr.scalp_queue = scalp_queue  # for stop callbacks
    tasks.append(scalp_queue.run())
```

---

## Interfaces

**ScalpQueue → PositionManager:**
- PositionManager calls `scalp_queue.on_scalp_close(addr, reason)` on every scalp position close
- ScalpQueue uses this to: (a) trigger stop cooldown if reason is stop_loss, (b) update ScalpCapitalManager

**ScalpQueue → Trader:**
- Calls `trader.buy(strategy="scalp", override_usd=200)` — same interface as all other strategies

**Trader → PositionManager:**
- strategy="scalp" routes to scalp branch, no changes to existing flow

---

## What This Does NOT Touch

- Main scanner strategy, position sizing, or risk manager
- Dip scanner (runs independently, same as before)
- Graduation sniper
- Paper slippage model
- Axiom price feed (ScalpQueue subscribes tokens the same way trader.buy() does)

---

## Testing

Each component has a clear unit test surface:

1. **ScalpCapitalManager:** capacity logic, daily loss limit, concurrent cap
2. **ScalpQueue quality gates:** mcap/age/volume/cooldown filters reject correctly
3. **Tick gate:** mock Axiom tick buffers, verify gate fires only when all 3 conditions met
4. **PositionManager scalp branch:** TP1 at 3%, TP2 at 5%, stop at 2.5%, time stop at 45min
5. **Integration:** full ScalpQueue → trader.buy() → PositionManager flow in paper mode
