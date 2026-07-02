"""nf5m_toxic_zone_blocks — the 'weak bounce already fizzled' band (2026-07-02 mine)."""
from core.bot_evaluator import nf5m_toxic_zone_blocks as ntb


def test_toxic_band_blocks():
    assert ntb(0.0)[0] is True       # lo inclusive
    assert ntb(150.0)[0] is True
    assert ntb(299.9)[0] is True


def test_outside_band_allows():
    assert ntb(-50.0)[0] is False    # still capitulating -> fine
    assert ntb(-0.01)[0] is False
    assert ntb(300.0)[0] is False    # real inflow -> fine
    assert ntb(5000.0)[0] is False


def test_missing_fails_open():
    assert ntb(None)[0] is False
    assert ntb(float("nan"))[0] is False
    assert ntb("x")[0] is False
    assert ntb(True)[0] is False
