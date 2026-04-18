import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.strategy import BreakoutStrategy
from breakout.scoring import Kline
from breakout.state import BreakoutState


def _k(close_time, o, h, l, c, v):
    return Kline(open_time=close_time - 899, open=o, high=h, low=l,
                 close=c, volume=v, close_time=close_time)


def _make_config():
    c = MagicMock()
    c.breakout_poll_interval_sec = 30.0
    c.breakout_candle_close_delay_sec = 0
    c.breakout_min_score = 7
    c.breakout_max_concurrent = 4
    c.breakout_position_usd = 500.0
    c.breakout_stop_pct = 3.0
    c.breakout_tp_pct = 4.0
    c.breakout_red_min_score = 8
    c.breakout_red_min_vol_ratio = 1.5
    return c


def _green_regime():
    from breakout.regime import BtcRegime
    return BtcRegime(label="green", btc_close=100.0, btc_ema50_1h=95.0,
                     btc_1h_pct=0.1, btc_15m_drop_pct=0.0)


def _red_regime():
    from breakout.regime import BtcRegime
    return BtcRegime(label="red", btc_close=95.0, btc_ema50_1h=100.0,
                     btc_1h_pct=-1.5, btc_15m_drop_pct=-0.2)


def _risk_off_regime():
    from breakout.regime import BtcRegime
    return BtcRegime(label="risk_off", btc_close=90.0, btc_ema50_1h=100.0,
                     btc_1h_pct=-2.0, btc_15m_drop_pct=-2.5)


def _uptrend_1h(n=210):
    # Mild uptrend: closes rise slowly from 85 → 95 so ema50_1h < 102.1 (candle.close)
    # and ema50_1h > ema200_1h (monotone series).
    out = []
    for i in range(n):
        p = 85.0 + (i / (n - 1)) * 10.0
        out.append(Kline(0, p, p + 0.3, p - 0.3, p, 1000.0, 0))
    return out


def _consolidation_15m_then_breakout():
    # 20 flat candles around 100.0, then a big breakout candle at 102
    base = [_k(1000 + i * 900, 100.0, 100.2, 99.8, 100.0, 1000.0) for i in range(20)]
    breakout = _k(1000 + 20 * 900, 100.0, 102.5, 99.9, 102.1, 2000.0)
    return base + [breakout]


@pytest.mark.asyncio
async def test_strategy_no_entry_when_watchlist_empty():
    client = AsyncMock()
    state = BreakoutState()
    execution = AsyncMock()
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()


@pytest.mark.asyncio
async def test_strategy_no_entry_when_candle_not_new():
    client = AsyncMock()
    client.fetch_klines = AsyncMock(return_value=_consolidation_15m_then_breakout())
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    latest_close = _consolidation_15m_then_breakout()[-1].close_time
    state.last_seen_close["BTCUSDT"] = latest_close
    execution = AsyncMock()
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()


@pytest.mark.asyncio
async def test_strategy_fires_entry_on_high_score_breakout():
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=False)
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_called_once()
    args, kwargs = execution.enter.call_args
    assert kwargs.get("symbol") == "BTCUSDT" or (args and args[0] == "BTCUSDT")


@pytest.mark.asyncio
async def test_strategy_rejects_when_score_below_min():
    cfg = _make_config()
    cfg.breakout_min_score = 11  # impossible → always reject
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=False)
    strat = BreakoutStrategy(client, state, cfg, execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_score_too_low", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_rejects_duplicate_symbol():
    from breakout.state import BreakoutPosition
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    state.open_positions["BTCUSDT"] = BreakoutPosition(
        symbol="BTCUSDT", entry_time="t", entry_price=100.0, qty=1, cost_usd=500,
        score=8, resistance_level=99.5, tp_price=104, stop_price=97,
        entry_candle_volume=1000, peak_price=100)
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=False)
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_duplicate", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_rejects_during_cooldown():
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["BTCUSDT"])
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=True)
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_cooldown", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_blocks_all_entries_when_risk_off():
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["ETHUSDT"])
    state.regime = _risk_off_regime()
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=False)
    strat = BreakoutStrategy(client, state, _make_config(), execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_risk_off", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_raises_score_floor_in_red():
    cfg = _make_config()
    cfg.breakout_min_score = 5     # normally low — would pass
    cfg.breakout_red_min_score = 11  # impossible floor in red
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["ETHUSDT"])
    state.regime = _red_regime()
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=False)
    strat = BreakoutStrategy(client, state, cfg, execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_red_score_floor", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_raises_vol_ratio_floor_in_red():
    cfg = _make_config()
    cfg.breakout_red_min_vol_ratio = 3.0  # fixture vol_ratio = 2.0 → rejected
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["ETHUSDT"])
    state.regime = _red_regime()
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=False)
    strat = BreakoutStrategy(client, state, cfg, execution)
    await strat.poll_once()
    execution.enter.assert_not_called()
    assert state.scan_counters.get("gate_red_vol_floor", 0) >= 1


@pytest.mark.asyncio
async def test_strategy_enters_in_red_when_gates_met():
    cfg = _make_config()
    cfg.breakout_red_min_score = 1     # easily met
    cfg.breakout_red_min_vol_ratio = 1.5  # fixture vol_ratio = 2.0 → passes
    k15 = _consolidation_15m_then_breakout()
    k1h = _uptrend_1h()
    client = AsyncMock()
    client.fetch_klines = AsyncMock(side_effect=lambda sym, interval, limit:
                                    k15 if interval == "15m" else k1h)
    state = BreakoutState()
    state.set_watchlist(["ETHUSDT"])
    state.regime = _red_regime()
    execution = AsyncMock()
    execution.can_open = MagicMock(return_value=True)
    execution.is_in_cooldown = MagicMock(return_value=False)
    strat = BreakoutStrategy(client, state, cfg, execution)
    await strat.poll_once()
    execution.enter.assert_called_once()
