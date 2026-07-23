# tests/test_rh_paper_fleet.py
"""RH FLEET v1 (2026-07-11) — N configs racing over ONE firehose/quote budget
(the selection instrument, Solana-fleet style). Per-POOL facts are shared and
computed once; each LaneBot applies its own thresholds and trades on its own
PerBotPositionManager/state. Ledger rows carry bot_id; the state file is
per-config keyed with legacy single-config migration to rh_young_v1."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import rh_paper_lane as mod  # noqa: E402
from rh_paper_lane import (  # noqa: E402
    LaneBot, ROSTER, LEGACY_BOT_ID, PaperLane,
    bite_gate, hour_allowed, ledger_iso, rise_from_open_pct,
    launch_trigger_blocks, flow_sums, entry_verdict,
    DIP_TRIGGER_PCT, MIN_LIQ_USD, MIN_POOL_AGE_H, DEMAND_MIN_BUY_USD,
    MAX_RT_COST_PCT, REENTRY_COOLDOWN_S, MAX_CONCURRENT,
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
    def __init__(self, watch=None):
        self.watch = watch if watch is not None else {}
        self.eth_price = 2000.0


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


class TestRoster:
    def test_roster_racers_unique_ids(self):
        # 10 scalp-fleet racers (mid-flight A/B, unchanged) + 3 aged-pool
        # racers (2026-07-11 decode-thesis cohort) + 5 candidate-factory
        # racers (2026-07-12 full-history mine, exclusion_group="factory")
        # + 1 LIVE FILL PROBE (2026-07-12, exclusion_group="fill_probe")
        # + 2 low-variance racers (2026-07-12, exclusion_group="lowvar")
        # + 1 deep-synth consolidated (2026-07-12 deep-decode, "deepsynth")
        # + 2 deep-exit barbell racers (2026-07-12 deep-cohort exit opt, "deepexit": un-capped control + _capped synthesis)
        # + 1 strength-trail exit racer (2026-07-12 winner-behavior decode, "strengthexit")
        # + 3 demand×depth racers (2026-07-13 winner-decode2) + 1 bank-fast (2026-07-13 exit/capture)
        # Roster grows as racers are mined — assert UNIQUENESS (the invariant), not a brittle count.
        assert len({b.bot_id for b in ROSTER}) == len(ROSTER)
        assert sum(1 for b in ROSTER
                   if b.exclusion_group == "factory") == 5
        assert sum(1 for b in ROSTER
                   if b.exclusion_group == "fill_probe") == 1
        assert sum(1 for b in ROSTER
                   if b.exclusion_group == "lowvar") == 2
        assert sum(1 for b in ROSTER
                   if b.exclusion_group == "deepsynth") == 1
        # deepexit: 1 (was 2 — rh_deep_barbell RETIRED 2026-07-17 by AxiS:
        # fidelity −$1,031 behind a +$16 paper mask; the capped synthesis stays)
        assert sum(1 for b in ROSTER
                   if b.exclusion_group == "deepexit") == 1
        assert sum(1 for b in ROSTER
                   if b.exclusion_group == "strengthexit") == 1

    def test_control_is_current_config_verbatim(self):
        c = ROSTER[0]
        assert c.bot_id == LEGACY_BOT_ID == "rh_young_v1"
        assert c.dip_trigger_pct == DIP_TRIGGER_PCT
        assert c.min_liq_usd == MIN_LIQ_USD
        assert c.min_pool_age_h == MIN_POOL_AGE_H
        assert c.demand_min_buy_usd == DEMAND_MIN_BUY_USD
        assert c.max_rt_cost_pct == MAX_RT_COST_PCT
        assert c.reentry_cooldown_s == REENTRY_COOLDOWN_S
        assert c.max_concurrent == MAX_CONCURRENT
        assert (c.tp1_pct, c.tp1_sell_fraction, c.tp2_pct) == (6.0, 0.75, 12.0)
        assert c.max_bites_per_token is None and not c.first_touch_only
        assert c.allowed_hours_utc is None and c.entry_mode == "dip"

    def test_hypothesis_axes(self):
        by_id = {b.bot_id: b for b in ROSTER}
        assert by_id["rh_deep_only"].dip_trigger_pct == -25.0
        assert by_id["rh_first_touch"].first_touch_only is True
        assert by_id["rh_bites2"].max_bites_per_token == 2
        w = by_id["rh_wide_ladder"]
        assert (w.tp1_pct, w.tp1_sell_fraction, w.tp2_pct) == (10.0, 0.75, 20.0)
        m = by_id["rh_moonbag"]
        assert (m.moonbag_fraction, m.moonbag_floor_pct,
                m.moonbag_trail_pp) == (0.10, 0.0, 20.0)
        assert by_id["rh_demand_heavy"].demand_min_buy_usd == 150.0
        assert by_id["rh_liq40"].min_liq_usd == 40_000.0
        assert by_id["rh_prime_hours"].allowed_hours_utc == (17, 18, 19, 20, 21)
        ls = by_id["rh_launch_scalp"]
        assert ls.entry_mode == "launch_strength"
        assert ls.time_stop_minutes == 10.0 and ls.hard_stop_pct == -8.0
        assert (ls.tp1_pct, ls.tp1_sell_fraction) == (5.0, 0.90)

    def test_bot_configs_construct(self):
        # BotConfig validation (fraction sums, moonbag bounds) must pass for
        # every racer or the lane dies at arm time.
        for b in ROSTER:
            cfg = b.bot_config()
            assert cfg.bot_id == b.bot_id


class TestPureGates:
    def test_bite_gate(self):
        assert bite_gate(False, None, 99) is None          # uncapped
        assert bite_gate(True, None, 0) is None            # first touch open
        assert bite_gate(True, None, 1) == "first_touch"
        assert bite_gate(False, 2, 1) is None
        assert bite_gate(False, 2, 2) == "bites_cap"
        assert bite_gate(True, 2, 1) == "first_touch"      # first_touch wins

    def test_hour_allowed(self):
        assert hour_allowed(None, 3) is True               # 24/7
        assert hour_allowed((17, 18, 19, 20, 21), 18) is True
        assert hour_allowed((17, 18, 19, 20, 21), 22) is False

    def test_ledger_iso_unique_within_second(self):
        a = ledger_iso(NOW, 1)
        b = ledger_iso(NOW, 2)
        assert a != b                                      # dedup-key safe
        assert a[:19] == b[:19]                            # same second
        assert a[:10] == "1970-01-12"                      # day aggregation ok
        assert a.endswith("+00:00")

    def test_rise_from_open(self):
        s = [(NOW - 300, 1.0), (NOW - 200, 1.05), (NOW - 10, 1.10)]
        assert abs(rise_from_open_pct(s, NOW) - 10.0) < 1e-9
        assert rise_from_open_pct([(NOW - 10, 1.0)], NOW) is None  # thin
        down = [(NOW - 300, 1.0), (NOW - 200, 0.9), (NOW - 10, 0.8)]
        assert rise_from_open_pct(down, NOW) < 0

    def test_launch_trigger_blocks(self):
        assert launch_trigger_blocks(5.0, 200.0, 150.0) == []
        assert "no_strength" in launch_trigger_blocks(-1.0, 200.0, 150.0)
        assert "no_strength" in launch_trigger_blocks(None, 200.0, 150.0)
        assert "weak_inflow" in launch_trigger_blocks(5.0, 100.0, 150.0)

    def test_flow_sums_window(self):
        rows = [_row("buy", 80, -20), _row("sell", 30, -5),
                _row("buy", 500, -300)]                    # 3rd outside 30s
        buys, sells = flow_sums(rows, NOW)
        assert (buys, sells) == (80.0, 30.0)
        buys2, _ = flow_sums(rows, NOW, window_s=600.0)
        assert buys2 == 580.0


class TestVerdictPerConfigThresholds:
    """Same shared pool facts -> different configs enter/block."""

    def _v(self, bot: LaneBot, dip=-20.0, buys=60.0, sells=10.0,
           liq=50_000.0, age_h=None, hour=13, bites=0):
        demand = buys >= bot.demand_min_buy_usd and (buys - sells) > 0
        return entry_verdict(
            dip, demand, {"avoid_block": False}, liq, True, 0, True, 0.0,
            age_h=age_h, drain_pct=None,
            dip_trigger_pct=bot.dip_trigger_pct, min_liq_usd=bot.min_liq_usd,
            min_pool_age_h=bot.min_pool_age_h,
            max_pool_age_h=bot.max_pool_age_h,
            max_concurrent=bot.max_concurrent,
            hour_ok=hour_allowed(bot.allowed_hours_utc, hour),
            bite_block=bite_gate(bot.first_touch_only,
                                 bot.max_bites_per_token, bites))

    def test_dip_threshold_routes(self):
        young = LaneBot(bot_id="a")
        deep = LaneBot(bot_id="b", dip_trigger_pct=-25.0)
        assert self._v(young, dip=-20.0)["enter"] is True
        assert "no_dip" in self._v(deep, dip=-20.0)["blocks"]
        assert self._v(deep, dip=-30.0)["enter"] is True

    def test_demand_threshold_routes(self):
        young = LaneBot(bot_id="a")
        heavy = LaneBot(bot_id="b", demand_min_buy_usd=150.0)
        assert self._v(young, buys=60.0)["enter"] is True
        assert "no_demand_turn" in self._v(heavy, buys=60.0)["blocks"]
        assert self._v(heavy, buys=200.0)["enter"] is True

    def test_liq_threshold_routes(self):
        young = LaneBot(bot_id="a")
        liq40 = LaneBot(bot_id="b", min_liq_usd=40_000.0)
        assert self._v(young, liq=35_000.0)["enter"] is True
        assert "liq_floor" in self._v(liq40, liq=35_000.0)["blocks"]

    def test_hour_window_routes(self):
        prime = LaneBot(bot_id="p", allowed_hours_utc=(17, 18, 19, 20, 21))
        assert "hour_window" in self._v(prime, hour=13)["blocks"]
        assert self._v(prime, hour=19)["enter"] is True

    def test_bite_policies_route(self):
        ft = LaneBot(bot_id="f", first_touch_only=True)
        b2 = LaneBot(bot_id="b", max_bites_per_token=2)
        assert self._v(ft, bites=0)["enter"] is True
        assert "first_touch" in self._v(ft, bites=1)["blocks"]
        assert self._v(b2, bites=1)["enter"] is True
        assert "bites_cap" in self._v(b2, bites=2)["blocks"]

    def test_age_ceiling_launch_mode(self):
        ls = [b for b in ROSTER if b.bot_id == "rh_launch_scalp"][0]
        # 30-min-old pool is past the 20-min launch window
        v = self._v(ls, age_h=0.5)
        assert "age_ceiling" in v["blocks"]
        # unknown age fails open on BOTH bounds (lane convention)
        v2 = entry_verdict(None, False, None, 50_000.0, True, 0, True, 0.0,
                           age_h=None, min_pool_age_h=ls.min_pool_age_h,
                           max_pool_age_h=ls.max_pool_age_h,
                           trigger_blocks=[])
        assert "age_floor" not in v2["blocks"]
        assert "age_ceiling" not in v2["blocks"]

    def test_trigger_blocks_replace_dip_pair(self):
        # launch mode: dip/demand judgments are skipped entirely
        v = entry_verdict(None, False, {"avoid_block": False}, 50_000.0,
                          True, 0, True, 0.0, trigger_blocks=[])
        assert v["enter"] is True
        v2 = entry_verdict(None, False, {"avoid_block": False}, 50_000.0,
                           True, 0, True, 0.0,
                           trigger_blocks=["no_strength"])
        assert v2["blocks"] == ["no_strength"]


class TestFleetEntryRouting:
    """Integration: ONE pool's shared facts routed through the full roster —
    different configs enter/block, quotes are NOT multiplied."""

    def _lane(self, tmp_path, monkeypatch, bots=None):
        _paths(tmp_path, monkeypatch)
        buy_in = int(25.0 / 2000.0 * 1e18)
        ex = FakeExecutor(buy_out_atomic=10 ** 21,           # 1000 tokens
                          sell_out_wei=int(buy_in * 0.97))   # 3% rt cost
        feed = FakeFeed({"0xp1": {"sym": "T", "liq": 50_000.0}})
        lane = PaperLane(feed, executor=ex,
                         registry={"0xp1": {"token": "0xtok"}},
                         bots=bots or ROSTER)
        lane.honeypot["0xtok"] = {"sellable": True}          # pre-cached
        return lane, ex

    def _dip_facts(self, lane):
        lane.prices["0xp1"] = [(NOW - 300, 1.0), (NOW - 200, 1.25),
                               (NOW - 10, 1.0)]              # -20% dip
        lane.tape["0xp1"] = [_row("buy", 40, -20), _row("buy", 25, -10),
                             _row("sell", 5, -5)]            # buys $65 net +

    def test_same_facts_route_per_config(self, tmp_path, monkeypatch):
        lane, ex = self._lane(tmp_path, monkeypatch)
        self._dip_facts(lane)
        lane._consider_entries(NOW)
        entered = {bid for bid, st in lane.state.items()
                   if "0xp1" in st.pos_meta}
        # dip -20, buys $65, liq 50k, hour 13 (NOW is deterministic).
        # Pool age is UNKNOWN (FakeFeed has no age_h) -> age gates fail open,
        # so the aged cohort qualifies too — but the exclusion group lets
        # only ONE aged racer take the token (roster order on the tie).
        # rh_fill_probe (2026-07-12): permissive gates pass here too — its
        # own exclusion_group, so it enters alongside (dormant = paper fill).
        # rh_lowvar_catstop (2026-07-12): control admission -> enters; its
        # sibling rh_lowvar_box is group-deduped out (one per token, roster
        # order breaks the tie). rh_deep_consolidated stays out: dip -20 is
        # shallower than its -25 capitulation trigger (no_dip below).
        # rh_stable_ageddeep (2026-07-13 stable-3): dip -12 trigger + default
        # $50 demand pass, 6h age gate fails OPEN on unknown age -> enters in its
        # OWN "stable" group (its two siblings are blocked: rh_stable_demand needs
        # $150 demand, rh_stable_deep needs a -25 dip).
        # rh_slcut_ageddeep (2026-07-17 SL1 loss-ladder A/B): same admission as
        # its parent rh_stable_ageddeep, but its OWN "slcut" group -> enters
        # alongside (paired A/B on the same token is the whole design; its two
        # slcut siblings are group-deduped/blocked the same way the parents are).
        # + the dipall entry-source quartet (2026-07-19): all four arms pass
        # on this aged synthetic pool (own-bot ids, no exclusion_group).
        assert entered == {"rh_young_v1", "rh_first_touch", "rh_bites2",
                           "rh_wide_ladder", "rh_moonbag", "rh_liq40",
                           "rh_aged_hold", "rh_fill_probe", "rh_lowvar_catstop",
                           "rh_stable_ageddeep", "rh_slcut_ageddeep",
                           "rh_dipall_ctrl", "rh_dipall_knife",
                           "rh_dipall_young1h", "rh_dipall_both",
                           "rh_bailfrac_ab", "rh_young_agedladder_ab",
                           "rh_letrun", "rh_letrun_sl1"}
        assert "no_dip" in lane.state["rh_deep_only"].block_hist
        assert "no_dip" in lane.state["rh_deep_consolidated"].block_hist
        assert "no_demand_turn" in lane.state["rh_demand_heavy"].block_hist
        assert "hour_window" in lane.state["rh_prime_hours"].block_hist
        assert "no_strength" in lane.state["rh_launch_scalp"].block_hist
        assert "sibling_excl" in lane.state["rh_aged_derisk"].block_hist
        assert "sibling_excl" in lane.state["rh_aged_deep"].block_hist

    def test_quote_budget_not_multiplied(self, tmp_path, monkeypatch):
        # 6 configs enter the same pool off ONE buy quote + ONE rt sell quote
        lane, ex = self._lane(tmp_path, monkeypatch)
        self._dip_facts(lane)
        lane._consider_entries(NOW)
        assert len([c for c in ex.calls if c[0] == "buy"]) == 1
        assert len([c for c in ex.calls if c[0] == "sell"]) == 1

    def test_rug_gate_enforce_blocks_whole_pool(self, tmp_path, monkeypatch):
        # ENFORCE + a warm CASHCATWIF-shape verdict -> NO config enters the pool,
        # NO buy/sell quote is spent, every entering config records rug_gate, and
        # one rug_gate_block ledger row is written. (0-latency dict read.)
        import time as _t
        monkeypatch.setenv("RH_RUG_GATE", "enforce")
        lane, ex = self._lane(tmp_path, monkeypatch)
        self._dip_facts(lane)
        lane._bs_prewarm["0xtok"] = (_t.time(), {
            "bs_source_ok": True, "bs_top1_pct": 10.6, "bs_top10_pct": 45.9})
        lane._consider_entries(NOW)
        entered = {bid for bid, st in lane.state.items()
                   if "0xp1" in st.pos_meta}
        assert entered == set()                              # whole pool blocked
        assert ex.calls == []                                # no quote spent
        assert any("rug_gate" in st.block_hist
                   for st in lane.state.values())
        rows = [r for r in _ledger_rows(tmp_path) if r["ev"] == "rug_gate_block"]
        assert len(rows) == 1 and rows[0]["token"] == "0xtok"

    def test_rug_gate_shadow_allows_entry_with_stamp(self, tmp_path, monkeypatch):
        # SHADOW + the same concentrated verdict -> entries STILL fire, and the
        # would-block verdict is stamped on the buy rows for grading.
        import time as _t
        monkeypatch.setenv("RH_RUG_GATE", "shadow")
        lane, ex = self._lane(tmp_path, monkeypatch)
        self._dip_facts(lane)
        lane._bs_prewarm["0xtok"] = (_t.time(), {
            "bs_source_ok": True, "bs_top1_pct": 10.6, "bs_top10_pct": 45.9})
        lane._consider_entries(NOW)
        entered = {bid for bid, st in lane.state.items()
                   if "0xp1" in st.pos_meta}
        assert entered                                       # shadow does NOT skip
        buys = [r for r in _ledger_rows(tmp_path) if r["ev"] == "buy"]
        assert buys and all(b["rug_gate"]["block"] is True for b in buys)

    def test_ledger_rows_carry_bot_id_and_unique_ts(self, tmp_path,
                                                    monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch)
        self._dip_facts(lane)
        lane._consider_entries(NOW)
        buys = [r for r in _ledger_rows(tmp_path) if r["ev"] == "buy"]
        # 6 scalp racers + 1 aged (group-deduped) + the fill probe (paper)
        # + 1 lowvar (group-deduped: catstop, not box) + 1 stable (ageddeep;
        # its two "stable" siblings are demand/dip-blocked) + 1 slcut (the SL1
        # A/B racer, own group, enters beside its parent by design) = 11
        # + the 2026-07-19 dipall entry-source quartet: this synthetic pool
        # (aged 5h in the fixture) passes ctrl+knife arms and, being >1h,
        # ALSO passes young1h+both -> all 4 enter (no exclusion_group) = 15
        # + the 07-20 exit-memo pair (bailfrac aged-clone, agedladder
        # young-clone; own/no groups) = 17
        assert len(buys) == 19
        assert all(r.get("bot_id") for r in buys)
        # dashboard ingest de-dups on (ts, ev, pool): keys must be distinct
        keys = {(r["ts"], r["ev"], r["pool"]) for r in buys}
        assert len(keys) == 19

    def test_launch_scalp_enters_on_strength_not_dip(self, tmp_path,
                                                     monkeypatch):
        lane, _ = self._lane(tmp_path, monkeypatch)
        lane.prices["0xp1"] = [(NOW - 300, 1.0), (NOW - 200, 1.05),
                               (NOW - 10, 1.10)]             # +10% vs open
        lane.tape["0xp1"] = [_row("buy", 120, -100), _row("buy", 80, -40),
                             _row("sell", 10, -20)]          # 120s net +$190
        lane._consider_entries(NOW)
        entered = {bid for bid, st in lane.state.items()
                   if "0xp1" in st.pos_meta}
        assert entered == {"rh_launch_scalp"}                # dip bots: no_dip
        assert "no_dip" in lane.state["rh_young_v1"].block_hist

    def test_bites_and_first_touch_block_reentry(self, tmp_path, monkeypatch):
        bots = (LaneBot(bot_id="rh_young_v1", reentry_cooldown_s=0.0),
                LaneBot(bot_id="rh_first_touch", first_touch_only=True,
                        reentry_cooldown_s=0.0),
                LaneBot(bot_id="rh_bites2", max_bites_per_token=2,
                        reentry_cooldown_s=0.0))
        lane, _ = self._lane(tmp_path, monkeypatch, bots=bots)
        self._dip_facts(lane)
        for st in lane.state.values():           # one prior bite each
            st.bites["0xp1"] = 1
        lane._consider_entries(NOW)
        entered = {bid for bid, st in lane.state.items()
                   if "0xp1" in st.pos_meta}
        assert entered == {"rh_young_v1", "rh_bites2"}
        assert "first_touch" in lane.state["rh_first_touch"].block_hist
        # bites counter advanced only for the racers that entered
        assert lane.state["rh_young_v1"].bites["0xp1"] == 2
        assert lane.state["rh_first_touch"].bites["0xp1"] == 1

    def test_bites_cap_blocks_third_entry(self, tmp_path, monkeypatch):
        bots = (LaneBot(bot_id="rh_bites2", max_bites_per_token=2,
                        reentry_cooldown_s=0.0),)
        lane, _ = self._lane(tmp_path, monkeypatch, bots=bots)
        self._dip_facts(lane)
        lane.state["rh_bites2"].bites["0xp1"] = 2
        lane._consider_entries(NOW)
        assert "0xp1" not in lane.state["rh_bites2"].pos_meta
        assert "bites_cap" in lane.state["rh_bites2"].block_hist


class TestSharedSellTicking:
    """A pool held by TWO configs is quoted ONCE, sized by the LARGER
    remaining qty; the price tick is shared (documented approximation)."""

    def _meta(self, rem):
        return {"qty_orig": 1000.0, "remaining_frac": rem, "token": "0xtok",
                "sym": "T", "entry_px": 1e-5, "entry_ts": 0.0}

    def test_doubly_held_pool_quoted_once_with_larger_qty(self):
        ex = FakeExecutor(sell_out_wei=int(0.005 * 1e18))
        bots = (LaneBot(bot_id="a"), LaneBot(bot_id="b"))
        lane = PaperLane(FakeFeed(), executor=ex, registry={}, bots=bots)
        lane.decimals["0xtok"] = 18
        lane.state["a"].pos_meta["0xp"] = self._meta(0.25)   # 250 left
        lane.state["b"].pos_meta["0xp"] = self._meta(1.0)    # 1000 left
        lane._quote_hot(NOW)
        sells = [c for c in ex.calls if c[0] == "sell"]
        assert len(sells) == 1                               # ONE quote
        assert sells[0][2] == int(1000.0 * 10 ** 18)         # larger qty
        assert lane.prices["0xp"][-1][1] > 0                 # shared tick


class TestFleetStatePersistence:
    def test_per_config_roundtrip(self, tmp_path, monkeypatch):
        _paths(tmp_path, monkeypatch)
        bots = (LaneBot(bot_id="rh_young_v1"), LaneBot(bot_id="rh_bites2",
                                                       max_bites_per_token=2))
        lane = PaperLane(FakeFeed(), executor=object(), registry={},
                         bots=bots)
        st = lane.state["rh_bites2"]
        st.pm.open_position(token="0xp1", entry_price=1e-8, size_usd=25.0,
                            entry_time=100.0, address="0xtok")
        st.pos_meta["0xp1"] = {"qty_orig": 5000.0, "remaining_frac": 1.0,
                               "token": "0xtok", "sym": "T",
                               "entry_px": 1e-8, "entry_ts": 100.0}
        st.daily_pnl_usd = -3.5
        st.bites["0xp1"] = 2
        lane.state["rh_young_v1"].daily_pnl_usd = 1.25
        lane.save_state()
        lane2 = PaperLane(FakeFeed(), executor=object(), registry={},
                          bots=bots)
        lane2.restore_state()
        st2 = lane2.state["rh_bites2"]
        assert "0xp1" in st2.pos_meta
        assert st2.pm.get_position("0xp1") is not None
        assert abs(st2.daily_pnl_usd - (-3.5)) < 1e-9
        assert st2.bites["0xp1"] == 2                        # cap survives
        assert abs(lane2.state["rh_young_v1"].daily_pnl_usd - 1.25) < 1e-9
        assert lane2.state["rh_young_v1"].pos_meta == {}

    def test_legacy_single_config_state_migrates_to_control(
            self, tmp_path, monkeypatch):
        _paths(tmp_path, monkeypatch)
        # build a pre-fleet state file (top-level pos_meta/pm_state)
        pm = mod.PerBotPositionManager(LaneBot(bot_id="x").bot_config())
        pm.open_position(token="0xp1", entry_price=1e-8, size_usd=25.0,
                         entry_time=100.0, address="0xtok")
        legacy = {"pos_meta": {"0xp1": {"qty_orig": 5000.0,
                                        "remaining_frac": 1.0,
                                        "token": "0xtok", "sym": "T",
                                        "entry_px": 1e-8, "entry_ts": 100.0}},
                  "daily_pnl_usd": -7.0,
                  "day": time.strftime("%Y-%m-%d", time.gmtime()),
                  "pm_state": pm.to_state_list()}
        with open(mod.STATE, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        lane = PaperLane(FakeFeed(), executor=object(), registry={},
                         bots=ROSTER)
        lane.restore_state()
        ctl = lane.state[LEGACY_BOT_ID]
        assert "0xp1" in ctl.pos_meta
        assert ctl.pm.get_position("0xp1") is not None
        assert abs(ctl.daily_pnl_usd - (-7.0)) < 1e-9
        # every other racer starts clean
        for bid, st in lane.state.items():
            if bid != LEGACY_BOT_ID:
                assert st.pos_meta == {} and st.daily_pnl_usd == 0.0

    def test_stale_day_zeroes_daily_pnl(self, tmp_path, monkeypatch):
        _paths(tmp_path, monkeypatch)
        bots = (LaneBot(bot_id="rh_young_v1"),)
        lane = PaperLane(FakeFeed(), executor=object(), registry={},
                         bots=bots)
        lane.state["rh_young_v1"].daily_pnl_usd = -9.9
        lane.save_state()
        raw = json.load(open(mod.STATE, encoding="utf-8"))
        raw["day"] = "2000-01-01"
        with open(mod.STATE, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        lane2 = PaperLane(FakeFeed(), executor=object(), registry={},
                          bots=bots)
        lane2.restore_state()
        assert lane2.state["rh_young_v1"].daily_pnl_usd == 0.0


class TestSellSideBotId:
    """Sell ledger rows + post-exit pending rows carry the racer's bot_id."""

    def test_sell_row_stamps_bot_id(self, tmp_path, monkeypatch):
        _paths(tmp_path, monkeypatch)
        from types import SimpleNamespace
        ex = FakeExecutor(sell_out_wei=int(0.005 * 1e18))
        bots = (LaneBot(bot_id="rh_young_v1"), LaneBot(bot_id="rh_moonbag",
                                                       moonbag_fraction=0.10,
                                                       moonbag_trail_pp=20.0))
        lane = PaperLane(FakeFeed(), executor=ex, registry={}, bots=bots)
        lane.decimals["0xtok"] = 18
        st = lane.state["rh_moonbag"]
        st.pm.open_position(token="0xp", entry_price=1e-5, size_usd=25.0,
                            entry_time=NOW - 600, address="0xtok")
        meta = {"qty_orig": 1000.0, "remaining_frac": 1.0, "token": "0xtok",
                "sym": "T", "entry_px": 1e-5, "entry_ts": NOW - 600}
        st.pos_meta["0xp"] = meta
        lane._paper_sell("0xp", meta, SimpleNamespace(
            kind="TP1", sell_fraction=0.75, reason="tp1"), NOW, st=st)
        sells = [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"]
        assert len(sells) == 1 and sells[0]["bot_id"] == "rh_moonbag"
        # exits/cooldowns land on the RIGHT racer
        assert st.pos_meta["0xp"]["remaining_frac"] == 0.25
        assert lane.state["rh_young_v1"].daily_pnl_usd == 0.0

    def test_postexit_pending_carries_bot_id(self, tmp_path, monkeypatch):
        _paths(tmp_path, monkeypatch)
        from types import SimpleNamespace
        ex = FakeExecutor(sell_out_wei=int(0.005 * 1e18))
        bots = (LaneBot(bot_id="rh_deep_only", dip_trigger_pct=-25.0),)
        lane = PaperLane(FakeFeed(), executor=ex, registry={}, bots=bots)
        lane.decimals["0xtok"] = 18
        st = lane.state["rh_deep_only"]
        st.pm.open_position(token="0xp", entry_price=1e-5, size_usd=25.0,
                            entry_time=NOW - 600, address="0xtok")
        meta = {"qty_orig": 1000.0, "remaining_frac": 1.0, "token": "0xtok",
                "sym": "T", "entry_px": 1e-5, "entry_ts": NOW - 600}
        st.pos_meta["0xp"] = meta
        lane._paper_sell("0xp", meta, SimpleNamespace(
            kind="HARD_STOP", sell_fraction=1.0, reason="stop"), NOW, st=st)
        with open(tmp_path / "pe.jsonl", encoding="utf-8") as f:
            pend = [json.loads(x) for x in f if x.strip()]
        assert len(pend) == 1 and pend[0]["bot_id"] == "rh_deep_only"
        assert st.n_exits == 1 and st.last_exit["0xp"] == NOW


class TestBackCompatSurface:
    """Pre-fleet callers/tests see a single-config lane (primary = first
    roster entry = rh_young_v1)."""

    def test_default_lane_is_single_control(self):
        lane = PaperLane(FakeFeed(), executor=object(), registry={})
        assert [b.bot_id for b in lane.bots] == [LEGACY_BOT_ID]
        assert lane.pm is lane.state[LEGACY_BOT_ID].pm
        assert lane.pos_meta is lane.state[LEGACY_BOT_ID].pos_meta
        lane.daily_pnl_usd = -2.0
        assert lane.state[LEGACY_BOT_ID].daily_pnl_usd == -2.0

    def test_counters_aggregate_across_fleet(self):
        lane = PaperLane(FakeFeed(), executor=object(), registry={},
                         bots=(LaneBot(bot_id="a"), LaneBot(bot_id="b")))
        lane.state["a"].n_entries = 2
        lane.state["b"].n_entries = 3
        lane.state["b"].n_exits = 1
        assert lane.n_entries == 5 and lane.n_exits == 1

    def test_summary_has_per_bot_lines(self):
        lane = PaperLane(FakeFeed(), executor=object(), registry={},
                         bots=(LaneBot(bot_id="a"), LaneBot(bot_id="b")))
        s = lane.summary()
        assert "[rh-paper] a: entries=0" in s
        assert "[rh-paper] b: entries=0" in s
