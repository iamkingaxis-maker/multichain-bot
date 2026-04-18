import os
from unittest.mock import patch
from utils.config import Config, _apply_env_overrides


def test_breakout_defaults():
    c = Config()
    assert c.breakout_enabled is False
    assert c.breakout_capital == 2000.0
    assert c.breakout_position_usd == 500.0
    assert c.breakout_max_concurrent == 4
    assert c.breakout_cooldown_minutes == 45.0
    assert c.breakout_min_score == 7
    assert c.breakout_tp_pct == 4.0
    assert c.breakout_tp_sell_pct == 0.50
    assert c.breakout_stop_pct == 3.0
    assert c.breakout_trail_pct == 2.0
    assert c.breakout_max_hold_hours == 4.0
    assert c.breakout_scan_interval_min == 10.0
    assert c.breakout_scan_top_n == 200
    assert c.breakout_min_vol_24h_usd == 50_000_000
    assert c.breakout_change_24h_min_pct == 3.0
    assert c.breakout_change_24h_max_pct == 15.0
    assert c.breakout_change_6h_max_pct == 12.0
    assert c.breakout_watchlist_size == 5
    assert c.breakout_poll_interval_sec == 30.0
    assert c.breakout_candle_close_delay_sec == 2.0
    assert c.breakout_paper_taker_fee == 0.006
    assert "USDT" in c.breakout_excluded_bases


def test_breakout_env_overrides():
    env = {
        "BREAKOUT_ENABLED": "true",
        "BREAKOUT_CAPITAL": "5000",
        "BREAKOUT_POSITION_USD": "1000",
        "BREAKOUT_MAX_CONCURRENT": "8",
        "BREAKOUT_MIN_SCORE": "6",
        "BREAKOUT_TP_PCT": "5.0",
        "BREAKOUT_STOP_PCT": "2.5",
    }
    with patch.dict(os.environ, env, clear=False):
        c = Config()
        _apply_env_overrides(c)
        assert c.breakout_enabled is True
        assert c.breakout_capital == 5000.0
        assert c.breakout_position_usd == 1000.0
        assert c.breakout_max_concurrent == 8
        assert c.breakout_min_score == 6
        assert c.breakout_tp_pct == 5.0
        assert c.breakout_stop_pct == 2.5
