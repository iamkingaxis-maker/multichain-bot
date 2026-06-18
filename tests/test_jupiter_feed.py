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
