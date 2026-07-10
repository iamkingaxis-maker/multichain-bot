# tests/test_onchain_wire.py
"""Task B4: wire the on-chain WS feed into the scanner (shadow-validated vs Jupiter).

Covers:
- _onchain_hot_mints() = armed set UNION open-position addresses.
- _fast_price_for(addr, jupiter_price) price-selection helper:
    * mode 'on' + FRESH on-chain price -> returns on-chain (source 'onchain').
    * mode 'shadow' + fresh on-chain -> returns JUPITER (on-chain LOGGED only).
    * stale / missing on-chain (any mode) -> returns Jupiter.
    * mode 'off' (default) -> always Jupiter, never even reads the feed.
- shadow spawns/owns an OnchainWsFeed for the hot subset.
"""
import asyncio
import logging
import time
import types

from feeds.dip_scanner import DipScanner


class _FakeFeed:
    """Stand-in for OnchainWsFeed with a seeded price_cache/ts (address-keyed lower)."""

    def __init__(self):
        self.price_cache = {}
        self.ts = {}

    def seed(self, mint, usd, ts):
        self.price_cache[mint.lower()] = usd
        self.ts[mint.lower()] = ts

    def get_price(self, mint):
        if not mint:
            return None
        k = mint.lower()
        if k in self.price_cache:
            return (self.price_cache[k], self.ts.get(k, 0.0))
        return None


def _bare_scanner():
    s = DipScanner.__new__(DipScanner)
    s._onchain_feed = None
    s._fast_armed = {}
    s.open_positions_ref = {}
    return s


def test_onchain_hot_mints_is_armed_union_open():
    s = _bare_scanner()
    s._fast_armed = {"ARMED1": {}, "ARMED2": {}}
    s.open_positions_ref = {"open1": object(), "ARMED2": object()}
    hot = set(s._onchain_hot_mints())
    assert hot == {"ARMED1", "ARMED2", "open1"}


def test_fast_price_for_mode_off_always_jupiter(monkeypatch):
    monkeypatch.delenv("ONCHAIN_WS_MODE", raising=False)  # default off
    s = _bare_scanner()
    f = _FakeFeed()
    f.seed("AAA", 9.99, time.time())   # fresh, but mode is off -> never used
    s._onchain_feed = f
    price, src = s._fast_price_for("AAA", 1.23)
    assert price == 1.23 and src == "jupiter"


def test_fast_price_for_mode_on_fresh_uses_onchain(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "on")
    s = _bare_scanner()
    f = _FakeFeed()
    f.seed("AAA", 9.99, time.time())   # fresh
    s._onchain_feed = f
    price, src = s._fast_price_for("AAA", 1.23)
    assert price == 9.99 and src == "onchain"


def test_fast_price_for_mode_shadow_logs_but_uses_jupiter(monkeypatch, caplog):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "shadow")
    s = _bare_scanner()
    f = _FakeFeed()
    f.seed("AAA", 2.0, time.time())    # fresh
    s._onchain_feed = f
    with caplog.at_level(logging.INFO):
        price, src = s._fast_price_for("AAA", 1.0)
    assert price == 1.0 and src == "jupiter"     # Jupiter used, on-chain NOT
    assert any("[onchain]" in r.getMessage() for r in caplog.records)
    # delta = (2.0-1.0)/1.0 = 100%
    assert any("delta=" in r.getMessage() for r in caplog.records)


def test_fast_price_for_stale_onchain_uses_jupiter(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "on")
    s = _bare_scanner()
    f = _FakeFeed()
    f.seed("AAA", 9.99, time.time() - 100.0)   # stale
    s._onchain_feed = f
    price, src = s._fast_price_for("AAA", 1.23)
    assert price == 1.23 and src == "jupiter"


def test_fast_price_for_missing_onchain_uses_jupiter(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "on")
    s = _bare_scanner()
    s._onchain_feed = _FakeFeed()   # no seed
    price, src = s._fast_price_for("AAA", 1.23)
    assert price == 1.23 and src == "jupiter"


def test_fast_price_for_no_feed_uses_jupiter(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "on")
    s = _bare_scanner()
    s._onchain_feed = None
    price, src = s._fast_price_for("AAA", 1.23)
    assert price == 1.23 and src == "jupiter"


def test_fast_price_for_shadow_zero_jupiter_no_div_by_zero(monkeypatch, caplog):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "shadow")
    s = _bare_scanner()
    f = _FakeFeed()
    f.seed("AAA", 2.0, time.time())
    s._onchain_feed = f
    with caplog.at_level(logging.INFO):
        price, src = s._fast_price_for("AAA", 0.0)   # would div-by-zero in delta
    assert src == "jupiter" and price == 0.0          # no crash


def test_shadow_spawns_onchain_feed_for_hot_subset(monkeypatch):
    """In ONCHAIN_WS_MODE=shadow the loop creates+owns an OnchainWsFeed seeded
    with the hot subset (armed union open)."""
    monkeypatch.setenv("ONCHAIN_WS_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_MODE", "off")  # keep the tick loop a no-op
    import feeds.dip_scanner as ds

    s = DipScanner.__new__(DipScanner)
    s._onchain_feed = None
    s._fast_armed = {"ARMED1": {}}
    s.open_positions_ref = {"open1": object()}
    s._buy_fire_lock = asyncio.Lock()

    spawned = {}

    class _Spy:
        def __init__(self, get_sol_usd=None, **kw):
            self.get_sol_usd = get_sol_usd
            spawned["feed"] = self

        async def run(self, get_mints=None):
            spawned["mints"] = list(get_mints() if callable(get_mints) else (get_mints or []))

    monkeypatch.setattr(ds, "OnchainWsFeed", _Spy)
    # capture the task coroutine and actually await it
    tasks = []
    real_create = ds.asyncio.create_task
    monkeypatch.setattr(ds.asyncio, "create_task",
                        lambda coro, *a, **k: tasks.append(coro) or real_create(coro))

    async def _drive():
        await s._maybe_spawn_onchain_feed()
        for t in tasks:
            await t

    asyncio.run(_drive())
    assert spawned.get("feed") is not None
    assert set(spawned.get("mints", [])) == {"ARMED1", "open1"}


def test_off_does_not_spawn_onchain_feed(monkeypatch):
    monkeypatch.delenv("ONCHAIN_WS_MODE", raising=False)  # default off
    import feeds.dip_scanner as ds

    s = DipScanner.__new__(DipScanner)
    s._onchain_feed = None
    s._fast_armed = {"ARMED1": {}}
    s.open_positions_ref = {}

    called = {"n": 0}

    class _Spy:
        def __init__(self, *a, **k):
            called["n"] += 1

        async def run(self, mints):
            pass

    monkeypatch.setattr(ds, "OnchainWsFeed", _Spy)

    asyncio.run(s._maybe_spawn_onchain_feed())
    assert called["n"] == 0           # off -> never constructed
    assert s._onchain_feed is None


# --- task #493: WS-MIGRATED shadow logging + enforce serving ------------------

class _FakeAmmFeed(_FakeFeed):
    """_FakeFeed + a migrated-token AMM cache (get_amm_price)."""

    def __init__(self):
        super().__init__()
        self.amm_price_cache = {}
        self.amm_ts = {}

    def seed_amm(self, mint, usd, ts):
        self.amm_price_cache[mint.lower()] = usd
        self.amm_ts[mint.lower()] = ts

    def get_amm_price(self, mint):
        if not mint:
            return None
        k = mint.lower()
        if k in self.amm_price_cache:
            return (self.amm_price_cache[k], self.amm_ts.get(k, 0.0))
        return None


def test_ws_migrated_shadow_logs_and_uses_jupiter(monkeypatch, caplog):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "shadow")
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    s = _bare_scanner()
    f = _FakeAmmFeed()
    f.seed_amm("MiGmInT", 2.0, time.time())   # fresh AMM price, no curve price
    s._onchain_feed = f
    with caplog.at_level(logging.INFO):
        price, src = s._fast_price_for("MiGmInT", 1.0)
    assert price == 1.0 and src == "jupiter"          # NEVER served in shadow
    msgs = [r.getMessage() for r in caplog.records]
    assert any("WS-MIGRATED shadow" in m for m in msgs)
    assert any("diff_pct=100.000" in m for m in msgs)  # (2-1)/1 = +100%


def test_ws_migrated_mode_off_no_shadow_log(monkeypatch, caplog):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "shadow")
    monkeypatch.delenv("ONCHAIN_WS_MIGRATED_MODE", raising=False)  # default off
    s = _bare_scanner()
    f = _FakeAmmFeed()
    f.seed_amm("MiGmInT", 2.0, time.time())
    s._onchain_feed = f
    with caplog.at_level(logging.INFO):
        price, src = s._fast_price_for("MiGmInT", 1.0)
    assert price == 1.0 and src == "jupiter"
    assert not any("WS-MIGRATED shadow" in r.getMessage() for r in caplog.records)


def test_ws_migrated_shadow_stale_amm_no_log(monkeypatch, caplog):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "shadow")
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    s = _bare_scanner()
    f = _FakeAmmFeed()
    f.seed_amm("MiGmInT", 2.0, time.time() - 100.0)   # stale
    s._onchain_feed = f
    with caplog.at_level(logging.INFO):
        price, src = s._fast_price_for("MiGmInT", 1.0)
    assert price == 1.0 and src == "jupiter"
    assert not any("WS-MIGRATED shadow" in r.getMessage() for r in caplog.records)


def test_ws_migrated_shadow_feed_without_amm_api_safe(monkeypatch, caplog):
    # Older/fake feed without get_amm_price must not break the price path.
    monkeypatch.setenv("ONCHAIN_WS_MODE", "shadow")
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    s = _bare_scanner()
    f = _FakeFeed()
    f.seed("AAA", 2.0, time.time())
    s._onchain_feed = f
    with caplog.at_level(logging.INFO):
        price, src = s._fast_price_for("AAA", 1.0)
    assert price == 1.0 and src == "jupiter"
    assert not any("WS-MIGRATED shadow" in r.getMessage() for r in caplog.records)


def test_ws_migrated_enforce_serves_amm_via_get_price(monkeypatch):
    """enforce + ONCHAIN_WS_MODE=on: the REAL feed's get_price falls through to
    the AMM cache, so a fresh migrated-token price drives the selection."""
    monkeypatch.setenv("ONCHAIN_WS_MODE", "on")
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "enforce")
    from core.onchain_ws_feed import OnchainWsFeed

    feed = OnchainWsFeed(get_sol_usd=lambda: 150.0)
    feed.amm_price_cache["migmint"] = 9.99
    feed.amm_ts["migmint"] = time.time()
    s = _bare_scanner()
    s._onchain_feed = feed
    price, src = s._fast_price_for("MigMint", 1.23)
    assert price == 9.99 and src == "onchain"


def test_ws_migrated_shadow_mode_amm_not_served_by_real_feed(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "on")
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    from core.onchain_ws_feed import OnchainWsFeed

    feed = OnchainWsFeed(get_sol_usd=lambda: 150.0)
    feed.amm_price_cache["migmint"] = 9.99
    feed.amm_ts["migmint"] = time.time()
    s = _bare_scanner()
    s._onchain_feed = feed
    price, src = s._fast_price_for("MigMint", 1.23)
    assert price == 1.23 and src == "jupiter"
