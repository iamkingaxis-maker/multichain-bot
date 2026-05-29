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


def test_close_result_carries_entry_price_for_self_verification():
    """CloseResult.entry_price lets sell records persist the entry price, so
    pnl_pct == (exit/entry - 1)*100 is independently verifiable. Without it,
    multi-bot sells stored entry_price=None and could not be audited."""
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    result = pm.close_position(token="SQUIRE", exit_price=0.0011, exit_time=2.0, reason="TP1")
    assert result.entry_price == pytest.approx(0.001, abs=1e-9)
    implied_pct = (0.0011 / result.entry_price - 1.0) * 100.0
    assert implied_pct == pytest.approx(result.pnl_pct, abs=0.01)

def test_close_unknown_position_raises():
    pm = PerBotPositionManager(_cfg())
    with pytest.raises(KeyError):
        pm.close_position("MISSING", 0.001, 2.0, "stop")


# ── Partial sells (P1: honor sell_fraction) ──────────────────────────────
def test_partial_close_keeps_position_open():
    """TP1 sells 75% — position stays open with 25% remaining, NOT removed."""
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    r = pm.close_position("SQUIRE", exit_price=0.00105, exit_time=2.0,
                          reason="TP1", sell_fraction=0.75)
    # sold 75% of $20 = $15 cost, proceeds 15*1.05 = 15.75, pnl 0.75
    assert r.cost_usd == pytest.approx(15.0, abs=0.01)
    assert r.proceeds_usd == pytest.approx(15.75, abs=0.01)
    assert r.realized_pnl_usd == pytest.approx(0.75, abs=0.01)
    assert r.fully_closed is False
    assert r.sell_fraction == pytest.approx(0.75)
    assert pm.open_count == 1  # still held
    assert pm.get_position("SQUIRE").remaining_fraction == pytest.approx(0.25)


def test_partial_then_full_close_sums_correctly():
    """TP1 75% then TP2 25% fully exits; total realized = both legs."""
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    r1 = pm.close_position("SQUIRE", 0.00105, 2.0, "TP1", sell_fraction=0.75)
    r2 = pm.close_position("SQUIRE", 0.00110, 3.0, "TP2", sell_fraction=0.25)
    # r2: 25% of $20 = $5 cost, proceeds 5*1.10 = 5.5, pnl 0.5
    assert r2.realized_pnl_usd == pytest.approx(0.5, abs=0.01)
    assert r2.fully_closed is True
    assert pm.open_count == 0
    total = r1.realized_pnl_usd + r2.realized_pnl_usd
    assert total == pytest.approx(1.25, abs=0.01)  # beats $1.00 full-close-at-TP1


def test_partial_remainder_full_exit_sells_only_remaining():
    """After TP1 75%, a hard-stop (sell_fraction=1.0) sells only the 25% left."""
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    pm.close_position("SQUIRE", 0.00105, 2.0, "TP1", sell_fraction=0.75)
    r = pm.close_position("SQUIRE", 0.00090, 3.0, "stop", sell_fraction=1.0)
    # only 25% remains: cost $5, proceeds 5*0.9 = 4.5, pnl -0.5
    assert r.cost_usd == pytest.approx(5.0, abs=0.01)
    assert r.realized_pnl_usd == pytest.approx(-0.5, abs=0.01)
    assert r.fully_closed is True
    assert pm.open_count == 0


def test_full_close_default_fraction_unchanged():
    """Default sell_fraction=1.0 preserves legacy full-close behavior."""
    pm = PerBotPositionManager(_cfg())
    pm.open_position("SQUIRE", 0.001, 20.0, entry_time=1.0)
    r = pm.close_position("SQUIRE", 0.0011, 2.0, "TP1")
    assert r.fully_closed is True
    assert r.cost_usd == pytest.approx(20.0, abs=0.01)
    assert pm.open_count == 0

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


def test_flat_exit_fires_on_dead_money():
    """Velocity exit: held past flat_exit_minutes, pnl flat (dead) → recycle."""
    pm = PerBotPositionManager(_cfg(flat_exit_minutes=45, flat_exit_band_pct=3.0))
    pm.open_position("DEAD", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick("DEAD", current_price=0.00101, now=1.0 + 45 * 60)  # +1%, 45min
    assert any(d.kind == "FLAT_EXIT" for d in decisions)


def test_flat_exit_does_not_fire_when_moving():
    """A position up +4% (outside flat band, climbing toward TP1) is NOT dead."""
    pm = PerBotPositionManager(_cfg(flat_exit_minutes=45, flat_exit_band_pct=3.0, tp1_pct=5.0))
    pm.open_position("WIN", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick("WIN", current_price=0.00104, now=1.0 + 45 * 60)  # +4%
    assert not any(d.kind == "FLAT_EXIT" for d in decisions)


def test_flat_exit_disabled_by_default():
    pm = PerBotPositionManager(_cfg())  # flat_exit_minutes None
    pm.open_position("X", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick("X", current_price=0.00101, now=1.0 + 99 * 60)
    assert not any(d.kind == "FLAT_EXIT" for d in decisions)


def test_slow_bleed_fires_after_hold_min_at_loss():
    pm = PerBotPositionManager(_cfg(
        slow_bleed_minutes=60,
        slow_bleed_pnl_threshold=-8.0,
        hard_stop_pct=-15.0,
    ))
    pm.open_position("VIRL", 0.001, 20.0, entry_time=1.0)
    decisions = pm.tick(token="VIRL", current_price=0.00090, now=1.0 + 3600.0)
    assert any(d.reason.startswith("slow_bleed") for d in decisions)


def test_stall_exit_fires_on_low_peak_drift():
    """Never-launched corpse: peaked +2% (< 5% cap), held >90min, drifted back
    to 0% (>=2pp off peak) → STALL_EXIT recycles it. Above slow_bleed's -8%."""
    pm = PerBotPositionManager(_cfg(
        stall_exit_minutes=90, stall_exit_peak_max=5.0, stall_exit_drift_pp=2.0,
    ))
    pm.open_position("CORPSE", 0.001, 20.0, entry_time=1.0)
    pm.tick("CORPSE", current_price=0.00102, now=2.0)            # +2% sets peak
    decisions = pm.tick("CORPSE", current_price=0.001, now=1.0 + 90 * 60)  # back to 0%
    assert any(d.kind == "STALL_EXIT" for d in decisions)


def test_stall_exit_does_not_fire_while_still_climbing():
    """Position holding at its low peak (not drifting off it) is not a stall."""
    pm = PerBotPositionManager(_cfg(
        stall_exit_minutes=90, stall_exit_peak_max=5.0, stall_exit_drift_pp=2.0,
    ))
    pm.open_position("LIVE", 0.001, 20.0, entry_time=1.0)
    pm.tick("LIVE", current_price=0.00102, now=2.0)              # +2% sets peak
    decisions = pm.tick("LIVE", current_price=0.00102, now=1.0 + 90 * 60)  # still +2%
    assert not any(d.kind == "STALL_EXIT" for d in decisions)


def test_stall_exit_disabled_by_default():
    pm = PerBotPositionManager(_cfg())  # stall_exit_minutes None
    pm.open_position("X", 0.001, 20.0, entry_time=1.0)
    pm.tick("X", current_price=0.00102, now=2.0)
    decisions = pm.tick("X", current_price=0.001, now=1.0 + 99 * 60)
    assert not any(d.kind == "STALL_EXIT" for d in decisions)
