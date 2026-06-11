"""Units for the badday microcap admission lane (2026-06-11)."""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.badday_lane import in_envelope, keep_token, buy_gate_skip


def test_envelope_states():
    # flush state qualifies
    assert in_envelope(100_000, 12, 20_000, -25)
    # momo state qualifies
    assert in_envelope(100_000, 12, 20_000, +40)
    # the dead middle does not
    assert not in_envelope(100_000, 12, 20_000, -5)
    # rug-screen age: under 6h never qualifies (catastrophes are young)
    assert not in_envelope(100_000, 3, 20_000, -25)
    # mcap bounds
    assert not in_envelope(30_000, 12, 20_000, -25)
    assert not in_envelope(600_000, 12, 20_000, -25)
    # liquidity floor
    assert not in_envelope(100_000, 12, 5_000, -25)
    # garbage is never admitted
    assert not in_envelope(None, None, None, None)


def test_keep_token_only_below_fleet_floor():
    assert keep_token(100_000, 20_000, 12, -25, 500_000)
    # above the fleet floor -> normal pipeline, lane stays out of it
    assert not keep_token(600_000, 20_000, 12, -25, 500_000)


def test_lane_off_kills_everything():
    os.environ["BADDAY_LANE"] = "off"
    try:
        assert not keep_token(100_000, 20_000, 12, -25, 500_000)
        assert not buy_gate_skip(True, False)
    finally:
        os.environ.pop("BADDAY_LANE", None)


def test_containment_matrix():
    # sub-floor + no mandate -> SKIP (controls/production protected)
    assert buy_gate_skip(True, False)
    # badday/young/lmp mandate -> trade
    assert not buy_gate_skip(True, True)
    # user-curated -> trade
    assert not buy_gate_skip(True, False, is_user_watch=True)
    # above floor -> never skipped
    assert not buy_gate_skip(False, False)
