# Axiom Full Utilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Axiom API usage from 37% to 100% by adding active user sentiment signals, enriched price feed data, copy wallet position close detection, deeper dev token scoring, tracked-wallet holder filtering, and trending token polling.

**Architecture:** Each feature is a self-contained addition to an existing file. No new files needed — all changes are additive to `feeds/axiom_price_feed.py`, `feeds/axiom_smart_wallet_tracker.py`, `feeds/axiom_scanner.py`, and `feeds/axiom_trending_scanner.py`. Tests live at the project root (no `tests/` subdirectory).

**Tech Stack:** Python 3.12, axiomtradeapi, asyncio, aiohttp, pytest

**Important:** All test files are at the project root (e.g. `test_axiom_price_feed.py`), matching the existing project convention.

---

## Task 1: Active User Count Signal

**What:** Subscribe to `subscribe_active_axiom_users()` WebSocket per open position. A spike in active Axiom users on a token precedes price moves — detect a 3x spike over the rolling baseline.

**Files:**
- Modify: `feeds/axiom_price_feed.py`
- Create: `test_axiom_price_feed.py` (project root)

- [ ] **Step 1: Write failing test**

```python
# test_axiom_price_feed.py
def test_active_user_spike_logged(caplog):
    """Active user count spike (3x baseline) triggers a WARNING log."""
    import asyncio, logging
    from unittest.mock import MagicMock
    from feeds.axiom_price_feed import AxiomPriceFeed

    auth = MagicMock()
    auth.has_credentials = True
    feed = AxiomPriceFeed(auth_manager=auth)

    # Pre-seed baseline: 3 readings of 100
    feed._user_baseline_window["TESTADDR"] = [100, 100, 100]

    with caplog.at_level(logging.WARNING):
        asyncio.get_event_loop().run_until_complete(
            feed._handle_user_count_update("TESTADDR", "TEST", 320)
        )
    assert "USER SPIKE" in caplog.text
```

- [ ] **Step 2: Run test — confirm it fails**
```bash
cd C:\Users\jcole\multichain-bot
pytest test_axiom_price_feed.py::test_active_user_spike_logged -v
```
Expected: `AttributeError: _handle_user_count_update` or `AttributeError: _user_baseline_window`

- [ ] **Step 3: Add attrs to `__init__` and implement `_handle_user_count_update` + `subscribe_active_users_for_token`**

In `feeds/axiom_price_feed.py`, in `__init__` after `self.price_updates_received = 0`, add:
```python
self.user_cache: Dict[str, int] = {}
self._user_baseline_window: Dict[str, list] = {}  # rolling window of last 5 readings
```

Add two new methods after `_handle_price_update`:
```python
async def _handle_user_count_update(self, token_address: str, ticker: str, count: int):
    """Track active user counts. Spike = current >= 3x rolling baseline."""
    history = self._user_baseline_window.setdefault(token_address, [])
    history.append(count)
    if len(history) > 5:
        history.pop(0)
    self.user_cache[token_address] = count

    if len(history) >= 3:
        baseline = sum(history[:-1]) / len(history[:-1])
        if baseline > 0 and count >= baseline * 3:
            logger.warning(
                f"[AxiomPriceFeed] 🔥 USER SPIKE: {ticker} — "
                f"{count} users ({count/baseline:.1f}x baseline)"
            )

async def subscribe_active_users_for_token(self, ws, token_address: str, ticker: str):
    """Subscribe to active user count updates for a token."""
    def make_user_callback(addr, sym):
        async def _on_users(count: int):
            await self._handle_user_count_update(addr, sym, count)
        return _on_users

    try:
        await ws.subscribe_active_users(
            make_user_callback(token_address, ticker),
            token_address=token_address
        )
        logger.debug(f"[AxiomPriceFeed] Subscribed to user count for {ticker}")
    except Exception as e:
        logger.debug(f"[AxiomPriceFeed] User count subscribe failed for {ticker}: {e}")
```

In `_connect_and_stream`, inside the loop that subscribes to price for each token, add right after `await ws.subscribe_token_price(...)`:
```python
await self.subscribe_active_users_for_token(ws, token_address, ticker)
```

- [ ] **Step 4: Run test — confirm it passes**
```bash
pytest test_axiom_price_feed.py::test_active_user_spike_logged -v
```
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add feeds/axiom_price_feed.py test_axiom_price_feed.py
git commit -m "feat(axiom): add active user count spike detection"
```

---

## Task 2: Enrich Price Feed Data (use all WebSocket fields)

**What:** `subscribe_token_price` sends volume, liquidity, and change% alongside price. Store them in dedicated caches.

**Files:**
- Modify: `feeds/axiom_price_feed.py`
- Modify: `test_axiom_price_feed.py`

- [ ] **Step 1: Write failing test**

```python
# append to test_axiom_price_feed.py
def test_price_feed_stores_volume_and_liquidity():
    """Price update handler stores volume_usd and liquidity_usd alongside price."""
    import asyncio
    from unittest.mock import MagicMock
    from feeds.axiom_price_feed import AxiomPriceFeed

    auth = MagicMock()
    feed = AxiomPriceFeed(auth_manager=auth)

    price_data = {
        "priceUsd": 0.001,
        "volume": 50000,
        "liquidity": 25000,
        "priceChange": 12.5,
    }
    asyncio.get_event_loop().run_until_complete(
        feed._handle_price_update("ADDR123", "TEST", price_data)
    )

    assert feed.price_cache["ADDR123"] == 0.001
    assert feed.volume_cache["ADDR123"] == 50000
    assert feed.liquidity_cache["ADDR123"] == 25000
    assert feed.change_cache["ADDR123"] == 12.5
```

- [ ] **Step 2: Run test — confirm it fails**
```bash
pytest test_axiom_price_feed.py::test_price_feed_stores_volume_and_liquidity -v
```
Expected: `AttributeError: volume_cache`

- [ ] **Step 3: Add caches to `__init__` and update `_handle_price_update`**

In `__init__`, add after `self.price_cache: Dict[str, float] = {}`:
```python
self.volume_cache: Dict[str, float] = {}
self.liquidity_cache: Dict[str, float] = {}
self.change_cache: Dict[str, float] = {}
```

In `_handle_price_update`, after `self.price_cache[token_address] = price_usd`, add:
```python
volume_usd = float(
    price_data.get("volume") or price_data.get("volumeUsd") or
    price_data.get("volume_usd") or 0
)
liquidity_usd = float(
    price_data.get("liquidity") or price_data.get("liquidityUsd") or
    price_data.get("liquidity_usd") or 0
)
change_pct = float(
    price_data.get("priceChange") or price_data.get("price_change") or
    price_data.get("change") or 0
)
if volume_usd > 0:
    self.volume_cache[token_address] = volume_usd
if liquidity_usd > 0:
    self.liquidity_cache[token_address] = liquidity_usd
if change_pct != 0:
    self.change_cache[token_address] = change_pct
```

- [ ] **Step 4: Run test — confirm it passes**
```bash
pytest test_axiom_price_feed.py::test_price_feed_stores_volume_and_liquidity -v
```

- [ ] **Step 5: Commit**
```bash
git add feeds/axiom_price_feed.py test_axiom_price_feed.py
git commit -m "feat(axiom): store volume, liquidity, change% from price feed WebSocket"
```

---

## Task 3: Copy Wallet Position Close Detection

**What:** After a buy signal fires from a tracked wallet, periodically call `get_meme_open_positions(wallet)`. When a previously-open token disappears from their positions — they closed it — alert if we're holding it too.

**Files:**
- Modify: `feeds/axiom_smart_wallet_tracker.py`
- Create: `test_axiom_smart_wallet_tracker.py` (project root)

- [ ] **Step 1: Update typing import in `axiom_smart_wallet_tracker.py`**

At the top of `feeds/axiom_smart_wallet_tracker.py`, change:
```python
from typing import Optional, List
```
to:
```python
from typing import Optional, List, Dict
```

- [ ] **Step 2: Write failing test**

```python
# test_axiom_smart_wallet_tracker.py
def test_position_close_detected(caplog):
    """When tracked wallet's open position disappears, logs a close alert."""
    import asyncio, logging
    from unittest.mock import MagicMock, AsyncMock
    from feeds.axiom_smart_wallet_tracker import AxiomSmartWalletTracker

    auth = MagicMock()
    client = MagicMock()
    auth.get_client.return_value = client
    # Second call returns empty — position closed
    client.get_meme_open_positions.side_effect = [
        [{"tokenAddress": "TOKEN1", "tokenTicker": "TST"}],
        [],
    ]

    tracker_obj = AxiomSmartWalletTracker(
        auth_manager=auth, trader=MagicMock(),
        signal_evaluator=None, security_checker=None,
        telegram=AsyncMock(), tracker=MagicMock(),
    )
    # Baseline: wallet has TOKEN1 open
    tracker_obj._wallet_positions["WALLET1"] = {"TOKEN1"}

    # First call: TOKEN1 still open
    asyncio.get_event_loop().run_until_complete(
        tracker_obj._check_wallet_positions("WALLET1")
    )

    with caplog.at_level(logging.INFO):
        # Second call: TOKEN1 gone
        asyncio.get_event_loop().run_until_complete(
            tracker_obj._check_wallet_positions("WALLET1")
        )
    assert "closed position" in caplog.text.lower()
```

- [ ] **Step 3: Run test — confirm it fails**
```bash
pytest test_axiom_smart_wallet_tracker.py::test_position_close_detected -v
```
Expected: `AttributeError: _check_wallet_positions`

- [ ] **Step 4: Add position tracking to `AxiomSmartWalletTracker`**

In `__init__`, add:
```python
self._wallet_positions: Dict[str, set] = {}
self._position_check_interval = 60
```

Add new method:
```python
async def _check_wallet_positions(self, wallet_address: str):
    """Detect when a tracked wallet closes a position we may also hold."""
    loop = asyncio.get_event_loop()
    try:
        client = self.auth.get_client()
        if not client:
            return
        positions = await loop.run_in_executor(
            None, client.get_meme_open_positions, wallet_address
        )
        if positions is None:
            return

        current_tokens = set()
        for p in (positions or []):
            addr = p.get("tokenAddress") or p.get("token_address") or ""
            if addr:
                current_tokens.add(addr)

        prev_tokens = self._wallet_positions.get(wallet_address, set())
        closed_tokens = prev_tokens - current_tokens

        for token_addr in closed_tokens:
            logger.info(
                f"[AxiomWallets] 🚪 Wallet {wallet_address[:8]} closed position: "
                f"{token_addr[:8]} — consider exiting if we hold it"
            )
            if hasattr(self.trader, 'open_positions') and token_addr in self.trader.open_positions:
                await self.telegram.send(
                    f"👛 *Copy Wallet Closed Position* [Solana]\n\n"
                    f"Wallet `{wallet_address[:8]}...` exited `{token_addr[:8]}...`\n"
                    f"We hold this token — consider exiting."
                )

        self._wallet_positions[wallet_address] = current_tokens

    except Exception as e:
        logger.debug(f"[AxiomWallets] Position check failed for {wallet_address[:8]}: {e}")
```

Add background monitor loop method:
```python
async def _position_monitor_loop(self):
    """Background task: check if tracked wallets closed their positions."""
    while True:
        await asyncio.sleep(self._position_check_interval)
        for wallet in list(self._wallet_positions.keys()):
            await self._check_wallet_positions(wallet)
```

In `run()`, before the while loop, start the monitor:
```python
asyncio.create_task(self._position_monitor_loop())
```

In `_handle_transaction`, after `self.signals_fired += 1`, add:
```python
if wallet_address not in self._wallet_positions:
    self._wallet_positions[wallet_address] = set()
    asyncio.create_task(self._check_wallet_positions(wallet_address))
```

- [ ] **Step 5: Run test — confirm it passes**
```bash
pytest test_axiom_smart_wallet_tracker.py::test_position_close_detected -v
```

- [ ] **Step 6: Commit**
```bash
git add feeds/axiom_smart_wallet_tracker.py test_axiom_smart_wallet_tracker.py
git commit -m "feat(axiom): detect copy wallet position closures via get_meme_open_positions"
```

---

## Task 4: Deeper Dev Token Scoring

**What:** Expand `axiom_enrich_check` dev history block to also detect high-frequency deployers (10+ tokens in 30 days) and fast-rug pattern (avg lifetime < 2 days).

**Files:**
- Modify: `feeds/axiom_scanner.py` — replace the `if dev_tokens:` block inside `axiom_enrich_check`
- Create: `test_axiom_enrich.py` (project root)

- [ ] **Step 1: Write failing test**

```python
# test_axiom_enrich.py
def test_dev_scoring_high_frequency_deployer_blocked():
    """Dev who launched 10+ tokens in 30 days is blocked."""
    import asyncio, time
    from unittest.mock import MagicMock
    from feeds.axiom_scanner import axiom_enrich_check

    auth = MagicMock()
    client = MagicMock()
    auth.get_client.return_value = client
    client.get_holder_data.return_value = []

    now_ms = int(time.time() * 1000)
    day_ms = 86400 * 1000
    # 10 tokens with non-zero liquidity (so dead_count=0 — isolates frequency check)
    client.get_dev_tokens.return_value = [
        {"liquidity": 1000.0, "volume24h": 500.0, "createdAt": now_ms - (i * day_ms * 2)}
        for i in range(10)
    ]

    passed, reason = asyncio.get_event_loop().run_until_complete(
        axiom_enrich_check(auth, "PAIR1", "DEV1")
    )
    assert not passed
    assert "frequent" in reason.lower() or "deployer" in reason.lower()
```

- [ ] **Step 2: Run test — confirm it fails**
```bash
pytest test_axiom_enrich.py::test_dev_scoring_high_frequency_deployer_blocked -v
```
Expected: test passes when it should fail (current code only checks dead_count >= 3, not frequency)

- [ ] **Step 3: Replace `if dev_tokens:` block in `axiom_enrich_check`**

Find this exact block in `feeds/axiom_scanner.py` (inside `axiom_enrich_check`):
```python
                if dev_tokens:
                    # Count tokens with zero liquidity AND zero volume — likely dead/rugged
                    dead_count = 0
                    for t in dev_tokens:
                        liq = float(
                            t.get("liquidity") or
                            t.get("liquidityUsd") or
                            t.get("liquidity_usd") or 0
                        )
                        vol = float(
                            t.get("volume24h") or
                            t.get("volume_24h") or
                            t.get("volumeUsd") or 0
                        )
                        if liq == 0 and vol == 0:
                            dead_count += 1

                    if dead_count >= 3:
                        return (
                            False,
                            f"Serial rugger — dev has {dead_count} dead tokens"
                        )
```

Replace with:
```python
                if dev_tokens:
                    import time as _time
                    now_ms = _time.time() * 1000
                    thirty_days_ms = 30 * 86400 * 1000

                    dead_count = 0
                    recent_launches = 0
                    lifetimes_days = []

                    for t in dev_tokens:
                        liq = float(t.get("liquidity") or t.get("liquidityUsd") or t.get("liquidity_usd") or 0)
                        vol = float(t.get("volume24h") or t.get("volume_24h") or t.get("volumeUsd") or 0)
                        created_at = t.get("createdAt") or t.get("created_at") or 0

                        if liq == 0 and vol == 0:
                            dead_count += 1

                        if created_at and (now_ms - float(created_at)) < thirty_days_ms:
                            recent_launches += 1

                        if liq == 0 and vol == 0 and created_at:
                            age_days = (now_ms - float(created_at)) / (86400 * 1000)
                            lifetimes_days.append(min(age_days, 90))

                    if dead_count >= 3:
                        return (False, f"Serial rugger — dev has {dead_count} dead tokens")

                    if recent_launches >= 10:
                        return (False, f"High-frequency deployer — {recent_launches} tokens in 30 days")

                    if len(lifetimes_days) >= 3:
                        avg_lifetime = sum(lifetimes_days) / len(lifetimes_days)
                        if avg_lifetime < 2.0:
                            return (False, f"Rug pattern — avg token lifetime {avg_lifetime:.1f} days")
```

- [ ] **Step 4: Run test — confirm it passes**
```bash
pytest test_axiom_enrich.py::test_dev_scoring_high_frequency_deployer_blocked -v
```

- [ ] **Step 5: Commit**
```bash
git add feeds/axiom_scanner.py test_axiom_enrich.py
git commit -m "feat(axiom): deeper dev scoring — frequency, lifetime, rug pattern detection"
```

---

## Task 5: Tracked-Wallet Holder Filter

**What:** After the existing holder concentration check, make a second call with `only_tracked_wallets=True` to see how many Axiom-verified smart wallets hold the token. Log the count as a positive signal.

**Files:**
- Modify: `feeds/axiom_scanner.py` (inside `axiom_enrich_check`, after holder concentration block)
- Modify: `test_axiom_enrich.py`

- [ ] **Step 1: Write failing test**

```python
# append to test_axiom_enrich.py
def test_tracked_wallet_holders_checked():
    """get_holder_data is called twice — once for all holders, once for tracked only."""
    import asyncio
    from unittest.mock import MagicMock, call
    from feeds.axiom_scanner import axiom_enrich_check

    auth = MagicMock()
    client = MagicMock()
    auth.get_client.return_value = client
    client.get_holder_data.side_effect = [
        [{"percentage": 5.0}],   # all holders — concentration OK
        [{"percentage": 2.0}, {"percentage": 1.5}, {"percentage": 1.0}],  # 3 tracked
    ]
    client.get_dev_tokens.return_value = []

    passed, reason = asyncio.get_event_loop().run_until_complete(
        axiom_enrich_check(auth, "PAIR1", "DEV1")
    )
    assert passed
    calls = client.get_holder_data.call_args_list
    assert any(
        c == call("PAIR1", True) or c == call("PAIR1", only_tracked_wallets=True)
        for c in calls
    )
```

- [ ] **Step 2: Run test — confirm it fails**
```bash
pytest test_axiom_enrich.py::test_tracked_wallet_holders_checked -v
```
Expected: AssertionError — only one `get_holder_data` call currently

- [ ] **Step 3: Add tracked-wallet secondary call in `axiom_enrich_check`**

In `feeds/axiom_scanner.py`, inside `axiom_enrich_check`, after the closing `except` of the holder concentration block (after the `# Fail open` comment), add:

```python
                # Secondary: tracked wallet presence (only_tracked_wallets=True)
                try:
                    tracked_holders = await loop.run_in_executor(
                        None, client.get_holder_data, pair_address, True
                    )
                    tracked_list = []
                    if isinstance(tracked_holders, list):
                        tracked_list = tracked_holders
                    elif isinstance(tracked_holders, dict):
                        tracked_list = (
                            tracked_holders.get("holders") or
                            tracked_holders.get("data") or []
                        )
                    tracked_count = len(tracked_list)
                    if tracked_count >= 3:
                        logger.debug(
                            f"[AxiomEnrich] {pair_address[:8]}: "
                            f"{tracked_count} tracked wallets holding — positive signal"
                        )
                except Exception as e:
                    logger.debug(f"[AxiomEnrich] Tracked holder check failed: {e}")
```

- [ ] **Step 4: Run test — confirm it passes**
```bash
pytest test_axiom_enrich.py::test_tracked_wallet_holders_checked -v
```

- [ ] **Step 5: Commit**
```bash
git add feeds/axiom_scanner.py test_axiom_enrich.py
git commit -m "feat(axiom): add tracked-wallet holder filter using only_tracked_wallets=True"
```

---

## Task 6: Trending Tokens via get_trending_tokens()

**What:** `AxiomTrendingScanner` already stores `self.auth = auth_manager` but not `self.auth_manager`. Add `self.auth_manager = auth_manager` as a second alias, then add `_fetch_axiom_trending()` that calls `get_trending_tokens("1h")`. In `_poll_once`, call it first and merge any results into `tokens_by_address` before the DexScreener polling runs. Do NOT restructure `_poll_once` — just prepend Axiom tokens to the dict. This preserves all deduplication, bounding, and stats logic.

Note: `axiom_trending_scanner.py` has no `AXIOM_AVAILABLE` constant — use `try/except ImportError` guard inside `_fetch_axiom_trending` instead.

**Files:**
- Modify: `feeds/axiom_trending_scanner.py`
- Create: `test_axiom_trending.py` (project root)

- [ ] **Step 1: Write failing test**

```python
# test_axiom_trending.py
def test_trending_scanner_stores_auth_manager():
    """AxiomTrendingScanner stores auth_manager as self.auth_manager."""
    from unittest.mock import MagicMock, AsyncMock
    from feeds.axiom_trending_scanner import AxiomTrendingScanner

    auth = MagicMock()
    scanner = AxiomTrendingScanner(
        auth_manager=auth,
        trader=MagicMock(), signal_evaluator=MagicMock(),
        security_checker=MagicMock(), telegram=AsyncMock(),
        tracker=MagicMock(),
    )
    assert scanner.auth_manager is auth
```

- [ ] **Step 2: Run test — confirm it fails**
```bash
pytest test_axiom_trending.py::test_trending_scanner_stores_auth_manager -v
```
Expected: `AttributeError: auth_manager` (only `self.auth` exists currently)

- [ ] **Step 3: Add `self.auth_manager` alias and `_fetch_axiom_trending` method**

In `feeds/axiom_trending_scanner.py`, in `__init__`, after `self.auth = auth_manager`, add:
```python
self.auth_manager = auth_manager  # second alias for Axiom-first methods
```

Add new method (no `AXIOM_AVAILABLE` needed — guard with try/except):
```python
async def _fetch_axiom_trending(self) -> dict:
    """
    Try get_trending_tokens('1h'). Returns {token_address: token_dict} or {} on failure.
    Returns a dict keyed by address so it can be merged into tokens_by_address in _poll_once.
    """
    if not self.auth_manager:
        return {}
    try:
        from axiomtradeapi import AxiomTradeClient  # noqa — just checking available
    except ImportError:
        return {}
    try:
        token_valid = await self.auth_manager.ensure_valid_token()
        if not token_valid:
            return {}
        client = self.auth_manager.get_client()
        if not client:
            return {}
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, client.get_trending_tokens, "1h")
        tokens = []
        if isinstance(data, list):
            tokens = data
        elif isinstance(data, dict):
            tokens = data.get("tokens") or data.get("data") or []
        result = {}
        for t in tokens:
            addr = t.get("tokenAddress") or t.get("address") or ""
            if addr:
                result[addr] = t
        if result:
            logger.info(f"[AxiomTrending] Axiom trending: {len(result)} tokens")
        return result
    except Exception as e:
        logger.debug(f"[AxiomTrending] Axiom trending unavailable (DexScreener fallback): {e}")
        return {}
```

In `_poll_once`, at the very start of the method (before the DexScreener aiohttp block), add:
```python
# Axiom-first: merge trending tokens before DexScreener polling
axiom_tokens = await self._fetch_axiom_trending()
tokens_by_address: dict = {**axiom_tokens}
```

Remove the existing `tokens_by_address: dict = {}` line that comes just after (since we now initialize it above with Axiom tokens).

- [ ] **Step 4: Run test — confirm it passes**
```bash
pytest test_axiom_trending.py::test_trending_scanner_stores_auth_manager -v
```

- [ ] **Step 5: Commit**
```bash
git add feeds/axiom_trending_scanner.py test_axiom_trending.py
git commit -m "feat(axiom): prepend get_trending_tokens(1h) results in trending scanner"
```

---

## Task 7: Skip Stale Wallet Transactions

**What:** Wallet transaction events include `created_at` (ms timestamp). Skip transactions older than 60 seconds — they are replayed history, not live signals.

**Files:**
- Modify: `feeds/axiom_smart_wallet_tracker.py`
- Modify: `test_axiom_smart_wallet_tracker.py`

- [ ] **Step 1: Write failing test**

```python
# append to test_axiom_smart_wallet_tracker.py
def test_stale_transaction_skipped():
    """Transactions older than 60s are skipped — token not added to _seen_tokens."""
    import asyncio, time
    from unittest.mock import MagicMock, AsyncMock
    from feeds.axiom_smart_wallet_tracker import AxiomSmartWalletTracker

    auth = MagicMock()
    tracker_obj = AxiomSmartWalletTracker(
        auth_manager=auth, trader=MagicMock(),
        signal_evaluator=None, security_checker=None,
        telegram=AsyncMock(), tracker=MagicMock(),
    )

    old_ts_ms = int((time.time() - 90) * 1000)  # 90 seconds ago
    tx_data = {
        "type": "buy",
        "total_sol": 1.0,
        "created_at": str(old_ts_ms),
        "pair": {"tokenAddress": "ADDR1", "tokenTicker": "TST"},
    }

    asyncio.get_event_loop().run_until_complete(
        tracker_obj._handle_transaction("WALLET1", tx_data)
    )
    # Stale tx must not add token to seen set
    assert "ADDR1" not in tracker_obj._seen_tokens
```

- [ ] **Step 2: Run test — confirm it fails**
```bash
pytest test_axiom_smart_wallet_tracker.py::test_stale_transaction_skipped -v
```
Expected: FAIL — "ADDR1" is currently added to `_seen_tokens` regardless of timestamp

- [ ] **Step 3: Add staleness check in `_handle_transaction`**

In `feeds/axiom_smart_wallet_tracker.py`, add `import time as _time` at the **module level** (top of file, with other imports).

In `_handle_transaction`, add the staleness check **before** `self.wallet_buys_seen += 1` and before any `self._seen_tokens` mutation:
```python
# Staleness check — skip replayed/old transactions (older than 60s)
created_at_raw = tx_data.get("created_at") or tx_data.get("createdAt") or ""
if created_at_raw:
    try:
        ts_ms = float(created_at_raw)
        age_sec = (_time.time() * 1000 - ts_ms) / 1000
        if age_sec > 60:
            logger.debug(f"[AxiomWallets] Skipping stale tx ({age_sec:.0f}s old)")
            return
        logger.debug(f"[AxiomWallets] Tx latency: {age_sec:.1f}s")
    except (ValueError, TypeError):
        pass
```

- [ ] **Step 4: Run test — confirm it passes**
```bash
pytest test_axiom_smart_wallet_tracker.py::test_stale_transaction_skipped -v
```

- [ ] **Step 5: Run all new tests together**
```bash
pytest test_axiom_price_feed.py test_axiom_smart_wallet_tracker.py test_axiom_enrich.py test_axiom_trending.py -v --tb=short
```
Expected: All pass.

- [ ] **Step 6: Commit**
```bash
git add feeds/axiom_smart_wallet_tracker.py test_axiom_smart_wallet_tracker.py
git commit -m "feat(axiom): skip stale wallet transactions older than 60s"
```

---

## Task 8: Deploy and Verify

- [ ] **Step 1: Run full existing test suite to confirm nothing broken**
```bash
pytest -v --tb=short 2>&1 | tail -40
```
Expected: All existing tests still pass.

- [ ] **Step 2: Deploy to Railway**
```bash
MSYS_NO_PATHCONV=1 railway up --detach
```

- [ ] **Step 3: Watch logs for new features**
```bash
MSYS_NO_PATHCONV=1 railway logs --tail 300 | grep -i "USER SPIKE\|tracked wallet\|axiom trending\|closed position\|stale tx\|frequent\|rug pattern\|lifetime"
```

- [ ] **Step 4: Reset dashboard**
```bash
curl -s -X POST https://gracious-inspiration-production.up.railway.app/api/reset \
  -H "Content-Type: application/json" \
  -d '{"secret": "axiom-refresh-2026"}'
```
