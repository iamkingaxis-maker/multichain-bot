import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.scanner import BreakoutScanner
from breakout.scoring import Kline
from breakout.state import BreakoutState


def _tkr(symbol, vol, pct24):
    return {
        "symbol": symbol,
        "quoteVolume": str(vol),
        "priceChangePercent": str(pct24),
        "lastPrice": "100.0",
    }


def _uptrend_klines_15m(n=25):
    return [Kline(0, 100+i*0.1, 100+i*0.1+0.5, 100+i*0.1-0.3, 100+i*0.1+0.2, 1000.0, 0)
            for i in range(n)]


def _uptrend_klines_1h(n=210):
    # monotone uptrend → ema50 > ema200; inject big bump on the last kline so recent vol > 20-bar avg
    out = [Kline(0, 100+i*0.5, 100+i*0.5+1, 100+i*0.5-1, 100+i*0.5+0.3, 1000.0, 0)
           for i in range(n)]
    return out


def _uptrend_klines_15m_with_last_high_vol(n=25):
    out = _uptrend_klines_15m(n)
    last = out[-1]
    out[-1] = Kline(last.open_time, last.open, last.high, last.low, last.close, 5000.0, last.close_time)
    return out


def _make_config():
    c = MagicMock()
    c.breakout_scan_top_n = 200
    c.breakout_min_vol_24h_usd = 50_000_000
    c.breakout_change_24h_min_pct = 3.0
    c.breakout_change_24h_max_pct = 15.0
    c.breakout_change_6h_max_pct = 12.0
    c.breakout_watchlist_size = 5
    c.breakout_excluded_bases = ["USDT", "USDC", "BUSD"]
    return c


def _klines_side_effect():
    def side(sym, interval, limit):
        return _uptrend_klines_15m_with_last_high_vol() if interval == "15m" else _uptrend_klines_1h()
    return side


@pytest.mark.asyncio
async def test_scanner_filters_low_volume():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("BTCUSDT", vol=10_000_000, pct24=5.0),   # too low volume
        _tkr("ETHUSDT", vol=100_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert "BTCUSDT" not in state.watchlist
    assert "ETHUSDT" in state.watchlist


@pytest.mark.asyncio
async def test_scanner_filters_pct_change_range():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("AAAUSDT", vol=100_000_000, pct24=1.0),   # below min
        _tkr("BBBUSDT", vol=100_000_000, pct24=20.0),  # above max
        _tkr("CCCUSDT", vol=100_000_000, pct24=8.0),   # in range
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert state.watchlist == ["CCCUSDT"]


@pytest.mark.asyncio
async def test_scanner_excludes_stablecoins():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("USDCUSDT", vol=100_000_000, pct24=5.0),
        _tkr("BUSDUSDT", vol=100_000_000, pct24=5.0),
        _tkr("BTCUSDT", vol=100_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert "USDCUSDT" not in state.watchlist
    assert "BUSDUSDT" not in state.watchlist
    assert "BTCUSDT" in state.watchlist


@pytest.mark.asyncio
async def test_scanner_caps_watchlist_size():
    cfg = _make_config()
    cfg.breakout_watchlist_size = 3
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr(f"COIN{i}USDT", vol=100_000_000, pct24=5.0 + i * 0.1) for i in range(10)
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, cfg)
    await scanner.scan_once()
    assert len(state.watchlist) == 3


@pytest.mark.asyncio
async def test_scanner_prefers_usdt_on_duplicate_base():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("BTCUSD", vol=100_000_000, pct24=5.0),
        _tkr("BTCUSDT", vol=200_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert len([s for s in state.watchlist if s in ("BTCUSD", "BTCUSDT")]) == 1
