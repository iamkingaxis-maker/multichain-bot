import asyncio, types, os, time
import pytest
from feeds import price_feed as pf
from feeds.price_feed import PriceFeed


def _minimal_feed():
    """Construct a PriceFeed without running __init__ side effects we don't need."""
    feed = PriceFeed.__new__(PriceFeed)
    feed._watched = set()
    feed._jup_backoff_until = 0.0
    feed._ds_backoff_until = 0.0
    return feed


def _patch_recorder(feed, monkeypatch):
    """Replace the two batch methods with stubs that record call order."""
    calls = []

    async def fake_dex(addresses):
        calls.append("dex")
        return len(addresses)

    async def fake_jup(addresses):
        calls.append("jup")
        return len(addresses)

    monkeypatch.setattr(feed, "_poll_batch", fake_dex)
    monkeypatch.setattr(feed, "_poll_batch_jupiter", fake_jup)
    return calls


def test_dispatch_flag_off_uses_dexscreener(monkeypatch):
    monkeypatch.delenv("JUPITER_PRICE_PRIMARY", raising=False)
    feed = _minimal_feed()
    calls = _patch_recorder(feed, monkeypatch)
    asyncio.run(feed._poll_one_sweep(["m1", "m2", "m3"]))
    assert calls == ["dex"]


def test_dispatch_flag_on_uses_jupiter(monkeypatch):
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    feed = _minimal_feed()
    feed._jup_backoff_until = 0.0  # not backing off
    calls = _patch_recorder(feed, monkeypatch)
    asyncio.run(feed._poll_one_sweep(["m1", "m2", "m3"]))
    assert calls == ["jup"]


def test_dispatch_jupiter_backoff_falls_to_dexscreener(monkeypatch):
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    feed = _minimal_feed()
    feed._jup_backoff_until = time.time() + 60.0  # simulate active 429 backoff
    calls = _patch_recorder(feed, monkeypatch)
    asyncio.run(feed._poll_one_sweep(["m1", "m2", "m3"]))
    assert calls == ["dex"]


def test_chunk_50_serialized():
    from feeds.price_feed import _jup_chunks
    ids = [f"m{i}" for i in range(120)]
    chunks = _jup_chunks(ids, 50)
    assert [len(c) for c in chunks] == [50, 50, 20]

def test_parse_jupiter_payload():
    from feeds.price_feed import _parse_jupiter
    payload = {"AAA": {"usdPrice": 0.0012, "blockId": 1000}, "BBB": {"usdPrice": None}, "CCC": {}}
    out = _parse_jupiter(payload)
    assert out["AAA"] == (0.0012, 1000)
    assert "BBB" not in out and "CCC" not in out   # null/missing price dropped

def test_strip_crlf_in_ids():
    from feeds.price_feed import _jup_clean_ids
    assert _jup_clean_ids(["AAA\r", " BBB ", "", "CCC\n"]) == ["AAA", "BBB", "CCC"]


# ── Jupiter staleness guard (_jup_is_stale) ─────────────────────────────────
def _stale_feed():
    feed = PriceFeed.__new__(PriceFeed)
    feed._jup_block_seen = {}
    feed._jup_stale_logged = set()
    return feed


def test_frozen_blockid_flagged_stale(monkeypatch):
    monkeypatch.setenv("JUPITER_STALE_SECS", "10.0")
    feed = _stale_feed()
    # First quote at block 100, t=0 -> fresh (records first_seen).
    assert feed._jup_is_stale("aaa", 100, 0.0) is False
    # Same block 4s later -> still within window, not stale.
    assert feed._jup_is_stale("aaa", 100, 4.0) is False
    # Same block 11s later (> stale_secs) -> STALE.
    assert feed._jup_is_stale("aaa", 100, 11.0) is True


def test_advancing_blockid_never_stale(monkeypatch):
    monkeypatch.setenv("JUPITER_STALE_SECS", "10.0")
    feed = _stale_feed()
    assert feed._jup_is_stale("bbb", 100, 0.0) is False
    assert feed._jup_is_stale("bbb", 101, 30.0) is False   # advanced — fresh despite long gap
    assert feed._jup_is_stale("bbb", 102, 100.0) is False
    assert feed._jup_is_stale("bbb", 103, 200.0) is False


def test_none_blockid_never_stale(monkeypatch):
    monkeypatch.setenv("JUPITER_STALE_SECS", "10.0")
    feed = _stale_feed()
    assert feed._jup_is_stale("ccc", None, 0.0) is False
    assert feed._jup_is_stale("ccc", None, 1000.0) is False


def test_block_change_resets_first_seen(monkeypatch):
    monkeypatch.setenv("JUPITER_STALE_SECS", "10.0")
    feed = _stale_feed()
    feed._jup_is_stale("ddd", 100, 0.0)
    feed._jup_is_stale("ddd", 100, 8.0)        # frozen but under window
    assert feed._jup_is_stale("ddd", 200, 9.0) is False   # new block -> resets, fresh
    # Now frozen on 200 from t=9; at t=18 only 9s -> not stale; at t=20 -> stale.
    assert feed._jup_is_stale("ddd", 200, 18.0) is False
    assert feed._jup_is_stale("ddd", 200, 20.0) is True


def test_custom_stale_secs_env(monkeypatch):
    monkeypatch.setenv("JUPITER_STALE_SECS", "3.0")
    feed = _stale_feed()
    assert feed._jup_is_stale("eee", 5, 0.0) is False
    assert feed._jup_is_stale("eee", 5, 2.0) is False
    assert feed._jup_is_stale("eee", 5, 4.0) is True


def test_process_jupiter_stale_skips_callbacks_and_fails_over(monkeypatch):
    """End-to-end: stale Jupiter quote must NOT fire realtime stop checks and
    MUST trigger a DexScreener re-fetch (pinned-pair path here)."""
    monkeypatch.setenv("JUPITER_STALE_SECS", "10.0")
    feed = PriceFeed.__new__(PriceFeed)
    feed._jup_block_seen = {}
    feed._jup_stale_logged = set()
    feed._watch_chains = {}
    feed._latest = {}
    feed._tick_count = 0
    feed.price_cache = {}
    feed.price_timestamps = {}
    feed.volume_cache = {}
    feed.liquidity_cache = {}
    feed._subscribers = {}
    feed._pair_addresses = {"tok": "pair123"}

    stop_calls = []

    class PM:
        def check_stop_loss_realtime(self, *a): stop_calls.append("stop")
        def check_take_profit_realtime(self, *a): stop_calls.append("tp")
        def check_exhaustion_realtime(self, *a): stop_calls.append("exh")
        def check_post_tp1_trail_realtime(self, *a): stop_calls.append("trail")

    feed.position_manager = PM()

    direct_calls = []

    async def fake_direct(token, pair):
        direct_calls.append((token, pair))

    monkeypatch.setattr(feed, "_poll_pair_direct", fake_direct)

    # Freeze time so block 50 stays frozen > 10s on the 2nd call.
    t = {"v": 0.0}
    monkeypatch.setattr(pf.time, "time", lambda: t["v"])

    # First call: fresh block -> fires callbacks, no failover.
    asyncio.run(feed._process_jupiter_price("tok", 1.0, 50))
    assert stop_calls == ["stop", "tp", "exh", "trail"]
    assert direct_calls == []
    assert feed.price_cache["tok"] == 1.0

    # Second call 11s later, same block -> STALE: cache still written, NO new
    # stop callbacks, DexScreener pinned-pair re-fetch triggered.
    stop_calls.clear()
    t["v"] = 11.0
    asyncio.run(feed._process_jupiter_price("tok", 2.0, 50))
    assert stop_calls == []                       # stale Jupiter did not drive stops
    assert direct_calls == [("tok", "pair123")]   # failed over to DexScreener
    assert feed.price_cache["tok"] == 2.0         # cache still written
