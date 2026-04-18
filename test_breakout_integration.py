import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.capital import BreakoutCapitalManager
from breakout.database import BreakoutDB
from breakout.execution import BreakoutExecution
from breakout.paper_fill import PaperFillEngine
from breakout.scanner import BreakoutScanner
from breakout.scoring import Kline
from breakout.state import BreakoutState
from breakout.strategy import BreakoutStrategy


def _tkr(symbol, vol=100_000_000, pct24=5.0):
    return {
        "symbol": symbol,
        "quoteVolume": str(vol),
        "priceChangePercent": str(pct24),
        "lastPrice": "100.0",
    }


def _consolidation_then_breakout():
    base = [Kline(1000 + i*900, 100.0, 100.2, 99.8, 100.0, 1000.0,
                  1000 + (i+1)*900 - 1) for i in range(20)]
    breakout = Kline(1000 + 20*900, 100.0, 102.5, 99.9, 102.1, 2000.0,
                     1000 + 21*900 - 1)
    return base + [breakout]


def _uptrend_1h(n=210):
    out = []
    for i in range(n):
        p = 85.0 + (i / (n - 1)) * 10.0
        out.append(Kline(0, p, p + 0.3, p - 0.3, p, 1000.0, 0))
    return out


def _book(bid, ask):
    return {"bids": [[str(bid), "1000"]], "asks": [[str(ask), "1000"]]}


def _make_config(**overrides):
    c = MagicMock()
    c.breakout_scan_top_n = 200
    c.breakout_min_vol_24h_usd = 50_000_000
    c.breakout_change_24h_min_pct = 3.0
    c.breakout_change_24h_max_pct = 15.0
    c.breakout_change_6h_max_pct = 12.0
    c.breakout_watchlist_size = 5
    c.breakout_excluded_bases = ["USDT", "USDC", "BUSD"]
    c.breakout_poll_interval_sec = 30.0
    c.breakout_candle_close_delay_sec = 0
    c.breakout_min_score = 6
    c.breakout_max_concurrent = 4
    c.breakout_position_usd = 500.0
    c.breakout_tp_pct = 4.0
    c.breakout_tp_sell_pct = 0.50
    c.breakout_stop_pct = 3.0
    c.breakout_trail_pct = 2.0
    c.breakout_max_hold_hours = 4.0
    c.breakout_cooldown_minutes = 45.0
    c.breakout_paper_taker_fee = 0.006
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


@pytest.mark.asyncio
async def test_full_cycle_entry_tp1_trail_exit(tmp_path):
    config = _make_config()
    state = BreakoutState()
    capital = BreakoutCapitalManager(total_capital=2000.0, max_concurrent=4)
    db = BreakoutDB(str(tmp_path / "breakout.db"))

    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[_tkr("BTCUSDT")])
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
        _consolidation_then_breakout() if interval == "15m" else _uptrend_1h())
    client.fetch_order_book = AsyncMock(return_value=_book(bid=100.0, ask=100.1))

    paper_fill = PaperFillEngine(client, taker_fee=0.006)
    execution = BreakoutExecution(
        data_client=client, paper_fill=paper_fill,
        capital=capital, state=state, db=db, config=config,
    )
    scanner = BreakoutScanner(client, state, config)
    strategy = BreakoutStrategy(client, state, config, execution)

    await scanner.scan_once()
    assert "BTCUSDT" in state.watchlist

    await strategy.poll_once()
    assert "BTCUSDT" in state.last_seen_close
    assert "BTCUSDT" in state.open_positions

    pos = state.open_positions["BTCUSDT"]
    assert pos.entry_price == pytest.approx(100.1, rel=1e-2)
    assert pos.tp_hit is False

    client.fetch_klines = AsyncMock(return_value=[
        Kline(0, 100.0, 105.0, 100.0, 104.5, 1200.0, 0),
        Kline(0, 104.5, 105.5, 104.0, 104.8, 1300.0, 0),
        Kline(0, 104.8, 105.6, 104.5, 104.6, 1400.0, 0),
    ])
    client.fetch_order_book = AsyncMock(return_value=_book(bid=104.5, ask=104.8))
    await execution.manage_positions()
    assert pos.tp_hit is True

    pos.peak_price = 108.0
    client.fetch_klines = AsyncMock(return_value=[
        Kline(0, 106.0, 106.5, 105.5, 106.0, 1000.0, 0),
        Kline(0, 106.0, 106.2, 105.5, 105.8, 1000.0, 0),
        Kline(0, 105.8, 105.9, 105.4, 105.5, 1000.0, 0),
    ])
    client.fetch_order_book = AsyncMock(return_value=_book(bid=105.5, ask=105.6))
    await execution.manage_positions()
    assert "BTCUSDT" not in state.open_positions

    closed = db.get_closed_positions()
    assert len(closed) == 1
    assert closed[0]["reason_exit"] == "trail"
