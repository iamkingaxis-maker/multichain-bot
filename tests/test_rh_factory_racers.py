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
    SESSION_ANCHOR_MAX_AGE_H, demand_ok, merge_session_seed,
    session_anchor_block,
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
        # rh_demand_broad (2026-07-13 deep+demand decode) intentionally uses the
        # breadth gate (min_buys_30s) — a demand-QUALITY racer, not a factory
        # cell. Exempt it from the "factory gates off" invariant.
        BREADTH_GATE_USERS = {"rh_demand_broad"}
        for b in ROSTER:
            if (b.exclusion_group or "") == "factory":
                continue
            assert b.dip_max_depth_pct is None
            if b.bot_id not in BREADTH_GATE_USERS:
                assert b.min_buys_30s is None
            assert b.max_arc_pct is None
            assert b.require_pop_within_s is None
            # 2026-07-12 no-fire fix: both new fields default to the pre-fix
            # shared-gate behavior — every pre-factory racer byte-identical
            assert b.demand_net_required is True
            assert b.require_session_anchor is False


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
        # cell-verbatim d25 (b30 only, NO net leg) + creation-anchored facts
        assert b.demand_net_required is False
        assert b.require_session_anchor is True
        assert (b.tp1_pct, b.tp1_sell_fraction, b.tp2_pct,
                b.tp2_sell_fraction, b.trail_pp) == (6.0, 0.50, 16.0,
                                                     0.30, 10.0)

    def test_arc_scalp_spec(self):
        b = self._by_id()["rh_f_arc_scalp"]
        assert (b.dip_trigger_pct, b.dip_max_depth_pct) == (-6.0, -25.0)
        assert b.max_arc_pct == 300.0
        assert b.demand_net_required is False   # cell-verbatim d25
        assert b.require_session_anchor is True
        # scalp ladder = LaneBot defaults (the control's exits)
        assert (b.tp1_pct, b.tp1_sell_fraction, b.tp2_pct) == (6.0, 0.75, 12.0)
        assert b.trail_pp is None

    def test_popret_spec(self):
        b = self._by_id()["rh_f_popret"]
        assert b.require_pop_within_s == 1800.0
        assert b.dip_trigger_pct == -12.0 and b.dip_max_depth_pct is None
        assert b.min_session_vol_usd == 480.0
        assert b.demand_min_buy_usd == 50.0
        assert b.demand_net_required is True    # cell-verbatim d50n
        assert b.require_session_anchor is False  # no arc gate; $480 floor is
        #                                         reachable post-promotion

    def test_reload_specs(self):
        r24 = self._by_id()["rh_f_reload24"]
        assert r24.min_pool_age_h == 24.0 and r24.max_pool_age_h is None
        assert r24.min_session_vol_usd == 16_000.0
        assert r24.dip_trigger_pct == -25.0
        assert r24.regime_hours is False        # cell-verbatim (see roster)
        assert r24.demand_net_required is False  # cell-verbatim d25
        assert r24.require_session_anchor is False  # aged pools are never
        #                                        creation-anchored in practice
        rm = self._by_id()["rh_f_reload_mid"]
        assert (rm.min_pool_age_h, rm.max_pool_age_h) == (6.0, 24.0)
        assert rm.dip_trigger_pct == -25.0
        assert rm.demand_net_required is True   # cell-verbatim d50n

    def test_all_factory_bot_configs_construct(self):
        for b in ROSTER:
            if b.exclusion_group == "factory":
                assert b.bot_config().bot_id == b.bot_id

    def test_cooldown_mirrors_mine(self):
        for b in ROSTER:
            if b.exclusion_group == "factory":
                assert b.reentry_cooldown_s == 600.0


class TestDeepConsolidatedRacer:
    """rh_deep_consolidated (2026-07-12 deep-decode; _rh_deep_decode.md): the
    fusion of today's three GREEN racers. Deep-capitulation entry + proven-vol
    floor + 2-bite cap, and — decisively — the SHARED scalp exit verbatim, with
    demand left at the DEFAULT (never raised: raising it is what sank the RED
    demand_heavy). Pure composition of already-tested knobs; this spec pins it."""

    def _bot(self):
        return {b.bot_id: b for b in ROSTER}["rh_deep_consolidated"]

    def test_in_own_exclusion_group(self):
        b = self._bot()
        assert b.exclusion_group == "deepsynth"
        # sole member of the group (the label is intentional, per instruction)
        assert [x.bot_id for x in ROSTER
                if x.exclusion_group == "deepsynth"] == ["rh_deep_consolidated"]

    def test_entry_is_deep_capitulation_plus_proven_vol(self):
        b = self._bot()
        assert b.dip_trigger_pct == -25.0            # deep_only's capitulation
        assert b.min_session_vol_usd == 4_800.0      # f_arc_scalp's vol floor
        # anchor OFF -> proven-vol gate reads OBSERVED lifetime volume, a
        # conservative lower bound (rh_f_reload24 rationale), not untracked block
        assert b.require_session_anchor is False
        assert b.max_bites_per_token == 2            # bites2's re-entry cap

    def test_does_not_chase_demand_strength(self):
        # THE anti-chase lesson: demand floor stays at the lane default ($50);
        # raising it to $150 is precisely what made demand_heavy the worst racer.
        assert self._bot().demand_min_buy_usd == mod.DEMAND_MIN_BUY_USD

    def test_exit_is_the_shared_scalp_ladder(self):
        b = self._bot()
        # LaneBot defaults verbatim = the ladder all three greens share
        assert (b.tp1_pct, b.tp1_sell_fraction) == (6.0, 0.75)
        assert (b.tp2_pct, b.tp2_sell_fraction) == (12.0, 0.25)
        assert b.hard_stop_pct == -15.0
        assert b.trail_pp is None                    # BotConfig default 3pp
        assert b.moonbag_fraction == 0.0             # NO bleeding tail
        assert b.time_stop_minutes is None           # NO time box
        assert b.max_pool_age_h == mod.SCALP_MAX_POOL_AGE_H
        assert b.bot_config().bot_id == "rh_deep_consolidated"


class TestDeepBarbellRacer:
    """rh_deep_barbell (2026-07-12, scratchpad/_deep_exit_optimization.md): the
    EXIT-SHAPE deliverable. Deep-flush entry (dip<=-25) + a BARBELL exit
    (fast-harvest the bulk to lock the robust-green median + a house-money
    moonbag runner for the fat bounce tail that RISES with depth)."""

    def _bot(self):
        m = [x for x in ROSTER if x.bot_id == "rh_deep_barbell"]
        assert len(m) == 1
        return m[0]

    def test_in_roster_own_exclusion_group(self):
        b = self._bot()
        assert b.exclusion_group == "deepexit"          # distinct from "factory"
        assert [x.bot_id for x in ROSTER
                if x.exclusion_group == "deepexit"] == ["rh_deep_barbell", "rh_deep_barbell_capped"]

    def test_deep_flush_entry(self):
        b = self._bot()
        assert b.dip_trigger_pct == -25.0               # the deep cohort trigger
        assert b.min_liq_usd == 5_000.0                 # feed watch floor
        assert b.demand_min_buy_usd == 25.0             # study admission
        assert b.reentry_cooldown_s == 600.0

    def test_barbell_exit_shape(self):
        b = self._bot()
        # fast-harvest the BULK (locks the robust-green median)
        assert (b.tp1_pct, b.tp1_sell_fraction) == (5.0, 0.60)
        assert b.tp2_pct == 12.0
        # HOUSE-MONEY runner for the fat tail: breakeven floor, wide trail
        assert b.moonbag_fraction == 0.30
        assert b.moonbag_floor_pct == 0.0               # breakeven = house money
        assert b.moonbag_trail_pp == 12.0
        assert b.hard_stop_pct == -15.0
        # the harvested-fast fraction is the majority
        assert b.tp1_sell_fraction >= 0.5
        # config plumbs the moonbag through to the PM
        cfg = b.bot_config()
        assert (cfg.moonbag_fraction, cfg.moonbag_floor_pct,
                cfg.moonbag_trail_pp) == (0.30, 0.0, 12.0)


class TestStrengthTrailRacer:
    """rh_strength_trail (2026-07-12 winner-behavior decode;
    scratchpad/_rh_winner_behavior.md): the EXIT-shape lever the 93 audited RH
    winners run and our scalp lacks — all-out single-leg peak trail armed from a
    LOW +2% (not +6), 3pp give-back, hard stop -15. Entry = a verbatim
    rh_deep_only clone so the racer isolates the exit. Pins the roster spec + the
    exit-engine mechanics (arm gate, all-out, hard-stop precedence, ladder bypass)."""

    def _bot(self):
        m = [x for x in ROSTER if x.bot_id == "rh_strength_trail"]
        assert len(m) == 1
        return m[0]

    def test_in_roster_own_exclusion_group(self):
        b = self._bot()
        assert b.exclusion_group == "strengthexit"
        assert [x.bot_id for x in ROSTER
                if x.exclusion_group == "strengthexit"] == ["rh_strength_trail"]

    def test_entry_is_verbatim_deep_only_clone(self):
        # entry/universe must match rh_deep_only exactly (the isolate-the-exit
        # contract): deep -25 capitulation, scalp age ceiling, default demand.
        b = self._bot()
        deep = {x.bot_id: x for x in ROSTER}["rh_deep_only"]
        assert b.dip_trigger_pct == deep.dip_trigger_pct == -25.0
        assert b.max_pool_age_h == deep.max_pool_age_h == mod.SCALP_MAX_POOL_AGE_H
        assert b.min_liq_usd == deep.min_liq_usd
        assert b.demand_min_buy_usd == deep.demand_min_buy_usd == mod.DEMAND_MIN_BUY_USD
        assert b.entry_mode == deep.entry_mode
        assert b.max_bites_per_token == 2      # modest re-entry cap (fat-tail add)

    def test_strength_trail_exit_config(self):
        b = self._bot()
        assert b.strength_trail_exit is True
        assert b.strength_trail_arm_pct == 2.0
        assert b.strength_trail_gap_pp == 3.0
        assert b.hard_stop_pct == -15.0
        cfg = b.bot_config()
        assert cfg.strength_trail_exit is True
        assert (cfg.strength_trail_arm_pct, cfg.strength_trail_gap_pp) == (2.0, 3.0)
        assert cfg.bot_id == "rh_strength_trail"

    # ── exit-engine mechanics (the lever under test) ──────────────────────────
    def _pm(self):
        from core.per_bot_position_manager import PerBotPositionManager
        return PerBotPositionManager(self._bot().bot_config())

    def _open(self, pm, entry=1.0):
        pm.open_position("0xtok", entry_price=entry, size_usd=25.0,
                         entry_time=0.0, bypass_max_concurrent=True)

    def test_no_exit_before_arm(self):
        # below the +2% arm the position rides — a small dip is NOT a trail exit
        # (only the hard stop protects it, exactly like the winner sits through
        # the early wobble).
        pm = self._pm(); self._open(pm)
        # peak +1.5% (< arm), then dips to +0.5% (gave back 1pp): no exit
        assert pm.tick("0xtok", 1.015, now=10.0) == []
        assert pm.tick("0xtok", 1.005, now=20.0) == []

    def test_all_out_trail_after_arm(self):
        # arm at +2%, run to +8% peak, then give back 3pp -> ALL-OUT single leg
        pm = self._pm(); self._open(pm)
        assert pm.tick("0xtok", 1.03, now=10.0) == []       # +3% arms, still rising
        assert pm.tick("0xtok", 1.08, now=20.0) == []       # +8% new peak, no give-back
        out = pm.tick("0xtok", 1.049, now=30.0)             # +4.9% <= 8 - 3 -> fire
        assert len(out) == 1
        assert out[0].kind == "STRENGTH_TRAIL"
        assert out[0].sell_fraction == 1.0                  # all-out, single leg

    def test_no_partial_tp_ladder(self):
        # the +6 TP1 must NEVER fire on this racer — even sitting well past +6,
        # the only exit is the peak trail (a +6.5% peak that never gives back 3pp
        # produces NO sell), proving the fixed ladder is bypassed.
        pm = self._pm(); self._open(pm)
        for i, px in enumerate((1.02, 1.05, 1.065)):        # arm, then hold >+6
            out = pm.tick("0xtok", px, now=10.0 * (i + 1))
            assert out == [], f"unexpected exit at px={px}: {out}"
        pos = pm.get_position("0xtok")
        assert pos is not None and pos.tp1_hit is False     # ladder never engaged

    def test_hard_stop_precedes_strength_trail(self):
        # catastrophe first: a never-armed position that craters to -15 books the
        # HARD_STOP (the strength trail never armed, so it cannot mask the stop).
        pm = self._pm(); self._open(pm)
        out = pm.tick("0xtok", 0.85, now=10.0)
        assert len(out) == 1 and out[0].kind == "HARD_STOP"

    def test_gap_boundary(self):
        # exactly peak - gap fires (<=), one tick shallower does not.
        pm = self._pm(); self._open(pm)
        pm.tick("0xtok", 1.10, now=10.0)                    # +10% peak (armed)
        assert pm.tick("0xtok", 1.071, now=20.0) == []      # +7.1% > 10-3, hold
        out = pm.tick("0xtok", 1.07, now=30.0)              # +7.0% == 10-3, fire
        assert len(out) == 1 and out[0].kind == "STRENGTH_TRAIL"


class TestStrengthTrailInertForOtherBots:
    """The new exit branch must not change any bot that leaves it OFF (default)."""

    def test_default_config_bypasses_strength_trail(self):
        from core.per_bot_position_manager import PerBotPositionManager
        from core.bot_config import BotConfig
        pm = PerBotPositionManager(BotConfig(bot_id="ctrl", display_name="ctrl"))
        assert pm.config.strength_trail_exit is False
        pm.open_position("0xt", entry_price=1.0, size_usd=25.0, entry_time=0.0,
                         bypass_max_concurrent=True)
        # a control bot at +6 fires the NORMAL TP1 ladder, not a strength trail
        out = pm.tick("0xt", 1.06, now=10.0)
        assert len(out) == 1 and out[0].kind == "TP1"
        assert out[0].sell_fraction == pm.config.tp1_sell_fraction


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
        # anchor-requiring racers block EXPLICITLY on the unanchored pool
        # (2026-07-12 no-fire fix): untracked_session, never a silently-wrong
        # thin_session_vol/arc_late reading
        assert "untracked_session" in lane.state["rh_f_arc_scalp"].block_hist
        assert ("thin_session_vol"
                not in lane.state["rh_f_arc_scalp"].block_hist)
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


# ── 2026-07-12 factory NO-FIRE fix (scratchpad/_rh_factory_nofire.md) ────────
class TestSessionAnchorBlock:
    def test_not_required_never_blocks(self):
        assert session_anchor_block(False, False) is None
        assert session_anchor_block(True, False) is None

    def test_required_blocks_unanchored_only(self):
        assert session_anchor_block(False, True) == "untracked_session"
        assert session_anchor_block(True, True) is None


class TestDemandOk:
    def test_floor_always_applies(self):
        assert not demand_ok(20.0, 0.0, 25.0, net_required=False)
        assert not demand_ok(20.0, 0.0, 25.0, net_required=True)

    def test_net_leg_only_when_required(self):
        # buys above floor, sells heavier (the dip reality: ~26-30% of the
        # mined d25 triggers had net<=0)
        assert demand_ok(30.0, 100.0, 25.0, net_required=False)
        assert not demand_ok(30.0, 100.0, 25.0, net_required=True)

    def test_net_positive_passes_both(self):
        assert demand_ok(60.0, 10.0, 25.0, net_required=True)
        assert demand_ok(60.0, 10.0, 25.0, net_required=False)


class TestMergeSessionSeed:
    """Seed px is swap-derived ATOMIC-relative; the merge rescales onto the
    quote basis so the decimals factor cancels (ratio math)."""

    def _seed(self):
        # atomic-relative prints, ~1e-12 scale (18-6 decimals apart)
        return {"first_px": 1e-12, "cum_eth": 2.5,
                "rows": [(NOW - 500, "buy", 1.0, 1e-12),
                         (NOW - 400, "buy", 0.5, 2e-12),
                         (NOW - 90, "sell", 1.0, 4e-12)]}

    def test_rescale_cancels_decimals_factor(self):
        # median of the last-3 seed px = 2e-12; first live quote 8.0
        # -> scale = 4e12
        fp, pts, cum = merge_session_seed(self._seed(), 8.0, NOW)
        assert fp == 1e-12 * 4e12 == 4.0
        assert [p for _, p in pts] == [4.0, 8.0, 16.0]
        assert cum == 2.5

    def test_window_filter_drops_stale_points(self):
        seed = self._seed()
        seed["rows"].insert(0, (NOW - 5_000, "buy", 1.0, 1e-12))
        fp, pts, cum = merge_session_seed(seed, 8.0, NOW)
        assert all(NOW - ts <= 600.0 for ts, _ in pts)

    def test_unusable_seed_returns_none(self):
        assert merge_session_seed(None, 8.0, NOW) is None
        assert merge_session_seed({"rows": []}, 8.0, NOW) is None
        assert merge_session_seed(self._seed(), 0.0, NOW) is None
        assert merge_session_seed(
            {"first_px": 1.0, "cum_eth": 1.0, "rows": [(NOW, "buy", 1, 0.0)]},
            8.0, NOW) is None


class TestNotePxSessionSeed:
    def _lane(self, seed):
        class FakeFeed:
            watch = {"0xp": {"sym": "T", "liq": 50_000.0,
                             "session_seed": seed}}
            eth_price = 2000.0
        return PaperLane(FakeFeed(), executor=object(),
                         bots=(LaneBot(bot_id="x"),))

    def test_seed_applied_on_first_quote(self):
        seed = {"first_px": 0.5e-12, "cum_eth": 2.0,
                "rows": [(NOW - 500, "buy", 1.0, 1e-12),
                         (NOW - 400, "buy", 0.5, 1e-12),
                         (NOW - 90, "sell", 1.0, 1e-12)]}
        lane = self._lane(seed)
        lane._note_px("0xp", NOW, 2.0)          # first live quote
        # scale = 2.0 / 1e-12 -> first_px = 0.5e-12 * scale = 1.0
        assert lane.first_px["0xp"] == 1.0
        # creation-era prints preloaded ahead of the live sample
        assert len(lane.prices["0xp"]) == 4
        assert lane.prices["0xp"][-1] == (NOW, 2.0)
        # creation->promotion volume credited: 2.0 ETH * $2000
        assert lane.cum_vol["0xp"] == 4_000.0
        assert lane.session_anchor["0xp"] is True

    def test_seed_applied_exactly_once(self):
        seed = {"first_px": 1e-12, "cum_eth": 1.0,
                "rows": [(NOW - 90, "buy", 1.0, 1e-12)]}
        lane = self._lane(seed)
        lane._note_px("0xp", NOW, 2.0)
        v = lane.cum_vol["0xp"]
        lane._note_px("0xp", NOW + 2, 2.1)
        assert lane.cum_vol["0xp"] == v          # no re-application

    def test_restored_first_px_blocks_reapplication(self):
        seed = {"first_px": 1e-12, "cum_eth": 1.0,
                "rows": [(NOW - 90, "buy", 1.0, 1e-12)]}
        lane = self._lane(seed)
        lane.first_px["0xp"] = 3.0               # persisted from a prior run
        lane.cum_vol["0xp"] = 500.0
        lane._note_px("0xp", NOW, 2.0)
        assert lane.first_px["0xp"] == 3.0
        assert lane.cum_vol["0xp"] == 500.0

    def test_no_seed_keeps_prefix_behavior(self):
        lane = self._lane(None)
        lane._note_px("0xp", NOW, 2.0)
        assert lane.first_px["0xp"] == 2.0
        assert lane.prices["0xp"] == [(NOW, 2.0)]
        assert "0xp" not in lane.session_anchor


class TestDrainNaturalAnchor:
    def _lane(self, age):
        class FakeFeed:
            watch = {"0xp": {"sym": "T", "liq": 50_000.0,
                             "created_block": 100}}
            eth_price = 2000.0

            def age_h(self, created_block):
                if age is None:
                    raise ValueError("unknown age")
                return age
        return PaperLane(FakeFeed(), executor=object(),
                         bots=(LaneBot(bot_id="x"),))

    def _push(self, lane):
        lane.q.put(("0xp", {"kind": "buy", "volume_usd": 10.0}))
        lane._drain(NOW)

    def test_first_tape_row_young_anchors(self):
        lane = self._lane(60.0 / 3600.0)        # first row at 1 min age
        self._push(lane)
        assert lane.session_anchor.get("0xp") is True
        assert lane.first_tape_age["0xp"] == 60.0 / 3600.0

    def test_first_tape_row_old_does_not_anchor(self):
        lane = self._lane(SESSION_ANCHOR_MAX_AGE_H * 3)
        self._push(lane)
        assert "0xp" not in lane.session_anchor

    def test_unknown_age_does_not_anchor(self):
        lane = self._lane(None)
        self._push(lane)
        assert "0xp" not in lane.session_anchor


class TestAnchoredEntryRouting:
    """End-to-end: an ANCHORED young pool with creation-faithful facts routes
    into rh_f_pullback; the same pool unanchored blocks with the single
    explicit untracked_session reason (never wrong-value vol/arc verdicts)."""

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
        # a -8.3% dip off the window high (in pullback's -6..-12 band),
        # $65 of 30s buys vs $5 sells, $5k session volume, arc +100%
        lane.prices["0xp1"] = [(NOW - 300, 1.0), (NOW - 200, 1.09),
                               (NOW - 10, 1.0)]
        lane.tape["0xp1"] = [
            {"kind": "buy", "volume_usd": 40, "_epoch": NOW - 20},
            {"kind": "buy", "volume_usd": 25, "_epoch": NOW - 10},
            {"kind": "sell", "volume_usd": 5, "_epoch": NOW - 5}]
        lane.cum_vol["0xp1"] = 5_000.0
        lane.first_px["0xp1"] = 0.5
        return lane

    def test_anchored_pool_enters_pullback(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        lane.session_anchor["0xp1"] = True
        lane._consider_entries(NOW)
        assert "0xp1" in lane.state["rh_f_pullback"].pos_meta
        assert ("untracked_session"
                not in lane.state["rh_f_pullback"].block_hist)

    def test_unanchored_pool_blocks_explicitly(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        lane._consider_entries(NOW)
        for bid in ("rh_f_pullback", "rh_f_arc_scalp"):
            st = lane.state[bid]
            assert "0xp1" not in st.pos_meta
            assert st.block_hist.get("untracked_session", 0) >= 1
            # the wrong-value verdicts are NOT consulted while unanchored
            assert "thin_session_vol" not in st.block_hist
            assert "arc_late" not in st.block_hist

    def test_anchor_survives_restart(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        lane.session_anchor["0xp1"] = True
        lane.save_state()
        lane2 = self._lane(tmp_path, monkeypatch)
        lane2.restore_state()
        assert lane2.session_anchor.get("0xp1") is True
