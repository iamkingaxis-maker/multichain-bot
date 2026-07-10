# tests/test_moonbag_exit.py — house-money MOONBAG exit shape (2026-07-10 A/B)
"""TP2 keeps moonbag_fraction of the ORIGINAL position open (sells only
remainder-after-TP1 minus the moonbag); the moonbag then closes in full at the
breakeven floor (MOONBAG_FLOOR) or the wide peak-trail (MOONBAG_TRAIL). While
it rides, the tight post-TP1 trail is suppressed. Worst case gives back
~nothing (profits banked, floor at entry); winner-kill ~0 by construction.
Moonbag off (default 0.0) = every existing bot byte-identical."""
import dataclasses
import json
import pathlib

import pytest

from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _pm(**over):
    base = dict(bot_id="t", display_name="t", tp1_pct=6.0, tp1_sell_fraction=0.75,
                tp2_pct=12.0, tp2_sell_fraction=0.25, trail_pp=2.0,
                hard_stop_pct=-12.0, moonbag_fraction=0.10,
                moonbag_floor_pct=0.0, moonbag_trail_pp=20.0)
    base.update(over)
    return PerBotPositionManager(BotConfig(**base))


def _open(pm, price=1.0):
    pm.open_position(token="TOK", entry_price=price, size_usd=25.0,
                     entry_time=900.0, address="mintTOK", pair_address="pairTOK")
    return pm.get_position("TOK")


def _tick(pm, pnl_pct, now=1000.0):
    px = 1.0 * (1 + pnl_pct / 100.0)
    return pm.tick("TOK", px, now)


def _execute(pm, decisions, pnl_pct, now=1000.0):
    """Book each decision like _execute_bot_sell does (frac of ORIGINAL,
    close_position clamps to remaining)."""
    px = 1.0 * (1 + pnl_pct / 100.0)
    results = []
    for d in decisions:
        if d.sell_fraction <= 0:
            continue
        results.append(pm.close_position("TOK", px, now, d.reason,
                                         sell_fraction=d.sell_fraction))
    return results


class TestMoonbagTP2:
    def test_tp2_leaves_moonbag_open(self):
        pm = _pm()
        p = _open(pm)
        d1 = _tick(pm, 6.5)
        assert [x.kind for x in d1] == ["TP1"]
        assert d1[0].sell_fraction == 0.75
        _execute(pm, d1, 6.5)
        assert p.remaining_fraction == pytest.approx(0.25)

        d2 = _tick(pm, 12.5)
        assert [x.kind for x in d2] == ["TP2"]
        # sells remainder-after-TP1 (0.25) minus the moonbag (0.10) = 0.15
        assert d2[0].sell_fraction == pytest.approx(0.15)
        assert "moonbag" in d2[0].reason
        r = _execute(pm, d2, 12.5)
        assert r[0].fully_closed is False
        # position STAYS OPEN holding exactly the moonbag
        assert pm.get_position("TOK") is p
        assert p.remaining_fraction == pytest.approx(0.10)
        assert p.tp2_hit is True
        assert p.state_blob.get("moonbag_active") is True

    def test_tp1_tp2_same_tick_gap_up(self):
        pm = _pm()
        p = _open(pm)
        d = _tick(pm, 14.0)                      # gap through both tiers
        assert [x.kind for x in d] == ["TP1", "TP2"]
        assert d[1].sell_fraction == pytest.approx(0.15)
        _execute(pm, d, 14.0)
        assert p.remaining_fraction == pytest.approx(0.10)

    def test_moonbag_never_enlarges_a_smaller_tp2(self):
        # tp2 deliberately smaller than remainder-minus-moonbag: keep tp2's size
        pm = _pm(tp1_sell_fraction=0.5, tp2_sell_fraction=0.10)
        _open(pm)
        _execute(pm, _tick(pm, 6.5), 6.5)
        d = _tick(pm, 12.5)
        assert d[0].sell_fraction == pytest.approx(0.10)  # min(0.10, 0.5-0.1=0.4)


def _to_moonbag(pm, tp2_pnl=12.5):
    """Drive a fresh position through TP1+TP2 into the moonbag phase."""
    p = _open(pm)
    _execute(pm, _tick(pm, 6.5), 6.5)
    _execute(pm, _tick(pm, tp2_pnl), tp2_pnl)
    assert p.remaining_fraction == pytest.approx(0.10)
    return p


class TestMoonbagExits:
    def test_floor_exit_at_breakeven(self):
        pm = _pm()
        _to_moonbag(pm)
        assert _tick(pm, 3.0) == []              # above floor: rides
        d = _tick(pm, -0.5)                      # pnl <= 0 -> floor
        assert [x.kind for x in d] == ["MOONBAG_FLOOR"]
        assert d[0].sell_fraction == 1.0
        r = _execute(pm, d, -0.5)
        assert r[0].fully_closed is True
        # clamped to the remaining moonbag slice only
        assert r[0].sell_fraction == pytest.approx(0.10)
        assert pm.get_position("TOK") is None

    def test_trail_exit_from_peak(self):
        pm = _pm()
        _to_moonbag(pm)
        assert _tick(pm, 40.0) == []             # runner runs (peak 40)
        assert _tick(pm, 21.0) == []             # 40-19: inside the 20pp trail
        d = _tick(pm, 19.5)                      # 40-20.5 -> trail fires
        assert [x.kind for x in d] == ["MOONBAG_TRAIL"]
        r = _execute(pm, d, 19.5)
        assert r[0].fully_closed is True

    def test_tight_trail_suppressed_while_moonbag_rides(self):
        pm = _pm()
        _to_moonbag(pm)
        _tick(pm, 30.0)                          # peak 30
        # peak-2pp would fire the tight POST_TP1_TRAIL; moonbag suppresses it
        assert _tick(pm, 27.0) == []
        assert _tick(pm, 12.0) == []             # still above floor + trail line
        d = _tick(pm, 9.5)                       # 30-20.5 -> the WIDE trail fires
        assert [x.kind for x in d] == ["MOONBAG_TRAIL"]

    def test_hard_stop_backstops_a_gap_through(self):
        pm = _pm()
        _to_moonbag(pm)
        d = _tick(pm, -13.0)                     # gap straight through the floor
        assert [x.kind for x in d] == ["HARD_STOP"]

    def test_floor_only_when_trail_unset(self):
        pm = _pm(moonbag_trail_pp=None)
        _to_moonbag(pm)
        _tick(pm, 40.0)
        assert _tick(pm, 5.0) == []              # 35pp off peak: no trail configured
        d = _tick(pm, -0.1)
        assert [x.kind for x in d] == ["MOONBAG_FLOOR"]


class TestMoonbagStatePersistence:
    def test_round_trip_through_state_list(self):
        pm = _pm()
        _to_moonbag(pm)
        _tick(pm, 40.0)                          # peak 40 stamped pre-restart
        state = pm.to_state_list()
        pm2 = _pm()
        assert pm2.load_state_list(state) == 1
        p2 = pm2.get_position("TOK")
        assert p2.tp1_hit is True and p2.tp2_hit is True
        assert p2.remaining_fraction == pytest.approx(0.10)
        assert p2.peak_pnl_pct == pytest.approx(40.0)
        assert p2.state_blob.get("moonbag_active") is True
        # restored moonbag still managed: wide trail fires off the restored peak
        d = pm2.tick("TOK", 1.195, 1100.0)       # +19.5 <= 40-20
        assert [x.kind for x in d] == ["MOONBAG_TRAIL"]
        r = pm2.close_position("TOK", 1.195, 1100.0, d[0].reason,
                               sell_fraction=d[0].sell_fraction)
        assert r.fully_closed is True


class TestMoonbagOffByteIdentical:
    def test_default_config_has_moonbag_off(self):
        cfg = BotConfig(bot_id="x", display_name="x")
        assert cfg.moonbag_fraction == 0.0
        assert cfg.moonbag_floor_pct == 0.0
        assert cfg.moonbag_trail_pp is None

    def test_regression_pin_existing_ladder_unchanged(self):
        # moonbag off -> TP1 0.75, TP2 sells the FULL remainder, position closes
        pm = _pm(moonbag_fraction=0.0, moonbag_floor_pct=0.0, moonbag_trail_pp=None)
        p = _open(pm)
        d1 = _tick(pm, 6.5)
        assert [x.kind for x in d1] == ["TP1"] and d1[0].sell_fraction == 0.75
        assert "moonbag" not in d1[0].reason
        _execute(pm, d1, 6.5)
        d2 = _tick(pm, 12.5)
        assert [x.kind for x in d2] == ["TP2"] and d2[0].sell_fraction == 0.25
        assert "moonbag" not in d2[0].reason
        r = _execute(pm, d2, 12.5)
        assert r[0].fully_closed is True         # TP2 stays a full-out door
        assert pm.get_position("TOK") is None
        assert p.state_blob.get("moonbag_active") is None

    def test_regression_pin_post_tp1_trail_unchanged(self):
        pm = _pm(moonbag_fraction=0.0, moonbag_trail_pp=None)
        _open(pm)
        _execute(pm, _tick(pm, 6.5), 6.5)
        _tick(pm, 8.0)                           # peak 8
        d = _tick(pm, 5.9)                       # 8-2.1 -> tight 2pp trail fires
        assert [x.kind for x in d] == ["POST_TP1_TRAIL"]

    def test_moonbag_plus_tp1_over_one_rejected(self):
        with pytest.raises(ValueError):
            BotConfig(bot_id="x", display_name="x",
                      tp1_sell_fraction=0.95, moonbag_fraction=0.10)


def test_jersey_integrity():
    """badday_young_moonbag_ab = byte-identical twin of badday_young_rt_paper
    except bot_id/display_name/exclusion_pool + the three moonbag fields."""
    mb = BotConfig(**json.loads(pathlib.Path(
        "config/bots/badday_young_moonbag_ab.json").read_text()))
    tw = BotConfig(**json.loads(pathlib.Path(
        "config/bots/badday_young_rt_paper.json").read_text()))
    assert mb.enabled is True
    assert mb.live_probe is False and tw.live_probe is False  # NEVER live
    assert mb.moonbag_fraction == 0.10
    assert mb.moonbag_floor_pct == 0.0
    assert mb.moonbag_trail_pp == 20.0
    assert mb.exclusion_pool == "badday_young_moonbag_ab"
    allowed_diffs = {"bot_id", "display_name", "exclusion_pool",
                     "moonbag_fraction", "moonbag_floor_pct", "moonbag_trail_pp"}
    diffs = {f.name for f in dataclasses.fields(BotConfig)
             if getattr(mb, f.name) != getattr(tw, f.name)}
    assert diffs <= allowed_diffs, f"unexpected twin drift: {diffs - allowed_diffs}"


def test_fast_trail_reprice_skips_moonbag_positions():
    # The fast-path trail-reprice (enforce) must never sell a post-TP2 moonbag
    # at the tight trail_pp — guarded by an explicit skip in _reprice_trail.
    import inspect
    import feeds.dip_scanner as ds
    src = inspect.getsource(ds.DipScanner._reprice_trail_exits)
    assert "moonbag_fraction" in src, \
        "_reprice_trail_exits lost its moonbag-phase exclusion"
