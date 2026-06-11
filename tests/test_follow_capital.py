"""Units for the smart-wallet capital pool + per-pool sweep (2026-06-11)."""
import os
import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _fresh_manager(tmp, pool=1000.0):
    os.environ["DATA_DIR"] = tmp
    os.environ["SMART_FOLLOW_POOL_USD"] = str(pool)
    os.environ.pop("SMART_FOLLOW_FLOOR_USD", None)
    import importlib
    import core.follow_capital as fc
    importlib.reload(fc)
    return fc.FollowCapitalManager()


def test_capacity_enforcement():
    with tempfile.TemporaryDirectory() as tmp:
        m = _fresh_manager(tmp, pool=200.0)
        assert m.can_open(100.0)
        m.record_open("tokA", 100.0)
        m.record_open("tokB", 100.0)
        assert not m.can_open(25.0)          # pool exhausted
        m.record_close("tokA", 1.0, +10.0)   # full close, +$10
        assert m.can_open(100.0)             # freed + grew


def test_sweep_banks_excess_above_floor():
    with tempfile.TemporaryDirectory() as tmp:
        m = _fresh_manager(tmp)
        m._last_sweep_check = -1e9           # force hourly gate open
        m.record_close("t1", 1.0, +12.0)     # equity 1012, floor 1000 -> sweep 12
        assert m.swept_total == 12.0
        assert m.equity() == 1000.0          # hot back to floor


def test_losses_block_sweep_until_earned_back():
    with tempfile.TemporaryDirectory() as tmp:
        m = _fresh_manager(tmp)
        m._last_sweep_check = -1e9
        m.record_close("t1", 1.0, -50.0)     # equity 950 — underwater
        assert m.swept_total == 0.0
        m._last_sweep_check = -1e9
        m.record_close("t2", 1.0, +40.0)     # 990 — still under floor
        assert m.swept_total == 0.0
        m._last_sweep_check = -1e9
        m.record_close("t3", 1.0, +30.0)     # 1020 -> sweep 20
        assert m.swept_total == 20.0


def test_state_survives_restart():
    with tempfile.TemporaryDirectory() as tmp:
        m = _fresh_manager(tmp)
        m._last_sweep_check = -1e9
        m.record_close("t1", 1.0, +25.0)     # sweeps 25
        m2 = _fresh_manager(tmp)             # reload from disk
        assert m2.swept_total == 25.0
        assert m2.equity() == 1000.0


def test_partial_close_frees_proportionally():
    with tempfile.TemporaryDirectory() as tmp:
        m = _fresh_manager(tmp, pool=200.0)
        m.record_open("tokA", 100.0)
        m.record_close("tokA", 0.75, +5.0)   # TP1 sells 75%
        assert abs(m.deployed() - 25.0) < 1e-6
