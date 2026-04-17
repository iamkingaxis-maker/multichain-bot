# Breakout Scanner Strategy — Design Spec

**Date:** 2026-04-17
**Status:** Draft, pending implementation plan
**Scope:** New CEX-based breakout trading strategy (Binance.US), paper-mode initially

---

## 1. Goal

Build a high-conviction breakout trading strategy that operates on liquid Binance.US spot pairs. Entry on 15-minute candle-close breakouts that satisfy a multi-factor strength score. Position management with fixed TP, stop, trailing stop, and early-exit signals. $2000 isolated capital pool, paper-mode only at ship time.

This is a **standalone strategy** independent from existing Solana-DEX strategies. It runs in the same `multichain-bot` process but shares no state with the Solana trader, risk manager, or scalp capital pool.

## 2. Architecture

### 2.1 Module Layout

```
multichain-bot/
  breakout/
    __init__.py
    data_client.py      # Binance.US REST + WS client (public endpoints)
    scanner.py          # Top-200 → top-5 watchlist builder (runs every 10–15min)
    scoring.py          # Pure functions: EMA, breakout_strength, engulfing, wick
    strategy.py         # Entry engine — per-coin 30s poll + candle-close gate
    execution.py        # Paper fills, position management, exit logic
    capital.py          # BreakoutCapitalManager — $2000 isolated pool
    paper_fill.py       # PaperFillEngine — simulates fills against bid/ask
  tests/
    test_breakout_scoring.py
    test_breakout_scanner.py
    test_breakout_execution.py
    test_breakout_strategy.py
```

### 2.2 Data Flow

```
[10–15 min] Binance.US REST → data_client.fetch_24h_tickers()
                                    ↓
                            scanner.build_watchlist()
                                    ↓
                            shared watchlist state (top 5)
                                    ↓
[every 30s] data_client.fetch_klines() per watchlist coin
                                    ↓
                            strategy.on_candle_close()
                                    ↓
                            scoring.breakout_strength_score()
                                    ↓  (if score ≥ 7 AND gates pass)
                            execution.enter()
                                    ↓
                            paper_fill.simulate_buy()
                                    ↓
                            DB write + dashboard update

[every 30s, independent loop] execution.manage_positions()
                                    ↓
                            check TP / stop / trail / early-exit / max-hold
                                    ↓
                            paper_fill.simulate_sell() on trigger
                                    ↓
                            DB write + cooldown set on loss + dashboard update
```

### 2.3 Process Model

- Breakout strategy runs as three async tasks added to `main.py`'s task list:
  1. `scanner.run()` — rebuilds watchlist every 10–15 min
  2. `strategy.run()` — per-coin 30s poll loop for candle-close entries
  3. `execution.run()` — 30s loop managing open positions
- All three tasks share an in-process `BreakoutState` object (watchlist, open positions, cooldowns, trade count)
- Strategy does NOT respect the global `TRADING_PAUSED` env flag (independent kill switch `BREAKOUT_ENABLED`)

## 3. Configuration

### 3.1 Config Block

Add to `utils/config.py`:

```python
# ── Breakout Strategy (Binance.US) ───────────────────────
breakout_enabled: bool = False              # BREAKOUT_ENABLED env — independent kill switch
breakout_capital: float = 2000.0            # isolated pool
breakout_position_usd: float = 500.0
breakout_max_concurrent: int = 4
breakout_cooldown_minutes: float = 45.0
breakout_min_score: int = 7

# Exits
breakout_tp_pct: float = 4.0                # first TP
breakout_tp_sell_pct: float = 0.50          # sell 50% at TP
breakout_stop_pct: float = 3.0              # hard stop below entry
breakout_trail_pct: float = 2.0             # trailing stop from peak after TP
breakout_max_hold_hours: float = 4.0

# Scanner / watchlist
breakout_scan_interval_min: float = 10.0
breakout_scan_top_n: int = 200
breakout_min_vol_24h_usd: float = 50_000_000
breakout_change_24h_min_pct: float = 3.0
breakout_change_24h_max_pct: float = 15.0
breakout_change_6h_max_pct: float = 12.0
breakout_watchlist_size: int = 5

# Stablecoin / excluded quote assets (symbols are rejected if base asset matches)
breakout_excluded_bases: List[str] = ["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "GUSD", "PYUSD"]

# Poll / timing
breakout_poll_interval_sec: float = 30.0
breakout_candle_close_delay_sec: float = 2.0   # wait after close detected before pulling final data
```

### 3.2 Env Var Overrides

Standard pattern — every config field above has a corresponding `BREAKOUT_*` env var override in the config loader.

### 3.3 Quote Currency

All trades quoted in USD/USDT. Binance.US offers both `BTCUSD` (true USD) and `BTCUSDT` (Tether) pairs. Strategy scans both pair types. Tie-breaker: prefer USDT pair (higher volume on .US typically).

## 4. Components

### 4.1 `data_client.py`

Public Binance.US API wrapper — no auth needed for paper mode.

**Methods:**
- `fetch_24h_tickers() -> list[dict]` — GET `/api/v3/ticker/24hr` (all symbols)
- `fetch_klines(symbol, interval, limit) -> list[Kline]` — GET `/api/v3/klines`
- `fetch_order_book(symbol, depth=5) -> dict` — GET `/api/v3/depth` (for paper-fill bid/ask)
- `stream_ticker_24hr(symbols, callback)` — WS `@ticker` stream (future enhancement, not in v1)

**Rate limiting:** Binance.US allows 1200 REQUEST_WEIGHT/min. Our v1 usage:
- Scanner: 1 request per 10min (weight 40 for all-tickers) = 4/min equivalent
- Strategy: 5 coins × 2 polls/min × weight 1 = 10/min
- Execution: 4 open positions × 2 polls/min × weight 5 (depth) = 40/min
- Total: ~55/min — comfortable margin

**Error handling:** retry with exponential backoff on 5xx/429. Surface HTTP errors to caller on 4xx.

### 4.2 `scanner.py`

Stateless watchlist builder.

**`run()` loop:**
1. Sleep `breakout_scan_interval_min` minutes
2. `tickers = data_client.fetch_24h_tickers()`
3. Filter cascade:
   - Symbols ending in `USD` or `USDT` (USD-quoted only)
   - `quoteVolume` > `breakout_min_vol_24h_usd` ($50M)
   - `priceChangePercent` in `[breakout_change_24h_min_pct, breakout_change_24h_max_pct]` (+3% to +15%)
   - Base asset NOT in `breakout_excluded_bases`
   - Reject if 6h change > `breakout_change_6h_max_pct` (+12%) — derived from 1h klines (last 6 × 1h closes)
   - `current_volume > avg_volume_last_20_candles` (15m candles)
   - `1h_price_change > 0` (last 1h kline)
4. Score remaining candidates:
   - Volume-increase score: `current_vol / avg_vol_last_20` (higher = better)
   - 1h momentum score: `pct_change_1h`
   - Trend strength score: `(price - ema50_1h) / ema50_1h` when `ema50 > ema200`
   - Composite: weighted sum (weights finalized during implementation, default 1:1:1)
5. Select top 5 by composite score → publish to `BreakoutState.watchlist`
6. Log scan summary (counts at each filter stage, top-5 symbols + composite scores)

### 4.3 `scoring.py`

Pure functions, no state, fully unit-testable.

**Core functions:**

```python
def ema(prices: list[float], period: int) -> float:
    """Exponential moving average of last N periods."""

def breakout_strength_score(
    candle: Kline,              # the 15m candle being evaluated
    avg_volume_20: float,        # average volume of prior 20 candles
    resistance: float,           # max high of prior 20 candles
    ema50_1h: float,             # 1h EMA50
    ema200_1h: float,            # 1h EMA200
    consolidation_range: float,  # max-min of prior 5 candles' closes
) -> tuple[int, dict]:
    """Returns (total_score, breakdown_dict) for logging.
    Max 10 points per spec:
      - Volume expansion (0-3): candle.volume / avg_volume_20
          >=1.5x → +3, >=1.2x → +2, >=1.0x → +1
      - Candle strength (0-2): body_ratio = |close-open| / (high-low)
          >0.7 → +2, >0.5 → +1
      - Breakout size (0-2): (close - resistance) / resistance
          >0.5% → +2, >0.2% → +1
      - Trend strength (0-2): (close - ema50_1h) / ema50_1h
          >1% separation → +2, >0% → +1
      - Clean structure (0-1): consolidation_range / resistance
          <1% (tight) → +1, else 0
    """

def is_bearish_engulfing(prev: Kline, curr: Kline) -> bool:
    """Prev is green (close>open), curr is red (close<open),
    curr body completely engulfs prev body."""

def has_upper_wick_rejection(candle: Kline, threshold: float = 0.6) -> bool:
    """Upper wick > `threshold` of total candle range indicates rejection."""

def volume_drop(current_vol: float, baseline_vol: float, threshold: float = 0.5) -> bool:
    """Current vol < `threshold` × baseline (e.g., <50% of entry-candle volume)."""
```

### 4.4 `strategy.py`

Entry engine — one task that polls all watchlist coins every 30s, detects candle-close transitions, and evaluates entries.

**`run()` loop:**
1. Sleep `breakout_poll_interval_sec` (30s)
2. For each symbol in `BreakoutState.watchlist`:
   - Fetch latest 15m kline (most recent closed or in-progress)
   - If kline's `close_time` > last seen `close_time` for this symbol:
     - Sleep `breakout_candle_close_delay_sec` (2s) — let exchange finalize
     - Re-fetch last 25× 15m klines + 210× 1h klines
     - Call `evaluate_entry(symbol, klines_15m, klines_1h)`

**`evaluate_entry()` sequence:**
1. Compute `ema50_1h`, `ema200_1h` from 1h closes
2. Compute `resistance = max(high) over prior 20 × 15m candles`
3. Compute `avg_volume_20 = mean(volume) over prior 20 × 15m candles`
4. Gate checks (reject counters bumped on each fail):
   - `close > ema50_1h` (else `gate_price_below_ema50++`)
   - `ema50_1h > ema200_1h` (else `gate_ema50_below_ema200++`)
   - `close > resistance` (else `gate_no_breakout++`)
   - `volume > avg_volume_20` (else `gate_vol_below_avg++`)
5. If all gates pass → `score, breakdown = breakout_strength_score(...)`
6. Entry execution gates:
   - `score >= breakout_min_score` (7) (else `gate_score_too_low++`)
   - `len(open_positions) < breakout_max_concurrent` (else `gate_max_concurrent++`)
   - `symbol not in open_positions` (else `gate_duplicate++`)
   - `now >= cooldowns.get(symbol, 0)` (else `gate_cooldown++`)
7. If all pass → `execution.enter(symbol, score, breakdown, candle)`
8. Log scan-summary every loop iteration (reject counters per stage, reset after log)

### 4.5 `execution.py`

Two concerns: (a) opening new positions, (b) managing open positions.

**`enter(symbol, score, breakdown, entry_candle)`:**
1. `fill = paper_fill.simulate_buy(symbol, breakout_position_usd)` — returns fill_price + qty
2. Create `BreakoutPosition` object:
   - `symbol, entry_time, entry_price, qty, score, breakdown, resistance_level`
   - `tp_price = entry * 1.04`
   - `stop_price = entry * 0.97`
   - `tp_hit = False, trail_active = False, peak_price = entry_price`
3. Deduct `breakout_position_usd` from `BreakoutCapitalManager` available pool
4. Insert row into `breakout_positions` DB table
5. Log entry event with full breakdown

**`manage_positions()` loop (runs every 30s independently):**
For each open `BreakoutPosition`:
1. Fetch latest price (from latest 15m kline `close` or WS ticker if available)
2. Update `peak_price = max(peak_price, current)`
3. Exit conditions checked in priority order:
   - **Stop**: `current <= stop_price` → exit full, reason="stop-loss"
   - **Max hold**: `(now - entry_time) > breakout_max_hold_hours` → exit full, reason="max-hold"
   - **TP1** (if not yet hit): `current >= tp_price` → sell 50%, activate trail, `tp_hit=True`, reason="tp1"
   - **Trail** (if tp_hit): `current <= peak_price * (1 - breakout_trail_pct/100)` → exit remaining, reason="trail"
4. Early-exit checks (all require the latest 2× 15m closed candles):
   - **Price back below breakout**: `current < resistance_level` → exit full, reason="breakout-failed"
   - **Bearish engulfing**: `is_bearish_engulfing(prev, curr)` → exit full, reason="bearish-engulfing"
   - **Upper wick rejection**: `has_upper_wick_rejection(curr)` → exit full, reason="wick-rejection"
   - **Volume drop**: `volume_drop(curr.volume, entry_candle.volume)` → exit full, reason="volume-drop"
5. On exit: `paper_fill.simulate_sell()` → update DB, log exit event
6. On losing close: set `cooldowns[symbol] = now + 45min`, persist to DB.
   **"Losing close" definition:** aggregate position P&L (all partials combined, net of fees) `< 0`.
   Cooldown is evaluated when the position fully closes (last partial exits), not after individual partials.
   Example: TP1 sells 50% at +4%, trail exits remaining 50% at -1% → aggregate pnl positive → no cooldown.
   Example: TP1 never fires, stop fires at -3% → aggregate pnl negative → cooldown set.
7. Release capital back to `BreakoutCapitalManager`

**Note:** TP1 + trail combo — after TP1 sells 50%, the other 50% exits on trail. If stop hits first the remaining 50% exits on stop. If an early-exit signal fires after TP1, the remaining 50% exits on that signal.

### 4.6 `capital.py` — `BreakoutCapitalManager`

Minimal isolated capital pool, modeled on `ScalpCapitalManager`.

**State:**
- `total_capital: float = 2000.0`
- `available: float` (starts at total)
- `deployed: float = 0`
- `realized_pnl: float = 0` (cumulative)

**Methods:**
- `can_open(position_usd) -> bool` — `available >= position_usd`
- `reserve(position_usd)` — move from available to deployed
- `release(proceeds_usd, cost_usd)` — move proceeds back to available, add pnl to realized
- `stats() -> dict` — for dashboard

No interaction with main `risk_manager` or `scalp_capital`.

### 4.7 `paper_fill.py` — `PaperFillEngine`

Simulated fills based on real bid/ask.

**`simulate_buy(symbol, usd_amount) -> Fill`:**
1. `book = data_client.fetch_order_book(symbol, depth=5)`
2. `ask_price = book["asks"][0][0]` (best ask)
3. Apply small slippage model: `fill_price = ask_price * (1 + slippage_pct)` where slippage scales with book depth
4. `qty = usd_amount / fill_price`
5. Apply fee: `proceeds = usd_amount * (1 - taker_fee_pct)` where taker fee = 0.6% (Binance.US retail)
6. Return `Fill(price, qty, fee_usd, timestamp)`

**`simulate_sell(symbol, qty) -> Fill`:**
1. `book = data_client.fetch_order_book(symbol, depth=5)`
2. `bid_price = book["bids"][0][0]`
3. Apply slippage: `fill_price = bid_price * (1 - slippage_pct)`
4. `usd_proceeds = qty * fill_price * (1 - taker_fee_pct)`
5. Return `Fill(price, qty, fee_usd, timestamp)`

**Fee is fixed at 0.6% taker** per current Binance.US retail schedule. Configurable via `breakout_paper_taker_fee: float = 0.006`.

## 5. Database Schema

### New tables:

```sql
CREATE TABLE breakout_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  entry_time DATETIME NOT NULL,
  entry_price REAL NOT NULL,
  qty REAL NOT NULL,
  cost_usd REAL NOT NULL,
  score INTEGER NOT NULL,
  score_breakdown TEXT,        -- JSON
  resistance_level REAL NOT NULL,
  tp_price REAL NOT NULL,
  stop_price REAL NOT NULL,
  entry_candle_volume REAL NOT NULL,
  tp_hit INTEGER DEFAULT 0,    -- bool
  peak_price REAL NOT NULL,
  UNIQUE(symbol)               -- no dup concurrent positions
);

CREATE TABLE breakout_closed_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  entry_time DATETIME NOT NULL,
  exit_time DATETIME NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL NOT NULL,
  qty REAL NOT NULL,
  cost_usd REAL NOT NULL,
  proceeds_usd REAL NOT NULL,
  pnl_usd REAL NOT NULL,
  pnl_pct REAL NOT NULL,
  score INTEGER NOT NULL,
  score_breakdown TEXT,
  reason_entry TEXT,
  reason_exit TEXT,
  fee_total_usd REAL NOT NULL
);

CREATE TABLE breakout_cooldowns (
  symbol TEXT PRIMARY KEY,
  cooldown_until_ts DATETIME NOT NULL,
  last_loss_pnl_usd REAL,
  last_loss_time DATETIME
);
```

Migration handled by `database.py`'s existing `ensure_schema()` pattern — add `CREATE TABLE IF NOT EXISTS` statements.

## 6. Dashboard

New panel in existing dashboard (reuse framework):

**Section: "Breakout Strategy"**
- Stat cards: Capital ($2000), Available, Deployed, Realized P&L, Open Positions, Trades Today
- Watchlist table: symbol, 24h vol, 24h %, 1h %, composite score, last scan time
- Open positions table: symbol, entry_time, entry_price, current_price, pnl_$, pnl_%, peak, tp_hit
- Closed positions (last 20): symbol, entry→exit, pnl_$, pnl_%, reason_entry, reason_exit, score
- Scan log (last 10): timestamp, pre-filter count, post-filter count, top-5 chosen

**New API endpoints:**
- `GET /api/breakout/state` — overall stats (for cards)
- `GET /api/breakout/watchlist` — current watchlist
- `GET /api/breakout/positions` — open positions
- `GET /api/breakout/closed` — closed positions (supports `?limit=N`)
- `GET /api/breakout/scans` — recent scan summaries

## 7. Testing

### 7.1 Unit Tests (fast, isolated)

**`test_breakout_scoring.py`:**
- `ema()` — known inputs → known outputs (cross-check against a trusted implementation)
- `breakout_strength_score()` — hand-crafted candle fixtures exercising every point tier (0 points, max 10, partial)
- `is_bearish_engulfing()` — positive + negative cases
- `has_upper_wick_rejection()` — high-wick vs low-wick candles
- `volume_drop()` — threshold boundary cases

**`test_breakout_scanner.py`:**
- Filter cascade with synthetic ticker data — each filter rejects the right rows
- Composite scoring produces deterministic ranking
- Watchlist size capped at 5

**`test_breakout_execution.py`:**
- Entry path: gate checks block when they should, pass when they should
- Position management: TP1 fires at +4%, trail activates, trail stop fires at peak×0.98
- Stop fires at -3%
- Early exits: each signal fires in isolation
- Max-hold fires at 4h
- Cooldown: losing close sets 45min cooldown, winning close doesn't
- Capital released correctly on close

**`test_breakout_strategy.py`:**
- Candle-close detection: timestamp flip triggers evaluation, no flip = no evaluation
- Rate-limit behavior on mock exceeding API budget

### 7.2 Integration Tests

One end-to-end test with mocked `data_client` and `paper_fill`:
- Feed synthetic ticker data → scanner builds watchlist
- Feed synthetic klines → strategy detects candle close, evaluates, enters
- Feed synthetic prices → execution manages position through TP1 → trail → exit
- Verify DB writes, capital accounting, cooldown state

No live Binance.US calls in any test.

## 8. Rollout Plan

1. **Ship behind `BREAKOUT_ENABLED=false`** — code lands, strategy dormant
2. **Verify deploy**: no startup errors, dashboard shows empty breakout section
3. **Flip `BREAKOUT_ENABLED=true`** — strategy starts scanning
4. **Observe 24h**: scan summaries, watchlist composition, filter-reject counters
5. **First entries**: verify entry logic, paper fills, position management, exit logic
6. **Week-1 evaluation**: WR, avg W, avg L, net P&L vs baseline
7. **Live-mode decision**: only after paper shows consistent edge

## 9. Explicit Non-Goals (v1)

- **WebSocket data**: v1 uses REST polling only; WS added later if rate limits become tight
- **Live trading**: paper only; `place_order` / `cancel_order` live-mode code NOT in v1
- **Maker orders**: v1 enters at market (taker fee). Maker-limit entry can be added later.
- **Multi-timeframe confirmation**: v1 uses only 15m + 1h. No m5 tick-based confirmation.
- **Dynamic scoring weights**: fixed 1:1:1 composite in scanner; tuning pass after observation window.
- **Daily trade cap**: removed per user. No limit.

## 10. Open Items (deferred to implementation)

- Exact `PaperFillEngine` slippage model (linear-in-book-depth vs fixed bps) — pick during implementation
- Excluded-base list may need updates as stablecoin landscape evolves
- Composite scoring weights in `scanner.py` — start 1:1:1, tune post-observation
