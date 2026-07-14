# tests/test_rh_regime.py
"""RH regime layer v1 (core/rh_regime + lane stamp wiring).

Covers: age bands, discovery regime, the ENFORCED aged-band 19-21 UTC gate
(the one two-window-proven rule), the CompositionTracker window math, the
expectancy dial (STAMP-only), regime_stamp shaping, and the lane-level
integration: every buy ledger row carries the regime stamp, and the dial's
realized record persists across restarts."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from core.rh_regime import (  # noqa: E402
    AGED_BLOCK_HOURS_UTC, CRASH_BUY_SHARE_FLOOR, DISC_BOT_ERA_POOLS_H,
    HOUR_BLOCKS, RH_DEFENSE_SIZE_MULT, RH_FULL_SIZE_MULT, CompositionTracker,
    age_band, aged_hour_gate_ok, crash_regime_block, discovery_regime,
    expectancy_dial, hour_block, regime_size, regime_stamp,
)
import rh_paper_lane as mod  # noqa: E402
from rh_paper_lane import LaneBot, PaperLane  # noqa: E402

NOW = 1_000_000.0   # 13:46:40 UTC (deterministic)


class TestAgeBand:
    def test_bands(self):
        assert age_band(0.1) == "young"
        assert age_band(5.99) == "young"
        assert age_band(6.0) == "mid"
        assert age_band(23.99) == "mid"
        assert age_band(24.0) == "aged"
        assert age_band(300.0) == "aged"

    def test_unknown_is_none(self):
        assert age_band(None) is None


class TestDiscoveryRegime:
    def test_warmup_none(self):
        assert discovery_regime(None) is None

    def test_split(self):
        assert discovery_regime(199.9) == "human"
        assert discovery_regime(DISC_BOT_ERA_POOLS_H) == "bot"
        assert discovery_regime(800.0) == "bot"


class TestAgedHourGate:
    def test_blocks_only_aged_in_19_21(self):
        for h in range(24):
            expected = h not in AGED_BLOCK_HOURS_UTC
            assert aged_hour_gate_ok(h, 30.0) is expected
            assert aged_hour_gate_ok(h, 2.0) is True     # young passes
            assert aged_hour_gate_ok(h, 12.0) is True    # mid passes
            assert aged_hour_gate_ok(h, None) is True    # fail-open

    def test_block_hours_are_the_mined_rule(self):
        assert AGED_BLOCK_HOURS_UTC == (19, 20, 21)

    def test_none_hour_fails_open(self):
        assert aged_hour_gate_ok(None, 30.0) is True


class TestHourBlock:
    def test_every_hour_mapped_once(self):
        seen = [hour_block(h) for h in range(24)]
        assert None not in seen
        # each block's hours round-trip
        for k, hs in HOUR_BLOCKS.items():
            for h in hs:
                assert hour_block(h) == k


class TestCompositionTracker:
    def test_snapshot_counts_and_distinct(self):
        c = CompositionTracker(window_s=1800.0)
        c.ingest(NOW - 100, "0xa", "buy", 100.0)
        c.ingest(NOW - 50, "0xa", "sell", 40.0)
        c.ingest(NOW - 10, "0xb", "buy", 60.0)
        s = c.snapshot(NOW)
        assert s["buy_usd"] == 160.0 and s["sell_usd"] == 40.0
        assert s["netflow_usd"] == 120.0
        assert abs(s["buy_share"] - 0.8) < 1e-9
        assert s["n_buys"] == 2 and s["n_sells"] == 1
        assert s["distinct_pools"] == 2

    def test_window_prunes_and_distinct_decrements(self):
        c = CompositionTracker(window_s=1800.0)
        c.ingest(NOW - 2000, "0xa", "buy", 100.0)   # outside window at NOW
        c.ingest(NOW - 10, "0xb", "sell", 30.0)
        s = c.snapshot(NOW)
        assert s["buy_usd"] == 0.0 and s["n_buys"] == 0
        assert s["distinct_pools"] == 1

    def test_empty_share_is_none(self):
        c = CompositionTracker()
        s = c.snapshot(NOW)
        assert s["buy_share"] is None and s["distinct_pools"] == 0

    def test_none_usd_tolerated(self):
        c = CompositionTracker()
        c.ingest(NOW, "0xa", "buy", None)
        assert c.snapshot(NOW)["n_buys"] == 1


class TestExpectancyDial:
    def test_below_min_n_none(self):
        d = expectancy_dial([1.0] * 9)
        assert d["state"] is None and d["exp_usd"] is None and d["n"] == 9

    def test_offense_defense(self):
        assert expectancy_dial([1.0] * 10)["state"] == "offense"
        assert expectancy_dial([-1.0] * 10)["state"] == "defense"

    def test_window_reads_last_20(self):
        # 30 old wins then 20 recent losses -> defense (only last 20 count)
        d = expectancy_dial([5.0] * 30 + [-1.0] * 20)
        assert d["state"] == "defense" and d["n"] == 20
        assert abs(d["exp_usd"] + 1.0) < 1e-9


class TestRegimeStamp:
    def test_shape_and_values(self):
        comp = {"buy_share": 0.61, "netflow_usd": 1200.0,
                "distinct_pools": 44, "n_buys": 70, "n_sells": 30}
        s = regime_stamp(19, 850.0, comp,
                         dial={"state": "offense", "exp_usd": 0.4, "n": 20},
                         eth_usd=1745.54, age_h=30.0)
        assert s["hour_utc"] == 19 and s["npph"] == 850.0
        assert s["disc"] == "bot" and s["band"] == "aged"
        assert s["buy_share_30m"] == 0.61
        assert s["netflow_30m_usd"] == 1200.0
        assert s["distinct_pools_30m"] == 44 and s["n_swaps_30m"] == 100
        assert s["dial"] == "offense" and s["dial_exp_usd"] == 0.4
        assert s["eth_usd"] == 1745.54

    def test_all_unknown_fails_open_to_nones(self):
        s = regime_stamp(3, None, {}, dial=None, eth_usd=None, age_h=None)
        assert s["disc"] is None and s["band"] is None
        assert s["dial"] is None and s["eth_usd"] is None
        assert s["n_swaps_30m"] == 0


# ── LOOSE crash-only regime gate (2026-07-13, SHADOW) ────────────────────────
class TestCrashRegimeGate:
    def _clear(self):
        os.environ.pop("RH_CRASH_GATE", None)

    def test_normal_tape_never_blocks(self):
        self._clear()
        # observed 07-10..12 range: buy_share 0.76-1.0, netflow > 0 always.
        d = crash_regime_block(0.89, 52909.0, age_h=30.0)
        assert d["block"] is False and d["reason"] is None

    def test_young_is_exempt_even_in_cascade(self):
        self._clear()
        # a genuine cascade reading, but on a YOUNG pool -> never block
        d = crash_regime_block(0.20, -10000.0, age_h=2.0)
        assert d["block"] is False and d["reason"] == "young_exempt"

    def test_true_cascade_on_aged_blocks(self):
        self._clear()
        # both legs: buy_share below floor AND net OUTFLOW, non-young band
        d = crash_regime_block(0.20, -10000.0, age_h=30.0)
        assert d["block"] is True and d["reason"] == "crash_cascade"

    def test_one_weak_leg_alone_never_blocks(self):
        self._clear()
        # low buy_share but net still POSITIVE -> loose gate stays open
        assert crash_regime_block(0.20, 5000.0, age_h=30.0)["block"] is False
        # net outflow but buy_share above floor -> stays open
        assert crash_regime_block(0.80, -5000.0, age_h=30.0)["block"] is False

    def test_floor_below_observed_range(self):
        # the loose-by-design invariant: floor sits under the observed min 0.76
        assert CRASH_BUY_SHARE_FLOOR < 0.76

    def test_missing_inputs_fail_open(self):
        self._clear()
        assert crash_regime_block(None, 100.0, age_h=30.0)["block"] is False
        assert crash_regime_block(0.2, None, age_h=30.0)["block"] is False
        assert crash_regime_block(0.2, -1.0, age_h=None)["block"] is False

    def test_off_mode_disables_stamp(self):
        os.environ["RH_CRASH_GATE"] = "off"
        try:
            assert crash_regime_block(0.20, -10000.0, age_h=30.0) is None
        finally:
            self._clear()

    def test_stamp_carries_shadow_decision(self):
        self._clear()   # default shadow
        comp = {"buy_share": 0.20, "netflow_usd": -10000.0,
                "distinct_pools": 44, "n_buys": 30, "n_sells": 70}
        s = regime_stamp(19, 850.0, comp, age_h=30.0)
        assert s["crash_gate"] == "shadow"
        assert s["crash_block"] is True
        assert s["crash_reason"] == "crash_cascade"
        # normal tape -> stamp present, decision False
        comp2 = {"buy_share": 0.89, "netflow_usd": 52909.0,
                 "distinct_pools": 44, "n_buys": 70, "n_sells": 30}
        s2 = regime_stamp(19, 850.0, comp2, age_h=30.0)
        assert s2["crash_block"] is False


# ── regime-SIZING read (2026-07-13, SHADOW) ──────────────────────────────────
class TestRegimeSize:
    def _clear(self):
        os.environ.pop("RH_REGIME_SIZE", None)

    def test_defense_downsizes_on_negative_dial(self):
        self._clear()   # default shadow
        s = regime_size({"state": "defense", "exp_usd": -0.84, "n": 20})
        assert s["state"] == "defense"
        assert s["would_size"] == RH_DEFENSE_SIZE_MULT
        assert s["score"] == -0.84

    def test_full_size_on_positive_dial(self):
        self._clear()
        s = regime_size({"state": "offense", "exp_usd": 1.03, "n": 20})
        assert s["state"] == "offense" and s["would_size"] == RH_FULL_SIZE_MULT

    def test_zero_expectancy_is_full_size(self):
        # threshold is strict < 0: breakeven regime keeps full size
        self._clear()
        s = regime_size({"state": "offense", "exp_usd": 0.0, "n": 20})
        assert s["would_size"] == RH_FULL_SIZE_MULT and s["state"] == "offense"

    def test_warmup_fails_to_full_size(self):
        self._clear()
        for d in (None, {}, {"state": None, "exp_usd": None, "n": 5}):
            s = regime_size(d)
            assert s["state"] == "warmup"
            assert s["would_size"] == RH_FULL_SIZE_MULT and s["score"] is None

    def test_off_mode_disables_stamp(self):
        os.environ["RH_REGIME_SIZE"] = "off"
        try:
            assert regime_size({"exp_usd": -1.0}) is None
        finally:
            self._clear()

    def test_defense_mult_is_a_downsize(self):
        assert 0.0 <= RH_DEFENSE_SIZE_MULT < RH_FULL_SIZE_MULT

    def test_stamp_carries_sizing_read(self):
        self._clear()   # default shadow
        comp = {"buy_share": 0.89, "netflow_usd": 52909.0,
                "distinct_pools": 44, "n_buys": 70, "n_sells": 30}
        # negative fleet dial -> defense -> would_size 0.3x
        s = regime_stamp(19, 850.0, comp, age_h=30.0,
                         size_dial={"state": "defense", "exp_usd": -1.33,
                                    "n": 20})
        assert s["regime_size_mode"] == "shadow"
        assert s["regime_size_state"] == "defense"
        assert s["regime_score"] == -1.33
        assert s["would_size"] == RH_DEFENSE_SIZE_MULT

    def test_stamp_falls_back_to_dial_when_no_size_dial(self):
        # single-dial callers still get a sizing stamp from `dial`
        self._clear()
        s = regime_stamp(3, None, {}, dial={"state": "offense", "exp_usd": 0.5,
                                            "n": 20})
        assert s["regime_score"] == 0.5
        assert s["would_size"] == RH_FULL_SIZE_MULT


# ── lane integration: stamp on every buy row; dial record persists ──────────
class FakeQuote:
    def __init__(self, amount_in, amount_out):
        self.amount_in, self.amount_out = amount_in, amount_out
        self.fee = 10000


class FakeExecutor:
    def __init__(self):
        self.buy_in = int(25.0 / 2000.0 * 1e18)

    def quote_sell(self, token, amount):
        return FakeQuote(amount, int(self.buy_in * 0.97))

    def quote_buy(self, token, wei):
        return FakeQuote(wei, 10 ** 21)

    def token_decimals(self, token):
        return 18


class FakeFeed:
    def __init__(self, watch):
        self.watch = watch
        self.eth_price = 2000.0

    def age_h(self, created_block):
        return float(created_block)


def _lane(tmp_path, monkeypatch, bots):
    monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mod, "LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(mod, "POSTEXIT_PENDING", str(tmp_path / "pe.jsonl"))
    monkeypatch.setattr(mod, "RUG_STAMP_ENABLED", False)
    feed = FakeFeed({"0xp1": {"sym": "T", "liq": 50_000.0,
                              "created_block": 8.0}})
    lane = PaperLane(feed, executor=FakeExecutor(),
                     registry={"0xp1": {"token": "0xtok"}}, bots=bots)
    lane.honeypot["0xtok"] = {"sellable": True}
    lane._regime_known = {"0xp1"}
    lane._regime_t0 = NOW - 3600.0
    lane._regime_seen = [NOW - i for i in range(120)]   # 120/h -> human
    return lane


def _rows(tmp_path):
    p = tmp_path / "ledger.jsonl"
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


class TestLaneStamp:
    def _enter(self, lane, t=NOW):
        lane.prices["0xp1"] = [(t - 300, 1.0), (t - 200, 1.25), (t - 10, 1.0)]
        lane.tape["0xp1"] = [
            {"kind": "buy", "volume_usd": 600.0, "_epoch": t - 20},
            {"kind": "sell", "volume_usd": 5.0, "_epoch": t - 5}]
        lane.comp.ingest(t - 20, "0xp1", "buy", 600.0)
        lane.comp.ingest(t - 5, "0xp1", "sell", 5.0)
        lane._consider_entries(t)

    def test_buy_row_carries_regime_stamp(self, tmp_path, monkeypatch):
        lane = _lane(tmp_path, monkeypatch, (LaneBot(bot_id="rh_young_v1"),))
        self._enter(lane)
        buys = [r for r in _rows(tmp_path) if r["ev"] == "buy"]
        assert len(buys) == 1
        rg = buys[0]["regime"]
        assert rg["hour_utc"] == 13
        assert rg["disc"] == "human" and rg["npph"] == 120.0
        assert rg["band"] == "mid"                  # 8h pool
        assert abs(rg["buy_share_30m"] - 600.0 / 605.0) < 1e-4  # 4dp stamp
        assert rg["distinct_pools_30m"] == 1
        assert rg["dial"] is None                   # no realized record yet
        assert rg["eth_usd"] == 2000.0

    def test_dial_stamps_after_closes_and_persists(self, tmp_path,
                                                   monkeypatch):
        lane = _lane(tmp_path, monkeypatch, (LaneBot(bot_id="rh_young_v1"),))
        st = lane.state["rh_young_v1"]
        st.recent_realized = [-1.0] * 12
        self._enter(lane)
        buys = [r for r in _rows(tmp_path) if r["ev"] == "buy"]
        assert buys[0]["regime"]["dial"] == "defense"
        # persistence round-trip
        lane.save_state()
        lane2 = _lane(tmp_path, monkeypatch, (LaneBot(bot_id="rh_young_v1"),))
        lane2.restore_state()
        assert lane2.state["rh_young_v1"].recent_realized == [-1.0] * 12

    def test_full_close_appends_realized(self, tmp_path, monkeypatch):
        lane = _lane(tmp_path, monkeypatch, (LaneBot(bot_id="rh_young_v1"),))
        self._enter(lane)
        st = lane.state["rh_young_v1"]
        assert "0xp1" in st.pos_meta
        from types import SimpleNamespace
        meta = st.pos_meta["0xp1"]
        lane._paper_sell("0xp1", meta, SimpleNamespace(
            kind="HARD_STOP", sell_fraction=1.0, reason="test"), NOW + 60,
            st=st)
        assert len(st.recent_realized) == 1
        # fleet-wide realized record also grew (regime-sizing dial)
        assert len(lane.fleet_realized) == 1
        assert lane.fleet_realized[-1] == st.recent_realized[-1]

    def test_buy_row_carries_sizing_stamp_from_fleet_dial(self, tmp_path,
                                                          monkeypatch):
        os.environ.pop("RH_REGIME_SIZE", None)   # default shadow
        lane = _lane(tmp_path, monkeypatch, (LaneBot(bot_id="rh_young_v1"),))
        # fleet is bleeding -> negative rolling dial -> defense would_size
        lane.fleet_realized = [-1.0] * 12
        self._enter(lane)
        buys = [r for r in _rows(tmp_path) if r["ev"] == "buy"]
        assert len(buys) == 1
        rg = buys[0]["regime"]
        assert rg["regime_size_mode"] == "shadow"
        assert rg["regime_size_state"] == "defense"
        assert rg["would_size"] == 0.3          # RH_DEFENSE_SIZE_MULT
        assert rg["regime_score"] == -1.0
        # SHADOW: the buy still books full $25 — nothing resized
        assert buys[0]["usd"] == 25.0

    def test_fleet_realized_persists_across_restart(self, tmp_path, monkeypatch):
        lane = _lane(tmp_path, monkeypatch, (LaneBot(bot_id="rh_young_v1"),))
        lane.fleet_realized = [-1.0, 0.5, -2.0]
        lane.save_state()
        lane2 = _lane(tmp_path, monkeypatch, (LaneBot(bot_id="rh_young_v1"),))
        lane2.restore_state()
        assert lane2.fleet_realized == [-1.0, 0.5, -2.0]
