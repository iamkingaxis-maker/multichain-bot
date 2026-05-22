import pytest
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager, OpenPosition


def _cfg(**overrides):
    base = dict(bot_id="b1", display_name="Bot 1")
    base.update(overrides)
    return BotConfig(**base)


def test_open_position_records_entry():
    pm = PerBotPositionManager(_cfg())
    p = pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1716480000.0)
    assert isinstance(p, OpenPosition)
    assert p.token == "SQUIRE"
    assert p.entry_price == 0.001
    assert p.size_usd == 20.0
    assert p.tp1_hit is False
    assert pm.open_count == 1

def test_open_position_rejects_over_max_concurrent():
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=2))
    pm.open_position("A", 0.001, 20.0, entry_time=1.0)
    pm.open_position("B", 0.001, 20.0, entry_time=2.0)
    with pytest.raises(ValueError, match="max_concurrent"):
        pm.open_position("C", 0.001, 20.0, entry_time=3.0)

def test_get_position_returns_open():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    p = pm.get_position("SQUIRE")
    assert p is not None
    assert p.token == "SQUIRE"

def test_close_position_returns_pnl_and_removes():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    result = pm.close_position(token="SQUIRE", exit_price=0.0011, exit_time=2.0, reason="TP1")
    assert result.token == "SQUIRE"
    assert result.cost_usd == 20.0
    assert result.proceeds_usd == pytest.approx(22.0, abs=0.01)
    assert result.realized_pnl_usd == pytest.approx(2.0, abs=0.01)
    assert pm.open_count == 0

def test_close_unknown_position_raises():
    pm = PerBotPositionManager(_cfg())
    with pytest.raises(KeyError):
        pm.close_position("MISSING", 0.001, 2.0, "stop")

def test_tick_emits_tp1_when_peak_hits_threshold():
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, tp1_sell_fraction=0.75))
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="SQUIRE", current_price=0.00105, now=2.0)
    assert any(d.kind == "TP1" for d in decisions)


def test_tick_emits_hard_stop_when_pnl_below_threshold():
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-15.0))
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="SQUIRE", current_price=0.00084, now=2.0)
    assert any(d.kind == "HARD_STOP" for d in decisions)


def test_tick_emits_post_tp1_trail_when_pulled_back_pp():
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, trail_pp=3.0))
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    pm.tick(token="SQUIRE", current_price=0.0011, now=2.0)
    p = pm.get_position("SQUIRE")
    assert p.peak_pnl_pct >= 9.9
    assert p.tp1_hit is True
    decisions = pm.tick(token="SQUIRE", current_price=0.00106, now=3.0)
    assert any(d.kind == "POST_TP1_TRAIL" for d in decisions)


def test_tick_no_decision_when_within_normal_band():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="SQUIRE", current_price=0.00102, now=2.0)
    assert decisions == []


def test_tick_unknown_token_returns_empty():
    pm = PerBotPositionManager(_cfg())
    assert pm.tick(token="MISSING", current_price=0.001, now=1.0) == []


def test_pre_stop_bail_fires_at_threshold_with_low_vol():
    pm = PerBotPositionManager(_cfg(
        pre_stop_bail_pnl_pct=-3.0,
        pre_stop_bail_vol_m5_max=500.0,
    ))
    pm.open_position("CHUD", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="CHUD", current_price=0.00097, now=2.0, vol_m5_usd=367.0)
    assert any(d.kind == "PRE_STOP_BAIL" for d in decisions)


def test_pre_stop_bail_does_NOT_fire_with_healthy_vol():
    pm = PerBotPositionManager(_cfg(
        pre_stop_bail_pnl_pct=-3.0,
        pre_stop_bail_vol_m5_max=500.0,
    ))
    pm.open_position("CHUD", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="CHUD", current_price=0.00097, now=2.0, vol_m5_usd=5000.0)
    assert not any(d.kind == "PRE_STOP_BAIL" for d in decisions)


def test_slow_bleed_fires_after_hold_min_at_loss():
    pm = PerBotPositionManager(_cfg(
        slow_bleed_minutes=60,
        slow_bleed_pnl_threshold=-8.0,
        hard_stop_pct=-15.0,
    ))
    pm.open_position("VIRL", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="VIRL", current_price=0.00090, now=1.0 + 3600.0)
    assert any(d.reason.startswith("slow_bleed") for d in decisions)
