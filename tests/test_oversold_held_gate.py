"""oversold_held_blocks — the positive selector that survived the held-out +
leave-one-out backtest (rsi_15m<=44 AND dev_pct_remaining>=10). FAIL-CLOSED:
block unless BOTH signals present and passing."""
from core.bot_evaluator import oversold_held_blocks as ohb


def test_selected_not_blocked():
    blocked, why = ohb(40.0, 25.0, rsi_max=44, dev_min=10)
    assert blocked is False
    assert "keep" in why


def test_boundary_inclusive():
    assert ohb(44.0, 10.0, rsi_max=44, dev_min=10)[0] is False  # <= and >= inclusive
    assert ohb(44.1, 10.0, rsi_max=44, dev_min=10)[0] is True   # rsi just over
    assert ohb(44.0, 9.9, rsi_max=44, dev_min=10)[0] is True    # dev just under


def test_not_oversold_blocked():
    blocked, _ = ohb(60.0, 25.0, rsi_max=44, dev_min=10)
    assert blocked is True


def test_dev_dumped_blocked():
    blocked, _ = ohb(40.0, 5.0, rsi_max=44, dev_min=10)
    assert blocked is True


def test_missing_either_fails_closed():
    assert ohb(None, 25.0, rsi_max=44, dev_min=10)[0] is True   # rsi missing -> blocked
    assert ohb(40.0, None, rsi_max=44, dev_min=10)[0] is True   # dev missing -> blocked
    assert ohb(None, None, rsi_max=44, dev_min=10)[0] is True


def test_nan_bool_garbage_fail_closed():
    assert ohb(float("nan"), 25.0, rsi_max=44, dev_min=10)[0] is True
    assert ohb(True, 25.0, rsi_max=44, dev_min=10)[0] is True       # bool != number
    assert ohb("x", 25.0, rsi_max=44, dev_min=10)[0] is True


def test_default_thresholds_44_10():
    assert ohb(40.0, 15.0)[0] is False   # passes default rsi<=44 & dev>=10
    assert ohb(50.0, 15.0)[0] is True
    assert ohb(40.0, 5.0)[0] is True
