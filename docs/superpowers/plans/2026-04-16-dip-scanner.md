# Dip Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate dip-buying strategy that targets established Solana tokens ($1M+ mcap, 7+ days old) that are dipping on the 1h/5m while the 24h trend is still green, with $500 positions and tight TP tiers (5%/10%).

**Architecture:** New `DipScanner` class polls DexScreener every 90 seconds for candidates matching entry criteria, then calls `trader.buy()` with `strategy="dip_buy"`. Position manager gains a third `dip_buy` branch between the MC and standard paths with its own TP1=5%/TP2=10%/stop=15% rules. Config gets a `dip_*` namespace.

**Tech Stack:** Python asyncio, aiohttp, DexScreener REST API (no API key), existing `Trader.buy()`, existing `PositionManager`.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| **Create** | `feeds/dip_scanner.py` | Polls DexScreener, filters candidates, fires `trader.buy()` |
| **Modify** | `core/position_manager.py` | Add `dip_buy` TP/SL branch in `_evaluate_position` |
| **Modify** | `utils/config.py` | Add `dip_*` config fields |
| **Modify** | `main.py` | Instantiate `DipScanner`, add to tasks, wire to anomaly watchdog |

---

## Task 1: Add dip config fields

**Files:**
- Modify: `utils/config.py`

- [ ] **Step 1: Add fields to the Config dataclass**

In `utils/config.py`, add after the `# ── Micro-Cap Mode` block (around line 145):

```python
    # ── Dip Buyer ────────────────────────────────────────────────
    dip_scanner_enabled: bool = True
    dip_position_usd: float = 500.0        # Fixed position size
    dip_min_mcap: float = 1_000_000        # $1M minimum mcap
    dip_min_age_days: float = 7.0          # Token pair must be ≥7 days old
    dip_min_volume_h24: float = 200_000    # $200k minimum 24h volume
    dip_tp1_pct: float = 5.0              # TP1 at +5% — sell 50%
    dip_tp1_sell: float = 0.50
    dip_tp2_pct: float = 10.0             # TP2 at +10% — sell remaining 100%
    dip_tp2_sell: float = 1.0
    dip_stop_pct: float = 15.0            # Hard stop at -15%
    dip_winner_trail_pct: float = 5.0     # Trail 5% from peak after TP1
    dip_cooldown_hours: float = 4.0       # Min hours before re-buying same token
    dip_max_concurrent: int = 3           # Max simultaneous dip positions
```

- [ ] **Step 2: Add env overrides to `_apply_env_overrides`**

In the `_apply_env_overrides` function, add at the end before the closing:

```python
    # Dip scanner
    if os.environ.get("DIP_SCANNER_ENABLED"):
        config.dip_scanner_enabled = env_bool("DIP_SCANNER_ENABLED", config.dip_scanner_enabled)
    if os.environ.get("DIP_POSITION_USD"):
        config.dip_position_usd = env_float("DIP_POSITION_USD", config.dip_position_usd)
    if os.environ.get("DIP_MIN_MCAP"):
        config.dip_min_mcap = env_float("DIP_MIN_MCAP", config.dip_min_mcap)
    if os.environ.get("DIP_MIN_VOLUME_H24"):
        config.dip_min_volume_h24 = env_float("DIP_MIN_VOLUME_H24", config.dip_min_volume_h24)
    if os.environ.get("DIP_STOP_PCT"):
        config.dip_stop_pct = env_float("DIP_STOP_PCT", config.dip_stop_pct)
```

- [ ] **Step 3: Commit**

```bash
git add utils/config.py
git commit -m "feat: add dip_* config namespace for dip scanner strategy"
```

---

## Task 2: Add dip_buy TP/SL branch to PositionManager

**Files:**
- Modify: `core/position_manager.py`

The MC path ends with `return  # End MC path` at approximately line 861. The dip_buy branch goes immediately after it, before `# ═══ STANDARD POSITION MANAGEMENT ═══`.

- [ ] **Step 1: Wire config values through PositionManager constructor**

In `PositionManager.__init__`, the constructor already accepts `mc_*` params. Find the `__init__` signature and add dip params. Search for `mc_winner_trail_pct` in the constructor — add after it:

```python
                 dip_tp1_pct: float = 5.0,
                 dip_tp1_sell: float = 0.50,
                 dip_tp2_pct: float = 10.0,
                 dip_tp2_sell: float = 1.0,
                 dip_stop_pct: float = 15.0,
                 dip_winner_trail_pct: float = 5.0,
```

And in the body of `__init__`, after `self.mc_winner_trail_pct = mc_winner_trail_pct`, add:

```python
        self.dip_tp1_pct = dip_tp1_pct
        self.dip_tp1_sell = dip_tp1_sell
        self.dip_tp2_pct = dip_tp2_pct
        self.dip_tp2_sell = dip_tp2_sell
        self.dip_stop_pct = dip_stop_pct
        self.dip_winner_trail_pct = dip_winner_trail_pct
```

- [ ] **Step 2: Insert dip_buy branch in `_evaluate_position`**

Locate the comment `# ═══════════════════════════════════════════════════════════════` before `# STANDARD POSITION MANAGEMENT` (after `return  # End MC path`). Insert the new block between the MC `return` and the standard path header:

```python
        # ═══════════════════════════════════════════════════════════════
        # DIP BUY POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.strategy == "dip_buy":

            # ── DIP STOP LOSS ─────────────────────────────────────────
            if pnl_pct <= -self.dip_stop_pct:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🛑 DIP STOP: "
                    f"{state.token_symbol} at {pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Dip stop -{self.dip_stop_pct:.0f}%"
                )
                self.stop_loss_hits += 1
                return

            # ── DIP WINNER TRAIL — after TP1, trail 5% from peak ─────
            if (state.tp1_hit
                    and state.peak_price > 0
                    and state.current_price <= state.peak_price * (1 - self.dip_winner_trail_pct / 100)):
                drop_from_peak = (state.peak_price - state.current_price) / state.peak_price * 100
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🔒 DIP TRAIL: "
                    f"{state.token_symbol} -{drop_from_peak:.1f}% from peak"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Dip trail -{drop_from_peak:.1f}% from peak"
                )
                return

            # ── DIP TAKE PROFIT TIERS ─────────────────────────────────
            if pnl_pct >= self.dip_tp2_pct and not state.tp2_hit:
                state.tp2_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 DIP TP2: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.dip_tp2_sell,
                    reason=f"Dip TP2 +{pnl_pct:.1f}%"
                )
                return

            if pnl_pct >= self.dip_tp1_pct and not state.tp1_hit:
                state.tp1_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 DIP TP1: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.dip_tp1_sell,
                    reason=f"Dip TP1 +{pnl_pct:.1f}%"
                )
                return

            return  # End dip_buy path

```

- [ ] **Step 3: Verify early exit skips dip_buy positions**

In `_evaluate_position`, the early exit block (30min/-5% and 3min/-8%) is guarded by `if not state.tp1_hit:`. Add a strategy check so dip_buy positions skip early exit entirely. Find the early exit block (look for `_early_exit_reason = None`) and wrap the whole check:

```python
        # Early exits only apply to scanner/graduation strategies, not dip buys
        if state.strategy != "dip_buy":
            _is_pyramid = "[PYRAMID]" in state.token_symbol
            _early_exit_reason = None
            if not state.tp1_hit:
                if age_seconds >= 1800 and pnl_pct <= -5.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — no momentum at 30min"
                elif age_seconds >= 180 and pnl_pct <= -8.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — fast dump at 3min"
                elif _is_pyramid and age_seconds >= 420 and pnl_pct <= -3.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — pyramid no momentum at 7min"
                elif _is_pyramid and age_seconds >= 180 and pnl_pct <= -5.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — pyramid fast dump at 3min"

            if _early_exit_reason:
                logger.info(
                    f"[PositionManager/{self.chain_name}] ⏱ EARLY EXIT: "
                    f"{state.token_symbol} {pnl_pct:+.1f}% at {age_seconds/60:.1f}min — {_early_exit_reason}"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=_early_exit_reason,
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=1800
                    )
                return
```

- [ ] **Step 4: Also update realtime stop to handle dip_buy**

In `_handle_realtime_price_update` (around line 1151), find the stop_pct logic. After the `elif state.strategy == "graduation":` branch that was just added, ensure `dip_buy` uses `dip_stop_pct`:

```python
        if state.is_micro_cap and age_seconds < 90:
            stop_pct = 12.0
        elif state.strategy == "graduation":
            stop_pct = 35.0
        elif state.strategy == "dip_buy":
            stop_pct = self.dip_stop_pct
        else:
            stop_pct = self.mc_stop_loss_pct if state.is_micro_cap else self.stop_loss_pct
```

And update the label:

```python
            label = (
                f"Grad stop loss -{stop_pct:.0f}% [realtime]"
                if state.strategy == "graduation" else
                f"Dip stop -{stop_pct:.0f}% [realtime]"
                if state.strategy == "dip_buy" else
                f"MC stop loss -{stop_pct:.0f}% [realtime]"
                if state.is_micro_cap else
                f"Stop loss -{stop_pct:.0f}% [realtime]"
            )
```

- [ ] **Step 5: Commit**

```bash
git add core/position_manager.py
git commit -m "feat: add dip_buy TP/SL branch in PositionManager (5%/10%/stop 15%)"
```

---

## Task 3: Create DipScanner

**Files:**
- Create: `feeds/dip_scanner.py`

- [ ] **Step 1: Write the file**

```python
"""
DipScanner — buys established Solana tokens dipping within an uptrend.

Entry criteria:
  - Market cap >= $1M
  - Pair age >= 7 days
  - 24h volume >= $200k (steady / high activity)
  - 24h price change > 0  (uptrend intact)
  - 1h price change < 0 OR 5m price change < 0  (dip in progress)
  - Not already in open positions
  - Not bought within last 4 hours (per-token cooldown)

Uses DexScreener REST (no API key).
"""

import asyncio
import logging
import time
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

_DEX_CHAIN = "solana"
_SEARCH_TERMS = ["sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "ai", "baby", "pump"]
_SCAN_INTERVAL = 90  # seconds between full scan cycles


class DipScanner:
    def __init__(self,
                 trader,
                 telegram,
                 open_positions_ref: dict,
                 position_usd: float = 500.0,
                 min_mcap: float = 1_000_000,
                 min_age_days: float = 7.0,
                 min_volume_h24: float = 200_000,
                 cooldown_hours: float = 4.0,
                 max_concurrent: int = 3):
        self.trader = trader
        self.telegram = telegram
        self.open_positions_ref = open_positions_ref
        self.position_usd = position_usd
        self.min_mcap = min_mcap
        self.min_age_ms = min_age_days * 86_400 * 1000  # convert to ms
        self.min_volume_h24 = min_volume_h24
        self.cooldown_secs = cooldown_hours * 3600
        self.max_concurrent = max_concurrent

        # per-token cooldown: address -> last buy monotonic time
        self._last_bought: dict[str, float] = {}
        self._start_monotonic = time.monotonic()
        self.signals_fired = 0
        self._last_buy_time = 0.0

    async def run(self):
        logger.info("[DipScanner] Starting — targeting $1M+ mcap dip entries")
        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[DipScanner] Scan cycle error: {e}")
            await asyncio.sleep(_SCAN_INTERVAL)

    async def _scan_cycle(self):
        # Don't scan if already at max concurrent dip positions
        dip_count = sum(
            1 for pos in self.open_positions_ref.values()
            if getattr(pos, "strategy", "") == "dip_buy"
        )
        if dip_count >= self.max_concurrent:
            logger.debug(f"[DipScanner] At max concurrent ({dip_count}) — skipping scan")
            return

        pairs = await self._fetch_candidates()
        now_ms = time.time() * 1000

        for pair in pairs:
            token_address = (pair.get("baseToken") or {}).get("address", "")
            token_symbol = (pair.get("baseToken") or {}).get("symbol", "?")

            if not token_address:
                continue

            # Skip if already in open positions
            if token_address in self.open_positions_ref:
                continue

            # Skip if bought recently (per-token cooldown)
            last = self._last_bought.get(token_address, 0)
            if last > 0 and (time.monotonic() - last) < self.cooldown_secs:
                continue

            # ── Hard filters ──────────────────────────────────────────
            mcap = pair.get("marketCap") or 0
            if mcap < self.min_mcap:
                continue

            created_ms = pair.get("pairCreatedAt") or 0
            if created_ms <= 0 or (now_ms - created_ms) < self.min_age_ms:
                continue

            vol_h24 = (pair.get("volume") or {}).get("h24", 0) or 0
            if vol_h24 < self.min_volume_h24:
                continue

            # ── Signal: green 24h, red 1h or 5m ─────────────────────
            pc_h24 = (pair.get("priceChange") or {}).get("h24", 0) or 0
            pc_h1 = (pair.get("priceChange") or {}).get("h1", 0) or 0
            pc_m5 = (pair.get("priceChange") or {}).get("m5", 0) or 0

            if pc_h24 <= 0:
                continue  # 24h must be green

            if pc_h1 >= 0 and pc_m5 >= 0:
                continue  # Need at least one red shorter timeframe

            # ── Stop adding once max_concurrent reached mid-cycle ────
            dip_count = sum(
                1 for pos in self.open_positions_ref.values()
                if getattr(pos, "strategy", "") == "dip_buy"
            )
            if dip_count >= self.max_concurrent:
                break

            logger.info(
                f"[DipScanner] Signal: {token_symbol} "
                f"mcap=${mcap/1e6:.1f}M | 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% 5m={pc_m5:+.1f}% "
                f"vol24h=${vol_h24/1000:.0f}k"
            )

            self._last_bought[token_address] = time.monotonic()
            self._last_buy_time = time.monotonic()
            self.signals_fired += 1

            await self.trader.buy(
                token_address=token_address,
                token_symbol=token_symbol,
                chain_id="solana",
                amount_usd=self.position_usd,
                reason=f"dip_buy: 24h={pc_h24:+.1f}% 1h={pc_h1:+.1f}% 5m={pc_m5:+.1f}%",
                strategy="dip_buy",
            )

    async def _fetch_candidates(self) -> list:
        """Fetch candidate pairs from DexScreener."""
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        pairs_out = []
        seen = set()

        async def _get(session, url) -> Optional[dict]:
            try:
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status != 200:
                        return None
                    return await r.json()
            except Exception:
                return None

        try:
            async with aiohttp.ClientSession() as session:
                urls = [
                    "https://api.dexscreener.com/token-boosts/top/v1",
                    "https://api.dexscreener.com/token-profiles/latest/v1",
                ] + [
                    f"https://api.dexscreener.com/latest/dex/search?q={kw}&chainId={_DEX_CHAIN}"
                    for kw in _SEARCH_TERMS
                ]

                results = await asyncio.gather(*[_get(session, u) for u in urls],
                                               return_exceptions=True)

                # Collect token addresses from stub endpoints for batch enrichment
                stub_addrs = []
                for res in results[:2]:
                    if isinstance(res, (list, dict)):
                        items = res if isinstance(res, list) else res.get("pairs", [])
                        for item in (items or []):
                            addr = item.get("tokenAddress") or item.get("address") or ""
                            if addr:
                                stub_addrs.append(addr)

                # Enrich stub addresses via /tokens batch
                if stub_addrs:
                    for i in range(0, len(stub_addrs), 30):
                        batch = stub_addrs[i:i+30]
                        url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                        data = await _get(session, url)
                        for p in (data or {}).get("pairs", []):
                            if p.get("chainId") == _DEX_CHAIN:
                                addr = (p.get("baseToken") or {}).get("address", "")
                                if addr and addr not in seen:
                                    seen.add(addr)
                                    pairs_out.append(p)

                # Direct pairs from keyword searches
                for res in results[2:]:
                    if isinstance(res, Exception) or not res:
                        continue
                    for p in (res.get("pairs") or []):
                        if p.get("chainId") != _DEX_CHAIN:
                            continue
                        addr = (p.get("baseToken") or {}).get("address", "")
                        if addr and addr not in seen:
                            seen.add(addr)
                            pairs_out.append(p)

        except Exception as e:
            logger.error(f"[DipScanner] Fetch error: {e}")

        return pairs_out
```

- [ ] **Step 2: Commit**

```bash
git add feeds/dip_scanner.py
git commit -m "feat: add DipScanner — polls DexScreener for established dip entries"
```

---

## Task 4: Wire DipScanner into main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add import at top of main.py**

After `from feeds.graduation_sniper import GraduationSniper`, add:

```python
from feeds.dip_scanner import DipScanner
```

- [ ] **Step 2: Pass dip config values to PositionManager**

In the `sol_position_mgr = PositionManager(...)` call, add after `mc_winner_trail_pct=config.mc_winner_trail_pct,`:

```python
            dip_tp1_pct=config.dip_tp1_pct,
            dip_tp1_sell=config.dip_tp1_sell,
            dip_tp2_pct=config.dip_tp2_pct,
            dip_tp2_sell=config.dip_tp2_sell,
            dip_stop_pct=config.dip_stop_pct,
            dip_winner_trail_pct=config.dip_winner_trail_pct,
```

- [ ] **Step 3: Instantiate DipScanner after PositionManager**

After the `axiom.set_graduation_sniper(grad_sniper)` line, add:

```python
        if config.dip_scanner_enabled:
            dip_scanner = DipScanner(
                trader=sol_trader,
                telegram=telegram,
                open_positions_ref=sol_trader.open_positions,
                position_usd=config.dip_position_usd,
                min_mcap=config.dip_min_mcap,
                min_age_days=config.dip_min_age_days,
                min_volume_h24=config.dip_min_volume_h24,
                cooldown_hours=config.dip_cooldown_hours,
                max_concurrent=config.dip_max_concurrent,
            )
            tasks.append(dip_scanner.run())
            logger.info(
                f"[Main] DipScanner enabled — "
                f"${config.dip_position_usd:.0f}/position, "
                f"min mcap ${config.dip_min_mcap/1e6:.0f}M, "
                f"max {config.dip_max_concurrent} concurrent"
            )
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: wire DipScanner into main — runs as separate async task"
```

---

## Task 5: Deploy and verify

- [ ] **Step 1: Deploy to Railway**

```bash
MSYS_NO_PATHCONV=1 railway up --detach
```

- [ ] **Step 2: Tail logs and confirm startup**

```bash
MSYS_NO_PATHCONV=1 railway logs --tail 50
```

Expected: `[Main] DipScanner enabled — $500/position, min mcap $1M, max 3 concurrent`
Expected: `[DipScanner] Starting — targeting $1M+ mcap dip entries`

- [ ] **Step 3: Watch for first signal**

Within a few minutes you should see logs like:
```
[DipScanner] Signal: BONK mcap=456.2M | 24h=+3.2% 1h=-1.4% 5m=-0.8% vol24h=$2,100k
```

Followed by the standard Trader buy flow.

---

## Self-Review

**Spec coverage:**
- ✅ $500 positions — `dip_position_usd=500`
- ✅ Market cap min $1M — `dip_min_mcap=1_000_000`
- ✅ No max mcap — no upper cap filter
- ✅ At least 1 week old — `min_age_ms = 7 * 86400 * 1000`
- ✅ Volume must be steady and high — `min_volume_h24=$200k` (configurable)
- ✅ Sell 50% at 5% TP1 — `dip_tp1_pct=5.0, dip_tp1_sell=0.50`
- ✅ Sell 100% at 10% TP2 — `dip_tp2_pct=10.0, dip_tp2_sell=1.0`
- ✅ 24h green + 1h or 5m red — signal check in `_scan_cycle`
- ✅ Stop loss 15% — `dip_stop_pct=15.0` in both polling and realtime paths
- ✅ Use existing tools — reuses `Trader.buy()`, `PositionManager`, DexScreener calls

**No placeholders found.**

**Type consistency:** `strategy="dip_buy"` string is consistent across `DipScanner._scan_cycle`, `PositionState.strategy` field (Task 2), and `_evaluate_position` branch checks.
