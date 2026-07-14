"""SOL-macro bail — enforce path for the sol_bail shadow (2026-07-14 exit-leak mine).

The sol_bail shadow (feeds/dip_scanner._stamp_sol_bail_shadow) stamps
sol_bail_shadow_pnl_pct on a PRE-TP1 leg that is red while SOL macro is dumping.
Honest realized forward grade (84 recent closes): +46.3pp saved / 0 winner-kills
(n=7) -> the one clean bail on the never-green -9.68% floor cohort. The enforce
gate in PerBotPositionManager reads that stamp and emits a full close. Default
mode = shadow (byte-identical no-op); winner-safe by the pnl<1% guard.
"""
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _cfg(**ov):
    base = dict(bot_id="b1", display_name="Bot 1", tp1_pct=50.0, hard_stop_pct=-90.0)
    base.update(ov)
    return BotConfig(**base)


def _armed(pm, stamp=-1.34):
    """Open a leg and simulate the dip_scanner sol_bail stamp landing."""
    pm.open_position("TOK", 1.0, 20.0, entry_time=0.0)
    pos = pm.get_position("TOK")
    if pos.state_blob is None:
        pos.state_blob = {}
    pos.state_blob["sol_bail_shadow_pnl_pct"] = stamp
    return pos


def test_enforce_emits_sol_macro_bail(monkeypatch):
    monkeypatch.setenv("SOL_BAIL_MODE", "enforce")
    pm = PerBotPositionManager(_cfg())
    _armed(pm)
    d = pm.tick(token="TOK", current_price=0.97, now=60.0)   # -3% red pre-TP1
    assert any(x.kind == "SOL_MACRO_BAIL" and x.sell_fraction == 1.0 for x in d)


def test_shadow_default_is_noop(monkeypatch):
    monkeypatch.setenv("SOL_BAIL_MODE", "shadow")
    pm = PerBotPositionManager(_cfg())
    _armed(pm)
    d = pm.tick(token="TOK", current_price=0.97, now=60.0)
    assert not any(x.kind == "SOL_MACRO_BAIL" for x in d)     # byte-identical default


def test_enforce_no_fire_without_stamp(monkeypatch):
    # no macro-down stamp -> gate must stay silent even under enforce
    monkeypatch.setenv("SOL_BAIL_MODE", "enforce")
    pm = PerBotPositionManager(_cfg())
    pm.open_position("TOK", 1.0, 20.0, entry_time=0.0)
    d = pm.tick(token="TOK", current_price=0.97, now=60.0)
    assert not any(x.kind == "SOL_MACRO_BAIL" for x in d)


def test_enforce_winner_safe_when_green(monkeypatch):
    # stamp present but the leg is green NOW -> pnl<1 guard must veto the bail
    monkeypatch.setenv("SOL_BAIL_MODE", "enforce")
    pm = PerBotPositionManager(_cfg())
    _armed(pm)
    d = pm.tick(token="TOK", current_price=1.05, now=60.0)   # +5% green
    assert not any(x.kind == "SOL_MACRO_BAIL" for x in d)


def test_enforce_fires_once(monkeypatch):
    monkeypatch.setenv("SOL_BAIL_MODE", "enforce")
    pm = PerBotPositionManager(_cfg())
    _armed(pm)
    d1 = pm.tick(token="TOK", current_price=0.97, now=60.0)
    assert any(x.kind == "SOL_MACRO_BAIL" for x in d1)
    # sol_bail_enforced latch set -> a second tick must not re-emit
    pos = pm.get_position("TOK")
    if pos is not None:
        d2 = pm.tick(token="TOK", current_price=0.96, now=120.0)
        assert not any(x.kind == "SOL_MACRO_BAIL" for x in d2)
