import pytest
from unittest.mock import MagicMock
from breakout.data_client import BinanceUSClient, parse_klines
from breakout.scoring import Kline


def test_parse_klines():
    raw = [
        [1000, "100.0", "102.0", "99.5", "101.5", "1234.5", 1899, "...", 0, "...", "...", "0"],
        [1900, "101.5", "103.0", "100.0", "102.5", "2000.0", 2799, "...", 0, "...", "...", "0"],
    ]
    klines = parse_klines(raw)
    assert len(klines) == 2
    assert isinstance(klines[0], Kline)
    assert klines[0].open_time == 1000
    assert klines[0].open == 100.0
    assert klines[0].close == 101.5
    assert klines[0].volume == 1234.5
    assert klines[0].close_time == 1899


class _MockResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        pass


def _mock_response(payload):
    return _MockResp(payload)


@pytest.mark.asyncio
async def test_fetch_24h_tickers_calls_right_url():
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response([{"symbol": "BTCUSDT"}]))
    client = BinanceUSClient(session=session)
    result = await client.fetch_24h_tickers()
    assert result == [{"symbol": "BTCUSDT"}]
    session.get.assert_called_once()
    assert "api.binance.us/api/v3/ticker/24hr" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_fetch_klines_parses_into_kline_list():
    raw = [[1000, "100.0", "102.0", "99.0", "101.0", "500.0", 1899, "...", 0, "...", "...", "0"]]
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(raw))
    client = BinanceUSClient(session=session)
    result = await client.fetch_klines("BTCUSDT", interval="15m", limit=20)
    assert len(result) == 1
    assert isinstance(result[0], Kline)
    assert "BTCUSDT" in session.get.call_args[0][0]
    assert "15m" in session.get.call_args[0][0]


@pytest.mark.asyncio
async def test_fetch_order_book_returns_payload():
    payload = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    session = MagicMock()
    session.get = MagicMock(return_value=_mock_response(payload))
    client = BinanceUSClient(session=session)
    result = await client.fetch_order_book("BTCUSDT", depth=5)
    assert result == payload
