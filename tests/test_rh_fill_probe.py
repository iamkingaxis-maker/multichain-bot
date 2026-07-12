# tests/test_rh_fill_probe.py
"""RH LIVE FILL PROBE (2026-07-12) — the routing glue between the paper lane
and core/rh_live_execution.RhLiveExecutor.

DORMANCY FIRST: with any of the FOUR conditions missing (triple gate legs +
RH_LIVE_PROBE_BOTS opt-in) the probe racer is pure paper and the lane never
touches a live executor. Then: routing gate combos, live fill booking (paper
ledger row marked live=true, real fill numbers), per-leg fill telemetry
(ledger + rh_live_fills.jsonl), error classification (pre_send / reverted /
unknown_spend = the Solana E1b class), sell fail-safety (position survives a
reverted live exit), the daily buy cap, state persistence, and the dust
test's dry-run mode. All offline — no network, no keys, ever."""
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import rh_paper_lane as mod  # noqa: E402
from rh_paper_lane import (  # noqa: E402
    ENTRY_USD, LaneBot, PaperLane, ROSTER,
    classify_live_error, daily_buys_block, fill_telemetry,
    live_probe_bots, live_route_open,
)
import core.rh_live_execution as rh_live  # noqa: E402
import rh_dust_test as dust  # noqa: E402

NOW = 1_000_000.0
TX = "0x" + "ab" * 32
GATE_ENV = ("RH_LIVE_CONFIRMED", "RH_PAPER_MODE", "RH_PRIVATE_KEY",
            "RH_LIVE_PROBE_BOTS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Determinism: every test starts with all four conditions CLOSED."""
    for k in GATE_ENV:
        monkeypatch.delenv(k, raising=False)


def _open_all(monkeypatch, bots="rh_fill_probe"):
    monkeypatch.setenv("RH_LIVE_CONFIRMED", "true")
    monkeypatch.setenv("RH_PAPER_MODE", "false")
    monkeypatch.setenv("RH_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("RH_LIVE_PROBE_BOTS", bots)


def _paths(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mod, "LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setattr(mod, "LIVE_FILLS", str(tmp_path / "fills.jsonl"))
    monkeypatch.setattr(mod, "POSTEXIT_PENDING", str(tmp_path / "pe.jsonl"))


def _rows(tmp_path, name="ledger.jsonl"):
    p = tmp_path / name
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in open(p, encoding="utf-8")
            if x.strip()]


class FakeQuote:
    def __init__(self, amount_in, amount_out):
        self.amount_in, self.amount_out = amount_in, amount_out
        self.fee = 10000


class FakeExecutor:
    """Paper quote rail (shared lane machinery)."""

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


def _live_rec(side, amount_out, gas_eth=1.6e-6):
    return {"side": side, "tx_signature": TX, "amount_out": amount_out,
            "quoted_out": amount_out, "real_fill_price": 6.25e-6,
            "decision_mid_price": 6.3e-6, "fill_vs_mid_slippage_pct": -0.4,
            "gas_cost_eth": gas_eth, "total_latency_ms": 420.0,
            "route": "uniswap_v3_direct", "fee_tier": 10000, "success": True}


class FakeLive:
    """RhLiveExecutor stand-in (records shaped like rh_live_swaps.jsonl)."""

    def __init__(self, buy_exc=None, sell_exc=None,
                 buy_out=6 * 10 ** 20, sell_out=int(0.0029e18)):
        self.buy_exc, self.sell_exc = buy_exc, sell_exc
        self.buy_out, self.sell_out = buy_out, sell_out
        self.buy_calls, self.sell_calls, self.realized = [], [], []

    def live_buy(self, token, usd, eth_price, **kw):
        self.buy_calls.append((token, usd, eth_price))
        if self.buy_exc:
            raise self.buy_exc
        return _live_rec("buy", self.buy_out)

    def live_sell(self, token, amount, **kw):
        self.sell_calls.append((token, amount))
        if self.sell_exc:
            raise self.sell_exc
        return _live_rec("sell", self.sell_out)

    def record_realized(self, pnl, now=None):
        self.realized.append(pnl)
        return {}


def _probe_bot(**kw):
    d = dict(bot_id="rh_fill_probe", min_liq_usd=30_000.0, max_concurrent=1,
             entry_usd=7.5, max_buys_per_day=4,
             exclusion_group="fill_probe")
    d.update(kw)
    return LaneBot(**d)


def _lane(tmp_path, monkeypatch, bots=None):
    _paths(tmp_path, monkeypatch)
    buy_in = int(ENTRY_USD / 2000.0 * 1e18)
    ex = FakeExecutor(buy_out_atomic=10 ** 21,           # 1000 tokens / $25
                      sell_out_wei=int(buy_in * 0.97))   # 3% rt cost: passes
    lane = PaperLane(FakeFeed({"0xp1": {"sym": "T", "liq": 50_000.0}}),
                     executor=ex, registry={"0xp1": {"token": "0xtok"}},
                     bots=bots or (_probe_bot(),))
    lane.honeypot["0xtok"] = {"sellable": True}
    return lane, ex


def _buy(lane, st):
    lane._paper_buy("0xp1", "0xtok", {"sym": "T", "liq": 50_000.0},
                    -15.0, {"avoid_block": False}, NOW, [], states=[st])


def _open_live_pos(lane, st, qty=600.0, usd=7.5):
    st.pm.open_position(token="0xp1", entry_price=6.25e-6, size_usd=usd,
                        entry_time=NOW - 300, address="0xtok")
    meta = {"qty_orig": qty, "remaining_frac": 1.0, "token": "0xtok",
            "sym": "T", "entry_px": 6.25e-6, "entry_ts": NOW - 300,
            "usd_size": usd, "live": True, "tx_buy": TX,
            "buy_gas_usd": 0.01}
    st.pos_meta["0xp1"] = meta
    return meta


# ════════════════════════ roster config ═════════════════════════════════════
class TestProbeConfig:
    def test_probe_in_roster_with_task_params(self):
        by_id = {b.bot_id: b for b in ROSTER}
        p = by_id["rh_fill_probe"]
        assert p.entry_usd == mod.PROBE_SIZE_USD
        assert p.max_buys_per_day == mod.PROBE_MAX_BUYS_DAY
        assert p.max_concurrent == 1
        assert p.min_liq_usd == 30_000.0
        assert p.exclusion_group == "fill_probe"
        assert p.entry_mode == "dip"                 # standard young trigger
        assert p.dip_trigger_pct == mod.DIP_TRIGGER_PCT
        # PERMISSIVE: no candidate-factory extras on the probe
        assert p.min_session_vol_usd is None and p.max_arc_pct is None
        assert p.min_buys_30s is None and p.require_pop_within_s is None
        assert p.dip_max_depth_pct is None
        # full NORMAL exit ladder (fills on both legs are the point)
        assert (p.tp1_pct, p.tp1_sell_fraction, p.tp2_pct) == (6.0, 0.75, 12.0)
        assert p.hard_stop_pct == -15.0 and p.time_stop_minutes is None
        p.bot_config()                               # BotConfig validates

    def test_probe_defaults(self):
        assert mod.PROBE_SIZE_USD == pytest.approx(
            float(os.environ.get("RH_PROBE_SIZE_USD", "7.50")))
        assert mod.PROBE_MAX_BUYS_DAY == int(
            os.environ.get("RH_PROBE_MAX_BUYS_DAY", "4"))

    def test_preexisting_racers_untouched(self):
        for b in ROSTER:
            if b.bot_id != "rh_fill_probe":
                assert b.entry_usd is None
                assert b.max_buys_per_day is None


# ════════════════════════ routing gate (4 conditions) ═══════════════════════
class TestRoutingGate:
    def test_probe_bots_parsing(self, monkeypatch):
        assert live_probe_bots() == set()
        monkeypatch.setenv("RH_LIVE_PROBE_BOTS", " rh_fill_probe , x ,")
        assert live_probe_bots() == {"rh_fill_probe", "x"}

    @pytest.mark.parametrize("confirmed,paper,key,optin", [
        (c, p, k, o)
        for c in (0, 1) for p in (0, 1) for k in (0, 1) for o in (0, 1)
        if not (c and p and k and o)])
    def test_any_missing_condition_routes_paper(self, monkeypatch, confirmed,
                                                paper, key, optin):
        if confirmed:
            monkeypatch.setenv("RH_LIVE_CONFIRMED", "true")
        if paper:                                  # paper=1 means gate leg OPEN
            monkeypatch.setenv("RH_PAPER_MODE", "false")
        if key:
            monkeypatch.setenv("RH_PRIVATE_KEY", "0x" + "11" * 32)
        if optin:
            monkeypatch.setenv("RH_LIVE_PROBE_BOTS", "rh_fill_probe")
        assert live_route_open("rh_fill_probe") is False

    def test_all_four_open(self, monkeypatch):
        _open_all(monkeypatch)
        assert live_route_open("rh_fill_probe") is True
        assert live_route_open("rh_young_v1") is False   # not opted in

    def test_env_read_at_call_time(self, monkeypatch):
        _open_all(monkeypatch)
        assert live_route_open("rh_fill_probe") is True
        monkeypatch.delenv("RH_PRIVATE_KEY")
        assert live_route_open("rh_fill_probe") is False


# ════════════════════════ dormancy (pure paper) ═════════════════════════════
class TestDormancy:
    def test_dormant_probe_books_pure_paper(self, tmp_path, monkeypatch):
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive()          # would explode the test if touched
        st = lane.state["rh_fill_probe"]
        _buy(lane, st)
        assert lane._live.buy_calls == []            # never routed
        rows = [r for r in _rows(tmp_path) if r["ev"] == "buy"]
        assert len(rows) == 1
        assert "live" not in rows[0] and "fill" not in rows[0]
        assert rows[0]["usd"] == 7.5                 # probe sizing still paper
        # paper qty scales linearly off the shared $25 quote
        assert rows[0]["qty"] == pytest.approx(1000.0 * 7.5 / ENTRY_USD)
        assert not os.path.exists(tmp_path / "fills.jsonl")
        assert "live" not in st.pos_meta["0xp1"]

    def test_preexisting_racer_row_byte_shape_unchanged(self, tmp_path,
                                                        monkeypatch):
        lane, ex = _lane(tmp_path, monkeypatch,
                         bots=(LaneBot(bot_id="rh_young_v1"),))
        st = lane.state["rh_young_v1"]
        _buy(lane, st)
        r = [x for x in _rows(tmp_path) if x["ev"] == "buy"][0]
        assert r["usd"] == ENTRY_USD and r["qty"] == pytest.approx(1000.0)
        assert "live" not in r and "fill" not in r
        assert lane._live is None                    # never constructed

    def test_opt_in_without_gate_stays_paper(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RH_LIVE_PROBE_BOTS", "rh_fill_probe")
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive()
        _buy(lane, lane.state["rh_fill_probe"])
        assert lane._live.buy_calls == []

    def test_gate_without_opt_in_stays_paper(self, tmp_path, monkeypatch):
        _open_all(monkeypatch, bots="somebody_else")
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive()
        _buy(lane, lane.state["rh_fill_probe"])
        assert lane._live.buy_calls == []

    def test_dormant_sell_never_touches_live(self, tmp_path, monkeypatch):
        lane, ex = _lane(tmp_path, monkeypatch)
        st = lane.state["rh_fill_probe"]
        st.pm.open_position(token="0xp1", entry_price=1e-5, size_usd=7.5,
                            entry_time=NOW - 60, address="0xtok")
        st.pos_meta["0xp1"] = {"qty_orig": 750.0, "remaining_frac": 1.0,
                               "token": "0xtok", "sym": "T",
                               "entry_px": 1e-5, "entry_ts": NOW - 60,
                               "usd_size": 7.5}     # NO live flag
        lane._live = FakeLive()
        lane._paper_sell("0xp1", st.pos_meta["0xp1"], SimpleNamespace(
            kind="TP1", sell_fraction=1.0, reason="x"), NOW, st=st)
        assert lane._live.sell_calls == []
        r = [x for x in _rows(tmp_path) if x["ev"] == "sell"][0]
        assert "live" not in r


# ════════════════════════ live buy routing ══════════════════════════════════
class TestLiveBuy:
    def test_live_buy_books_real_fill(self, tmp_path, monkeypatch):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        live = FakeLive()
        lane._live = live
        st = lane.state["rh_fill_probe"]
        _buy(lane, st)
        assert live.buy_calls == [("0xtok", 7.5, 2000.0)]
        meta = st.pos_meta["0xp1"]
        assert meta["live"] is True and meta["tx_buy"] == TX
        assert meta["qty_orig"] == pytest.approx(600.0)   # LIVE amount_out
        assert meta["entry_px"] == pytest.approx(6.25e-6)  # LIVE fill px
        assert meta["usd_size"] == 7.5
        r = [x for x in _rows(tmp_path) if x["ev"] == "buy"][0]
        assert r["live"] is True and r["usd"] == 7.5
        assert r["price_eth"] == pytest.approx(6.25e-6)
        assert r["qty"] == pytest.approx(600.0)
        fills = _rows(tmp_path, "fills.jsonl")
        assert len(fills) == 1 and fills[0]["leg"] == "buy"
        assert fills[0]["tx"] == TX and fills[0]["usd"] == 7.5

    def test_buy_telemetry_on_ledger_row(self, tmp_path, monkeypatch):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive()
        _buy(lane, lane.state["rh_fill_probe"])
        tel = [x for x in _rows(tmp_path) if x["ev"] == "buy"][0]["fill"]
        for k in ("decision_ts", "quote_ts", "order_sent_ts",
                  "landed_wall_ts", "tx_landed_ts", "decision_to_landed_ms",
                  "fill_vs_quote_pct", "gas_cost_eth", "tx"):
            assert k in tel
        assert tel["decision_to_landed_ms"] >= 0
        assert tel["fill_vs_quote_pct"] == -0.4
        assert tel["gas_cost_eth"] == pytest.approx(1.6e-6)

    def test_pre_send_failure_books_nothing(self, tmp_path, monkeypatch):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive(buy_exc=rh_live.RhContainmentError(
            "position cap: $7.50 > $5.00"))
        st = lane.state["rh_fill_probe"]
        _buy(lane, st)
        assert st.pos_meta == {} and st.pm.get_position("0xp1") is None
        assert st.n_entries == 0 and st.day_buys == 0
        errs = [x for x in _rows(tmp_path) if x["ev"] == "rh_live_exec_error"]
        assert len(errs) == 1
        assert errs[0]["class"] == "pre_send" and errs[0]["leg"] == "buy"
        assert errs[0]["manual_reconcile"] is False
        assert [x for x in _rows(tmp_path) if x["ev"] == "buy"] == []

    def test_unknown_spend_buy_is_loud_manual_reconcile(self, tmp_path,
                                                        monkeypatch, capsys):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive(buy_exc=rh_live.RhSwapError(
            f"buy 0xtok failed: receipt timeout tx={TX}"))
        st = lane.state["rh_fill_probe"]
        _buy(lane, st)
        assert st.pos_meta == {}                       # never book a guess
        errs = [x for x in _rows(tmp_path) if x["ev"] == "rh_live_exec_error"]
        assert errs[0]["class"] == "unknown_spend"
        assert errs[0]["manual_reconcile"] is True
        out = capsys.readouterr().out
        assert "MANUAL RECONCILE" in out and "E1b" in out

    def test_undecodable_landed_fill_is_unknown_spend(self, tmp_path,
                                                      monkeypatch):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        live = FakeLive()
        live.buy_out = 0                                # landed, no decode
        lane._live = live
        st = lane.state["rh_fill_probe"]
        _buy(lane, st)
        assert st.pos_meta == {}
        errs = [x for x in _rows(tmp_path) if x["ev"] == "rh_live_exec_error"]
        assert errs and errs[0]["class"] == "unknown_spend"


# ════════════════════════ live sell routing ═════════════════════════════════
class TestLiveSell:
    def test_partial_sell_exact_atomic_amount(self, tmp_path, monkeypatch):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        live = FakeLive()
        lane._live = live
        st = lane.state["rh_fill_probe"]
        meta = _open_live_pos(lane, st)
        lane._paper_sell("0xp1", meta, SimpleNamespace(
            kind="TP1", sell_fraction=0.75, reason="tp1"), NOW, st=st)
        assert live.sell_calls == [("0xtok", int(450.0 * 1e18))]
        assert meta["remaining_frac"] == pytest.approx(0.25)
        r = [x for x in _rows(tmp_path) if x["ev"] == "sell"][0]
        assert r["live"] is True and "fill" in r
        # REAL proceeds: 0.0029 ETH * $2000 = $5.80
        assert r["usd_out"] == pytest.approx(5.80, abs=0.01)
        fills = _rows(tmp_path, "fills.jsonl")
        assert fills[-1]["leg"] == "sell" and fills[-1]["frac"] == 0.75
        # realized live pnl feeds the executor daily stop
        assert len(live.realized) == 1
        assert live.realized[0] == pytest.approx(r["pnl_usd"], abs=0.01)

    def test_full_close_sells_all(self, tmp_path, monkeypatch):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        live = FakeLive()
        lane._live = live
        st = lane.state["rh_fill_probe"]
        meta = _open_live_pos(lane, st)
        lane._paper_sell("0xp1", meta, SimpleNamespace(
            kind="STOP", sell_fraction=1.0, reason="stop"), NOW, st=st)
        assert live.sell_calls == [("0xtok", "all")]   # dust swept
        assert "0xp1" not in st.pos_meta

    def test_reverted_sell_keeps_position_and_retries_throttled(
            self, tmp_path, monkeypatch):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        live = FakeLive(sell_exc=rh_live.RhSwapError(
            f"sell 0xtok failed: revert (status=0) tx={TX}"))
        lane._live = live
        st = lane.state["rh_fill_probe"]
        meta = _open_live_pos(lane, st)
        d = SimpleNamespace(kind="TP1", sell_fraction=0.75, reason="tp1")
        lane._paper_sell("0xp1", meta, d, NOW, st=st)
        # position INTACT — nothing changed on-chain
        assert "0xp1" in st.pos_meta
        assert meta["remaining_frac"] == 1.0
        assert st.pm.get_position("0xp1") is not None
        assert [x for x in _rows(tmp_path) if x["ev"] == "sell"] == []
        errs = [x for x in _rows(tmp_path) if x["ev"] == "rh_live_exec_error"]
        assert errs[0]["class"] == "reverted" and errs[0]["leg"] == "sell"
        # retry inside the cooldown: NO second live attempt
        lane._paper_sell("0xp1", meta, d, NOW + 5, st=st)
        assert len(live.sell_calls) == 1
        # past the cooldown: retries
        lane._paper_sell("0xp1", meta, d,
                         NOW + mod.LIVE_SELL_RETRY_COOLDOWN_S + 1, st=st)
        assert len(live.sell_calls) == 2

    def test_unknown_spend_sell_books_estimate_with_flag(self, tmp_path,
                                                         monkeypatch, capsys):
        _open_all(monkeypatch)
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive(sell_exc=rh_live.RhSwapError(
            f"sell 0xtok failed: receipt timeout tx={TX}"))
        st = lane.state["rh_fill_probe"]
        meta = _open_live_pos(lane, st)
        lane._paper_sell("0xp1", meta, SimpleNamespace(
            kind="STOP", sell_fraction=1.0, reason="stop"), NOW, st=st)
        assert "0xp1" not in st.pos_meta          # closed on the estimate
        r = [x for x in _rows(tmp_path) if x["ev"] == "sell"][0]
        assert r["live"] is True
        assert r["live_unconfirmed"] is True and r["manual_reconcile"] is True
        assert "fill" not in r                    # no confirmed fill exists
        assert "MANUAL RECONCILE" in capsys.readouterr().out

    def test_gate_closed_mid_hold_keeps_position(self, tmp_path, monkeypatch):
        """Env torn down while a live position is open: the REAL executor
        refuses (RhLiveGateError -> pre_send) and the position survives —
        a live bag must never be paper-closed while tokens sit on-chain."""
        lane, ex = _lane(tmp_path, monkeypatch)
        lane._live = rh_live.RhLiveExecutor()     # real policy layer, no env
        st = lane.state["rh_fill_probe"]
        meta = _open_live_pos(lane, st)
        lane._paper_sell("0xp1", meta, SimpleNamespace(
            kind="STOP", sell_fraction=1.0, reason="stop"), NOW, st=st)
        assert "0xp1" in st.pos_meta
        errs = [x for x in _rows(tmp_path) if x["ev"] == "rh_live_exec_error"]
        assert errs and errs[0]["class"] == "pre_send"


# ════════════════════════ daily buy cap ═════════════════════════════════════
class TestDailyBuyCap:
    def test_daily_buys_block_pure(self):
        assert daily_buys_block(0, None) is None
        assert daily_buys_block(99, None) is None
        assert daily_buys_block(3, 4) is None
        assert daily_buys_block(4, 4) == "daily_buys_cap"
        assert daily_buys_block(5, 4) == "daily_buys_cap"

    def test_day_buys_increment_on_booking(self, tmp_path, monkeypatch):
        lane, ex = _lane(tmp_path, monkeypatch)
        st = lane.state["rh_fill_probe"]
        _buy(lane, st)
        assert st.day_buys == 1

    def test_day_buys_persist_same_day(self, tmp_path, monkeypatch):
        lane, ex = _lane(tmp_path, monkeypatch)
        lane.state["rh_fill_probe"].day_buys = 3
        lane.save_state()
        lane2, _ = _lane(tmp_path, monkeypatch)
        lane2.restore_state()
        assert lane2.state["rh_fill_probe"].day_buys == 3

    def test_day_buys_reset_on_stale_day(self, tmp_path, monkeypatch):
        lane, ex = _lane(tmp_path, monkeypatch)
        lane.state["rh_fill_probe"].day_buys = 4
        lane.save_state()
        raw = json.load(open(mod.STATE, encoding="utf-8"))
        raw["day"] = "2000-01-01"
        with open(mod.STATE, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        lane2, _ = _lane(tmp_path, monkeypatch)
        lane2.restore_state()
        assert lane2.state["rh_fill_probe"].day_buys == 0

    def test_cap_blocks_in_entry_verdict_path(self):
        from rh_paper_lane import entry_verdict
        v = entry_verdict(-15.0, True, {"avoid_block": False}, 50_000.0,
                          True, 0, True, 0.0,
                          extra_blocks=["daily_buys_cap"])
        assert v["enter"] is False and "daily_buys_cap" in v["blocks"]


# ════════════════════════ error classification ══════════════════════════════
class TestClassifyLiveError:
    def test_gate_and_containment_are_pre_send(self):
        assert classify_live_error(
            rh_live.RhLiveGateError("live gate CLOSED")) == "pre_send"
        assert classify_live_error(
            rh_live.RhContainmentError("position cap")) == "pre_send"
        assert classify_live_error(
            rh_live.RhCanaryHaltError("canary red")) == "pre_send"

    def test_swap_error_without_tx_is_pre_send(self):
        assert classify_live_error(rh_live.RhSwapError(
            "no V3 route for buy 0xtok")) == "pre_send"
        assert classify_live_error(rh_live.RhSwapError(
            "buy 0xtok failed: whatever tx=None")) == "pre_send"

    def test_mined_revert_is_reverted(self):
        assert classify_live_error(rh_live.RhSwapError(
            f"sell 0xt failed: revert (status=0) tx={TX}")) == "reverted"
        assert classify_live_error(rh_live.RhSwapError(
            f"approve reverted tx={TX}")) == "reverted"

    def test_broadcast_unknown_is_unknown_spend(self):
        assert classify_live_error(rh_live.RhSwapError(
            f"buy 0xt failed: is not in the chain after 60s tx={TX}"
        )) == "unknown_spend"

    def test_non_executor_error_is_pre_send(self):
        assert classify_live_error(ValueError("boom")) == "pre_send"


# ════════════════════════ telemetry shape ═══════════════════════════════════
class TestFillTelemetry:
    def test_shape_and_math(self):
        rec = _live_rec("buy", 10 ** 18)
        tel = fill_telemetry(rec, 100.0, 100.4, 100.5, 101.7,
                             tx_landed_ts=1_752_000_000)
        assert tel["decision_to_landed_ms"] == pytest.approx(1700.0)
        assert tel["tx_landed_ts"] == 1_752_000_000
        assert tel["quote_ts"] == 100.4 and tel["order_sent_ts"] == 100.5
        assert tel["fill_vs_quote_pct"] == -0.4
        assert tel["gas_cost_eth"] == pytest.approx(1.6e-6)
        assert tel["tx"] == TX and tel["fee_tier"] == 10000

    def test_never_raises_on_garbage(self):
        tel = fill_telemetry({}, None, None, None, "not-a-float")
        assert tel["decision_to_landed_ms"] is None
        assert tel["tx"] is None

    def test_tx_landed_ts_fail_open(self, tmp_path, monkeypatch):
        lane, _ = _lane(tmp_path, monkeypatch)
        lane._live = FakeLive()                    # no _executor attr
        assert lane._tx_landed_ts(TX) is None
        assert lane._tx_landed_ts(None) is None


# ════════════════════════ dust test (dry-run) ═══════════════════════════════
class TestDustTestDryRun:
    def test_dry_run_passes(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(dust, "LIVE_FILLS", str(tmp_path / "df.jsonl"))
        rc = dust.run(["--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "dust_buy" in out and "dust_sell" in out
        assert "DUST TEST PASSED" in out
        rows = [json.loads(x) for x in
                open(tmp_path / "df.jsonl", encoding="utf-8") if x.strip()]
        assert [r["leg"] for r in rows] == ["dust_buy", "dust_sell"]
        for r in rows:
            assert "decision_to_landed_ms" in r and "gas_cost_eth" in r

    def test_dry_run_buy_failure_exit_5(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dust, "LIVE_FILLS", str(tmp_path / "df.jsonl"))
        rc = dust.run(["--dry-run"], live=dust.DryRunLive(fail_leg="buy"))
        assert rc == 5

    def test_dry_run_sell_failure_exit_6_loud(self, tmp_path, monkeypatch,
                                              capsys):
        monkeypatch.setattr(dust, "LIVE_FILLS", str(tmp_path / "df.jsonl"))
        rc = dust.run(["--dry-run"], live=dust.DryRunLive(fail_leg="sell"))
        assert rc == 6
        assert "DUST STUCK" in capsys.readouterr().out

    def test_dry_run_wallet_truth_failure_exit_3(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dust, "LIVE_FILLS", str(tmp_path / "df.jsonl"))
        rc = dust.run(["--dry-run"], truth_fn=lambda **k: {"ok": False})
        assert rc == 3

    def test_real_mode_refuses_with_gate_closed(self, capsys):
        rc = dust.run([])                           # env cleaned by fixture
        assert rc == 2
        assert "REFUSED" in capsys.readouterr().out


# ════════════════════════ wallet helper ═════════════════════════════════════
class TestMakeWallet:
    def test_gitignore_actually_covers_key_file(self):
        import rh_make_wallet as mw
        assert mw.gitignore_covers() is True        # repo .gitignore updated

    def test_gitignore_covers_fail_closed(self, tmp_path):
        import rh_make_wallet as mw
        gi = tmp_path / ".gitignore"
        assert mw.gitignore_covers(str(gi)) is False       # missing file
        gi.write_text("*.env\n", encoding="utf-8")
        assert mw.gitignore_covers(str(gi)) is False       # not covered
        gi.write_text("*.env\nrh_wallet_key.txt\n", encoding="utf-8")
        assert mw.gitignore_covers(str(gi)) is True

    def test_key_never_required_at_import(self):
        # importing every probe-facing module must work keyless (fixture
        # already stripped the env): reaching here proves rh_paper_lane,
        # rh_dust_test and rh_make_wallet imported without RH_PRIVATE_KEY.
        import rh_make_wallet  # noqa: F401
        assert os.environ.get("RH_PRIVATE_KEY") is None
