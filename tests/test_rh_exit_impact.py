# tests/test_rh_exit_impact.py
"""Exit-impact leak fixes (AxiS 2026-07-10): held pools tick on SELL-side
executable prices (decisions fire on what we'd GET); entries gate on the
real quoted round-trip cost."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import rh_paper_lane as mod  # noqa: E402


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
    watch = {}
    eth_price = 2000.0


def _lane(ex):
    lane = mod.PaperLane(FakeFeed(), executor=ex, registry={})
    lane.decimals["0xtok"] = 18
    return lane


class TestSellSideTicking:
    def test_held_pool_ticks_on_sell_exec_price(self):
        # holding 1000 tokens; selling them returns 0.005 ETH -> exec price
        # 5e-6 ETH/token regardless of what a buyer would pay
        ex = FakeExecutor(sell_out_wei=int(0.005 * 1e18))
        lane = _lane(ex)
        lane.pos_meta["0xp"] = {"qty_orig": 1000.0, "remaining_frac": 1.0,
                                "token": "0xtok", "sym": "T",
                                "entry_px": 1e-5, "entry_ts": 0.0}
        lane._quote_hot(1_000_000.0)
        assert ex.calls and ex.calls[0][0] == "sell"        # sell-side quote
        ts, px = lane.prices["0xp"][-1]
        assert abs(px - 5e-6) < 1e-12                       # exec = out/qty

    def test_partial_position_quotes_remaining_only(self):
        ex = FakeExecutor(sell_out_wei=int(0.001 * 1e18))
        lane = _lane(ex)
        lane.pos_meta["0xp"] = {"qty_orig": 1000.0, "remaining_frac": 0.25,
                                "token": "0xtok", "sym": "T",
                                "entry_px": 1e-5, "entry_ts": 0.0}
        lane._quote_hot(1_000_000.0)
        _, _, amount = ex.calls[0]
        assert amount == int(250.0 * 10 ** 18)              # 25% remaining


class TestRoundTripGate:
    def _try_buy(self, eth_back_frac):
        buy_in = int(25.0 / 2000.0 * 1e18)                  # $25 of ETH
        ex = FakeExecutor(buy_out_atomic=10 ** 21,
                          sell_out_wei=int(buy_in * eth_back_frac))
        lane = _lane(ex)
        lane.pm.open_position = lambda **kw: (_ for _ in ()).throw(
            AssertionError("entry should have been blocked"))
        w = {"sym": "T", "liq": 50000.0}
        lane._paper_buy("0xp", "0xtok", w, -15.0,
                        {"avoid_block": False, "flow_confirm": True},
                        1_000_000.0, [])
        return lane

    def test_expensive_round_trip_blocked(self):
        lane = self._try_buy(0.90)                          # 10% rt cost > 6%
        assert lane.block_hist.get("rt_cost") == 1
        assert lane.n_entries == 0

    def test_reverted_sell_quote_blocked_fail_closed(self):
        buy_in = int(25.0 / 2000.0 * 1e18)
        ex = FakeExecutor(buy_out_atomic=10 ** 21, sell_out_wei=None)
        lane = _lane(ex)
        w = {"sym": "T", "liq": 50000.0}
        lane._paper_buy("0xp", "0xtok", w, -15.0,
                        {"avoid_block": False}, 1_000_000.0, [])
        assert lane.block_hist.get("rt_cost") == 1

    def test_cheap_round_trip_passes(self):
        buy_in = int(25.0 / 2000.0 * 1e18)
        ex = FakeExecutor(buy_out_atomic=10 ** 21,
                          sell_out_wei=int(buy_in * 0.97))  # 3% rt cost
        lane = _lane(ex)
        w = {"sym": "T", "liq": 50000.0}
        lane._paper_buy("0xp", "0xtok", w, -15.0,
                        {"avoid_block": False}, 1_000_000.0, [])
        assert lane.n_entries == 1
        assert "0xp" in lane.pos_meta
