"""Unit tests for core/rh_honeypot.py — FAIL-CLOSED EVM honeypot guard.

Pure verdict math + simulate_sell orchestration with a fake executor.
NO network, NO keys. Everything unknown must resolve to sellable=False.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.rh_honeypot import (
    DEFAULT_MAX_EXCESS_LOSS_PCT,
    simulate_sell,
    verdict_from_round_trip,
)

TOKEN = "0x1111111111111111111111111111111111111111"
WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PROBE_ETH = 0.01
PROBE_WEI = int(PROBE_ETH * 1e18)


def _quote(amount_out, fee=3000):
    return SimpleNamespace(amount_out=amount_out, fee=fee)


class FakeExecutor:
    """Duck-typed stand-in for RhExecutor: canned quotes, no network."""

    def __init__(self, buy=None, sell=None, raise_on=None):
        self._buy = buy
        self._sell = sell
        self._raise_on = raise_on or set()

    def quote_buy(self, token, wei):
        if "buy" in self._raise_on:
            raise RuntimeError("rpc boom")
        return self._buy

    def quote_sell(self, token, amount):
        if "sell" in self._raise_on:
            raise RuntimeError("rpc boom")
        return self._sell

    def token_balance(self, token, addr=None):
        return 0


# ── pure verdict math ─────────────────────────────────────────────────────────
class TestVerdictMath:
    def test_clean_round_trip_is_sellable(self):
        # two 0.3% pool-fee legs, nothing else lost
        eth_back = PROBE_WEI * (1 - 0.003) ** 2
        v = verdict_from_round_trip(PROBE_WEI, eth_back, 3000, 3000)
        assert v["sellable"] is True
        assert v["reason"] == "ok"
        assert v["buy_tax_pct"] == pytest.approx(0.0, abs=0.05)
        assert v["sell_tax_pct"] == pytest.approx(0.0, abs=0.05)

    def test_taxed_token_fails_closed(self):
        v = verdict_from_round_trip(PROBE_WEI, int(PROBE_WEI * 0.6), 3000, 3000)
        assert v["sellable"] is False
        assert "excess_round_trip_loss" in v["reason"]
        # ~39.7% combined excess, split evenly for reporting
        assert v["buy_tax_pct"] == pytest.approx(19.85, abs=0.5)
        assert v["excess_loss_pct"] > DEFAULT_MAX_EXCESS_LOSS_PCT

    def test_threshold_is_configurable(self):
        eth_back = int(PROBE_WEI * 0.90)  # ~9.5% excess over two 0.3% legs
        assert verdict_from_round_trip(
            PROBE_WEI, eth_back, 3000, 3000)["sellable"] is True
        assert verdict_from_round_trip(
            PROBE_WEI, eth_back, 3000, 3000,
            max_excess_loss_pct=5.0)["sellable"] is False

    def test_one_percent_tiers_expected_loss_ok(self):
        # hood.fun 1% pools: two 1% legs must NOT read as a tax
        eth_back = PROBE_WEI * (1 - 0.01) ** 2
        v = verdict_from_round_trip(PROBE_WEI, eth_back, 10000, 10000)
        assert v["sellable"] is True
        assert v["excess_loss_pct"] == pytest.approx(0.0, abs=0.05)

    def test_unknown_fee_assumes_worst_common_tier(self):
        # fee=None -> assume 1% legs; a clean 0.3%-pool round trip still passes
        eth_back = PROBE_WEI * (1 - 0.003) ** 2
        assert verdict_from_round_trip(PROBE_WEI, eth_back)["sellable"] is True

    def test_better_than_expected_clamps_to_zero_excess(self):
        v = verdict_from_round_trip(PROBE_WEI, PROBE_WEI, 3000, 3000)
        assert v["excess_loss_pct"] == 0.0
        assert v["sellable"] is True

    def test_fail_closed_on_garbage_inputs(self):
        for ei, eb in ((None, 1), (1, None), ("x", 1), (0, 1), (-5, 1), (1, -1)):
            v = verdict_from_round_trip(ei, eb)
            assert v["sellable"] is False
            assert v["reason"] == "unparseable_round_trip"


# ── simulate_sell orchestration (fake executor, no network) ───────────────────
class TestSimulateSell:
    def test_clean_token_sellable(self):
        tokens = 5000 * 10 ** 18
        ex = FakeExecutor(buy=_quote(tokens, fee=3000),
                          sell=_quote(int(PROBE_WEI * 0.99), fee=3000))
        v = simulate_sell(TOKEN, executor=ex, probe_eth=PROBE_ETH)
        assert v["sellable"] is True
        assert v["reason"] == "ok"
        assert set(v["checks"]) >= {"buy_quote", "sell_quote", "round_trip"}
        assert v["buy_fee_tier"] == 3000 and v["sell_fee_tier"] == 3000

    def test_no_buy_route_fails_closed(self):
        ex = FakeExecutor(buy=None, sell=_quote(1))
        v = simulate_sell(TOKEN, executor=ex, probe_eth=PROBE_ETH)
        assert v["sellable"] is False
        assert v["reason"].startswith("no_buy_route")

    def test_unsellable_quote_fails_closed(self):
        # token->WETH unquotable = the classic one-way honeypot signature
        ex = FakeExecutor(buy=_quote(5000 * 10 ** 18), sell=None)
        v = simulate_sell(TOKEN, executor=ex, probe_eth=PROBE_ETH)
        assert v["sellable"] is False
        assert v["reason"].startswith("sell_quote_reverted")

    def test_taxed_round_trip_fails_closed(self):
        ex = FakeExecutor(buy=_quote(5000 * 10 ** 18),
                          sell=_quote(int(PROBE_WEI * 0.5)))
        v = simulate_sell(TOKEN, executor=ex, probe_eth=PROBE_ETH)
        assert v["sellable"] is False
        assert "excess_round_trip_loss" in v["reason"]
        assert v["buy_tax_pct"] is not None

    def test_executor_exception_fails_closed(self):
        # FAIL-CLOSED contract: simulation errors NEVER return sellable=True
        for stage in ("buy", "sell"):
            ex = FakeExecutor(buy=_quote(1), sell=_quote(1), raise_on={stage})
            v = simulate_sell(TOKEN, executor=ex, probe_eth=PROBE_ETH)
            assert v["sellable"] is False
            assert v["reason"].startswith("simulation_error")

    def test_never_raises(self):
        v = simulate_sell(TOKEN, executor=object(), probe_eth=PROBE_ETH)
        assert v["sellable"] is False

    def test_wallet_without_balance_skips_live_check(self):
        ex = FakeExecutor(buy=_quote(5000 * 10 ** 18, fee=3000),
                          sell=_quote(int(PROBE_WEI * 0.99), fee=3000))
        v = simulate_sell(TOKEN, wallet_addr=WALLET, executor=ex,
                          probe_eth=PROBE_ETH)
        # no balance -> the live-sell eth_call is SKIPPED, not failed:
        # missing balance is wallet state, not token evidence
        assert v["sellable"] is True
        assert any(c == "live_sell_call:skipped_no_balance" for c in v["checks"])

    def test_holder_sell_revert_fails_closed(self):
        # wallet HOLDS + approved, but the real sell eth_call reverts -> honeypot
        ex = FakeExecutor(buy=_quote(5000 * 10 ** 18, fee=3000),
                          sell=_quote(int(PROBE_WEI * 0.99), fee=3000))
        ex.token_balance = lambda token, addr=None: 10 ** 18
        w3 = MagicMock()
        contract = MagicMock()
        contract.functions.allowance.return_value.call.return_value = 2 ** 255
        w3.eth.contract.return_value = contract
        w3.eth.call.side_effect = Exception("execution reverted: blacklisted")
        ex.w3 = w3
        v = simulate_sell(TOKEN, wallet_addr=WALLET, executor=ex,
                          probe_eth=PROBE_ETH)
        assert v["sellable"] is False
        assert v["reason"].startswith("sell_call_reverted")

    def test_holder_sell_ok_stays_sellable(self):
        ex = FakeExecutor(buy=_quote(5000 * 10 ** 18, fee=3000),
                          sell=_quote(int(PROBE_WEI * 0.99), fee=3000))
        ex.token_balance = lambda token, addr=None: 10 ** 18
        w3 = MagicMock()
        contract = MagicMock()
        contract.functions.allowance.return_value.call.return_value = 2 ** 255
        w3.eth.contract.return_value = contract
        w3.eth.call.return_value = b""  # sell simulation succeeds
        ex.w3 = w3
        v = simulate_sell(TOKEN, wallet_addr=WALLET, executor=ex,
                          probe_eth=PROBE_ETH)
        assert v["sellable"] is True
        assert any(c == "live_sell_call:ok" for c in v["checks"])

    def test_verdict_has_required_keys(self):
        ex = FakeExecutor(buy=None)
        v = simulate_sell(TOKEN, executor=ex, probe_eth=PROBE_ETH)
        for k in ("sellable", "buy_tax_pct", "sell_tax_pct", "reason"):
            assert k in v
