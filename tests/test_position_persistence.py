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


def test_state_blob_and_last_close_times_persist():
    # 2026-05-27 audit: state_blob (slip_pct) and _last_close_time (reentry cooldown)
    # must survive a restart, else sells use wrong slippage + cooldown is dead.
    pm = PerBotPositionManager(_cfg(max_concurrent_positions=3))
    p = pm.open_position("Z", 0.001, 20.0, entry_time=10.0)
    p.state_blob["slip_pct"] = 0.42
    pm._last_close_time["OLDTOK"] = 12345.0
    snap = pm.to_state_list()
    times = pm.last_close_times_dict()
    pm2 = PerBotPositionManager(_cfg(max_concurrent_positions=3))
    pm2.load_state_list(snap)
    pm2.load_last_close_times(times)
    assert pm2.get_position("Z").state_blob.get("slip_pct") == 0.42, "slip_pct lost on restart"
    assert pm2.in_reentry_cooldown("OLDTOK", now=12345.0 + 100, cooldown_secs=3600) is True, "cooldown lost"


def test_entry_price_zero_is_skipped_on_load():
    pm = PerBotPositionManager(_cfg())
    n = pm.load_state_list([{"token": "BAD", "entry_price": 0.0, "size_usd": 20.0},
                            {"token": "OK", "entry_price": 1.0, "size_usd": 20.0}])
    assert n == 1 and pm.get_position("BAD") is None and pm.get_position("OK") is not None


def test_load_empty_is_clean_slate():
    pm = PerBotPositionManager(_cfg())
    pm.open_position("X", 0.001, 20.0, entry_time=1.0)
    assert pm.load_state_list([]) == 0 and pm.open_count == 0
    assert pm.load_state_list(None) == 0
