# tests/test_loss_streak_pause.py
"""Loss-streak pause (2026-07-06 session-discipline decode).

Losses cluster in time (a market state, not tilt — the revenge-tax dissolved
within-wallet). After loss_streak_n consecutive losing FULL closes the bot
holds fire for loss_streak_pause_secs instead of re-firing the same signal
into the degraded stretch. Fleet join: +1,626pp/9d, 16/17 bots positive;
young lane exempt (-11.2pp there). Streak judges the WHOLE position (sum of
legs) — a TP1 winner whose runner leg closes red is still a WIN.
"""
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _pm(**over):
    base = dict(bot_id="t", display_name="t", tp1_pct=6.0, tp1_sell_fraction=0.75,
                tp2_pct=12.0, tp2_sell_fraction=0.25, trail_pp=2.0,
                hard_stop_pct=-12.0, loss_streak_pause=True)
    base.update(over)
    return PerBotPositionManager(BotConfig(**base))


def _lose(pm, token, t):
    pm.open_position(token=token, entry_price=1.0, size_usd=25.0,
                     entry_time=t, address="m" + token)
    pm.close_position(token, exit_price=0.93, exit_time=t + 60, reason="stop")


def _win(pm, token, t):
    pm.open_position(token=token, entry_price=1.0, size_usd=25.0,
                     entry_time=t, address="m" + token)
    pm.close_position(token, exit_price=1.08, exit_time=t + 60, reason="tp")


class TestStreakCounting:
    def test_three_losses_pauses(self):
        pm = _pm()
        for i, tok in enumerate(["A", "B", "C"]):
            assert not pm.in_loss_streak_pause(1000.0 + i * 100)
            _lose(pm, tok, 1000.0 + i * 100)
        assert pm.in_loss_streak_pause(1400.0) is True

    def test_win_resets(self):
        pm = _pm()
        _lose(pm, "A", 1000.0)
        _lose(pm, "B", 1100.0)
        _win(pm, "C", 1200.0)
        _lose(pm, "D", 1300.0)
        assert pm._loss_streak == 1
        assert not pm.in_loss_streak_pause(1400.0)

    def test_pause_expires(self):
        pm = _pm(loss_streak_pause_secs=3600.0)
        for i, tok in enumerate(["A", "B", "C"]):
            _lose(pm, tok, 1000.0 + i * 100)
        last_loss_close = 1200.0 + 60
        assert pm.in_loss_streak_pause(last_loss_close + 3599)
        assert not pm.in_loss_streak_pause(last_loss_close + 3601)

    def test_position_level_not_leg_level(self):
        """TP1 winner (+75% slice at +8) whose runner leg closes red must
        count as a WIN — the whole position is net positive."""
        pm = _pm()
        _lose(pm, "A", 1000.0)
        _lose(pm, "B", 1100.0)
        pm.open_position(token="C", entry_price=1.0, size_usd=25.0,
                         entry_time=1200.0, address="mC")
        pm.close_position("C", exit_price=1.08, exit_time=1260.0,
                          reason="TP1", sell_fraction=0.75)   # +$1.50
        pm.close_position("C", exit_price=0.98, exit_time=1320.0,
                          reason="trail", sell_fraction=1.0)  # -$0.125 on 0.25
        assert pm._loss_streak == 0   # net winner reset the streak
        assert not pm.in_loss_streak_pause(1400.0)

    def test_net_losing_partial_position_counts_once(self):
        """A position that TP1s tiny but bleeds the runner to a net loss
        increments the streak exactly ONCE, at the final leg."""
        pm = _pm()
        _lose(pm, "A", 1000.0)
        _lose(pm, "B", 1100.0)
        pm.open_position(token="C", entry_price=1.0, size_usd=25.0,
                         entry_time=1200.0, address="mC")
        pm.close_position("C", exit_price=1.005, exit_time=1260.0,
                          reason="TP1", sell_fraction=0.25)   # +$0.03
        assert pm._loss_streak == 2   # not yet fully closed
        pm.close_position("C", exit_price=0.85, exit_time=1320.0,
                          reason="stop", sell_fraction=1.0)   # net loser
        assert pm._loss_streak == 3
        assert pm.in_loss_streak_pause(1400.0)


class TestGates:
    def test_config_off_never_pauses(self):
        pm = _pm(loss_streak_pause=False)
        for i, tok in enumerate(["A", "B", "C", "D"]):
            _lose(pm, tok, 1000.0 + i * 100)
        assert not pm.in_loss_streak_pause(1500.0)

    def test_env_kill(self, monkeypatch):
        pm = _pm()
        for i, tok in enumerate(["A", "B", "C"]):
            _lose(pm, tok, 1000.0 + i * 100)
        monkeypatch.setenv("LOSS_STREAK_PAUSE_MODE", "off")
        assert not pm.in_loss_streak_pause(1400.0)
        monkeypatch.setenv("LOSS_STREAK_PAUSE_MODE", "on")
        assert pm.in_loss_streak_pause(1400.0)

    def test_configurable_n(self):
        pm = _pm(loss_streak_n=2)
        _lose(pm, "A", 1000.0)
        _lose(pm, "B", 1100.0)
        assert pm.in_loss_streak_pause(1200.0)
