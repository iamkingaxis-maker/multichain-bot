# tests/test_rh_factory_racers.py
"""CANDIDATE FACTORY (2026-07-12) — pure gate helpers + factory racer wiring.

Provenance: scratchpad/_rh_candidate_factory.md; the four gates were mined
from the full-history sweep (rh_factory/) + the <1h winner-delta decode:
  dip_max_depth_pct  — winners buy MODERATE pullbacks; deep flush = loser profile
  min_buys_30s       — demand breadth (one $50 print is one actor, not demand)
  max_arc_pct        — losers buy LATE in the launch arc (px 12x+ off first print)
  require_pop_within_s — pop-retrace family (entry only near a detected pop)
All fields default OFF: every pre-factory racer is byte-identical.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import rh_paper_lane as mod  # noqa: E402
from rh_paper_lane import (  # noqa: E402
    LaneBot, PaperLane, ROSTER,
    dip_depth_block, buys_breadth_block, arc_pct, arc_block,
    pop_fired, pop_recency_block, POP_COOLDOWN_S,
)

NOW = 1_000_000.0


class TestDipDepthBlock:
    def test_off_when_none(self):
        assert dip_depth_block(-40.0, None) is None

    def test_no_reading_passes(self):
        # the dip trigger itself already blocks (no_dip); depth cap only
        # rules on a real reading
        assert dip_depth_block(None, -25.0) is None

    def test_blocks_deeper_than_cap(self):
        assert dip_depth_block(-30.0, -25.0) == "dip_too_deep"

    def test_boundary_and_shallower_pass(self):
        assert dip_depth_block(-25.0, -25.0) is None
        assert dip_depth_block(-12.0, -25.0) is None


class TestBuysBreadthBlock:
    def test_off_when_none(self):
        assert buys_breadth_block(0, None) is None

    def test_blocks_below_floor(self):
        assert buys_breadth_block(2, 3) == "demand_breadth"

    def test_passes_at_floor(self):
        assert buys_breadth_block(3, 3) is None


class TestArcGate:
    def test_arc_pct_math(self):
        assert arc_pct(1.0, 3.0) == 200.0

    def test_arc_pct_none_cases(self):
        assert arc_pct(None, 2.0) is None
        assert arc_pct(0.0, 2.0) is None
        assert arc_pct(1.0, None) is None

    def test_block_late_arc(self):
        assert arc_block(1500.0, 1000.0) == "arc_late"

    def test_fail_open_no_reading_and_off(self):
        assert arc_block(None, 1000.0) is None
        assert arc_block(1500.0, None) is None
        assert arc_block(900.0, 1000.0) is None


class TestPopDetector:
    def test_too_few_points(self):
        assert pop_fired([(NOW - 10, 1.0), (NOW - 5, 1.4)], NOW) is None

    def test_no_pop(self):
        s = [(NOW - 300, 1.0), (NOW - 200, 1.1), (NOW - 10, 1.2)]
        assert pop_fired(s, NOW) is None

    def test_pop_detected_with_magnitude(self):
        s = [(NOW - 300, 1.0), (NOW - 200, 1.05), (NOW - 10, 1.5)]
        mag = pop_fired(s, NOW)
        assert mag is not None and abs(mag - 50.0) < 1e-9

    def test_old_points_outside_window_ignored(self):
        s = [(NOW - 5000, 0.5), (NOW - 300, 1.0), (NOW - 200, 1.05),
             (NOW - 10, 1.2)]
        assert pop_fired(s, NOW) is None   # 0.5 low is stale history


class TestPopRecencyBlock:
    def test_off_when_none(self):
        assert pop_recency_block(None, NOW, None) is None

    def test_no_pop_blocks(self):
        assert pop_recency_block(None, NOW, 1800.0) == "no_recent_pop"

    def test_recent_pop_passes(self):
        assert pop_recency_block(NOW - 600, NOW, 1800.0) is None

    def test_old_pop_blocks(self):
        assert pop_recency_block(NOW - 3600, NOW, 1800.0) == "no_recent_pop"


class TestNotePx:
    def _lane(self):
        class FakeFeed:
            watch = {}
            eth_price = 2000.0
        return PaperLane(FakeFeed(), executor=object(),
                         bots=(LaneBot(bot_id="x"),))

    def test_first_px_set_once(self):
        lane = self._lane()
        lane._note_px("0xp", NOW, 2.0)
        lane._note_px("0xp", NOW + 1, 3.0)
        assert lane.first_px["0xp"] == 2.0

    def test_pop_book_records_and_cooldown(self):
        lane = self._lane()
        lane._note_px("0xp", NOW - 20, 1.0)
        lane._note_px("0xp", NOW - 10, 1.05)
        lane._note_px("0xp", NOW, 1.5)         # pop fires (+50% off min)
        assert "0xp" in lane.pop_book
        ts0, mag0 = lane.pop_book["0xp"]
        assert ts0 == NOW and mag0 == 50.0
        # within cooldown: a second pop does NOT re-stamp
        lane._note_px("0xp", NOW + 60, 2.5)
        assert lane.pop_book["0xp"][0] == NOW
        # past cooldown, with a fresh 3-point window forming a new pop
        t2 = NOW + POP_COOLDOWN_S + 61
        lane._note_px("0xp", t2 - 20, 2.0)
        lane._note_px("0xp", t2 - 10, 2.1)
        lane._note_px("0xp", t2, 3.0)          # +50% off the window min
        assert lane.pop_book["0xp"][0] == t2

    def test_series_capped(self):
        lane = self._lane()
        for i in range(700):
            lane._note_px("0xp", NOW + i, 1.0 + 0.0001 * i)
        assert len(lane.prices["0xp"]) <= 600


class TestFactoryDefaultsInert:
    def test_all_prefactory_racers_have_gates_off(self):
        for b in ROSTER:
            if (b.exclusion_group or "") == "factory":
                continue
            assert b.dip_max_depth_pct is None
            assert b.min_buys_30s is None
            assert b.max_arc_pct is None
            assert b.require_pop_within_s is None


class TestFactoryRoster:
    def _by_id(self):
        return {b.bot_id: b for b in ROSTER}

    def test_five_factory_racers_in_group(self):
        fac = [b for b in ROSTER if b.exclusion_group == "factory"]
        assert {b.bot_id for b in fac} == {
            "rh_f_pullback", "rh_f_arc_scalp", "rh_f_popret",
            "rh_f_reload24", "rh_f_reload_mid"}

    def test_pullback_spec(self):
        b = self._by_id()["rh_f_pullback"]
        assert (b.dip_trigger_pct, b.dip_max_depth_pct) == (-6.0, -12.0)
        assert (b.min_pool_age_h, b.max_pool_age_h) == (0.0, 10.0 / 60.0)
        assert b.min_session_vol_usd == 4_800.0
        assert b.max_arc_pct == 300.0
        assert (b.tp1_pct, b.tp1_sell_fraction, b.tp2_pct,
                b.tp2_sell_fraction, b.trail_pp) == (6.0, 0.50, 16.0,
                                                     0.30, 10.0)

    def test_arc_scalp_spec(self):
        b = self._by_id()["rh_f_arc_scalp"]
        assert (b.dip_trigger_pct, b.dip_max_depth_pct) == (-6.0, -25.0)
        assert b.max_arc_pct == 300.0
        # scalp ladder = LaneBot defaults (the control's exits)
        assert (b.tp1_pct, b.tp1_sell_fraction, b.tp2_pct) == (6.0, 0.75, 12.0)
        assert b.trail_pp is None

    def test_popret_spec(self):
        b = self._by_id()["rh_f_popret"]
        assert b.require_pop_within_s == 1800.0
        assert b.dip_trigger_pct == -12.0 and b.dip_max_depth_pct is None
        assert b.min_session_vol_usd == 480.0
        assert b.demand_min_buy_usd == 50.0

    def test_reload_specs(self):
        r24 = self._by_id()["rh_f_reload24"]
        assert r24.min_pool_age_h == 24.0 and r24.max_pool_age_h is None
        assert r24.min_session_vol_usd == 16_000.0
        assert r24.dip_trigger_pct == -25.0
        assert r24.regime_hours is False        # cell-verbatim (see roster)
        rm = self._by_id()["rh_f_reload_mid"]
        assert (rm.min_pool_age_h, rm.max_pool_age_h) == (6.0, 24.0)
        assert rm.dip_trigger_pct == -25.0

    def test_all_factory_bot_configs_construct(self):
        for b in ROSTER:
            if b.exclusion_group == "factory":
                assert b.bot_config().bot_id == b.bot_id

    def test_cooldown_mirrors_mine(self):
        for b in ROSTER:
            if b.exclusion_group == "factory":
                assert b.reentry_cooldown_s == 600.0


class TestFactoryEntryRouting:
    """A fresh popped pool routes to rh_f_popret while the proven-volume
    factory racers block for their stated reasons (shared-facts contract)."""

    def _lane(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))
        monkeypatch.setattr(mod, "LEDGER", str(tmp_path / "ledger.jsonl"))
        monkeypatch.setattr(mod, "POSTEXIT_PENDING",
                            str(tmp_path / "pe.jsonl"))

        class FakeQuote:
            def __init__(self, amount_in, amount_out):
                self.amount_in, self.amount_out = amount_in, amount_out
                self.fee = 10000

        class FakeExecutor:
            def quote_sell(self, token, amount):
                return FakeQuote(amount, int(25.0 / 2000.0 * 1e18 * 0.97))

            def quote_buy(self, token, wei):
                return FakeQuote(wei, 10 ** 21)

            def token_decimals(self, token):
                return 18

        class FakeFeed:
            watch = {"0xp1": {"sym": "T", "liq": 50_000.0}}
            eth_price = 2000.0

        lane = PaperLane(FakeFeed(), executor=FakeExecutor(),
                         registry={"0xp1": {"token": "0xtok"}}, bots=ROSTER)
        lane.honeypot["0xtok"] = {"sellable": True}
        return lane

    def test_popret_enters_on_pop_then_dip(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        lane.prices["0xp1"] = [(NOW - 300, 1.0), (NOW - 200, 1.3),
                               (NOW - 10, 1.0)]          # -23% off high
        lane.tape["0xp1"] = [
            {"kind": "buy", "volume_usd": 40, "_epoch": NOW - 20},
            {"kind": "buy", "volume_usd": 25, "_epoch": NOW - 10},
            {"kind": "sell", "volume_usd": 5, "_epoch": NOW - 5}]
        lane.pop_book["0xp1"] = (NOW - 300, 42.0)        # pop 5 min ago
        lane.cum_vol["0xp1"] = 1_000.0                   # > popret's 480
        lane._consider_entries(NOW)
        st = lane.state["rh_f_popret"]
        assert "0xp1" in st.pos_meta
        # proven-volume racers blocked: $1k < their $4.8k floor
        assert "thin_session_vol" in lane.state["rh_f_arc_scalp"].block_hist
        # pullback additionally sees a TOO-DEEP dip (-23 < its -12 cap)
        assert "dip_too_deep" in lane.state["rh_f_pullback"].block_hist

    def test_no_pop_blocks_popret(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        lane.prices["0xp1"] = [(NOW - 300, 1.0), (NOW - 200, 1.3),
                               (NOW - 10, 1.0)]
        lane.tape["0xp1"] = [
            {"kind": "buy", "volume_usd": 60, "_epoch": NOW - 10}]
        lane.cum_vol["0xp1"] = 1_000.0
        lane._consider_entries(NOW)
        assert "0xp1" not in lane.state["rh_f_popret"].pos_meta
        assert "no_recent_pop" in lane.state["rh_f_popret"].block_hist

    def test_cum_vol_persists_roundtrip(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        lane.cum_vol["0xp1"] = 1234.56
        lane.first_px["0xp1"] = 2.5
        lane.save_state()
        lane2 = self._lane(tmp_path, monkeypatch)
        lane2.restore_state()
        assert lane2.cum_vol["0xp1"] == 1234.56
        assert lane2.first_px["0xp1"] == 2.5
