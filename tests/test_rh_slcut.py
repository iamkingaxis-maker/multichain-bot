# tests/test_rh_slcut.py
"""SL1 LOSS-SIDE LADDER (2026-07-17 dollar-conversion mine).

The fleet's leak is structural: median trade beats the tape every day, net $
is red every day — wins bank in partials while losses close FULL-SIZE
(HARD_STOP -$1,641 at -$5.18/leg vs +$1.15 avg win leg). SL1 mirrors TP1
downward: pre-TP1, first touch of sl1_pct sells sl1_sell_fraction; only the
tail rides to the stop. Default None = byte-identical for every other bot.
Priced by A/B race (rh_slcut_* vs zero-illusion parents), never assumed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import rh_paper_lane as mod  # noqa: E402
from rh_paper_lane import LaneBot, PaperLane  # noqa: E402

NOW = 1_000_000.0


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


class FakeQuote:
    def __init__(self, amount_in, amount_out):
        self.amount_in, self.amount_out = amount_in, amount_out
        self.fee = 10000


class FakeExecutor:
    def __init__(self, sell_out_wei=None, buy_out_atomic=None):
        self.sell_out_wei = sell_out_wei
        self.buy_out_atomic = buy_out_atomic

    def quote_sell(self, token, amount):
        return FakeQuote(amount, self.sell_out_wei)

    def quote_buy(self, token, wei):
        return FakeQuote(wei, self.buy_out_atomic)

    def token_decimals(self, token):
        return 18


class FakeFeed:
    def __init__(self, watch=None):
        self.watch = watch if watch is not None else {}
        self.eth_price = 2000.0

    def age_h(self, created_block):
        return created_block


SLCUT = LaneBot(bot_id="t_slcut", sl1_pct=-6.0, sl1_sell_fraction=0.75,
                hard_stop_pct=-15.0)


def _lane(tmp_path, monkeypatch, bot=SLCUT):
    _paths(tmp_path, monkeypatch)
    ex = FakeExecutor(buy_out_atomic=10 ** 21,
                      sell_out_wei=int(25.0 / 2000.0 * 1e18 * 0.97))
    lane = PaperLane(FakeFeed({"0xp1": {"sym": "T", "liq": 50_000.0}}),
                     executor=ex, registry={"0xp1": {"token": "0xtok"}},
                     bots=(bot,))
    lane.honeypot["0xtok"] = {"sellable": True}
    return lane


def _open(lane, bot_id="t_slcut"):
    st = lane.state[bot_id]
    st.pm.open_position(token="0xp1", entry_price=1e-5, size_usd=25.0,
                        entry_time=NOW - 120, address="0xtok")
    st.pos_meta["0xp1"] = {"qty_orig": 1000.0, "remaining_frac": 1.0,
                           "token": "0xtok", "sym": "T",
                           "entry_px": 1e-5, "entry_ts": NOW - 120}
    # live tape volume: suppresses the core PM's PRE_STOP_BAIL (fires at -3%
    # when vol_m5 < $500), so these tests isolate the SL1 leg itself.
    lane.tape["0xp1"] = [{"kind": "buy", "volume_usd": 1000.0,
                          "_epoch": NOW - 10}]
    return st


def test_sl1_fires_once_and_banks_fraction(tmp_path, monkeypatch):
    lane = _lane(tmp_path, monkeypatch)
    st = _open(lane)
    lane.prices["0xp1"] = [(NOW - 5, 1e-5 * 0.93)]   # -7% <= -6 trigger
    lane._manage_exits(NOW)
    assert st.pos_meta["0xp1"]["remaining_frac"] == 0.25
    sells = [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"]
    assert len(sells) == 1 and sells[0]["kind"] == "SL1_DERISK"
    assert abs(sells[0]["frac"] - 0.75) < 1e-9
    assert st.pos_meta["0xp1"]["_sl1_done"] is True
    # second tick, still red: latched — no re-fire
    lane._manage_exits(NOW + 60)
    sells = [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"
             and r["kind"] == "SL1_DERISK"]
    assert len(sells) == 1


def test_sl1_no_fire_above_threshold(tmp_path, monkeypatch):
    lane = _lane(tmp_path, monkeypatch)
    st = _open(lane)
    lane.prices["0xp1"] = [(NOW - 5, 1e-5 * 0.96)]   # -4% — above the -6 line
    lane._manage_exits(NOW)
    assert st.pos_meta["0xp1"]["remaining_frac"] == 1.0
    assert not [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"]


def test_sl1_stands_down_after_any_partial(tmp_path, monkeypatch):
    # TP1 (or any partial) already banked -> remaining_frac < 1.0 -> SL1 must
    # NOT fire (first partial wins; SL1 is strictly the PRE-TP1 loss mirror).
    lane = _lane(tmp_path, monkeypatch)
    st = _open(lane)
    st.pos_meta["0xp1"]["remaining_frac"] = 0.25
    lane.prices["0xp1"] = [(NOW - 5, 1e-5 * 0.90)]   # -10%, deep red
    lane._manage_exits(NOW)
    assert not [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"
                and r["kind"] == "SL1_DERISK"]


def test_sl1_off_is_byte_identical(tmp_path, monkeypatch):
    # a bot without sl1_pct must never emit SL1_DERISK, even deep red pre-TP1
    plain = LaneBot(bot_id="t_plain", hard_stop_pct=-50.0)
    lane = _lane(tmp_path, monkeypatch, bot=plain)
    _open(lane, "t_plain")
    lane.prices["0xp1"] = [(NOW - 5, 1e-5 * 0.90)]
    lane._manage_exits(NOW)
    assert not [r for r in _ledger_rows(tmp_path) if r["ev"] == "sell"
                and r["kind"] == "SL1_DERISK"]


def test_roster_has_three_slcut_racers_and_no_leak():
    slcut = [b for b in mod.ROSTER if b.sl1_pct is not None]
    assert {b.bot_id for b in slcut} == {"rh_slcut_ageddeep",
                                         "rh_slcut_agedhold",
                                         "rh_slcut_demand"}
    assert all(b.sl1_pct == -6.0 and b.sl1_sell_fraction == 0.75
               and b.exclusion_group == "slcut" for b in slcut)
    # every other bot: sl1 OFF (byte-identical fleet)
    assert all(b.sl1_pct is None for b in mod.ROSTER
               if b.bot_id not in {x.bot_id for x in slcut})
