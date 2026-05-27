"""Position-book persistence (2026-05-27 fix): pm._positions must survive a
restart losslessly — the old trades-reconstruction orphaned post-TP1 positions."""
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _cfg(**kw):
    base = dict(bot_id="t", display_name="t", tp1_pct=5.0, tp1_sell_fraction=0.75,
                tp2_pct=10.0, tp2_sell_fraction=0.25, hard_stop_pct=-15.0)
    base.update(kw)
    return BotConfig(**base)


def test_roundtrip_preserves_all_fields():
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=3))
    pm.open_position("A", 0.001, 20.0, entry_time=100.0, address="addrA")
    pm.open_position("B", 0.002, 30.0, entry_time=200.0)
    snap = pm.to_state_list()
    pm2 = PerBotPositionManager(_cfg(max_concurrent_positions=3))
    n = pm2.load_state_list(snap)
    assert n == 2 and pm2.open_count == 2
    a = pm2.get_position("A")
    assert a.entry_price == 0.001 and a.size_usd == 20.0 and a.entry_time == 100.0
    assert a.address == "addrA" and a.remaining_fraction == 1.0


def test_post_tp1_partial_survives_restart_and_can_close():
    # The exact orphaning case: a position that hit TP1 (partial) then a restart.
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=3))
    pm.open_position("RUN", 1.0, 100.0, entry_time=0.0)
    # price hits TP1 → partial sell of 0.75
    decs = pm.tick("RUN", 1.06, now=60.0)
    assert any(d.kind == "TP1" for d in decs)
    res = pm.close_position("RUN", 1.06, exit_time=61.0, reason="TP1", sell_fraction=0.75)
    assert res.fully_closed is False
    p = pm.get_position("RUN")
    assert p.tp1_hit is True and abs(p.remaining_fraction - 0.25) < 1e-9

    # --- restart: serialize, load into a fresh manager ---
    snap = pm.to_state_list()
    pm2 = PerBotPositionManager(_cfg(max_concurrent_positions=3))
    pm2.load_state_list(snap)
    p2 = pm2.get_position("RUN")
    assert p2 is not None, "post-TP1 position was orphaned (the bug)"
    assert p2.tp1_hit is True and abs(p2.remaining_fraction - 0.25) < 1e-9

    # the leftover 0.25 can now terminally close (trail/stop) — no orphan, in_flight frees
    res2 = pm2.close_position("RUN", 1.20, exit_time=120.0, reason="trail", sell_fraction=1.0)
    assert res2.fully_closed is True
    assert pm2.open_count == 0


def test_load_empty_is_clean_slate():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("X", 0.001, 20.0, entry_time=1.0)
    assert pm.load_state_list([]) == 0 and pm.open_count == 0
    assert pm.load_state_list(None) == 0
