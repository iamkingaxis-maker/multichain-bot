"""post_pump_corpse entry-gate helper (2026-06-27) — entry-path port of the
fleet-enforced filter that leaks on the fast/live path. Block on (a) pc_h1>=500
OR (b) pc_h24>=200 AND buys_per_min_recent<=2. Fail-open on missing/NaN."""
from core.bot_evaluator import post_pump_corpse_blocks as ppc


def test_blocks_on_extreme_h1_pump():
    block, why = ppc(pc_h1=3397.0, pc_h24=421.0, buys_per_min_recent=1)
    assert block is True
    assert "pc_h1" in why


def test_blocks_on_pumped_h24_and_calm():
    block, why = ppc(pc_h1=10.0, pc_h24=250.0, buys_per_min_recent=1)
    assert block is True
    assert "post-pump corpse" in why


def test_passes_when_pumped_h24_but_still_active():
    # pc_h24 pumped but buyers still active (bpm>2) -> not a corpse
    assert ppc(pc_h1=10.0, pc_h24=300.0, buys_per_min_recent=8)[0] is False


def test_passes_normal_dip():
    assert ppc(pc_h1=-20.0, pc_h24=-10.0, buys_per_min_recent=5) == (False, "")


def test_boundary_h1_500_blocks():
    assert ppc(pc_h1=500.0, pc_h24=0.0, buys_per_min_recent=10)[0] is True


def test_boundary_h24_200_bpm_2_blocks():
    assert ppc(pc_h1=0.0, pc_h24=200.0, buys_per_min_recent=2)[0] is True


def test_just_under_thresholds_pass():
    assert ppc(pc_h1=499.0, pc_h24=199.0, buys_per_min_recent=2)[0] is False


def test_fail_open_on_missing():
    assert ppc(None, None, None) == (False, "")
    # (b) needs bpm; missing bpm with pumped h24 must NOT block
    assert ppc(pc_h1=10.0, pc_h24=300.0, buys_per_min_recent=None)[0] is False


def test_fail_open_on_nan():
    assert ppc(float("nan"), float("nan"), float("nan")) == (False, "")


def test_fail_open_on_garbage():
    assert ppc("x", "y", "z") == (False, "")
