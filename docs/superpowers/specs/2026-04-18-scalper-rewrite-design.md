# Scalper Rewrite — Design Spec

> Source: user-provided spec (scalper.txt, 2026-04-18). Captured verbatim below.
> Option (a) approved: fully replace ScalpQueue's entry logic with the 4-phase
> setup detector (impulse → pullback → liquidity sweep → reclaim). Use
> GeckoTerminal for free OHLCV data. Keep ScalpCapitalManager & PositionManager
> plumbing, overhaul scalp exit logic.

---

## System Overview

You are building an automated Solana memecoin trading bot designed for short-term scalping using strict risk management and confirmation-based entries.

The bot trades with:

* Total capital: $2000
* Fixed position size per trade: $200
* Max concurrent trades: 3–5

Your objective is NOT to maximize trade frequency. Your objective is to maximize risk-adjusted returns by only entering high-probability setups and exiting quickly.

---

### CORE STRATEGY

The bot must NOT "buy dips blindly."

The bot ONLY enters trades after:

1. A strong upward impulse move
2. A controlled pullback
3. A liquidity sweep (stop hunt)
4. A confirmed reclaim with volume

---

### MARKET SELECTION RULES

Scan tokens continuously and ONLY consider tokens that meet ALL:

* 5-minute volume ≥ configurable threshold (suggest $50k+)
* Liquidity ≥ $30k
* Token age between 5 minutes and 6 hours
* No signs of rug (no sudden liquidity removal >10%)

Reject tokens with:

* Low volume
* Continuous downtrend (lower highs + lower lows)
* No recent impulsive move

---

### GLOBAL NO-TRADE FILTERS

DO NOT open trades if ANY are true:

* Solana (SOL) is trending down strongly on short timeframes
* Majority of scanned tokens are red simultaneously
* 3+ consecutive strong red candles with no lower wicks
* Volume declining during price drop

---

### SETUP DETECTION LOGIC

A valid trade setup requires ALL of the following:

1. IMPULSE MOVE

   * Price increases at least 10–30% within a short period
   * Volume expansion confirms strength

2. PULLBACK

   * Price retraces 30–60% of the impulse
   * Structure is not fully broken

3. LIQUIDITY SWEEP

   * Price wicks below recent low
   * Stops are taken
   * A long lower wick is formed
   * Volume spike ≥ 1.5x recent average

4. RECLAIM CONFIRMATION

   * Candle CLOSES above prior support OR short-term VWAP/EMA

---

### ENTRY RULES

Enter ONLY if ALL conditions are met:

* Liquidity sweep confirmed
* Volume spike present
* Price has reclaimed key level
* Risk/reward ≥ 2:1

DO NOT:

* Enter during falling price
* Enter before candle confirmation
* Attempt to catch bottoms

---

### POSITION MANAGEMENT

Each trade:

* Position size: $200
* Stop loss: 5–6% below entry or below sweep low
* Risk per trade: $10–$12

---

### TAKE PROFIT STRATEGY

Scale out of positions:

* At +10% → sell 50% of position
* At +15–20% → sell additional 30–40%
* Remaining position becomes a runner (optional)

---

### TIME-BASED EXIT

If trade does NOT reach +5% within 3–5 candles:

→ EXIT position immediately

Rationale: dead trades reduce capital efficiency

---

### STOP LOSS RULES

* Hard stop must always be active
* Max loss per trade: 6%
* No overrides, no delays

---

### TRADE MANAGEMENT

After entry:

* If volume decreases → reduce or exit
* If momentum stalls → exit
* If strong rejection wick appears → exit

---

### RE-ENTRY RULES

Re-entry is ONLY allowed if:

* A NEW liquidity sweep forms
* Volume confirms again

No revenge trading.

---

### CAPITAL MANAGEMENT

* Never allocate more than 60–80% of total capital at once
* Maintain free capital for better setups

---

### EXECUTION PRIORITY

The bot must prioritize:

1. Capital preservation
2. High-quality setups
3. Fast exits
4. Consistency over large wins

---

### OUTPUT REQUIREMENTS

For every trade, log:

* Entry reason (which conditions were met)
* Entry price
* Stop loss
* Take profit levels
* Exit reason
* Profit/loss %

---

### FINAL BEHAVIOR

The bot should behave like a disciplined scalper:

* Patient before entry
* Aggressive once confirmed
* Ruthless with cutting losses
* Unemotional and rule-based

Do not deviate from rules under any condition.

---

## Implementation notes (decisions made on top of spec)

- **OHLCV source:** GeckoTerminal `/networks/solana/pools/{pool_address}/ohlcv/minute?aggregate=5` — free, no API key, 30 req/min.
- **Midpoint resolution of spec ranges:**
  - Impulse magnitude: `scalp_impulse_min_pct=10.0`, `scalp_impulse_max_pct=30.0`.
  - Pullback retrace: `scalp_pullback_min_pct=30.0`, `scalp_pullback_max_pct=60.0`.
  - Sweep volume spike: `scalp_sweep_vol_mult=1.5`.
  - TP2 tier: `scalp_tp2_pct=15.0` (floor), `scalp_tp2_sell=0.35` (midpoint of 30–40%).
  - Time exit window: `scalp_time_exit_candles=4` (mid of 3–5), `scalp_time_exit_min_pct=5.0`.
  - Hard stop ceiling: `scalp_stop_pct=6.0` (spec max).
  - Max concurrent: `scalp_max_concurrent=5` (spec max).
  - Capital deployment cap: `scalp_max_deployment_pct=0.80` (spec upper bound).
- **"3+ consecutive red candles without lower wicks"** — the SOL-regime guard. Defined as: last 3 SOL 5m candles where `close < open` AND `(low == min(open, close))` i.e. no lower wick.
- **"Majority red" filter** piggybacks on the per-token scan: if >50% of tokens evaluated in the most recent scan have negative m5 change, block new entries until next scan.
- **Rug detection:** on each candidate refresh, compare current liquidity USD vs value from 10 minutes ago (tracked in-memory). Drop if liquidity falls >10%.
- **Runner handling:** once TP2 fires, remaining position exits on standard trailing-stop logic (already present in position_manager via winner_trail_pct).
- **R/R calculation:** on reclaim confirmation, `stop_price = min(sweep_low * 0.998, entry_price * (1 - scalp_stop_pct/100))` (whichever is lower ensures we're below the wick). `tp_price = entry_price * (1 + scalp_tp1_pct/100)`. Reject if `(tp_price - entry_price) / (entry_price - stop_price) < 2.0`.
