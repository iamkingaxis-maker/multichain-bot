"""Root-cause test for the phantom paper-close bug (2026-06-21).

`Trader._get_token_balance_atomic` must distinguish an RPC read FAILURE
(return -1, so the caller keeps the position OPEN and retries) from a
genuine confirmed-zero balance (return 0). Conflating the two made a
transient RPC hiccup look like "0 tokens on chain", which booked a
phantom PAPER close on a real live position (BOB stayed in the wallet
while the bot thought it had sold).

The caller's read-failed sentinel is `bal is None or bal < 0`, so a
failed read MUST be negative — never 0.
"""
import asyncio

from core.trader import Trader


class _Stub:
    is_active = False
    _kill_reason = ""

    def get_position(self, *a, **k):
        return None


def _make_trader():
    t = Trader(
        private_key="",
        rpc_url="https://example.invalid",
        tracker=_Stub(),
        telegram=_Stub(),
        risk_manager=_Stub(),
        kill_switch=_Stub(),
    )
    # Force past the paper-mode short-circuit and the pubkey derivation so
    # the test exercises the RPC-response handling, not key plumbing.
    t.private_key = "fake-key-for-test"
    t._get_public_key = lambda: "OwnerPubkey1111111111111111111111111111111"
    return t


def _balance(post_rpc_return):
    t = _make_trader()

    async def _fake_post_rpc(payload, total_timeout=5.0):
        return post_rpc_return

    t._post_rpc = _fake_post_rpc
    return asyncio.run(t._get_token_balance_atomic("SomeMint1111111111111111111111111111111111"))


def test_rpc_none_is_read_failure_not_zero():
    """_post_rpc returning None (timeout/429/network) must be -1, not 0."""
    assert _balance(None) == -1


def test_rpc_empty_dict_is_read_failure():
    """An empty/garbage dict with no 'result' is a failed read, not a real 0."""
    assert _balance({}) == -1


def test_rpc_error_response_is_read_failure():
    """A JSON-RPC error response must be a read failure, not a confirmed 0."""
    assert _balance({"error": {"code": -32005, "message": "rate limited"}}) == -1


def test_rpc_success_no_account_is_genuine_zero():
    """RPC succeeded and the owner truly holds no account for this mint -> 0."""
    assert _balance({"result": {"context": {}, "value": []}}) == 0


def test_rpc_success_with_balance_sums_atomic():
    """RPC succeeded with a token account -> the atomic amount."""
    resp = {"result": {"value": [
        {"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "1347000"}}}}}},
    ]}}
    assert _balance(resp) == 1347000


def test_rpc_success_multiple_accounts_sums():
    resp = {"result": {"value": [
        {"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "1000"}}}}}},
        {"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "234"}}}}}},
    ]}}
    assert _balance(resp) == 1234
