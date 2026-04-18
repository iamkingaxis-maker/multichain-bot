import pytest
from unittest.mock import AsyncMock
from breakout.paper_fill import PaperFillEngine, Fill


@pytest.fixture
def book():
    return {
        "bids": [["99.90", "100"], ["99.80", "200"], ["99.70", "300"]],
        "asks": [["100.10", "100"], ["100.20", "200"], ["100.30", "300"]],
    }


@pytest.mark.asyncio
async def test_simulate_buy_at_ask_minus_fee(book):
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value=book)
    engine = PaperFillEngine(client, taker_fee=0.006)
    fill = await engine.simulate_buy("BTCUSDT", usd_amount=100.0)
    assert isinstance(fill, Fill)
    assert fill.symbol == "BTCUSDT"
    assert fill.price == pytest.approx(100.10, rel=1e-3)
    expected_qty = (100.0 * (1 - 0.006)) / 100.10
    assert fill.qty == pytest.approx(expected_qty, rel=1e-3)
    assert fill.fee_usd == pytest.approx(100.0 * 0.006, rel=1e-3)
    assert fill.side == "buy"


@pytest.mark.asyncio
async def test_simulate_sell_at_bid_minus_fee(book):
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value=book)
    engine = PaperFillEngine(client, taker_fee=0.006)
    fill = await engine.simulate_sell("BTCUSDT", qty=1.0)
    assert fill.price == pytest.approx(99.90, rel=1e-3)
    gross = 1.0 * 99.90
    assert fill.usd_proceeds == pytest.approx(gross * (1 - 0.006), rel=1e-3)
    assert fill.fee_usd == pytest.approx(gross * 0.006, rel=1e-3)
    assert fill.side == "sell"


@pytest.mark.asyncio
async def test_simulate_buy_applies_book_walk_slippage():
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value={
        "bids": [["99.90", "10"]],
        "asks": [["100.10", "10"], ["100.20", "10"], ["100.30", "10"]],
    })
    engine = PaperFillEngine(client, taker_fee=0.0)
    fill = await engine.simulate_buy("BTCUSDT", usd_amount=3000.0)
    assert fill.price > 100.10
    assert fill.price < 100.30


@pytest.mark.asyncio
async def test_simulate_buy_empty_book_raises():
    client = AsyncMock()
    client.fetch_order_book = AsyncMock(return_value={"bids": [], "asks": []})
    engine = PaperFillEngine(client, taker_fee=0.006)
    with pytest.raises(ValueError):
        await engine.simulate_buy("BTCUSDT", usd_amount=100.0)
