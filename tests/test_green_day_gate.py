"""green_day_blocks — regime gate from the 2026-07-01 measured green-day study.
Rules: sol_h1>1 block; sol_h6<=0 pass; mild green needs pc_h6<=-25 capitulation;
rip (>1.5) needs oversold_held. FAIL-OPEN on missing sol fields."""
from core.bot_evaluator import green_day_blocks as gdb


def test_home_turf_passes():
    blocked, why = gdb(-1.2, 0.1, +5.0, 60.0, 0.0)
    assert blocked is False
    assert "home turf" in why


def test_missing_sol_fails_open():
    assert gdb(None, None, -30.0, 40.0, 20.0)[0] is False
    assert gdb("x", None, None, None, None)[0] is False   # garbage -> missing


def test_h1_spike_blocks_regardless():
    blocked, why = gdb(-0.5, 1.5, -40.0, 30.0, 50.0)  # even deep capit + osh
    assert blocked is True
    assert "h1_spike" in why


def test_mild_green_requires_capitulation():
    assert gdb(1.0, 0.2, -30.0, 60.0, 0.0)[0] is False   # capit -> pass
    assert gdb(1.0, 0.2, -10.0, 60.0, 0.0)[0] is True    # retrace -> block
    assert gdb(1.0, 0.2, None, 60.0, 0.0)[0] is True     # pc_h6 missing -> block
    assert gdb(1.5, 0.2, -25.0, 60.0, 0.0)[0] is False   # boundary inclusive


def test_rip_requires_oversold_held():
    assert gdb(2.0, 0.5, -30.0, 40.0, 20.0)[0] is False  # osh -> pass
    assert gdb(2.0, 0.5, -30.0, 60.0, 20.0)[0] is True   # not oversold -> block
    assert gdb(2.0, 0.5, -30.0, 40.0, 5.0)[0] is True    # dev dumped -> block
    b, why = gdb(2.0, 0.5, -60.0, None, None)            # capit alone NOT enough on rip
    assert b is True and "rip_not_oversold" in why


def test_never_raises_on_garbage():
    assert isinstance(gdb(object(), [], {}, "a", b"z")[0], bool)
