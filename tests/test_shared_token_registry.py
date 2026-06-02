"""Shared no-same-token exclusion registry (cross-bot de-concentration).

Bots sharing the same non-empty `exclusion_pool` may not hold the same token
concurrently. The registry is DERIVED (stateless) — 'held' is read live from
each pool member's position manager, so a close auto-frees the token (no
release bookkeeping, no stuck-token bug). Bots with exclusion_pool=None are
never blocked (preserves current single-bot behavior).
"""
import types
import asyncio
from core.shared_token_registry import SharedTokenRegistry


class StubPM:
    def __init__(self, pool, held=()):
        self.config = types.SimpleNamespace(exclusion_pool=pool)
        self._held = set(held)
    def get_position(self, token):
        return object() if token in self._held else None
    def token_buys_today(self, token, iso):   # risk-floor path
        return 0


def reg(pms):
    return SharedTokenRegistry(pms)


def test_not_blocked_when_neither_in_a_pool():
    pms = {"a": StubPM(None), "b": StubPM(None, held=["T"])}
    assert reg(pms).is_blocked("a", "T") is False


def test_blocked_when_pool_sibling_holds():
    pms = {"a": StubPM("P"), "b": StubPM("P", held=["T"])}
    assert reg(pms).is_blocked("a", "T") is True


def test_not_blocked_by_other_pool():
    pms = {"a": StubPM("P"), "b": StubPM("Q", held=["T"])}
    assert reg(pms).is_blocked("a", "T") is False


def test_not_blocked_by_self_holding():
    # the holder isn't blocked from its own token (re-entry is handled elsewhere)
    pms = {"a": StubPM("P", held=["T"])}
    assert reg(pms).is_blocked("a", "T") is False


def test_not_blocked_when_token_free():
    pms = {"a": StubPM("P"), "b": StubPM("P", held=["OTHER"])}
    assert reg(pms).is_blocked("a", "T") is False


def test_unknown_bot_never_blocked():
    pms = {"b": StubPM("P", held=["T"])}
    assert reg(pms).is_blocked("ghost", "T") is False


def test_holder_returns_pool_holder():
    pms = {"a": StubPM("P"), "b": StubPM("P", held=["T"])}
    assert reg(pms).holder("T", "P") == "b"
    assert reg(pms).holder("T", "Q") is None


def test_close_auto_frees_token_no_release_call():
    b = StubPM("P", held=["T"])
    pms = {"a": StubPM("P"), "b": b}
    r = reg(pms)
    assert r.is_blocked("a", "T") is True
    b._held.discard("T")              # position closed — nothing else called
    assert r.is_blocked("a", "T") is False   # self-heals from live PM state


def test_registry_reflects_pool_added_later():
    # held by reference: a bot added to the dict after construction is seen
    pms = {"a": StubPM("P")}
    r = reg(pms)
    assert r.is_blocked("a", "T") is False
    pms["b"] = StubPM("P", held=["T"])
    assert r.is_blocked("a", "T") is True


# ── wiring: _execute_bot_buy must short-circuit BEFORE spending capital ──
from feeds.dip_scanner import DipScanner


class SpyCapital:
    def __init__(self):
        self.reserved = []
        self.daily_pnl_usd = 0.0
    def reserve_for_buy(self, usd):
        self.reserved.append(usd)
    def daily_loss_breached(self, lim, iso):   # risk-floor path
        return False


def test_buy_blocked_by_pool_returns_before_capital():
    pmA = StubPM("P")            # buyer
    pmB = StubPM("P", held=["T"])  # sibling already holds T
    pmA.config.reentry_cooldown_secs = None
    ds = DipScanner.__new__(DipScanner)
    ds.bot_position_managers = {"a": pmA, "b": pmB}
    cap = SpyCapital()
    ds.bot_capitals = {"a": cap}
    ds.trade_store = None
    ds._token_registry = SharedTokenRegistry(ds.bot_position_managers)
    ds._addr_by_token = {}
    decision = types.SimpleNamespace(bot_id="a", token="T", address="addr",
                                     pair_address="pair", entry_price=1.0,
                                     size_usd=20.0, size_tier="base",
                                     triggers_fired=[])
    asyncio.run(ds._execute_bot_buy(decision, types.SimpleNamespace(raw_meta={})))
    assert cap.reserved == []   # blocked before reserving — no money committed


def test_buy_not_blocked_when_token_free_reaches_capital():
    pmA = StubPM("P")
    pmB = StubPM("P", held=["OTHER"])   # sibling holds a different token
    pmA.config.reentry_cooldown_secs = None
    ds = DipScanner.__new__(DipScanner)
    ds.bot_position_managers = {"a": pmA, "b": pmB}
    cap = SpyCapital()
    ds.bot_capitals = {"a": cap}
    ds.trade_store = None
    ds.trader = types.SimpleNamespace(private_key="")   # paper (no live route)
    ds._token_registry = SharedTokenRegistry(ds.bot_position_managers)
    ds._addr_by_token = {}
    decision = types.SimpleNamespace(bot_id="a", token="T", address="addr",
                                     pair_address="pair", entry_price=1.0,
                                     size_usd=20.0, size_tier="base",
                                     triggers_fired=[])
    # reserve_for_buy raises to halt the path right after the exclusion gate
    # (proves we got PAST the gate without needing the full open machinery).
    def _raise(usd):
        cap.reserved.append(usd); raise ValueError("stop-after-gate")
    cap.reserve_for_buy = _raise
    asyncio.run(ds._execute_bot_buy(decision, types.SimpleNamespace(raw_meta={})))
    assert cap.reserved == [20.0]   # not blocked — proceeded to reserve capital
