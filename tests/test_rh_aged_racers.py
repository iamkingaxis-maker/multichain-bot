# tests/test_rh_aged_racers.py
"""AGED-POOL racers (2026-07-11) — the full-history-decode thesis cohort:
aged/established pools + longer holds. Covers the new pure gates (regime
hour, depth re-entry, derisk slice, sibling exclusion + same-tick group
dedupe), the roster wiring (scalp fleet UNCHANGED), and the lane-level
integration of each mechanism. Plus the quote-leg latency fixes in
core/rh_execution (decimals memoization + batched tier sweep)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import rh_paper_lane as mod  # noqa: E402
from rh_paper_lane import (  # noqa: E402
    LaneBot, ROSTER, PaperLane, BotState,
    regime_hour_ok, reentry_depth_gate, derisk_slice,
    sibling_exclusion_keys, dedupe_group_entries,
    AGED_MIN_POOL_AGE_H, AGED_TP1_PCT, AGED_TP2_PCT, AGED_TRAIL_PP,
    DERISK_AFTER_S, DERISK_MAX_FRAC, REENTRY_MIN_DIP_PCT, REENTRY_MIN_VOL_M5,
    REGIME_BOT_ERA_POOLS_H, REGIME_HUMAN_HOURS,
    DIP_TRIGGER_PCT, MIN_LIQ_USD, MIN_POOL_AGE_H, REENTRY_COOLDOWN_S,
)

NOW = 1_000_000.0   # 1970-01-12T13:46:40 UTC -> hour 13 (deterministic)


def _row(kind, usd, dt):
    return {"kind": kind, "volume_usd": usd, "_epoch": NOW + dt}


class FakeQuote:
    def __init__(self, amount_in, amount_out):
        self.amount_in, self.amount_out = amount_in, amount_out
        self.fee = 10000


class FakeExecutor:
    def __init__(self, sell_out_wei=None, buy_out_atomic=None):
        self.sell_out_wei = sell_out_wei
        self.buy_out_atomic = buy_out_atomic
        self.calls = []

    def quote_sell(self, token, amount):
        self.calls.append(("sell", token, amount))
        return FakeQuote(amount, self.sell_out_wei)

    def quote_buy(self, token, wei):
        self.calls.append(("buy", token, wei))
        return FakeQuote(wei, self.buy_out_atomic)

    def token_decimals(self, token):
        return 18


class FakeFeed:
    """age_h(created_block) returns the created_block value directly — tests
    stamp the desired AGE (in hours) into created_block."""

    def __init__(self, watch=None):
        self.watch = watch if watch is not None else {}
        self.eth_price = 2000.0

    def age_h(self, created_block):
        return float(created_block)


def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mod, "LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(mod, "POSTEXIT_PENDING", str(tmp_path / "pe.jsonl"))


def _ledger_rows(tmp_path):
    out = []
    p = tmp_path / "ledger.jsonl"
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


AGED = tuple(b for b in ROSTER if b.exclusion_group == "aged")


class TestRosterWiring:
    def test_three_aged_racers(self):
        assert [b.bot_id for b in AGED] == [
            "rh_aged_hold", "rh_aged_derisk", "rh_aged_deep"]

    def test_scalp_fleet_unchanged(self):
        # The first 10 racers are the mid-flight A/B — respect active
        # experiments: ids in order, and every NEW mechanism defaulted OFF.
        assert [b.bot_id for b in ROSTER[:10]] == [
            "rh_young_v1", "rh_deep_only", "rh_first_touch", "rh_bites2",
            "rh_wide_ladder", "rh_moonbag", "rh_demand_heavy", "rh_liq40",
            "rh_prime_hours", "rh_launch_scalp"]
        for b in ROSTER[:10]:
            assert b.exclusion_group is None
            assert b.reentry_min_dip_pct is None
            assert b.derisk_after_s is None
            assert b.regime_hours is False
            assert b.trail_pp is None          # BotConfig default 3.0 kept
            assert b.bot_config().trail_pp == 3.0

    def test_aged_shared_thresholds(self):
        for b in AGED:
            assert b.min_pool_age_h == AGED_MIN_POOL_AGE_H == 6.0
            assert b.max_pool_age_h is None    # feed caps at 24h; no ceiling
            assert (b.tp1_pct, b.tp2_pct) == (AGED_TP1_PCT, AGED_TP2_PCT)
            assert b.trail_pp == AGED_TRAIL_PP == 10.0
            assert b.bot_config().trail_pp == 10.0
            assert b.time_stop_minutes is None  # fat-tailed winning holds
            assert b.regime_hours is True
            assert b.dip_trigger_pct == DIP_TRIGGER_PCT  # control-parity axis
            assert b.min_liq_usd == MIN_LIQ_USD

    def test_aged_axes(self):
        hold, derisk, deep = AGED
        # pure thesis: half banked at TP1, tail rides the 10pp trail
        assert (hold.tp1_sell_fraction, hold.tp2_sell_fraction) == (0.50, 0.30)
        assert hold.derisk_after_s is None
        # derisk: principal-banking slice + 20-min exposure cap
        assert (derisk.tp1_sell_fraction, derisk.tp2_sell_fraction) == (0.75, 0.15)
        assert derisk.derisk_after_s == DERISK_AFTER_S == 1200.0
        assert derisk.derisk_max_frac == DERISK_MAX_FRAC == 0.25
        # deep: depth-gated loss re-entry, NO flat cooldown
        assert deep.reentry_min_dip_pct == REENTRY_MIN_DIP_PCT == -26.0
        assert deep.reentry_min_vol_m5_usd == REENTRY_MIN_VOL_M5 == 500.0
        assert deep.reentry_cooldown_s == 0.0
        assert hold.reentry_cooldown_s == REENTRY_COOLDOWN_S  # gate-less keep it

    def test_min_pool_age_above_control(self):
        assert AGED_MIN_POOL_AGE_H > MIN_POOL_AGE_H  # 6h vs the 1h rug floor


class TestRegimeHourOk:
    def test_warmup_fails_open(self):
        assert regime_hour_ok(3, None) is True

    def test_bot_era_24_7(self):
        for h in (0, 3, 13, 22):
            assert regime_hour_ok(h, 800.0) is True
        assert regime_hour_ok(3, REGIME_BOT_ERA_POOLS_H) is True  # boundary

    def test_human_era_gates_to_14_23(self):
        assert regime_hour_ok(15, 50.0) is True
        assert regime_hour_ok(23, 50.0) is True
        assert regime_hour_ok(13, 50.0) is False
        assert regime_hour_ok(3, 50.0) is False
        assert REGIME_HUMAN_HOURS == tuple(range(14, 24))


class TestReentryDepthGate:
    def test_no_recent_loss_passes(self):
        assert reentry_depth_gate(False, -5.0, 0.0) is None

    def test_shallow_blocked_deep_passes(self):
        # live boundary: -25 slaughtered, -26 and deeper paid
        assert reentry_depth_gate(True, -25.0, 5000.0) == "reentry_shallow"
        assert reentry_depth_gate(True, -26.0, 5000.0) is None
        assert reentry_depth_gate(True, -31.6, 5000.0) is None

    def test_no_dip_reading_blocked(self):
        assert reentry_depth_gate(True, None, 5000.0) == "reentry_shallow"

    def test_dead_tape_blocked(self):
        # MONSIEUR's dead tape was vol_m5 $109 — under the $500 bail floor
        assert reentry_depth_gate(True, -31.6, 109.0) == "reentry_dead_tape"
        assert reentry_depth_gate(True, -31.6, None) == "reentry_dead_tape"
        assert reentry_depth_gate(True, -31.6, 500.0) is None


class TestDeriskSlice:
    def test_inside_window_zero(self):
        assert derisk_slice(1.0, 1199.0, 1200.0, 0.25) == 0.0

    def test_off_when_disabled(self):
        assert derisk_slice(1.0, 99999.0, None, 0.25) == 0.0

    def test_caps_full_position(self):
        assert abs(derisk_slice(1.0, 1200.0, 1200.0, 0.25) - 0.75) < 1e-12

    def test_noop_when_tp1_already_banked_more(self):
        assert derisk_slice(0.25, 5000.0, 1200.0, 0.25) == 0.0
        assert derisk_slice(0.10, 5000.0, 1200.0, 0.25) == 0.0


class TestSiblingExclusion:
    def _st(self, bot_id, group="aged"):
        return BotState(LaneBot(bot_id=bot_id, exclusion_group=group))

    def test_held_pool_and_token_excluded(self):
        a, b = self._st("a"), self._st("b")
        a.pos_meta["0xp1"] = {"token": "0xtok"}
        keys = sibling_exclusion_keys([a, b], "b", "aged", NOW, 1200.0)
        assert "0xp1" in keys and "0xtok" in keys

    def test_own_holdings_never_exclude_self(self):
        a, b = self._st("a"), self._st("b")
        a.pos_meta["0xp1"] = {"token": "0xtok"}
        assert sibling_exclusion_keys([a, b], "a", "aged", NOW, 1200.0) == set()

    def test_recent_loss_stop_excluded_win_free(self):
        a, b = self._st("a"), self._st("b")
        a.exit_book["0xploss"] = {"ts": NOW - 100, "loss": True,
                                  "token": "0xtl"}
        a.exit_book["0xpwin"] = {"ts": NOW - 100, "loss": False,
                                 "token": "0xtw"}
        keys = sibling_exclusion_keys([a, b], "b", "aged", NOW, 1200.0)
        assert "0xploss" in keys and "0xtl" in keys
        assert "0xpwin" not in keys and "0xtw" not in keys

    def test_stale_loss_stop_expires(self):
        a, b = self._st("a"), self._st("b")
        a.exit_book["0xp"] = {"ts": NOW - 1201, "loss": True, "token": "0xt"}
        assert sibling_exclusion_keys([a, b], "b", "aged", NOW, 1200.0) == set()

    def test_other_group_ignored(self):
        a = self._st("a", group="other")
        b = self._st("b")
        a.pos_meta["0xp1"] = {"token": "0xtok"}
        assert sibling_exclusion_keys([a, b], "b", "aged", NOW, 1200.0) == set()


class TestDedupeGroupEntries:
    def _st(self, bot_id, group, n_open=0):
        st = BotState(LaneBot(bot_id=bot_id, exclusion_group=group))
        for i in range(n_open):
            st.pos_meta[f"0xheld{i}"] = {}
        return st

    def test_ungrouped_all_pass(self):
        sts = [self._st("a", None), self._st("b", None)]
        kept, blocked = dedupe_group_entries(sts)
        assert kept == sts and blocked == []

    def test_one_per_group_roster_order_tie(self):
        a, b, c = (self._st("a", "aged"), self._st("b", "aged"),
                   self._st("c", None))
        kept, blocked = dedupe_group_entries([a, b, c])
        assert kept == [a, c] and blocked == [b]

    def test_fewest_open_positions_wins(self):
        a, b = self._st("a", "aged", n_open=2), self._st("b", "aged")
        kept, blocked = dedupe_group_entries([a, b])
        assert kept == [b] and blocked == [a]


class TestLaneIntegration:
    def _lane(self, tmp_path, monkeypatch, bots, watch=None, registry=None):
        _paths(tmp_path, monkeypatch)
        buy_in = int(25.0 / 2000.0 * 1e18)
        ex = FakeExecutor(buy_out_atomic=10 ** 21,           # 1000 tokens
                          sell_out_wei=int(buy_in * 0.97))   # 3% rt cost
        feed = FakeFeed(watch if watch is not None
                        else {"0xp1": {"sym": "T", "liq": 50_000.0,
                                       "created_block": 8.0}})  # age 8h
        lane = PaperLane(feed, executor=ex,
                         registry=registry if registry is not None
                         else {"0xp1": {"token": "0xtok"}}, bots=bots)
        lane.honeypot["0xtok"] = {"sellable": True}
        # regime warm-up state: rate unknown -> hour gate fails open
        lane._regime_known = {"0xp1"}
        lane._regime_t0 = NOW
        return lane, ex

    def _dip_facts(self, lane, dip_hi=1.25, vol_usd=600.0):
        # latest 1.0 vs window high dip_hi: dip = (1-dip_hi)/dip_hi
        lane.prices["0xp1"] = [(NOW - 300, 1.0), (NOW - 200, dip_hi),
                               (NOW - 10, 1.0)]
        lane.tape["0xp1"] = [_row("buy", vol_usd, -20), _row("sell", 5, -5)]

    def test_age_floor_blocks_young_pool(self, tmp_path, monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, AGED,
                             watch={"0xp1": {"sym": "T", "liq": 50_000.0,
                                             "created_block": 2.0}})  # 2h
        self._dip_facts(lane)
        lane._consider_entries(NOW)
        assert all("0xp1" not in st.pos_meta for st in lane.state.values())
        assert lane.state["rh_aged_hold"].block_hist.get("age_floor")

    def test_aged_pool_admitted_one_sibling_only(self, tmp_path, monkeypatch):
        lane, ex = self._lane(tmp_path, monkeypatch, AGED)
        self._dip_facts(lane)
        lane._consider_entries(NOW)
        entered = {bid for bid, st in lane.state.items()
                   if "0xp1" in st.pos_meta}
        assert entered == {"rh_aged_hold"}      # group dedupe, roster order
        assert lane.state["rh_aged_derisk"].block_hist.get("sibling_excl")
        # quote budget not multiplied: one buy + one rt-cost sell
        assert len([c for c in ex.calls if c[0] == "buy"]) == 1
        buys = [r for r in _ledger_rows(tmp_path) if r["ev"] == "buy"]
        assert len(buys) == 1 and buys[0]["age_h"] == 8.0

    def test_sibling_holding_excludes_across_ticks(self, tmp_path,
                                                   monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, AGED)
        self._dip_facts(lane)
        # rh_aged_hold already holds the token (prior tick)
        lane.state["rh_aged_hold"].pos_meta["0xp1"] = {
            "qty_orig": 1000.0, "remaining_frac": 1.0, "token": "0xtok",
            "sym": "T", "entry_px": 1e-5, "entry_ts": NOW - 60}
        lane._consider_entries(NOW)
        assert "0xp1" not in lane.state["rh_aged_derisk"].pos_meta
        assert lane.state["rh_aged_derisk"].block_hist.get("sibling_excl")

    def test_sibling_loss_stop_excludes_within_window(self, tmp_path,
                                                      monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, AGED)
        self._dip_facts(lane)
        lane.state["rh_aged_hold"].exit_book["0xp1"] = {
            "ts": NOW - 300, "loss": True, "token": "0xtok"}
        lane.state["rh_aged_hold"].last_exit["0xp1"] = NOW - 300
        lane._consider_entries(NOW)
        assert all("0xp1" not in st.pos_meta for st in lane.state.values()
                   if st.bot.bot_id != "rh_aged_hold")
        # ... and the stopping racer itself is NOT sibling-excluded (its own
        # cooldown governs it — 300s elapsed == cooldown, so it's blocked by
        # cooldown here, not by exclusion)
        assert "sibling_excl" not in lane.state["rh_aged_hold"].block_hist

    def test_depth_gate_blocks_shallow_reentry_after_loss(self, tmp_path,
                                                          monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[2],))  # deep only
        st = lane.state["rh_aged_deep"]
        st.exit_book["0xp1"] = {"ts": NOW - 100, "loss": True,
                                "token": "0xtok"}
        self._dip_facts(lane, dip_hi=1.25)      # -20% dip: shallow
        lane._consider_entries(NOW)
        assert "0xp1" not in st.pos_meta
        assert st.block_hist.get("reentry_shallow")

    def test_depth_gate_admits_deep_reentry_with_live_tape(self, tmp_path,
                                                           monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[2],))
        st = lane.state["rh_aged_deep"]
        st.exit_book["0xp1"] = {"ts": NOW - 100, "loss": True,
                                "token": "0xtok"}
        self._dip_facts(lane, dip_hi=1.43)      # -30.1% dip: deep
        lane._consider_entries(NOW)             # cooldown 0 -> immediate
        assert "0xp1" in st.pos_meta

    def test_depth_gate_blocks_dead_tape(self, tmp_path, monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[2],))
        st = lane.state["rh_aged_deep"]
        st.exit_book["0xp1"] = {"ts": NOW - 100, "loss": True,
                                "token": "0xtok"}
        self._dip_facts(lane, dip_hi=1.43, vol_usd=60.0)  # deep but $60 tape
        lane._consider_entries(NOW)
        assert "0xp1" not in st.pos_meta
        assert st.block_hist.get("reentry_dead_tape")

    def test_depth_gate_ignores_old_loss(self, tmp_path, monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[2],))
        st = lane.state["rh_aged_deep"]
        st.exit_book["0xp1"] = {"ts": NOW - 1300, "loss": True,
                                "token": "0xtok"}       # outside 20-min window
        self._dip_facts(lane, dip_hi=1.25)      # shallow -20% would block
        lane._consider_entries(NOW)
        assert "0xp1" in st.pos_meta            # stale loss: normal entry

    def test_regime_human_era_blocks_off_hours(self, tmp_path, monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[0],))
        # human-era rate: 50 discoveries over a full observed hour
        lane._regime_t0 = NOW - 3600.0
        lane._regime_seen = [NOW - i for i in range(50)]
        self._dip_facts(lane)
        lane._consider_entries(NOW)             # hour 13 not in 14-23
        st = lane.state["rh_aged_hold"]
        assert "0xp1" not in st.pos_meta
        assert st.block_hist.get("hour_regime")

    def test_regime_bot_era_trades_any_hour(self, tmp_path, monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[0],))
        lane._regime_t0 = NOW - 3600.0
        lane._regime_seen = [NOW - i for i in range(800)]  # bot-era rate
        self._dip_facts(lane)
        lane._consider_entries(NOW)
        assert "0xp1" in lane.state["rh_aged_hold"].pos_meta

    def test_derisk_cap_fires_after_window(self, tmp_path, monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[1],))  # derisk
        st = lane.state["rh_aged_derisk"]
        st.pm.open_position(token="0xp1", entry_price=1e-5, size_usd=25.0,
                            entry_time=NOW - 1300, address="0xtok")
        st.pos_meta["0xp1"] = {"qty_orig": 1000.0, "remaining_frac": 1.0,
                               "token": "0xtok", "sym": "T",
                               "entry_px": 1e-5, "entry_ts": NOW - 1300}
        lane.prices["0xp1"] = [(NOW - 10, 1e-5)]
        lane._manage_exits(NOW)
        assert abs(st.pos_meta["0xp1"]["remaining_frac"] - 0.25) < 1e-9
        sells = [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"]
        assert len(sells) == 1 and sells[0]["kind"] == "DERISK_CAP"
        assert abs(sells[0]["frac"] - 0.75) < 1e-9

    def test_derisk_noop_inside_window_and_after_tp1(self, tmp_path,
                                                     monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch, (AGED[1],))
        st = lane.state["rh_aged_derisk"]
        # inside the window: untouched
        st.pm.open_position(token="0xp1", entry_price=1e-5, size_usd=25.0,
                            entry_time=NOW - 600, address="0xtok")
        st.pos_meta["0xp1"] = {"qty_orig": 1000.0, "remaining_frac": 1.0,
                               "token": "0xtok", "sym": "T",
                               "entry_px": 1e-5, "entry_ts": NOW - 600}
        lane.prices["0xp1"] = [(NOW - 10, 1e-5)]
        lane._manage_exits(NOW)
        assert st.pos_meta["0xp1"]["remaining_frac"] == 1.0
        # past the window but TP1 already banked 0.75: cap satisfied
        st.pos_meta["0xp1"]["entry_ts"] = NOW - 1300
        st.pos_meta["0xp1"]["remaining_frac"] = 0.25
        lane._manage_exits(NOW)
        assert st.pos_meta["0xp1"]["remaining_frac"] == 0.25
        assert not [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"]

    def test_full_close_stamps_exit_book_loss_sign(self, tmp_path,
                                                   monkeypatch):
        from types import SimpleNamespace
        lane, ex = self._lane(tmp_path, monkeypatch, (AGED[2],))
        st = lane.state["rh_aged_deep"]
        st.pm.open_position(token="0xp1", entry_price=1e-5, size_usd=25.0,
                            entry_time=NOW - 600, address="0xtok")
        st.pos_meta["0xp1"] = {"qty_orig": 1000.0, "remaining_frac": 1.0,
                               "token": "0xtok", "sym": "T",
                               "entry_px": 1e-5, "entry_ts": NOW - 600}
        ex.sell_out_wei = int(0.005 * 1e18)     # $10 back on $25 = loss
        lane._paper_sell("0xp1", st.pos_meta["0xp1"], SimpleNamespace(
            kind="HARD_STOP", sell_fraction=1.0, reason="stop"), NOW, st=st)
        info = st.exit_book["0xp1"]
        assert info["loss"] is True and info["token"] == "0xtok"
        assert info["ts"] == NOW


class TestRegimeTracking:
    def test_first_tick_seeds_without_counting(self):
        feed = FakeFeed({"0xa": {}, "0xb": {}})
        feed.cand = {"0xc": {}}
        lane = PaperLane(feed, executor=object(), registry={})
        lane._track_new_pools(NOW)
        assert lane.new_pools_per_hour(NOW) is None      # warm-up
        assert lane._regime_seen == []                   # backfill not counted
        feed.cand["0xnew"] = {}
        lane._track_new_pools(NOW + 60)
        assert len(lane._regime_seen) == 1

    def test_rate_extrapolates_and_prunes(self):
        feed = FakeFeed({})
        lane = PaperLane(feed, executor=object(), registry={})
        lane._regime_known = set()
        lane._regime_t0 = NOW - 1800.0                   # 30 min uptime
        lane._regime_seen = [NOW - 5000.0] + [NOW - i for i in range(100)]
        lane._track_new_pools(NOW)                       # prunes the stale one
        rate = lane.new_pools_per_hour(NOW)
        assert abs(rate - 100 * 3600.0 / 1800.0) < 1e-6  # 200/h


class TestQuoteLegLatencyFixes:
    """core/rh_execution: decimals memoization + batched tier sweep (the
    measured root cause: ~185ms/eth_call x (4 tiers + decimals) per quote
    side = 1.9-2.9s per paper fill; one batch POST answers all tiers in
    ~160ms)."""

    def test_decimals_cache_first_no_network(self):
        from core.rh_execution import RhExecutor
        ex = RhExecutor(rpc_url="http://127.0.0.1:1")    # unreachable
        ex._decimals_cache["0xtok"] = 6
        assert ex.token_decimals("0xTOK") == 6           # case-folded, cached
        assert ex.token_decimals("0xtok") == 6

    def test_batch_payload_shape(self):
        from core.rh_execution import (build_tier_quote_batch, FEE_TIERS,
                                       QUOTER_V2, WETH9)
        p = build_tier_quote_batch(WETH9, "0x" + "11" * 20, 10 ** 15)
        assert len(p) == len(FEE_TIERS) == 4
        assert [x["id"] for x in p] == [0, 1, 2, 3]
        for x in p:
            assert x["method"] == "eth_call"
            assert x["params"][0]["to"] == QUOTER_V2
            assert x["params"][0]["data"].startswith("0x")
            assert x["params"][1] == "latest"

    def test_decode_amount_out(self):
        from core.rh_execution import decode_quoted_amount_out
        word = "0x" + hex(12345)[2:].rjust(64, "0") + "00" * 96
        assert decode_quoted_amount_out(word) == 12345
        assert decode_quoted_amount_out("0x") is None
        assert decode_quoted_amount_out(None) is None
        assert decode_quoted_amount_out("0xzz") is None

    def test_parse_batch_semantics(self):
        from core.rh_execution import parse_tier_quote_batch, FEE_TIERS
        ok = "0x" + hex(777)[2:].rjust(64, "0")
        resp = [{"id": 0, "error": {"message": "execution reverted"}},
                {"id": 1, "result": ok},
                {"id": 2, "result": "0x" + "0" * 64},   # zero out -> skipped
                {"id": 3, "result": ok}]
        out = parse_tier_quote_batch(resp)
        assert out == {FEE_TIERS[1]: 777, FEE_TIERS[3]: 777}
        assert list(out) == [FEE_TIERS[1], FEE_TIERS[3]]  # tier order kept
        # missing tier -> unknown state -> None (caller falls back)
        assert parse_tier_quote_batch(resp[:3]) is None
        assert parse_tier_quote_batch({"not": "a list"}) is None

    def test_best_quote_uses_batch_and_falls_back(self, monkeypatch):
        from core.rh_execution import RhExecutor, FEE_TIERS
        ex = RhExecutor(rpc_url="http://127.0.0.1:1")
        monkeypatch.setattr(ex, "_quote_all_tiers_batched",
                            lambda *a: {3000: 10, 500: 20})
        assert ex._best_quote("0xa", "0xb", 1) == (500, 20, {3000: 10, 500: 20})
        # batch unavailable -> sequential per-tier path
        monkeypatch.setattr(ex, "_quote_all_tiers_batched", lambda *a: None)
        monkeypatch.setattr(ex, "_quote_single",
                            lambda ti, to, amt, fee: 42 if fee == 100 else None)
        assert ex._best_quote("0xa", "0xb", 1) == (100, 42, {100: 42})
        # nothing quotes anywhere -> None
        monkeypatch.setattr(ex, "_quote_single", lambda *a: None)
        assert ex._best_quote("0xa", "0xb", 1) is None
