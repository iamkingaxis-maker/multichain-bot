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


def _downtrend_klines_1h(n=210, start=200.0, end=100.0):
    # monotone downtrend → last close < ema50 → red regime
    step = (end - start) / (n - 1)
    return [Kline(0, start+i*step, start+i*step+0.2, start+i*step-0.2,
                  start+i*step, 1000.0, 0) for i in range(n)]


def _flat_klines_15m(close=100.0, n=25):
    return [Kline(0, close, close+0.1, close-0.1, close, 1000.0, 0) for _ in range(n)]


def _risk_off_klines_15m(close=100.0, n=25):
    out = _flat_klines_15m(close, n)
    last = out[-1]
    # -2.5% drop on last 15m candle
    out[-1] = Kline(last.open_time, close, close, close*0.975, close*0.975, 1000.0, last.close_time)
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
    c.breakout_regime_symbol = "BTCUSDT"
    c.breakout_regime_red_1h_pct = -1.0
    c.breakout_regime_risk_off_drop_pct = 2.0
    c.breakout_red_watchlist_size = 3
    return c


def _flat_klines_1h(n=210, price=100.0):
    # Flat series → BTC regime has btc_1h_pct = 0.0 and close < ema50 is false (close == ema50 == price).
    # Actually with flat, close == ema50 → label will fall through to green.
    return [Kline(0, price, price+0.1, price-0.1, price, 1000.0, 0) for _ in range(n)]


def _klines_side_effect():
    """Default side effect: BTC is flat (green, btc_1h_pct=0), candidates uptrend."""
    def side(sym, interval, limit):
        if sym == "BTCUSDT":
            return _flat_klines_15m() if interval == "15m" else _flat_klines_1h()
        return _uptrend_klines_15m_with_last_high_vol() if interval == "15m" else _uptrend_klines_1h()
    return side


def _klines_side_effect_btc(btc_1h, btc_15m):
    """Returns custom BTC klines, default candidate klines for everything else."""
    def side(sym, interval, limit):
        if sym == "BTCUSDT":
            return list(btc_15m) if interval == "15m" else list(btc_1h)
        return _uptrend_klines_15m_with_last_high_vol() if interval == "15m" else _uptrend_klines_1h()
    return side


@pytest.mark.asyncio
async def test_scanner_filters_low_volume():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("DOGEUSDT", vol=10_000_000, pct24=5.0),   # too low volume
        _tkr("ETHUSDT", vol=100_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert "DOGEUSDT" not in state.watchlist
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
        _tkr("ETHUSDT", vol=100_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert "USDCUSDT" not in state.watchlist
    assert "BUSDUSDT" not in state.watchlist
    assert "ETHUSDT" in state.watchlist


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
        _tkr("DOGEUSD", vol=100_000_000, pct24=5.0),
        _tkr("DOGEUSDT", vol=200_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert len([s for s in state.watchlist if s in ("DOGEUSD", "DOGEUSDT")]) == 1


@pytest.mark.asyncio
async def test_scanner_publishes_regime_to_state():
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("COIN0USDT", vol=100_000_000, pct24=5.0),
    ])
    client.fetch_klines = AsyncMock(side_effect=_klines_side_effect())
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, _make_config())
    await scanner.scan_once()
    assert state.regime is not None
    assert state.regime.label == "green"


@pytest.mark.asyncio
async def test_scanner_trims_watchlist_in_red_market():
    cfg = _make_config()
    cfg.breakout_watchlist_size = 5
    cfg.breakout_red_watchlist_size = 3
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr(f"COIN{i}USDT", vol=100_000_000, pct24=5.0 + i * 0.1) for i in range(10)
    ])
    client.fetch_klines = AsyncMock(
        side_effect=_klines_side_effect_btc(_downtrend_klines_1h(), _flat_klines_15m())
    )
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, cfg)
    await scanner.scan_once()
    assert state.regime.label == "red"
    assert len(state.watchlist) <= 3


@pytest.mark.asyncio
async def test_scanner_trims_watchlist_in_risk_off():
    cfg = _make_config()
    cfg.breakout_watchlist_size = 5
    cfg.breakout_red_watchlist_size = 3
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr(f"COIN{i}USDT", vol=100_000_000, pct24=5.0 + i * 0.1) for i in range(10)
    ])
    client.fetch_klines = AsyncMock(
        side_effect=_klines_side_effect_btc(_uptrend_klines_1h(), _risk_off_klines_15m())
    )
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, cfg)
    await scanner.scan_once()
    assert state.regime.label == "risk_off"
    assert len(state.watchlist) <= 3


@pytest.mark.asyncio
async def test_scanner_rejects_candidate_weaker_than_btc():
    # BTC 1h +5% — flatlined coin has ~0% 1h change → should be filtered by RS gate
    cfg = _make_config()
    # Build BTC 1h with strong last-hour pump: uptrend then spike last bar
    btc_1h = _uptrend_klines_1h()
    last = btc_1h[-1]
    prev = btc_1h[-2]
    # Force +5% on BTC's last 1h bar
    btc_1h[-1] = Kline(last.open_time, last.open, last.high, last.low,
                       prev.close * 1.05, last.volume, last.close_time)
    client = AsyncMock()
    client.fetch_24h_tickers = AsyncMock(return_value=[
        _tkr("WEAKUSDT", vol=100_000_000, pct24=5.0),
    ])
    # WEAKUSDT returns default uptrend 1h (last-bar ~0.3% change) — weaker than BTC's +5%
    client.fetch_klines = AsyncMock(
        side_effect=_klines_side_effect_btc(btc_1h, _flat_klines_15m())
    )
    state = BreakoutState()
    scanner = BreakoutScanner(client, state, cfg)
    await scanner.scan_once()
    assert "WEAKUSDT" not in state.watchlist
