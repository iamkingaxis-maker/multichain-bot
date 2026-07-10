"""Unit tests for core/rh_execution.py — the Robinhood Chain (4663) EVM rail.

All PURE logic: calldata building, slippage math, fill-price decoding from a
fixture receipt, paper-mode fail-closed paths, telemetry logging. NO network,
NO keys required. Live-RPC integration tests are @skipif(no RH_RPC_URL).
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from eth_abi import decode as abi_decode

from core.rh_execution import (
    ADDRESS_THIS,
    DEFAULT_RPC_URL,
    FEE_TIERS,
    RH_CHAIN_ID,
    SWAP_ROUTER02,
    TRANSFER_TOPIC,
    WETH9,
    WETH_DEPOSIT_TOPIC,
    WETH_WITHDRAWAL_TOPIC,
    RhChainMismatchError,
    RhExecutor,
    RhPaperModeError,
    build_buy_calldata,
    build_sell_calldata,
    effective_fill_from_receipt,
    encode_exact_input_single,
    encode_multicall,
    encode_unwrap_weth9,
    log_rh_swap,
    min_out_after_slippage,
)

TOKEN = "0x1111111111111111111111111111111111111111"
WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
POOL = "0x9999999999999999999999999999999999999999"
# throwaway key for offline signing-identity tests — NOT a real wallet
TEST_KEY = "0x" + "11" * 32


def _pad_addr(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


def _u256(n: int) -> str:
    return "0x" + format(n, "064x")


# ── calldata building (pure) ──────────────────────────────────────────────────
class TestCalldata:
    def test_exact_input_single_selector(self):
        # SwapRouter02 exactInputSingle (7-field struct, NO deadline) = 0x04e45aaf
        data = encode_exact_input_single(WETH9, TOKEN, 3000, WALLET,
                                         10 ** 16, 12345)
        assert data.startswith("0x04e45aaf")

    def test_exact_input_single_params_roundtrip(self):
        data = encode_exact_input_single(WETH9, TOKEN, 10000, WALLET,
                                         7 * 10 ** 15, 999, 0)
        (params,) = abi_decode(
            ["(address,address,uint24,address,uint256,uint256,uint160)"],
            bytes.fromhex(data[10:]))
        assert params[0].lower() == WETH9.lower()
        assert params[1].lower() == TOKEN.lower()
        assert params[2] == 10000
        assert params[3].lower() == WALLET.lower()
        assert params[4] == 7 * 10 ** 15
        assert params[5] == 999
        assert params[6] == 0

    def test_unwrap_weth9_selector(self):
        data = encode_unwrap_weth9(1000, WALLET)
        assert data.startswith("0x49404b7c")

    def test_multicall_selector_and_nesting(self):
        inner = encode_unwrap_weth9(1, WALLET)
        data = encode_multicall([inner])
        assert data.startswith("0xac9650d8")
        (calls,) = abi_decode(["bytes[]"], bytes.fromhex(data[10:]))
        assert calls[0].hex().startswith("49404b7c")

    def test_build_buy_calldata_shape(self):
        call = build_buy_calldata(TOKEN, 10 ** 16, 5000, WALLET, fee=10000)
        assert call["to"] == SWAP_ROUTER02
        assert call["value"] == 10 ** 16
        assert call["data"].startswith("0x04e45aaf")

    def test_build_sell_calldata_is_multicall_to_address_this(self):
        call = build_sell_calldata(TOKEN, 5000 * 10 ** 18, 10 ** 15,
                                   WALLET, fee=3000)
        assert call["to"] == SWAP_ROUTER02
        assert call["value"] == 0
        assert call["data"].startswith("0xac9650d8")
        (calls,) = abi_decode(["bytes[]"], bytes.fromhex(call["data"][10:]))
        assert len(calls) == 2
        # leg 1: exactInputSingle token->WETH with recipient=ADDRESS_THIS
        assert calls[0].hex().startswith("04e45aaf")
        (params,) = abi_decode(
            ["(address,address,uint24,address,uint256,uint256,uint160)"],
            calls[0][4:])
        assert params[0].lower() == TOKEN.lower()
        assert params[1].lower() == WETH9.lower()
        assert params[3].lower() == ADDRESS_THIS.lower()
        # leg 2: unwrapWETH9 to the wallet
        assert calls[1].hex().startswith("49404b7c")


# ── slippage math (pure, FAIL-CLOSED) ─────────────────────────────────────────
class TestMinOut:
    def test_basic(self):
        assert min_out_after_slippage(1_000_000, 100) == 990_000

    def test_zero_bps_keeps_quote(self):
        assert min_out_after_slippage(777, 0) == 777

    def test_floors_not_rounds(self):
        assert min_out_after_slippage(999, 1) == 998  # 999*9999//10000

    def test_bad_quote_raises(self):
        with pytest.raises(ValueError):
            min_out_after_slippage(0, 100)
        with pytest.raises(ValueError):
            min_out_after_slippage(-5, 100)

    def test_bad_bps_raises(self):
        with pytest.raises(ValueError):
            min_out_after_slippage(1000, -1)
        with pytest.raises(ValueError):
            min_out_after_slippage(1000, 10_000)


# ── fill decoding from receipt logs (pure, FAIL-OPEN) ─────────────────────────
def _buy_receipt(eth_in_wei=10 ** 16, tokens_out=5000 * 10 ** 18):
    return {
        "status": 1,
        "gasUsed": 210_000,
        "effectiveGasPrice": 10 ** 7,
        "logs": [
            {   # router wraps the ETH -> WETH Deposit
                "address": WETH9,
                "topics": [WETH_DEPOSIT_TOPIC, _pad_addr(SWAP_ROUTER02)],
                "data": _u256(eth_in_wei),
            },
            {   # pool sends tokens to the wallet
                "address": TOKEN,
                "topics": [TRANSFER_TOPIC, _pad_addr(POOL), _pad_addr(WALLET)],
                "data": _u256(tokens_out),
            },
        ],
    }


def _sell_receipt(tokens_in=5000 * 10 ** 18, eth_out_wei=95 * 10 ** 14):
    return {
        "status": 1,
        "gasUsed": "0x33450",           # hex-string form must also decode
        "effectiveGasPrice": "0x989680",
        "logs": [
            {
                "address": TOKEN,
                "topics": [TRANSFER_TOPIC, _pad_addr(WALLET), _pad_addr(POOL)],
                "data": _u256(tokens_in),
            },
            {   # router unwraps -> WETH Withdrawal
                "address": WETH9,
                "topics": [WETH_WITHDRAWAL_TOPIC, _pad_addr(SWAP_ROUTER02)],
                "data": _u256(eth_out_wei),
            },
        ],
    }


class TestFillDecoding:
    def test_buy_fill_price(self):
        fill = effective_fill_from_receipt(
            _buy_receipt(), wallet=WALLET, token=TOKEN, side="buy",
            token_decimals=18)
        assert fill is not None
        assert fill["eth_amount_wei"] == 10 ** 16
        assert fill["token_amount_atomic"] == 5000 * 10 ** 18
        # 0.01 ETH for 5000 tokens = 2e-6 ETH/token
        assert fill["fill_price_eth_per_token"] == pytest.approx(2e-6)
        assert fill["gas_cost_eth"] == pytest.approx(210_000 * 10 ** 7 / 1e18)

    def test_sell_fill_price_hex_fields(self):
        fill = effective_fill_from_receipt(
            _sell_receipt(), wallet=WALLET, token=TOKEN, side="sell",
            token_decimals=18)
        assert fill is not None
        assert fill["eth_amount_wei"] == 95 * 10 ** 14
        assert fill["fill_price_eth_per_token"] == pytest.approx(0.0095 / 5000)

    def test_non18_decimals(self):
        fill = effective_fill_from_receipt(
            _buy_receipt(tokens_out=5000 * 10 ** 6), wallet=WALLET,
            token=TOKEN, side="buy", token_decimals=6)
        assert fill["fill_price_eth_per_token"] == pytest.approx(2e-6)

    def test_other_wallet_transfers_ignored(self):
        rcpt = _buy_receipt()
        rcpt["logs"].append({  # unrelated transfer to someone else
            "address": TOKEN,
            "topics": [TRANSFER_TOPIC, _pad_addr(POOL), _pad_addr(POOL)],
            "data": _u256(10 ** 30),
        })
        fill = effective_fill_from_receipt(
            rcpt, wallet=WALLET, token=TOKEN, side="buy", token_decimals=18)
        assert fill["token_amount_atomic"] == 5000 * 10 ** 18

    def test_fail_open_on_garbage(self):
        # FAIL-OPEN: instrumentation only, never raises
        assert effective_fill_from_receipt(
            {"logs": None}, wallet=WALLET, token=TOKEN, side="buy") is None
        assert effective_fill_from_receipt(
            {"logs": [{"topics": []}]}, wallet=WALLET, token=TOKEN,
            side="buy") is None
        assert effective_fill_from_receipt(
            None, wallet=WALLET, token=TOKEN, side="buy") is None

    def test_missing_leg_returns_none(self):
        rcpt = _buy_receipt()
        rcpt["logs"] = rcpt["logs"][:1]  # deposit only, no token transfer
        assert effective_fill_from_receipt(
            rcpt, wallet=WALLET, token=TOKEN, side="buy") is None


# ── executor identity / paper mode (FAIL-CLOSED) ──────────────────────────────
class TestExecutorPaperMode:
    def test_paper_only_without_key(self, monkeypatch):
        monkeypatch.delenv("RH_PRIVATE_KEY", raising=False)
        ex = RhExecutor(rpc_url="http://localhost:1")
        assert ex.paper_only is True
        assert ex.wallet_address is None

    def test_sign_and_send_raises_in_paper_mode(self, monkeypatch):
        monkeypatch.delenv("RH_PRIVATE_KEY", raising=False)
        ex = RhExecutor(rpc_url="http://localhost:1")
        with pytest.raises(RhPaperModeError, match="RH_PRIVATE_KEY"):
            ex._sign_and_send({})

    def test_key_gives_wallet_and_is_never_in_repr(self):
        ex = RhExecutor(rpc_url="http://localhost:1", private_key=TEST_KEY)
        assert ex.paper_only is False
        assert ex.wallet_address.startswith("0x")
        r = repr(ex)
        assert "1111" not in r  # no key material, ever
        assert ex.wallet_address in r

    def test_default_rpc_from_env(self, monkeypatch):
        monkeypatch.setenv("RH_RPC_URL", "http://example.invalid:8545")
        ex = RhExecutor()
        assert ex.rpc_url == "http://example.invalid:8545"
        monkeypatch.delenv("RH_RPC_URL")
        assert RhExecutor().rpc_url == DEFAULT_RPC_URL


class TestChainIdGate:
    def _mock_web3(self, chain_id):
        w3_cls = MagicMock()
        inst = MagicMock()
        inst.eth.chain_id = chain_id
        w3_cls.return_value = inst
        return w3_cls, inst

    def test_connect_rejects_wrong_chain(self):
        w3_cls, _ = self._mock_web3(42161)  # Arbitrum One, not Robinhood
        with patch("core.rh_execution.Web3", w3_cls):
            ex = RhExecutor(rpc_url="http://localhost:1")
            with pytest.raises(RhChainMismatchError, match="42161"):
                ex.connect()
        assert ex.w3 is None  # FAIL-CLOSED: never half-connected

    def test_connect_accepts_4663(self):
        w3_cls, inst = self._mock_web3(RH_CHAIN_ID)
        with patch("core.rh_execution.Web3", w3_cls):
            ex = RhExecutor(rpc_url="http://localhost:1")
            assert ex.connect() is inst
            assert ex.w3 is inst


# ── quoting across fee tiers (mocked network) ────────────────────────────────
class TestQuoting:
    def _executor_with_quotes(self, by_fee):
        """RhExecutor whose _quote_single answers from a dict; no network."""
        ex = RhExecutor(rpc_url="http://localhost:1")
        ex.w3 = MagicMock()  # pretend connected
        ex._quote_single = lambda ti, to, amt, fee: by_fee.get(fee)
        ex.token_decimals = lambda t: 18
        return ex

    def test_best_fee_tier_wins(self):
        ex = self._executor_with_quotes({500: 100, 3000: 300, 10000: 250})
        q = ex.quote_buy(TOKEN, 10 ** 16)
        assert q.fee == 3000
        assert q.amount_out == 300
        assert set(q.quotes_by_fee) == {500, 3000, 10000}

    def test_no_pool_returns_none(self):
        ex = self._executor_with_quotes({})
        assert ex.quote_buy(TOKEN, 10 ** 16) is None
        assert ex.quote_sell(TOKEN, 10 ** 18) is None

    def test_mid_price_math(self):
        # 0.01 ETH -> 5000 tokens => 2e-6 ETH/token
        ex = self._executor_with_quotes({10000: 5000 * 10 ** 18})
        q = ex.quote_buy(TOKEN, 10 ** 16)
        assert q.mid_price_eth_per_token == pytest.approx(2e-6)

    def test_fee_tiers_cover_launchpad_pools(self):
        # hood.fun graduates to 1% pools — 10000 must be probed
        assert 10000 in FEE_TIERS and 3000 in FEE_TIERS


# ── telemetry log (FAIL-OPEN, mirrors live_swap fields) ───────────────────────
class TestSwapLog:
    def test_writes_record_with_normalized_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        log_rh_swap(side="buy", token_address=TOKEN, success=False,
                    error_text="execution reverted", decision_mid_price=2e-6,
                    real_fill_price=None, fill_vs_mid_slippage_pct=None,
                    total_latency_ms=123.4)
        lines = (tmp_path / "rh_live_swaps.jsonl").read_text().strip().splitlines()
        rec = json.loads(lines[-1])
        assert rec["chain"] == "robinhood" and rec["chain_id"] == 4663
        assert rec["failure_reason"] == "revert"
        assert rec["ts"]  # stamped
        # the Solana-mirrored fidelity fields are present
        for k in ("decision_mid_price", "real_fill_price",
                  "fill_vs_mid_slippage_pct", "total_latency_ms"):
            assert k in rec

    def test_success_is_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        log_rh_swap(side="sell", token_address=TOKEN, success=True)
        rec = json.loads((tmp_path / "rh_live_swaps.jsonl").read_text())
        assert rec["failure_reason"] == "ok"

    def test_fail_open_on_bad_dir(self, monkeypatch):
        monkeypatch.setenv("DATA_DIR", "Z:\\definitely\\not\\a\\dir")
        log_rh_swap(side="buy", token_address=TOKEN, success=True)  # no raise


# ── live-RPC integration (skipped without RH_RPC_URL) ─────────────────────────
@pytest.mark.skipif(not os.environ.get("RH_RPC_URL"),
                    reason="RH_RPC_URL not set — live-RPC integration skipped")
class TestLiveIntegration:
    def test_connect_verifies_chain_4663(self):
        ex = RhExecutor()
        w3 = ex.connect()
        assert w3.eth.chain_id == RH_CHAIN_ID

    def test_router_and_quoter_have_bytecode(self):
        from core.rh_execution import QUOTER_V2
        ex = RhExecutor()
        w3 = ex.connect()
        assert len(w3.eth.get_code(SWAP_ROUTER02)) > 2
        assert len(w3.eth.get_code(QUOTER_V2)) > 2
