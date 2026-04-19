import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import time
from feeds.gecko_ohlcv import GeckoTerminalClient
from feeds.candle_utils import Candle


_SAMPLE_GT_RESPONSE = {
    "data": {
        "attributes": {
            # GT returns [timestamp_sec, open, high, low, close, volume_usd], newest first
            "ohlcv_list": [
                [1700000900, 1.10, 1.12, 1.08, 1.11, 5000.0],
                [1700000600, 1.08, 1.11, 1.07, 1.10, 4800.0],
                [1700000300, 1.05, 1.09, 1.05, 1.08, 4500.0],
            ]
        }
    }
}


class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


class _FakeSession:
    def __init__(self, data):
        self._data = data
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        return _FakeResp(self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None


@pytest.mark.asyncio
async def test_fetches_and_parses_candles():
    fake_sess = _FakeSession(_SAMPLE_GT_RESPONSE)
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=60)
    candles = await client.fetch_5m("POOLADDR")
    assert len(candles) == 3
    # Sorted oldest-first in return value
    assert candles[0].open_time == 1700000300
    assert candles[-1].open_time == 1700000900
    assert candles[-1].close == 1.11
    assert candles[-1].volume == 5000.0
    # close_time = open_time + 299 (5m bar - 1s)
    assert candles[-1].close_time == 1700001199


@pytest.mark.asyncio
async def test_cache_serves_repeat_requests():
    fake_sess = _FakeSession(_SAMPLE_GT_RESPONSE)
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=60)
    await client.fetch_5m("POOLADDR")
    await client.fetch_5m("POOLADDR")
    assert fake_sess.calls == 1  # second call served from cache


@pytest.mark.asyncio
async def test_cache_expires():
    fake_sess = _FakeSession(_SAMPLE_GT_RESPONSE)
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=0)
    await client.fetch_5m("POOLADDR")
    await client.fetch_5m("POOLADDR")
    assert fake_sess.calls == 2


@pytest.mark.asyncio
async def test_bad_response_returns_empty():
    fake_sess = _FakeSession({"data": {}})  # malformed
    client = GeckoTerminalClient(session_factory=lambda: fake_sess, cache_ttl=60)
    candles = await client.fetch_5m("POOLADDR")
    assert candles == []
