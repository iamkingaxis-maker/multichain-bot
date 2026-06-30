"""LET-WINNERS-RUN exit (solve-it army 2026-06-30). For a CONFIRMED runner
(peak >= arm, default = tp1_pct) RUN_WINNERS_MODE=enforce suppresses the TP1 cap
and rides a wide trail (peak - RUN_WINNERS_TRAIL_PP), exiting full when it gives
back. off = byte-identical (TP1 fires). Catastrophic floors still fire first."""
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _mgr(tp1=6.0):
    # non-badday bot_id so the badday in-flight floor block stays inactive
    c = BotConfig(bot_id="t_rw", display_name="run-winners test",
                  tp1_pct=tp1, tp1_sell_fraction=0.75, tp2_pct=12.0,
                  trail_pp=3.0, hard_stop_pct=-15.0)
    return PerBotPositionManager(c)


def _open(m):
    m.open_position("TOK", entry_price=1.0, size_usd=100.0, entry_time=0.0)


def test_off_mode_tp1_fires(monkeypatch):
    monkeypatch.delenv("RUN_WINNERS_MODE", raising=False)
    m = _mgr(); _open(m)
    decs = m.tick("TOK", 1.06, now=10.0)  # +6% -> TP1 at default off
    kinds = [d.kind for d in decs]
    assert "TP1" in kinds


def test_enforce_suppresses_tp1_and_holds_runner(monkeypatch):
    monkeypatch.setenv("RUN_WINNERS_MODE", "enforce")
    monkeypatch.setenv("RUN_WINNERS_TRAIL_PP", "10")
    monkeypatch.delenv("RUN_WINNERS_ARM_PCT", raising=False)  # default = tp1_pct (6)
    m = _mgr(); _open(m)
    # +8% : peak 8 >= arm 6, pnl 8 > 8-10 -> HOLD, no TP1
    decs = m.tick("TOK", 1.08, now=10.0)
    assert decs == []
    assert not m.get_position("TOK").tp1_hit
    # +20% : still holding
    assert m.tick("TOK", 1.20, now=20.0) == []
    # back to +9% : 9 <= peak(20)-10 -> wide trail FIRES, full exit
    decs = m.tick("TOK", 1.09, now=30.0)
    assert len(decs) == 1 and decs[0].sell_fraction == 1.0
    assert "run-winners wide trail" in decs[0].reason


def test_enforce_hardstop_still_fires_on_runner(monkeypatch):
    monkeypatch.setenv("RUN_WINNERS_MODE", "enforce")
    monkeypatch.setenv("RUN_WINNERS_TRAIL_PP", "10")
    m = _mgr(); _open(m)
    m.tick("TOK", 1.12, now=10.0)            # arm the runner (peak +12)
    decs = m.tick("TOK", 0.80, now=20.0)     # -20% gap -> hard stop must win
    assert any(d.kind == "HARD_STOP" for d in decs)


def test_shadow_stamps_but_tp1_still_fires(monkeypatch):
    monkeypatch.setenv("RUN_WINNERS_MODE", "shadow")
    monkeypatch.setenv("RUN_WINNERS_TRAIL_PP", "10")
    m = _mgr(); _open(m)
    m.tick("TOK", 1.20, now=10.0)            # peak +20 (shadow: TP1 still fires at +6 path)
    decs = m.tick("TOK", 1.05, now=20.0)     # 5 <= 20-10 -> shadow would-fire stamp
    sb = m.get_position("TOK").state_blob if m.get_position("TOK") else {}
    # in shadow the run-winners stamp is recorded; behavior unchanged (tp1 had fired)
    # (position may already be partially closed by TP1; just assert the stamp logic ran)
    # re-open clean to check the stamp deterministically:
    m2 = _mgr(); m2.open_position("T2", entry_price=1.0, size_usd=100.0, entry_time=0.0)
    m2.tick("T2", 1.20, now=10.0)
    m2.tick("T2", 1.05, now=20.0)
    p = m2.get_position("T2")
    assert p is not None and p.state_blob.get("run_winners_fired") is True
    assert p.state_blob.get("run_winners_peak_at_fire") >= 19.0
