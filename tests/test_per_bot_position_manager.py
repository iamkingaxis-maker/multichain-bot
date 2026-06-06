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


# ── Never-runner exit (2026-06-02 mine; peak<3 gate = winner-safe by construction) ──
def _nr_cfg(**ov):
    base = dict(never_runner_exit_enabled=True, never_runner_peak_max=3.0,
                never_runner_loss_floor=-6.0, never_runner_minutes=45,
                slow_bleed_minutes=60, slow_bleed_pnl_threshold=-8.0)
    base.update(ov)
    return _cfg(**base)


def test_never_runner_timebox_fires_when_enabled():
    # peak<3 (flat at -1%), held 45min -> time-box arm fires
    pm = PerBotPositionManager(_nr_cfg())
    pm.open_position("FLAT", 1.0, 100.0, entry_time=0.0)
    d = pm.tick(token="FLAT", current_price=0.99, now=45 * 60, vol_m5_usd=None)
    assert any(x.kind == "NEVER_RUNNER" for x in d)


def test_never_runner_floor_fires_early_when_bleeding():
    # peak<3, only 20min held but pnl -7% <= -6% floor -> floor arm fires early
    pm = PerBotPositionManager(_nr_cfg())
    pm.open_position("BLEED", 1.0, 100.0, entry_time=0.0)
    d = pm.tick(token="BLEED", current_price=0.93, now=20 * 60, vol_m5_usd=None)
    assert any(x.kind == "NEVER_RUNNER" for x in d)


def test_never_runner_winner_safe_when_peaked_ge_3():
    # peaked +4% then fell to -7% at 50min: peak gate (>=3) blocks the exit -> trail-safe
    pm = PerBotPositionManager(_nr_cfg())
    pm.open_position("RUN", 1.0, 100.0, entry_time=0.0)
    pm.tick(token="RUN", current_price=1.04, now=60.0, vol_m5_usd=None)   # set peak +4%
    d = pm.tick(token="RUN", current_price=0.93, now=50 * 60, vol_m5_usd=None)
    assert not any(x.kind == "NEVER_RUNNER" for x in d)


def test_never_runner_does_not_fire_before_either_arm():
    # peak<3, 30min held, pnl -2% (above floor, below time) -> neither arm
    pm = PerBotPositionManager(_nr_cfg())
    pm.open_position("EARLY", 1.0, 100.0, entry_time=0.0)
    d = pm.tick(token="EARLY", current_price=0.98, now=30 * 60, vol_m5_usd=None)
    assert not any(x.kind == "NEVER_RUNNER" for x in d)


def test_never_runner_disabled_does_not_exit_but_shadow_stamps():
    # default disabled: no NEVER_RUNNER decision, but the shadow flag is stamped
    pm = PerBotPositionManager(_cfg(slow_bleed_minutes=60))  # enabled defaults False
    pm.open_position("SHDW", 1.0, 100.0, entry_time=0.0)
    d = pm.tick(token="SHDW", current_price=0.99, now=45 * 60, vol_m5_usd=None)
    assert not any(x.kind == "NEVER_RUNNER" for x in d)
    assert pm.get_position("SHDW").state_blob.get("never_runner_fired") is True
    assert pm.get_position("SHDW").state_blob.get("never_runner_arm") == "timebox"


def test_mae_excursion_tracks_max_adverse():
    # dips to -7% then recovers to -1%: MAE (max-adverse) should stick at the -7% trough
    pm = PerBotPositionManager(_cfg())
    pm.open_position("MAE", 1.0, 100.0, entry_time=0.0)
    pm.tick(token="MAE", current_price=0.93, now=60.0)    # -7%
    pm.tick(token="MAE", current_price=0.99, now=120.0)   # recovered to -1%
    sb = pm.get_position("MAE").state_blob
    assert abs(sb.get("mae_pct") - (-7.0)) < 1e-6
    assert sb.get("mae_at_secs") == 60


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


# ── Give-back SHADOW (measure-only, 2026-05-31) ───────────────────────────
# Records whether a position went green (peak>=+3%) then fell back to <=0%
# while pre-TP1 — input for the future breakeven-rescue winner-kill audit.

def test_giveback_shadow_fires_green_then_breakeven_pre_tp1():
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, hard_stop_pct=-15.0))
    pm.open_position("G", 0.001, 20.0, entry_time=0.0)
    pm.tick("G", current_price=0.00104, now=10.0)   # +4% peak (below TP1)
    pm.tick("G", current_price=0.001, now=20.0)      # back to 0%
    sb = pm.get_position("G").state_blob
    assert sb.get("gb_shadow_fired") is True
    assert sb.get("gb_shadow_peak_at_fire") == pytest.approx(4.0, abs=0.05)
    assert sb.get("gb_shadow_pnl_at_fire") == pytest.approx(0.0, abs=0.05)
    assert sb.get("gb_shadow_secs_at_fire") == 20


def test_giveback_shadow_no_fire_if_peak_below_3():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("G", 0.001, 20.0, entry_time=0.0)
    pm.tick("G", current_price=0.00102, now=10.0)   # +2% peak only
    pm.tick("G", current_price=0.001, now=20.0)      # 0%
    assert not (pm.get_position("G").state_blob or {}).get("gb_shadow_fired")


def test_giveback_shadow_no_fire_post_tp1():
    # trail_pp huge so the post-TP1 trail doesn't close the position here
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, tp1_sell_fraction=0.75, trail_pp=50.0))
    pm.open_position("G", 0.001, 20.0, entry_time=0.0)
    pm.tick("G", current_price=0.00106, now=10.0)   # +6% -> TP1 fires
    p = pm.get_position("G")
    assert p is not None and p.tp1_hit
    pm.tick("G", current_price=0.001, now=20.0)      # 0% but post-TP1
    assert not (p.state_blob or {}).get("gb_shadow_fired")


def test_giveback_shadow_records_first_crossing_only():
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("G", 0.001, 20.0, entry_time=0.0)
    pm.tick("G", current_price=0.00104, now=10.0)    # +4% peak
    pm.tick("G", current_price=0.0009995, now=20.0)  # ~-0.05% -> fires
    pm.tick("G", current_price=0.0009, now=30.0)     # -10% -> must NOT overwrite
    sb = pm.get_position("G").state_blob
    assert sb["gb_shadow_fired"] is True
    assert sb["gb_shadow_secs_at_fire"] == 20        # first crossing, not 30
    assert sb["gb_shadow_pnl_at_fire"] > -1.0        # ~-0.05, not -10


# ── Paper-mode uncap (2026-05-31 data accelerator) ─────────────────────────
from core.per_bot_position_manager import paper_uncapped


def test_paper_uncapped_off_by_default(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    assert paper_uncapped() is False


def test_paper_uncapped_requires_BOTH_flags(monkeypatch):
    monkeypatch.setenv("PAPER_UNCAPPED", "1")
    monkeypatch.delenv("PAPER_MODE", raising=False)
    assert paper_uncapped() is False          # PAPER_UNCAPPED alone is NOT enough
    monkeypatch.setenv("PAPER_MODE", "true")
    assert paper_uncapped() is True           # both -> uncapped


def test_open_position_uncapped_exceeds_max_concurrent(monkeypatch):
    monkeypatch.setenv("PAPER_UNCAPPED", "1")
    monkeypatch.setenv("PAPER_MODE", "true")
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=2))
    pm.open_position("A", 0.001, 20.0, entry_time=1.0)
    pm.open_position("B", 0.001, 20.0, entry_time=2.0)
    p = pm.open_position("C", 0.001, 20.0, entry_time=3.0)   # would raise if capped
    assert p.token == "C" and pm.open_count == 3


def test_open_position_still_caps_when_uncap_off(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=2))
    pm.open_position("A", 0.001, 20.0, entry_time=1.0)
    pm.open_position("B", 0.001, 20.0, entry_time=2.0)
    with pytest.raises(ValueError, match="max_concurrent"):
        pm.open_position("C", 0.001, 20.0, entry_time=3.0)


def test_uncapped_still_blocks_duplicate_token(monkeypatch):
    monkeypatch.setenv("PAPER_UNCAPPED", "1")
    monkeypatch.setenv("PAPER_MODE", "true")
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=2))
    pm.open_position("A", 0.001, 20.0, entry_time=1.0)
    with pytest.raises(ValueError, match="already holds"):
        pm.open_position("A", 0.001, 20.0, entry_time=2.0)


# ── Never-green fast-stop SHADOW (2026-05-31, primary avg-loss lever) ───────
# Fires when a position that NEVER peaked >=2% hits <=-4% (the 78%-of-loss
# never-green dying slice). peak>=2 winners are NOT cut (the whipsaw guard).

def test_ng_faststop_fires_when_never_green_and_down_4(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)  # don't interfere w/ caps
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00101, now=10.0)   # +1% peak (never >=2)
    pm.tick("D", current_price=0.00096, now=20.0)   # -4% -> fires
    sb = pm.get_position("D").state_blob
    assert sb.get("ng_faststop_fired") is True
    assert sb.get("ng_faststop_peak_at_fire") == pytest.approx(1.0, abs=0.05)
    assert sb.get("ng_faststop_pnl_at_fire") == pytest.approx(-4.0, abs=0.05)


def test_ng_faststop_no_fire_if_peaked_above_2(monkeypatch):
    # green-then-dip: peaked +3% then fell to -4% -> NOT a never-green dud,
    # must NOT fire (this is the whipsaw guard — these can recover).
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00103, now=10.0)   # +3% peak (>=2)
    pm.tick("D", current_price=0.00096, now=20.0)   # -4%
    assert not (pm.get_position("D").state_blob or {}).get("ng_faststop_fired")


def test_ng_faststop_no_fire_if_not_down_4(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00101, now=10.0)   # +1% peak
    pm.tick("D", current_price=0.00098, now=20.0)   # -2% (not <=-4)
    assert not (pm.get_position("D").state_blob or {}).get("ng_faststop_fired")


def test_ng_faststop_records_first_crossing(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00101, now=10.0)    # +1% peak
    pm.tick("D", current_price=0.000958, now=20.0)   # ~-4.2% -> fires
    pm.tick("D", current_price=0.0009, now=30.0)     # -10% -> must NOT overwrite
    sb = pm.get_position("D").state_blob
    assert sb["ng_faststop_secs_at_fire"] == 20
    assert sb["ng_faststop_pnl_at_fire"] > -5.0     # ~-4.2, not -10


def test_ng_faststop_captures_dip_moment_features(monkeypatch):
    # The finer dip-moment snapshot must populate at fire: vol_m5 (passed),
    # drop_velocity + secs_from_peak (from state).
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00101, now=10.0)                    # +1% peak @10s
    pm.tick("D", current_price=0.00096, now=40.0, vol_m5_usd=123.0)  # -4% @40s -> fires
    sb = pm.get_position("D").state_blob
    assert sb["ng_faststop_fired"] is True
    assert sb["ng_faststop_vol_m5_at_fire"] == 123.0
    assert sb["ng_faststop_secs_from_peak"] == 30          # 40s fire - 10s peak
    # drop ~5pp (+1 -> -4) over 30s = ~0.167 pp/s
    assert sb["ng_faststop_drop_velocity_pp_s"] == pytest.approx(0.167, abs=0.02)


# ── tp1-knee + time-stop SHADOWS (2026-06-02, measure-only forward data) ──────

def _shadow_cfg():
    return _cfg(tp1_pct=5.0, slow_bleed_pnl_threshold=-8.0, slow_bleed_minutes=60)


def test_tp1_knee_shadow_fires_at_plus3_pre_tp1():
    pm = PerBotPositionManager(_shadow_cfg())
    pm.open_position("K", 0.001, 20.0, entry_time=0.0)
    pm.tick(token="K", current_price=0.00103, now=10.0)   # +3% (pre-TP1, TP1=5%)
    sb = pm.get_position("K").state_blob
    assert sb["tp1_knee_3_hit"] is True
    assert sb["tp1_knee_3_secs"] == 10
    assert "tp1_knee_4_hit" not in sb                      # +3 only, not +4 yet


def test_tp1_knee_shadow_fires_at_plus4():
    pm = PerBotPositionManager(_shadow_cfg())
    pm.open_position("K", 0.001, 20.0, entry_time=0.0)
    pm.tick(token="K", current_price=0.00104, now=20.0)   # +4%
    sb = pm.get_position("K").state_blob
    assert sb["tp1_knee_3_hit"] is True and sb["tp1_knee_4_hit"] is True


def test_tp1_knee_shadow_fires_once_keeps_first_secs():
    pm = PerBotPositionManager(_shadow_cfg())
    pm.open_position("K", 0.001, 20.0, entry_time=0.0)
    pm.tick(token="K", current_price=0.00103, now=10.0)   # +3% at t=10
    pm.tick(token="K", current_price=0.00103, now=99.0)   # +3% again later
    assert pm.get_position("K").state_blob["tp1_knee_3_secs"] == 10   # not overwritten


def test_tp1_knee_shadow_skipped_when_already_tp1_hit():
    pm = PerBotPositionManager(_shadow_cfg())
    p = pm.open_position("K", 0.001, 20.0, entry_time=0.0)
    p.tp1_hit = True
    pm.tick(token="K", current_price=0.00103, now=10.0)   # +3% but post-TP1
    assert "tp1_knee_3_hit" not in pm.get_position("K").state_blob


def test_timestop45_shadow_fires_after_45min_below_threshold():
    pm = PerBotPositionManager(_shadow_cfg())
    pm.open_position("T", 0.001, 20.0, entry_time=0.0)
    pm.tick(token="T", current_price=0.00091, now=2700.0)  # 45min, -9% (<= -8 slow_bleed_thr)
    sb = pm.get_position("T").state_blob
    assert sb["timestop45_fired"] is True
    assert sb["timestop45_pnl_at_fire"] == pytest.approx(-9.0, abs=0.05)
    assert sb["timestop45_secs"] == 2700


def test_timestop45_shadow_not_before_45min():
    pm = PerBotPositionManager(_shadow_cfg())
    pm.open_position("T", 0.001, 20.0, entry_time=0.0)
    pm.tick(token="T", current_price=0.00091, now=2699.0)  # < 45min
    assert "timestop45_fired" not in pm.get_position("T").state_blob


def test_timestop45_shadow_not_when_above_threshold():
    pm = PerBotPositionManager(_shadow_cfg())
    pm.open_position("T", 0.001, 20.0, entry_time=0.0)
    pm.tick(token="T", current_price=0.00095, now=2700.0)  # 45min but -5% (> -8)
    assert "timestop45_fired" not in pm.get_position("T").state_blob


# ── Phase-2a trajectory SHADOW (2026-06-02, measure-only +8min demand-shape) ──

def _traj_cfg():
    # loose TP/stop so the position survives to the +8min checkpoint
    return _cfg(tp1_pct=80.0, tp2_pct=90.0, hard_stop_pct=-95.0,
               slow_bleed_minutes=999, pre_stop_bail_pnl_pct=-95.0)


def test_trajectory_shadow_stamps_shape_at_8min():
    pm = PerBotPositionManager(_traj_cfg())
    pm.open_position("J", 1.0, 20.0, entry_time=0.0)
    # rising-then-fading path over the first 8 min (one tick/min)
    path = {0:1.0, 60:1.02, 120:1.05, 180:1.04, 240:1.03, 300:1.02, 360:1.01, 420:1.0}
    for t, px in path.items():
        pm.tick(token="J", current_price=px, now=float(t), vol_m5_usd=1000.0)
    sb = pm.get_position("J").state_blob
    assert "scalein_n" not in sb                 # not yet at +8min (last tick 420 < 480)
    pm.tick(token="J", current_price=1.0, now=480.0, vol_m5_usd=1000.0)   # +8min checkpoint
    sb = pm.get_position("J").state_blob
    assert sb.get("scalein_shape_done") is True
    assert sb.get("scalein_n", 0) >= 4
    assert 0.0 <= sb["scalein_peak_position"] <= 1.0
    assert isinstance(sb["scalein_higher_low_n"], int)
    assert "scalein_traj" not in sb              # raw path freed after computation


def test_trajectory_shadow_fires_once():
    pm = PerBotPositionManager(_traj_cfg())
    pm.open_position("J", 1.0, 20.0, entry_time=0.0)
    for t in range(0, 481, 60):
        pm.tick(token="J", current_price=1.02, now=float(t), vol_m5_usd=500.0)
    n1 = pm.get_position("J").state_blob.get("scalein_n")
    assert n1 is not None
    pm.tick(token="J", current_price=1.5, now=600.0, vol_m5_usd=500.0)   # later, higher
    assert pm.get_position("J").state_blob.get("scalein_n") == n1        # not recomputed


def test_trajectory_shadow_absent_if_closed_before_8min():
    pm = PerBotPositionManager(_traj_cfg())
    pm.open_position("J", 1.0, 20.0, entry_time=0.0)
    pm.tick(token="J", current_price=1.05, now=120.0, vol_m5_usd=500.0)  # only 2 min in
    sb = pm.get_position("J").state_blob
    assert "scalein_shape_done" not in sb
    assert "scalein_n" not in sb


def test_trajectory_shape_features_peak_early_vs_late():
    from core.per_bot_position_manager import _trajectory_shape_features
    # peak early (minute 1) -> low peak_position
    traj_early = [(0,1.0,100),(60,1.10,100),(120,1.05,100),(180,1.03,100),(240,1.02,100)]
    f = _trajectory_shape_features(traj_early, 1.0)
    assert f["n"] >= 4 and f["peak_position"] < 0.5
    # too few buckets -> None
    assert _trajectory_shape_features([(0,1.0,100),(60,1.01,100)], 1.0) is None
    # empty / bad entry -> None
    assert _trajectory_shape_features([], 1.0) is None
    assert _trajectory_shape_features(traj_early, 0.0) is None


# ── Scale-in / staged entry EXECUTION (2026-06-05, SOL-flicker resolution) ────────
# Distinct from the trajectory SHADOW above (scalein_n/peak_position); these test the
# actual half-tranche-then-complete-on-confirm blend.

def _scin_cfg():
    return _cfg(scalein_enabled=True, scalein_confirm_pct=1.0, scalein_first_fraction=0.5)


def test_scalein_ready_false_when_not_pending():
    pm = PerBotPositionManager(_scin_cfg())
    pm.open_position("S", 1.0, 50.0, entry_time=0.0)   # no pending flag set
    assert pm.scalein_ready("S", 5.0) is False


def test_scalein_ready_true_only_at_or_above_confirm():
    pm = PerBotPositionManager(_scin_cfg())
    p = pm.open_position("S", 1.0, 50.0, entry_time=0.0)
    p.state_blob["scalein_pending"] = True
    p.state_blob["scalein_confirm_pct"] = 1.0
    assert pm.scalein_ready("S", 0.5) is False   # below confirm
    assert pm.scalein_ready("S", 1.0) is True    # at confirm
    assert pm.scalein_ready("S", 3.0) is True    # above


def test_complete_scalein_blends_entry_and_grows_size():
    pm = PerBotPositionManager(_scin_cfg())
    p = pm.open_position("S", 1.0, 50.0, entry_time=0.0)   # half tranche @ $1.00
    p.state_blob["scalein_pending"] = True
    p.state_blob["scalein_confirm_pct"] = 1.0
    ok = pm.complete_scalein("S", fill_price=1.01, add_usd=50.0)   # 2nd half @ +1%
    assert ok is True
    # token-weighted blend: 50/1.00 + 50/1.01 tokens for $100 cost
    tokens = 50/1.0 + 50/1.01
    assert p.size_usd == pytest.approx(100.0, abs=1e-6)
    assert p.entry_price == pytest.approx(100.0 / tokens, abs=1e-6)
    assert p.state_blob["scalein_pending"] is False
    assert p.state_blob["scalein_completed"] is True
    assert p.state_blob["scalein_added_usd"] == pytest.approx(50.0, abs=1e-6)


def test_complete_scalein_rebases_peak_onto_new_entry():
    pm = PerBotPositionManager(_scin_cfg())
    p = pm.open_position("S", 1.0, 50.0, entry_time=0.0)
    p.state_blob["scalein_pending"] = True
    p.peak_pnl_pct = 1.0   # peaked +1% on the old (lower) entry
    pm.complete_scalein("S", fill_price=1.01, add_usd=50.0)
    # peak price 1.01 vs new entry ~1.00497 -> rebased peak ~+0.5%, never negative
    assert 0.0 <= p.peak_pnl_pct < 1.0
    assert p.peak_pnl_pct == pytest.approx((1.01 / p.entry_price - 1.0) * 100.0, abs=1e-6)


def test_complete_scalein_noop_if_not_pending():
    pm = PerBotPositionManager(_scin_cfg())
    p = pm.open_position("S", 1.0, 50.0, entry_time=0.0)   # not pending
    assert pm.complete_scalein("S", fill_price=1.01, add_usd=50.0) is False
    assert p.size_usd == 50.0   # unchanged
    assert p.entry_price == 1.0


def test_complete_scalein_rejects_bad_inputs():
    pm = PerBotPositionManager(_scin_cfg())
    p = pm.open_position("S", 1.0, 50.0, entry_time=0.0)
    p.state_blob["scalein_pending"] = True
    assert pm.complete_scalein("S", fill_price=0.0, add_usd=50.0) is False
    assert pm.complete_scalein("S", fill_price=1.01, add_usd=0.0) is False
    assert pm.complete_scalein("MISSING", fill_price=1.01, add_usd=50.0) is False
    assert p.state_blob["scalein_pending"] is True   # still pending after rejects


# ── ng_faststop ACTING exit + narrow giveback shadow (2026-06-05 drawdown-mine) ──

def test_ng_faststop_exit_fires_when_enabled(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0, ng_faststop_exit_enabled=True))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00101, now=10.0)            # +1% peak (never >=2)
    decs = pm.tick("D", current_price=0.00096, now=20.0)     # -4% -> NG_FASTSTOP exit
    assert any(d.kind == "NG_FASTSTOP" for d in decs)


def test_ng_faststop_exit_off_when_disabled_but_shadow_stamps(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0, ng_faststop_exit_enabled=False))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00101, now=10.0)
    decs = pm.tick("D", current_price=0.00096, now=20.0)
    assert not any(d.kind == "NG_FASTSTOP" for d in decs)    # no exit when disabled
    assert pm.get_position("D").state_blob.get("ng_faststop_fired") is True  # shadow still fires


def test_ng_faststop_exit_winner_safe_peak_ge_2(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0, ng_faststop_exit_enabled=True))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00103, now=10.0)            # +3% peak (>=2 -> protected runner)
    decs = pm.tick("D", current_price=0.00096, now=20.0)     # -4%
    assert not any(d.kind == "NG_FASTSTOP" for d in decs)


def test_ng_faststop_exit_not_until_minus4(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0, ng_faststop_exit_enabled=True))
    pm.open_position("D", 0.001, 20.0, entry_time=0.0)
    pm.tick("D", current_price=0.00101, now=10.0)
    decs = pm.tick("D", current_price=0.00098, now=20.0)     # -2% (not <=-4)
    assert not any(d.kind == "NG_FASTSTOP" for d in decs)


def test_gb_narrow_shadow_stamps_in_band(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("G", 0.001, 20.0, entry_time=0.0)
    pm.tick("G", current_price=0.001037, now=10.0)           # +3.7% peak (in [3,5))
    pm.tick("G", current_price=0.00094, now=20.0)            # -6% (<=-5) -> stamp
    assert pm.get_position("G").state_blob.get("gb_narrow_fired") is True


def test_gb_narrow_shadow_not_above_band(monkeypatch):
    monkeypatch.delenv("PAPER_UNCAPPED", raising=False)
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-50.0))
    pm.open_position("G", 0.001, 20.0, entry_time=0.0)
    pm.tick("G", current_price=0.00106, now=10.0)            # +6% peak (>=5, outside band)
    pm.tick("G", current_price=0.00094, now=20.0)            # -6%
    assert not (pm.get_position("G").state_blob or {}).get("gb_narrow_fired")


# ── flash-slip scale-in de-size (2026-06-05 flash-signature mine) ────────────────
from core.per_bot_position_manager import scalein_first_fraction


def test_scalein_first_fraction_default_when_slip_null():
    cfg = _cfg(scalein_first_fraction=0.5)
    assert scalein_first_fraction(cfg, None) == 0.5            # quote unavailable -> default


def test_scalein_first_fraction_default_when_slip_low():
    cfg = _cfg(scalein_first_fraction=0.5)
    assert scalein_first_fraction(cfg, 3.0) == 0.5             # healthy depth -> default


def test_scalein_first_fraction_flash_when_slip_high():
    cfg = _cfg(scalein_first_fraction=0.5, scalein_flash_slip_pct=6.0, scalein_flash_first_fraction=0.33)
    assert scalein_first_fraction(cfg, 7.0) == 0.33            # thin executable depth -> smaller
    assert scalein_first_fraction(cfg, 6.0) == 0.33            # at threshold


def test_scalein_first_fraction_never_enlarges():
    # flash fraction > default -> min keeps the smaller default (never up-sizes)
    cfg = _cfg(scalein_first_fraction=0.25, scalein_flash_first_fraction=0.33)
    assert scalein_first_fraction(cfg, 7.0) == 0.25


def test_scalein_first_fraction_bad_slip_falls_back():
    cfg = _cfg(scalein_first_fraction=0.5)
    assert scalein_first_fraction(cfg, "oops") == 0.5
