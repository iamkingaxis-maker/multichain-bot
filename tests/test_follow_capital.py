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


# ── phantom guard + reconcile (2026-06-13, RAGEGUY +$242k bug) ──────────────────
def test_record_close_rejects_phantom_pnl():
    with tempfile.TemporaryDirectory() as tmp:
        m = _fresh_manager(tmp, pool=1000.0)
        m.record_open("rage", 50.0)
        # RAGEGUY-class glitch: +$242,668 at +485,336% — must NOT book
        m.record_close("rage", 1.0, +242668.0, pnl_pct=485336.0)
        assert m.realized == 0.0              # phantom rejected, nothing booked
        assert "rage" not in m._open          # but the slot IS released (it closed)
        # a real win still books
        m.record_open("good", 50.0)
        m.record_close("good", 1.0, +12.0, pnl_pct=24.0)
        assert abs(m.realized - 12.0) < 1e-9


def test_reconcile_from_ledger_corrects_phantom_inflation():
    with tempfile.TemporaryDirectory() as tmp:
        m = _fresh_manager(tmp, pool=1000.0)
        m.epoch = "2026-06-11T00:00:00+00:00"
        m.realized = 242445.29      # corrupted state (phantom already booked + persisted)
        m.swept_total = 242445.24
        trades = [
            {"type": "buy",  "strategy": "smart_follow", "address": "a1", "time": "2026-06-12T01:00:00"},
            {"type": "sell", "strategy": "smart_follow", "address": "a1", "pnl": -20.0, "pnl_pct": -40.0, "time": "2026-06-12T02:00:00"},
            {"type": "sell", "strategy": "smart_follow", "address": "a2", "pnl": +11.0, "pnl_pct": 22.0,  "time": "2026-06-12T03:00:00"},
            # the RAGEGUY phantom (excluded by pnl_pct>200)
            {"type": "sell", "strategy": "smart_follow", "address": "rage", "pnl": +242668.0, "pnl_pct": 485336.0, "time": "2026-06-13T15:00:00"},
            # pre-epoch (excluded)
            {"type": "sell", "strategy": "smart_follow", "address": "old", "pnl": +5.0, "pnl_pct": 10.0, "time": "2026-06-10T01:00:00"},
            # other strategy (excluded)
            {"type": "sell", "strategy": "scanner", "address": "x", "pnl": +99.0, "pnl_pct": 50.0, "time": "2026-06-12T05:00:00"},
        ]
        assert m.reconcile_from_ledger(trades) is True
        assert abs(m.realized - (-9.0)) < 1e-6     # -20 + 11, phantom/pre-epoch/other excluded
        assert m.swept_total == 0.0
        # idempotent: a second run is a no-op
        assert m.reconcile_from_ledger(trades) is False
