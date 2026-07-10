"""Unit tests for scripts/rh_firehose_feed.py pure decode helpers.

NO network.  Fixtures are REAL bytes captured from the live Nitro broadcast
feed wss://feed.mainnet.chain.robinhood.com on 2026-07-10 and cross-verified
against the public RPC at capture time:
  * REAL_L2MSG          — one full l2Msg (kind-3 batch wrapping 4 kind-4
                          signed txs) from broadcast seq 5978048.
  * EIS_RAW             — an EIP-1559 SwapRouter02.exactInputSingle tx;
                          hash AND recovered sender verified against
                          eth_getTransactionByHash (block 5977959).
  * UR_RAW              — a LEGACY-RLP Universal Router execute() tx
                          (commands 0x0b WRAP_ETH, 0x01 V3_SWAP_EXACT_OUT,
                          0x0c UNWRAP_WETH) — exercises legacy decoding and
                          the reversed exact-out path.
  * POOL/PAIR_CREATED   — real factory creation events used to pin the
                          CREATE2 derivations (V3 canonical init hash
                          verified == factory.getPool live).
"""
import base64
import json

from scripts.rh_chain_feed import ETH_USD_POOL, WETH, tape_row
from scripts.rh_firehose_feed import (
    SWAP_ROUTER02,
    UNIVERSAL_ROUTER,
    decode_router_calldata,
    decode_signed_tx,
    decode_v3_path,
    extract_messages,
    intent_to_trade,
    recover_sender,
    split_l2_msg,
    v2_pair_address,
    v3_pool_address,
)

# ── real captured fixtures (2026-07-10, see module docstring) ────────────────
REAL_L2MSG = base64.b64decode(
    "AwAAAAAAAAFPBPkBSwKEBSYC8IMEk+CUyvaBpm0CBgE0IpdJOGPnjJWeXLKAuORHK0Pz"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAQMyWOZdJfx0VqwAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAD6AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAAAAAAAA"
    "AAAAAAAA5fwicP2Ps++WWhXlzAX4uvt532IAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAgAAAAAAAAAAAAAAAEEa5bKCOkuhg9QPkh+vf6QujPGnAAAAAAAAAAAAAAAA"
    "C9fTCPjhY5+rmI3xioAR9B6srXOCJJGg2lNtCPcS9oKyCCxg7dVj4EMiFpz0D0iMnNP5"
    "1+xwffmgcAQIciaYbmccbDsyl/q4wBHLi96QM+AtpJZq1VcvASYAAAAAAAAAsAT4rYIi"
    "NYQH2eNAgwehIJQJ6pnFKolkshE4ZFl9bM3EiJE2AoC4RAlep7MAAAAAAAAAAAAAAADK"
    "9oGmbQIGATQil0k4Y+eMlZ5csv//////////////////////////////////////////"
    "giSRoEIjwJM1cfEKVVQKt3nM6de3v8cZbfyd1z6l2UIX52knoDL4zFodWgh7mqMYKf4m"
    "3N30v6D76MQn+LuP6AF0dtf5AAAAAAAAAVkEAvkBVIISN4CAhATOlCCDAnXtlMr2gaZt"
    "AgYBNCKXSThj54yVnlyyhgK6fe8wALjkBORarwAAAAAAAAAAAAAAAAvX0wj44WOfq5iN"
    "8YqAEfQerK1zAAAAAAAAAAAAAAAAHGJPJqzPcOS0mskFf+Vk6+ucCUEAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAB9AAAAAAAAAAAAAAAAFhJtFMl6q+/kU/LoByKIfRF"
    "ffmkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACun3vMAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAALvkuOvViMLVQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAwAGg"
    "15w/hMDGBj01g7xJvgGwy+Qe5BRnH0glthtv6UrIghagcH9tP7YOwT5GFRw41LdLQftf"
    "FNQzvmcpe4KsMjiDZO0AAAAAAAABWQQC+QFUghI3gICEBM6UIIMCde2UyvaBpm0CBgE0"
    "IpdJOGPnjJWeXLKGArp97zAAuOQE5FqvAAAAAAAAAAAAAAAAC9fTCPjhY5+rmI3xioAR"
    "9B6srXMAAAAAAAAAAAAAAAAcYk8mrM9w5LSayQV/5WTr65wJQQAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAH0AAAAAAAAAAAAAAAAji2wdNSyJJEUN9Ikr5jtiQMXbkkA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAK6fe8wAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAu+S469WIwtVAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADAAaBcBWv6"
    "2WQOp5h6kouv9EBksOh1HFA4+lp3r/B/QTMV1qBrkBrmEOyegjsp+v/rDsZ0V8CSAYg0"
    "c23x/qkrs46Dzg==")

EIS_RAW = bytes.fromhex(
    "02f90150821237820c628084082767008302864394caf681a66d020601342297493863"
    "e78c959e5cb280b8e404e45aaf00000000000000000000000026af24efb4da70360e67"
    "e13bacd8b0ba6a0f32d70000000000000000000000000bd7d308f8e1639fab988df18a"
    "8011f41eacad730000000000000000000000000000000000000000000000000000000000"
    "0027100000000000000000000000001b9657607785dd16150097a938b7cad0940d622a"
    "00000000000000000000000000000000000000000002a93f2909eeb06ad338b4000000"
    "000000000000000000000000000000000000000000000d47a136040fff000000000000"
    "0000000000000000000000000000000000000000000000000000c001a0536d5665fb67"
    "2093fdeb84078e9b60e7bfaefb084e211678d755295acdcaf9a0a06dd649e28e7eaa9f"
    "4cd34bd0fe1725a42383ca12ef0d8fb62b6bfc1f969c495b")
EIS_HASH = "0x9bcfa4100e218fa43a75cd20657b2fb0ad6339f40336753a5e75e3ea1ce4b079"
EIS_SENDER = "0x1b9657607785dd16150097a938b7cad0940d622a"  # RPC-verified
EIS_TOKEN = "0x26af24efb4da70360e67e13bacd8b0ba6a0f32d7"

UR_RAW = bytes.fromhex(
    "f903b21e8404f64b50830493e0948876789976decbfcbbbe364623c63652db8c090486"
    "07eeaee3d04eb9034424856bc300000000000000000000000000000000000000000000"
    "0000000000000000004000000000000000000000000000000000000000000000000000"
    "0000000000008000000000000000000000000000000000000000000000000000000000"
    "000000030b010c00000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000030000"
    "0000000000000000000000000000000000000000000000000000000000600000000000"
    "0000000000000000000000000000000000000000000000000000c00000000000000000"
    "0000000000000000000000000000000000000000000002400000000000000000000000"
    "0000000000000000000000000000000000000000400000000000000000000000008876"
    "789976decbfcbbbe364623c63652db8c09048000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000001600000000000000000000000009852a7bf42b4fc6c25bd3b"
    "006e3ae5d3109f60b9000000000000000000000000000000000000000000000000013f"
    "be85edc90000000000000000000000000000000000000000000000000000000007eeae"
    "e3d04e00000000000000000000000000000000000000000000000000000000000000c0"
    "0000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000120000000000000"
    "000000000000000000000000000000000000000000000000002b020bfc650a365f8bb2"
    "6819deaabf3e21291018b40027100bd7d308f8e1639fab988df18a8011f41eacad7300"
    "0000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000001000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000400000000000000000000000009852a7bf42b4fc6c25bd3b00"
    "6e3ae5d3109f60b9000000000000000000000000000000000000000000000000000000"
    "0000000000822491a065358aaf5c64ac807afe1a43a04368c1b582bdf0c7c1763d5724"
    "6d9c90978c1fa010f4879c5afdaf7dae0aaa160e350a6cb6a81040670fadc0e5001206"
    "77ca2d25")
UR_HASH = "0x5101522d4e79b8cd296736b1ec5716dcbf8d101a446d1db82a99703fd0297b43"
UR_TOKEN = "0x020bfc650a365f8bb26819deaabf3e21291018b4"

# real factory creation events (CREATE2 pins)
POOL_CREATED = {"token0": WETH,
                "token1": "0x59087bd31f472f4d448f47a475c133aa820e503e",
                "fee": 10000,
                "pool": "0x5d727e5131dcbb341cffab09195566d57eff6d47"}
PAIR_CREATED = {"token0": WETH,
                "token1": "0x834b31164b5e5f4a08b441d796abb85f714bb7c1",
                "pair": "0x60e50467d9ffa04a08c2720e859ff1ddcb09c36e"}
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"

TOKEN = "0x1111111111111111111111111111111111111111"


def _u(n: int) -> str:
    return format(n, "064x")


def _a(addr: str) -> str:
    return "0" * 24 + addr[2:].lower()


# ── frame parsing ────────────────────────────────────────────────────────────
class TestExtractMessages:
    def _frame(self, seq=5978048, ts=1783676335, kind=3,
               l2msg=REAL_L2MSG) -> str:
        return json.dumps({"version": 1, "messages": [{
            "sequenceNumber": seq,
            "message": {"message": {
                "header": {"kind": kind, "sender": "0xa4b0",
                           "blockNumber": 25501400, "timestamp": ts,
                           "requestId": None, "baseFeeL1": None},
                "l2Msg": base64.b64encode(l2msg).decode()},
                "delayedMessagesRead": 26}}]})

    def test_real_shape(self):
        msgs = extract_messages(self._frame())
        assert msgs == [(5978048, 1783676335, REAL_L2MSG)]

    def test_non_kind3_header_skipped(self):
        assert extract_messages(self._frame(kind=9)) == []

    def test_garbage_never_raises(self):
        assert extract_messages("not json") == []
        assert extract_messages("{}") == []
        assert extract_messages('{"messages":[{"broken":1}]}') == []


# ── l2Msg batch walk ─────────────────────────────────────────────────────────
class TestSplitL2Msg:
    def test_real_batch_yields_4_signed_txs(self):
        txs = split_l2_msg(REAL_L2MSG)
        assert len(txs) == 4
        for t in txs:
            assert decode_signed_tx(t) is not None

    def test_bare_signed_tx(self):
        assert split_l2_msg(b"\x04" + EIS_RAW) == [EIS_RAW]

    def test_nested_batch(self):
        inner = b"\x04" + EIS_RAW
        batch = b"\x03" + len(inner).to_bytes(8, "big") + inner
        nested = b"\x03" + len(batch).to_bytes(8, "big") + batch
        assert split_l2_msg(nested) == [EIS_RAW]

    def test_truncated_length_prefix_stops_cleanly(self):
        inner = b"\x04" + EIS_RAW
        good = len(inner).to_bytes(8, "big") + inner
        bad = (10 ** 6).to_bytes(8, "big") + b"\x04short"  # overruns
        assert split_l2_msg(b"\x03" + good + bad) == [EIS_RAW]

    def test_empty_and_unknown_kind(self):
        assert split_l2_msg(b"") == []
        assert split_l2_msg(b"\x07payload") == []


# ── signed tx envelope decoding ──────────────────────────────────────────────
class TestDecodeSignedTx:
    def test_eip1559_real_tx_hash_and_to(self):
        d = decode_signed_tx(EIS_RAW)
        assert d["hash"] == EIS_HASH        # == eth_getTransactionByHash
        assert d["to"] == SWAP_ROUTER02
        assert d["type"] == 2
        assert d["value"] == 0
        assert d["data"][:4].hex() == "04e45aaf"

    def test_legacy_real_tx(self):
        d = decode_signed_tx(UR_RAW)
        assert d["hash"] == UR_HASH
        assert d["to"] == UNIVERSAL_ROUTER
        assert d["type"] == 0
        assert d["value"] == 0x07EEAEE3D04E  # WRAP_ETH amount == msg.value

    def test_malformed_returns_none(self):
        assert decode_signed_tx(b"\x02\xff\xff") is None
        assert decode_signed_tx(b"\x03" + EIS_RAW[1:]) is None  # blob type
        assert decode_signed_tx(b"\x10garbage") is None

    def test_sender_recovery_matches_rpc(self):
        assert recover_sender(EIS_RAW) == EIS_SENDER
        assert recover_sender(b"\x02junk") == ""


# ── calldata -> swap intents ─────────────────────────────────────────────────
class TestDecodeRouterCalldata:
    def test_real_exact_input_single(self):
        d = decode_signed_tx(EIS_RAW)
        intents = decode_router_calldata(d["data"])
        assert intents == [{
            "dex": "v3", "token_in": EIS_TOKEN, "token_out": WETH,
            "fee": 10000, "exact_in": True,
            "amount_in": 0x02A93F2909EEB06AD338B4, "amount_out": None,
            "amount_in_max": None, "amount_out_min": 0x0D47A136040FFF}]

    def test_real_ur_execute_exact_out_reversed_path(self):
        d = decode_signed_tx(UR_RAW)
        intents = decode_router_calldata(d["data"])
        # commands 0b010c: WRAP_ETH + V3_SWAP_EXACT_OUT + UNWRAP_WETH; the
        # exact-out path is tokenOut-first and must come back re-reversed.
        assert intents == [{
            "dex": "v3", "token_in": WETH, "token_out": UR_TOKEN,
            "fee": 10000, "exact_in": False,
            "amount_in": None, "amount_out": 0x013FBE85EDC90000,
            "amount_in_max": 0x07EEAEE3D04E, "amount_out_min": None}]

    def test_exact_output_single(self):
        data = bytes.fromhex(
            "5023b4df" + _a(WETH) + _a(TOKEN) + _u(2500) + _a(TOKEN) +
            _u(7 * 10 ** 20) + _u(10 ** 18) + _u(0))
        assert decode_router_calldata(data) == [{
            "dex": "v3", "token_in": WETH, "token_out": TOKEN, "fee": 2500,
            "exact_in": False, "amount_in": None, "amount_out": 7 * 10 ** 20,
            "amount_in_max": 10 ** 18, "amount_out_min": None}]

    def test_v2_swap_exact_in(self):
        data = bytes.fromhex(
            "472b43f3" + _u(5 * 10 ** 17) + _u(10 ** 20) + _u(0x80) +
            _a(TOKEN) + _u(2) + _a(WETH) + _a(TOKEN))
        assert decode_router_calldata(data) == [{
            "dex": "v2", "token_in": WETH, "token_out": TOKEN, "fee": None,
            "exact_in": True, "amount_in": 5 * 10 ** 17, "amount_out": None,
            "amount_in_max": None, "amount_out_min": 10 ** 20}]

    def test_multicall_deadline_recurses(self):
        inner = bytes.fromhex(
            "04e45aaf" + _a(WETH) + _a(TOKEN) + _u(10000) + _a(TOKEN) +
            _u(10 ** 17) + _u(5) + _u(0))
        # multicall(uint256 deadline, bytes[] data)
        data = bytes.fromhex(
            "5ae401dc" + _u(1783676000) + _u(0x40) + _u(1) + _u(0x20) +
            _u(len(inner))) + inner + b"\x00" * 28
        [intent] = decode_router_calldata(data)
        assert intent["token_in"] == WETH
        assert intent["token_out"] == TOKEN
        assert intent["amount_in"] == 10 ** 17

    def test_ur_v2_swap_exact_in(self):
        inp = bytes.fromhex(
            _u(0x1) + _u(3 * 10 ** 17) + _u(9 * 10 ** 19) + _u(0xa0) +
            _u(1) + _u(2) + _a(WETH) + _a(TOKEN))
        commands = b"\x08"
        data = bytes.fromhex(
            "24856bc3" + _u(0x40) + _u(0x80) + _u(1)) + commands + \
            b"\x00" * 31 + bytes.fromhex(_u(1) + _u(0x20) + _u(len(inp))) + inp
        [intent] = decode_router_calldata(data)
        assert intent == {
            "dex": "v2", "token_in": WETH, "token_out": TOKEN, "fee": None,
            "exact_in": True, "amount_in": 3 * 10 ** 17, "amount_out": None,
            "amount_in_max": None, "amount_out_min": 9 * 10 ** 19}

    def test_multihop_and_unknown_selector_skipped(self):
        # 2-hop exactInput path -> no single-hop intent
        path = (bytes.fromhex(TOKEN[2:]) + (500).to_bytes(3, "big") +
                bytes.fromhex(USDG[2:]) + (3000).to_bytes(3, "big") +
                bytes.fromhex(WETH[2:]))
        data = bytes.fromhex(
            "b858183f" + _u(0x20) + _u(0x80) + _a(TOKEN) + _u(10 ** 18) +
            _u(1) + _u(len(path))) + path + b"\x00" * 21
        assert decode_router_calldata(data) == []
        assert decode_router_calldata(b"\xde\xad\xbe\xef" + b"\x00" * 64) == []
        assert decode_router_calldata(b"") == []


# ── V3 path decoding ─────────────────────────────────────────────────────────
class TestDecodeV3Path:
    def test_single_hop(self):
        path = (bytes.fromhex(WETH[2:]) + (10000).to_bytes(3, "big") +
                bytes.fromhex(TOKEN[2:]))
        assert decode_v3_path(path) == [(WETH, 10000, TOKEN)]

    def test_two_hops(self):
        path = (bytes.fromhex(WETH[2:]) + (500).to_bytes(3, "big") +
                bytes.fromhex(USDG[2:]) + (3000).to_bytes(3, "big") +
                bytes.fromhex(TOKEN[2:]))
        assert decode_v3_path(path) == [(WETH, 500, USDG),
                                        (USDG, 3000, TOKEN)]

    def test_garbage_tail_ignored(self):
        assert decode_v3_path(b"\x01\x02\x03") == []


# ── CREATE2 pool derivation (pinned to live-verified events) ─────────────────
class TestCreate2:
    def test_v3_pool_from_real_pool_created(self):
        assert v3_pool_address(POOL_CREATED["token0"], POOL_CREATED["token1"],
                               POOL_CREATED["fee"]) == POOL_CREATED["pool"]

    def test_v3_pool_token_order_irrelevant(self):
        assert v3_pool_address(POOL_CREATED["token1"], POOL_CREATED["token0"],
                               POOL_CREATED["fee"]) == POOL_CREATED["pool"]

    def test_v3_eth_usd_pool(self):
        # WETH/USDG 0.01% -> the v1 feed's keyless ETH/USD price pool
        # (== factory.getPool, RPC-verified 2026-07-10)
        assert v3_pool_address(WETH, USDG, 100) == ETH_USD_POOL

    def test_v2_pair_from_real_pair_created(self):
        assert v2_pair_address(PAIR_CREATED["token0"],
                               PAIR_CREATED["token1"]) == PAIR_CREATED["pair"]
        assert v2_pair_address(PAIR_CREATED["token1"],
                               PAIR_CREATED["token0"]) == PAIR_CREATED["pair"]


# ── intent -> trade classification ───────────────────────────────────────────
class TestIntentToTrade:
    def _i(self, ti, to, exact_in, **kw):
        base = {"dex": "v3", "token_in": ti, "token_out": to, "fee": 10000,
                "exact_in": exact_in, "amount_in": None, "amount_out": None,
                "amount_in_max": None, "amount_out_min": None}
        base.update(kw)
        return base

    def test_buy_exact_in_is_exact(self):
        t = intent_to_trade(self._i(WETH, TOKEN, True, amount_in=10 ** 17))
        assert t == ("buy", 10 ** 17, True)

    def test_buy_exact_out_uses_in_max_estimate(self):
        t = intent_to_trade(self._i(WETH, TOKEN, False,
                                    amount_out=5, amount_in_max=2 * 10 ** 17))
        assert t == ("buy", 2 * 10 ** 17, False)

    def test_sell_exact_in_uses_out_min_estimate(self):
        t = intent_to_trade(self._i(TOKEN, WETH, True, amount_in=10 ** 21,
                                    amount_out_min=3 * 10 ** 16))
        assert t == ("sell", 3 * 10 ** 16, False)

    def test_sell_exact_out_is_exact(self):
        t = intent_to_trade(self._i(TOKEN, WETH, False,
                                    amount_out=4 * 10 ** 16,
                                    amount_in_max=10 ** 22))
        assert t == ("sell", 4 * 10 ** 16, True)

    def test_token_token_swap_ignored(self):
        assert intent_to_trade(self._i(TOKEN, USDG, True,
                                       amount_in=10 ** 18)) is None

    def test_real_fixture_is_a_sell(self):
        d = decode_signed_tx(EIS_RAW)
        [intent] = decode_router_calldata(d["data"])
        kind, weth_wei, exact = intent_to_trade(intent)
        assert kind == "sell"
        assert weth_wei == 0x0D47A136040FFF  # amountOutMinimum (estimate)
        assert exact is False


# ── tape schema parity with the v1 feed ──────────────────────────────────────
class TestTapeSchema:
    def test_row_schema_matches_v1_plus_flags(self):
        row = tape_row("buy", 5 * 10 ** 17, 4000.0, 1783676335,
                       EIS_SENDER, POOL_CREATED["pool"], "TEST",
                       1783676335.62)
        row["pre_conf"] = True
        assert set(row) == {"kind", "volume_usd", "ts", "maker", "pair",
                            "sym", "lag_secs", "pre_conf"}
        assert row["volume_usd"] == 2000.0  # 0.5 WETH * $4000
        assert row["lag_secs"] == 0.62
        assert row["ts"] == "2026-07-10T09:38:55+00:00"  # epoch 1783676335 UTC
