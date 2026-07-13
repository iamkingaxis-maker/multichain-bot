"""Robinhood Chain (chain_id 4663) EVM execution rail — Uniswap V3 direct.

GREENFIELD, fully additive: nothing in the Solana runtime imports this module.

Robinhood Chain is a permissionless Arbitrum Orbit L2 (ETH gas, ~100ms blocks,
live since 2026-07-01). All addresses below were LIVE-VERIFIED on 2026-07-09:

  * RPC (official docs):   https://rpc.mainnet.chain.robinhood.com
    -> eth_chainId returned 0x1237 (4663).
  * SwapRouter02:          0xCaf681a66D020601342297493863E78C959E5cb2
    (Blockscout-verified source; WETH9()/factory() cross-checked via eth_call)
  * QuoterV2:              0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7
    (same WETH9 + factory as SwapRouter02 — the canonical deployment; other
    QuoterV2 deployments on the chain point at different factories)
  * WETH9:                 0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73
  * Uniswap V3 factory:    0x1F7D7550B1B028f7571E69a784071F0205fd2eFA
  * Universal Router:      0x8876789976decbfcbbbe364623c63652db8c0904
    (bytecode present on-chain; we route through SwapRouter02 instead because
    it uses plain ERC20 approvals — Universal Router requires the Permit2 flow)
  * v4 PoolManager:        0x8366a39CC670B4001A1121B8F6A443A643e40951

ROUTING CHOICE: direct Uniswap V3 exactInputSingle via SwapRouter02.
  - 1inch v6 (`api.1inch.dev/swap/v6.0/4663`) answers 401 without an API key —
    there is NO keyless public tier (probed 2026-07-09). When ONEINCH_API_KEY
    is set we try 1inch first for routing+calldata and FALL BACK to direct V3
    on any failure (bad key, unsupported chain, timeout).
  - hood.fun graduates into V3 1%-fee pools and Noxa.fun creates direct V3
    pools, so V3 fee tiers (100, 500, 3000, 10000) are quoted and the best
    wins. Robinfun V5 graduates to Uniswap V2 — V2 routing is an explicit
    follow-up (not built here).

ENV:
  RH_RPC_URL      — RPC endpoint (defaults to the official public RPC).
  RH_PRIVATE_KEY  — hot-wallet key. NEVER logged / printed / repr'd. Absent ->
                    PAPER-ONLY mode: quoting, balance reads and build_* helpers
                    all work; sign/send raise RhPaperModeError.
  ONEINCH_API_KEY — optional; enables the 1inch v6 routing path.
  DATA_DIR        — where rh_live_swaps.jsonl telemetry is appended (default
                    /data, same convention as core/live_swap_log.py).

Instrumentation mirrors the Solana live_swap fields (decision_mid_price,
real_fill_price, fill_vs_mid_slippage_pct, total_latency_ms, ...) so the
existing wallet-truth / fidelity tooling can read both rails the same way.

FAIL-OPEN vs FAIL-CLOSED (per function, documented inline):
  * Telemetry + fill decoding: FAIL-OPEN (never block a trade on logging).
  * Anything that guards money movement (chain-id check, slippage floor,
    sign/send without a key): FAIL-CLOSED (raise, never guess).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from web3 import Web3
from eth_account import Account

from core.probe_instrument import fill_slippage_pct
from core.live_swap_log import classify_failure_reason

logger = logging.getLogger(__name__)

# ── Chain constants (LIVE-VERIFIED 2026-07-09; see module docstring) ──────────
RH_CHAIN_ID = 4663  # eth_chainId == 0x1237
DEFAULT_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"

WETH9 = Web3.to_checksum_address("0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73")
SWAP_ROUTER02 = Web3.to_checksum_address("0xCaf681a66D020601342297493863E78C959E5cb2")
QUOTER_V2 = Web3.to_checksum_address("0x33e885eD0Ec9bF04EcfB19341582aADCb4c8A9E7")
UNISWAP_V3_FACTORY = Web3.to_checksum_address("0x1F7D7550B1B028f7571E69a784071F0205fd2eFA")
UNIVERSAL_ROUTER = Web3.to_checksum_address("0x8876789976decbfcbbbe364623c63652db8c0904")
V4_POOL_MANAGER = Web3.to_checksum_address("0x8366a39CC670B4001A1121B8F6A443A643e40951")

# hood.fun graduates at 1% (10000); Noxa/direct pools commonly 0.3%/0.05%.
FEE_TIERS = (10000, 3000, 500, 100)

# SwapRouter02 sentinel: recipient==address(2) means "the router itself"
# (used for the sell leg: swap token->WETH into the router, then unwrapWETH9).
ADDRESS_THIS = "0x0000000000000000000000000000000000000002"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
MAX_UINT256 = 2 ** 256 - 1

# Event topics (keccak256 of the canonical signatures).
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WETH_DEPOSIT_TOPIC = "0xe1fffcc4923d04b559f4d29a8bfc6cda04eb5b0d3c460751c2402c5c5cc9109c"
WETH_WITHDRAWAL_TOPIC = "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65"

# 1inch native-ETH pseudo-address (their convention for the gas token).
ONEINCH_NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
ONEINCH_BASE = "https://api.1inch.dev/swap/v6.0"

RH_SWAP_LOG_BASENAME = "rh_live_swaps.jsonl"

# ── Minimal ABIs (only what we call) ─────────────────────────────────────────
ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

# SwapRouter02 exactInputSingle: NO deadline field (that was the old SwapRouter).
# Selector must be 0x04e45aaf — asserted in tests.
SWAP_ROUTER02_ABI = [
    {"name": "exactInputSingle", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenIn", "type": "address"},
         {"name": "tokenOut", "type": "address"},
         {"name": "fee", "type": "uint24"},
         {"name": "recipient", "type": "address"},
         {"name": "amountIn", "type": "uint256"},
         {"name": "amountOutMinimum", "type": "uint256"},
         {"name": "sqrtPriceLimitX96", "type": "uint160"},
     ]}],
     "outputs": [{"name": "amountOut", "type": "uint256"}]},
    {"name": "unwrapWETH9", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "amountMinimum", "type": "uint256"},
                {"name": "recipient", "type": "address"}],
     "outputs": []},
    {"name": "multicall", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "data", "type": "bytes[]"}],
     "outputs": [{"name": "results", "type": "bytes[]"}]},
]

# QuoterV2: struct order is (tokenIn, tokenOut, amountIn, fee, sqrtPriceLimitX96)
# — note amountIn BEFORE fee (differs from the router struct).
QUOTER_V2_ABI = [
    {"name": "quoteExactInputSingle", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "params", "type": "tuple", "components": [
         {"name": "tokenIn", "type": "address"},
         {"name": "tokenOut", "type": "address"},
         {"name": "amountIn", "type": "uint256"},
         {"name": "fee", "type": "uint24"},
         {"name": "sqrtPriceLimitX96", "type": "uint160"},
     ]}],
     "outputs": [{"name": "amountOut", "type": "uint256"},
                 {"name": "sqrtPriceX96After", "type": "uint160"},
                 {"name": "initializedTicksCrossed", "type": "uint32"},
                 {"name": "gasEstimate", "type": "uint256"}]},
]


# ── Errors ────────────────────────────────────────────────────────────────────
class RhExecutionError(RuntimeError):
    """Base error for the Robinhood-chain rail."""


class RhPaperModeError(RhExecutionError):
    """Raised on sign/send when RH_PRIVATE_KEY is absent (paper-only mode)."""


class RhChainMismatchError(RhExecutionError):
    """Raised when the RPC answers a chain_id != 4663 (FAIL-CLOSED)."""


class RhSwapError(RhExecutionError):
    """Raised when a swap cannot be quoted/built/confirmed (FAIL-CLOSED)."""


# ── Pure helpers (no network — unit-tested directly) ─────────────────────────
_codec = Web3()  # provider-less Web3, used only for offline ABI encoding
_ROUTER_CODEC = _codec.eth.contract(abi=SWAP_ROUTER02_ABI)
_ERC20_CODEC = _codec.eth.contract(abi=ERC20_ABI)
_QUOTER_CODEC = _codec.eth.contract(abi=QUOTER_V2_ABI)


def _encode_abi(contract, fn_name: str, args) -> str:
    """web3 v6/v7 compat: contract calldata encoding."""
    if hasattr(contract, "encode_abi"):  # web3 >= 7
        return contract.encode_abi(abi_element_identifier=fn_name, args=args)
    return contract.encodeABI(fn_name=fn_name, args=args)  # web3 6.x


def encode_quoter_calldata(token_in: str, token_out: str, amount_in: int,
                           fee: int) -> str:
    """Calldata (hex str) for QuoterV2.quoteExactInputSingle. Pure."""
    return _encode_abi(_QUOTER_CODEC, "quoteExactInputSingle", [(
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        int(amount_in), int(fee), 0)])


def build_tier_quote_batch(token_in: str, token_out: str, amount_in: int,
                           fee_tiers=FEE_TIERS) -> list:
    """JSON-RPC batch payload quoting EVERY fee tier in ONE HTTP round trip.
    Pure. id == index into fee_tiers (parse_tier_quote_batch relies on it).

    WHY (quote-leg latency root cause, measured 2026-07-11): each QuoterV2
    eth_call costs ~185ms server-side on the public RH RPC (raw RTT ~55ms),
    so the sequential 4-tier sweep is ~750ms per quote side and a paper fill
    (buy sweep + rt-cost sell sweep + decimals) ran 1.9-2.9s — over the 2s
    Solana-parity budget. One batched POST answers all 4 tiers in ~160ms
    (the server evaluates them concurrently; rh_chain_feed.Rpc.batch already
    proved this RPC supports batching)."""
    return [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
             "params": [{"to": QUOTER_V2,
                         "data": encode_quoter_calldata(
                             token_in, token_out, amount_in, fee)},
                        "latest"]}
            for i, fee in enumerate(fee_tiers)]


def _quote_timeout_s(default: float = 10.0) -> float:
    """HTTP timeout (secs) for the batched quote POST. Env RH_QUOTE_TIMEOUT_S
    (default 10.0 = pre-2026-07-13 behavior). LOWER it (e.g. 2.5) to fast-fail
    the RPC-latency tail: a hung quote should miss the fill, never fire it
    3-18s late. FAIL-SAFE — a timeout returns None (no quote -> no trade)."""
    try:
        v = float(os.environ.get("RH_QUOTE_TIMEOUT_S", default))
        return v if v > 0 else default
    except Exception:
        return default


def build_roundtrip_quote_batch(token_in: str, eth_in_wei: int,
                                est_token_out: int, fee_tiers=FEE_TIERS) -> list:
    """ONE JSON-RPC batch quoting the BUY (WETH->token, eth_in_wei) across all
    tiers AND the RT-COST SELL (token->WETH, est_token_out) across all tiers in
    a SINGLE HTTP round trip. Buy tiers get ids 0..N-1, sell tiers ids N..2N-1
    (parse_roundtrip_quote_batch relies on the split). Pure.

    WHY (2026-07-13 quote-leg latency mine): a paper fill's stamped lat_quote_s
    (median ~1.06s, p90 ~2.1s, the leg that pushes 51% of fills over the 1.71s
    Solana-parity budget) is TWO sequential batched POSTs — quote_buy then the
    RT-cost quote_sell of the buy's exact output. They are dependent (the sell
    amount is the buy's output), so they cannot be parallelized as-is. This
    collapses them to ONE POST by quoting the sell of an ESTIMATED token amount
    (from the pool's last quote px). The caller uses the sell leg ONLY for the
    friction (rt-cost) gate — the booked fill price always comes from the EXACT
    buy quote in this same response — so the only approximation is a small error
    in the rt-cost gate input, bounded by the gate's several-pp threshold.
    Opt-in behind RH_RT_COMBINED; the exact two-POST path is the default."""
    n = len(fee_tiers)
    buys = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
             "params": [{"to": QUOTER_V2,
                         "data": encode_quoter_calldata(
                             WETH9, token_in, eth_in_wei, fee)}, "latest"]}
            for i, fee in enumerate(fee_tiers)]
    sells = [{"jsonrpc": "2.0", "id": n + i, "method": "eth_call",
              "params": [{"to": QUOTER_V2,
                          "data": encode_quoter_calldata(
                              token_in, WETH9, est_token_out, fee)}, "latest"]}
             for i, fee in enumerate(fee_tiers)]
    return buys + sells


def parse_roundtrip_quote_batch(response, fee_tiers=FEE_TIERS) -> Optional[tuple]:
    """Combined batch response -> ({fee: buy_out}, {fee: sell_out}), same
    per-tier semantics as parse_tier_quote_batch (error entry = no pool at that
    tier, skipped; zero/undecodable skipped). None when the shape is wrong or
    ANY tier id is missing (transport problem -> caller falls back). Pure."""
    if not isinstance(response, list):
        return None
    n = len(fee_tiers)
    by_id = {}
    for o in response:
        if isinstance(o, dict) and isinstance(o.get("id"), int):
            by_id[o["id"]] = o
    buys, sells = {}, {}
    for i, fee in enumerate(fee_tiers):
        for base, out in ((0, buys), (n, sells)):
            entry = by_id.get(base + i)
            if entry is None:
                return None          # tier unaccounted for — transport problem
            if entry.get("error") is not None:
                continue             # revert = no pool at this tier
            amt = decode_quoted_amount_out(entry.get("result"))
            if amt:
                out[fee] = amt
    return buys, sells


def decode_quoted_amount_out(result_hex) -> Optional[int]:
    """QuoterV2.quoteExactInputSingle eth_call result -> amountOut (first
    32-byte word) or None on anything undecodable. Pure, never raises."""
    try:
        h = str(result_hex)
        if h.startswith("0x"):
            h = h[2:]
        if len(h) < 64:
            return None
        return int(h[:64], 16)
    except Exception:
        return None


def parse_tier_quote_batch(response, fee_tiers=FEE_TIERS) -> Optional[dict]:
    """Batch response -> {fee: amount_out} (insertion order = fee_tiers order,
    matching the sequential sweep's tie-break). Semantics per tier mirror
    _quote_single: an "error" entry (revert = no pool at that tier) or a
    zero/undecodable result is skipped. Returns None when the response shape
    is wrong or ANY tier is missing (unknown state -> caller must fall back
    to the sequential path; FAIL-OPEN, never guess a quote). Pure."""
    if not isinstance(response, list):
        return None
    by_id = {}
    for o in response:
        if isinstance(o, dict) and isinstance(o.get("id"), int):
            by_id[o["id"]] = o
    out = {}
    for i, fee in enumerate(fee_tiers):
        entry = by_id.get(i)
        if entry is None:
            return None  # tier unaccounted for — transport problem
        if entry.get("error") is not None:
            continue     # revert = no pool at this tier (same as sequential)
        amt = decode_quoted_amount_out(entry.get("result"))
        if amt:
            out[fee] = amt
    return out


def min_out_after_slippage(quoted_out: int, max_slippage_bps: int) -> int:
    """Floor amountOutMinimum from a quote. FAIL-CLOSED: bad inputs raise
    (a wrong slippage floor is a money bug, never guess)."""
    q = int(quoted_out)
    bps = int(max_slippage_bps)
    if q <= 0:
        raise ValueError(f"quoted_out must be > 0, got {quoted_out}")
    if not (0 <= bps < 10_000):
        raise ValueError(f"max_slippage_bps must be in [0, 10000), got {max_slippage_bps}")
    return q * (10_000 - bps) // 10_000


def encode_exact_input_single(token_in: str, token_out: str, fee: int,
                              recipient: str, amount_in: int,
                              amount_out_min: int,
                              sqrt_price_limit_x96: int = 0) -> str:
    """Calldata (hex str) for SwapRouter02.exactInputSingle. Pure."""
    params = (
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        int(fee),
        Web3.to_checksum_address(recipient),
        int(amount_in),
        int(amount_out_min),
        int(sqrt_price_limit_x96),
    )
    return _encode_abi(_ROUTER_CODEC, "exactInputSingle", [params])


def encode_unwrap_weth9(amount_minimum: int, recipient: str) -> str:
    """Calldata for SwapRouter02.unwrapWETH9 (router WETH -> native ETH). Pure."""
    return _encode_abi(_ROUTER_CODEC, "unwrapWETH9",
                       [int(amount_minimum), Web3.to_checksum_address(recipient)])


def encode_multicall(calls: list) -> str:
    """Calldata for SwapRouter02.multicall(bytes[]). Pure."""
    blobs = [bytes.fromhex(c[2:] if c.startswith("0x") else c) for c in calls]
    return _encode_abi(_ROUTER_CODEC, "multicall", [blobs])


def build_buy_calldata(token_addr: str, eth_amount_wei: int, min_out: int,
                       recipient: str, fee: int) -> dict:
    """ETH -> token buy calldata against SwapRouter02. Pure (no network).
    tokenIn=WETH9 + msg.value: SwapRouter02 wraps the ETH itself.
    Returns {to, data, value}."""
    data = encode_exact_input_single(
        WETH9, token_addr, fee, recipient, int(eth_amount_wei), int(min_out))
    return {"to": SWAP_ROUTER02, "data": data, "value": int(eth_amount_wei)}


def build_sell_calldata(token_addr: str, token_amount: int, min_out_wei: int,
                        recipient: str, fee: int) -> dict:
    """token -> native ETH sell calldata against SwapRouter02. Pure.

    multicall([ exactInputSingle(token -> WETH, recipient = ADDRESS_THIS),
                unwrapWETH9(min_out_wei, wallet) ])
    so the wallet receives native ETH, keeping the wallet-truth delta on the
    gas token. Requires a prior ERC20 approve(token -> SwapRouter02)."""
    swap = encode_exact_input_single(
        token_addr, WETH9, fee, ADDRESS_THIS, int(token_amount), int(min_out_wei))
    unwrap = encode_unwrap_weth9(int(min_out_wei), recipient)
    return {"to": SWAP_ROUTER02, "data": encode_multicall([swap, unwrap]),
            "value": 0}


def _hexstr(x) -> str:
    """Normalize bytes / HexBytes / str to a lowercase 0x-hex string."""
    if x is None:
        return ""
    if isinstance(x, (bytes, bytearray)):
        return "0x" + bytes(x).hex().lower()
    s = str(x)
    if hasattr(x, "hex") and not s.startswith("0x"):  # HexBytes-like
        s = x.hex()
    if not s.startswith("0x"):
        s = "0x" + s
    return s.lower()


def _topic_addr(topic) -> str:
    """address (lowercase 0x40) from a 32-byte indexed topic."""
    h = _hexstr(topic)
    return "0x" + h[-40:]


def _data_int(data) -> int:
    h = _hexstr(data)
    if h in ("", "0x"):
        return 0
    return int(h, 16)


def effective_fill_from_receipt(receipt, *, wallet: str, token: str, side: str,
                                token_decimals: int = 18,
                                weth: str = WETH9) -> Optional[dict]:
    """Effective fill price (ETH per token unit) decoded from receipt logs.

    Works on both raw-RPC dict receipts (hex strings) and web3 AttributeDicts.
    buy : eth_in  = WETH Deposit wad (router wrap)  [fallback: WETH Transfer
          out of the wallet], tokens_out = token Transfers TO the wallet.
    sell: tokens_in = token Transfers FROM the wallet, eth_out = WETH
          Withdrawal wad [fallback: WETH Transfers TO the wallet].

    FAIL-OPEN: instrumentation only — undecodable receipt -> None, never raises.
    """
    try:
        wallet_l = wallet.lower()
        token_l = token.lower()
        weth_l = weth.lower()
        logs = receipt.get("logs") if isinstance(receipt, dict) else receipt["logs"]
        token_in = token_out = eth_in = eth_out = 0
        for lg in logs or []:
            addr = str(lg.get("address") or "").lower()
            topics = lg.get("topics") or []
            if not topics:
                continue
            t0 = _hexstr(topics[0])
            amt = _data_int(lg.get("data"))
            if addr == weth_l and t0 == WETH_DEPOSIT_TOPIC:
                eth_in += amt
            elif addr == weth_l and t0 == WETH_WITHDRAWAL_TOPIC:
                eth_out += amt
            elif t0 == TRANSFER_TOPIC and len(topics) >= 3:
                src = _topic_addr(topics[1])
                dst = _topic_addr(topics[2])
                if addr == token_l:
                    if dst == wallet_l:
                        token_out += amt
                    if src == wallet_l:
                        token_in += amt
                elif addr == weth_l:
                    if src == wallet_l:
                        eth_in += amt
                    if dst == wallet_l:
                        eth_out += amt
        if side == "buy":
            tok_amt, eth_amt = token_out, eth_in
        else:
            tok_amt, eth_amt = token_in, eth_out
        if tok_amt <= 0 or eth_amt <= 0:
            return None
        price = (eth_amt / 1e18) / (tok_amt / (10 ** int(token_decimals)))
        gas_used = _data_int(receipt.get("gasUsed", 0)) if isinstance(
            receipt.get("gasUsed", 0), str) else int(receipt.get("gasUsed") or 0)
        egp_raw = receipt.get("effectiveGasPrice") or 0
        egp = _data_int(egp_raw) if isinstance(egp_raw, str) else int(egp_raw)
        return {
            "token_amount_atomic": tok_amt,
            "eth_amount_wei": eth_amt,
            "fill_price_eth_per_token": price,
            "gas_cost_eth": (gas_used * egp) / 1e18 if gas_used and egp else None,
        }
    except Exception as e:  # pragma: no cover - defensive fail-open
        logger.debug("[rh-exec] fill decode failed: %s", e)
        return None


def _swap_log_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), RH_SWAP_LOG_BASENAME)


def log_rh_swap(**fields) -> None:
    """Append one swap-telemetry record to DATA_DIR/rh_live_swaps.jsonl.

    Field names mirror core/live_swap_log.py (decision_mid_price,
    real_fill_price, fill_vs_mid_slippage_pct, total_latency_ms, ...) so the
    existing fidelity tooling reads both rails identically. FAIL-OPEN: never
    raises into a trading path."""
    try:
        rec = dict(fields)
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
        rec.setdefault("chain", "robinhood")
        rec.setdefault("chain_id", RH_CHAIN_ID)
        rec["failure_reason"] = classify_failure_reason(
            bool(rec.get("success")), rec.get("failure_reason"),
            rec.get("error_text"))
        with open(_swap_log_path(), "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[rh-exec] swap-log emit failed: %s", e)


# ── Executor ──────────────────────────────────────────────────────────────────
@dataclass
class RhQuote:
    """Best V3 quote across fee tiers."""
    token: str
    side: str                    # 'buy' | 'sell'
    amount_in: int               # atomic (wei for buys, token units for sells)
    amount_out: int              # atomic quoted output
    fee: int                     # winning fee tier
    mid_price_eth_per_token: Optional[float] = None
    quotes_by_fee: dict = field(default_factory=dict)


class RhExecutor:
    """Robinhood Chain execution: quote -> build -> sign -> send -> decode fill.

    Without RH_PRIVATE_KEY this is PAPER-ONLY: connect/quote/balances/build_*
    work; _sign_and_send (and therefore quote_and_swap_buy / swap_sell past
    the build step) raises RhPaperModeError. The key is held only inside the
    eth_account object and is never logged, printed, or included in repr.
    """

    def __init__(self, rpc_url: Optional[str] = None,
                 private_key: Optional[str] = None,
                 receipt_timeout_s: float = 60.0):
        self.rpc_url = rpc_url or os.environ.get("RH_RPC_URL") or DEFAULT_RPC_URL
        pk = private_key if private_key is not None else os.environ.get("RH_PRIVATE_KEY")
        self._account = Account.from_key(pk) if pk else None
        self.receipt_timeout_s = receipt_timeout_s
        self.w3: Optional[Web3] = None
        self._send_lock = threading.Lock()  # serialize nonce use
        # quote-leg latency fixes (2026-07-11, measured on the public RPC):
        # ERC20 decimals are immutable -> memoize (each uncached read is a
        # ~185ms eth_call, and quote_buy/quote_sell each made one per call).
        self._decimals_cache: dict = {}
        # keep-alive session for the batched tier sweep (lazy; requests is a
        # web3 dependency so it is always importable).
        self._batch_session = None

    def __repr__(self) -> str:  # NEVER expose key material
        mode = "paper-only" if self.paper_only else f"live wallet={self.wallet_address}"
        return f"<RhExecutor chain_id={RH_CHAIN_ID} rpc={self.rpc_url} {mode}>"

    # ── connection / identity ────────────────────────────────────────────
    @property
    def paper_only(self) -> bool:
        return self._account is None

    @property
    def wallet_address(self) -> Optional[str]:
        return self._account.address if self._account else None

    def connect(self) -> Web3:
        """Connect + verify chain_id == 4663. FAIL-CLOSED: a mismatched chain
        (wrong RPC in env) must never be traded against."""
        w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 15}))
        cid = w3.eth.chain_id
        if int(cid) != RH_CHAIN_ID:
            raise RhChainMismatchError(
                f"RPC {self.rpc_url} answered chain_id={cid}, expected {RH_CHAIN_ID}")
        self.w3 = w3
        logger.info("[rh-exec] connected chain_id=%s rpc=%s mode=%s",
                    cid, self.rpc_url, "paper-only" if self.paper_only else "live")
        return w3

    def _require_w3(self) -> Web3:
        if self.w3 is None:
            self.connect()
        return self.w3

    # ── balances (wallet-truth delta pattern) ────────────────────────────
    def eth_balance(self, addr: Optional[str] = None) -> float:
        """Native ETH balance in ETH. FAIL-CLOSED (raises on RPC error):
        wallet-truth reads must never silently report a stale/zero number."""
        w3 = self._require_w3()
        a = Web3.to_checksum_address(addr or self.wallet_address)
        return w3.eth.get_balance(a) / 1e18

    def token_balance(self, token_addr: str, addr: Optional[str] = None) -> int:
        """ERC20 balance in atomic units. FAIL-CLOSED (raises on RPC error)."""
        w3 = self._require_w3()
        c = w3.eth.contract(address=Web3.to_checksum_address(token_addr),
                            abi=ERC20_ABI)
        a = Web3.to_checksum_address(addr or self.wallet_address)
        return int(c.functions.balanceOf(a).call())

    def token_decimals(self, token_addr: str) -> int:
        """ERC20 decimals; FAIL-OPEN to 18 (only affects price REPORTING —
        all trade math stays in atomic units). MEMOIZED (2026-07-11 quote-leg
        latency fix): decimals are immutable, and the uncached read is a
        ~185ms eth_call paid on EVERY quote_buy/quote_sell. Failures are NOT
        cached (a transient RPC error must not pin 18 forever)."""
        key = token_addr.lower()
        cached = self._decimals_cache.get(key)
        if cached is not None:
            return cached
        try:
            w3 = self._require_w3()
            c = w3.eth.contract(address=Web3.to_checksum_address(token_addr),
                                abi=ERC20_ABI)
            val = int(c.functions.decimals().call())
            self._decimals_cache[key] = val
            return val
        except Exception:
            return 18

    # ── quoting (QuoterV2 across fee tiers) ──────────────────────────────
    def _quote_single(self, token_in: str, token_out: str, amount_in: int,
                      fee: int) -> Optional[int]:
        """One QuoterV2 eth_call. None on revert (= no pool at that tier)."""
        try:
            w3 = self._require_w3()
            q = w3.eth.contract(address=QUOTER_V2, abi=QUOTER_V2_ABI)
            out = q.functions.quoteExactInputSingle((
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
                int(amount_in), int(fee), 0)).call()
            return int(out[0])
        except Exception:
            return None

    def _quote_all_tiers_batched(self, token_in: str, token_out: str,
                                 amount_in: int) -> Optional[dict]:
        """All fee tiers in ONE JSON-RPC batch POST (~160ms vs ~750ms
        sequential, measured 2026-07-11). Returns {fee: amount_out} or None
        on ANY transport/shape problem — the caller falls back to the
        sequential per-tier path. FAIL-OPEN by design: batching is a latency
        optimization, never a dependency."""
        try:
            import requests
            if self._batch_session is None:
                self._batch_session = requests.Session()
            payload = build_tier_quote_batch(token_in, token_out, amount_in)
            r = self._batch_session.post(
                self.rpc_url, json=payload, timeout=_quote_timeout_s(),
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return None
            return parse_tier_quote_batch(r.json())
        except Exception:
            return None

    def _best_quote(self, token_in: str, token_out: str,
                    amount_in: int) -> Optional[tuple]:
        by_fee = self._quote_all_tiers_batched(token_in, token_out, amount_in)
        if by_fee is None:  # batch unavailable -> sequential sweep (fallback)
            # RH_QUOTE_FALLBACK=none: skip the slow per-tier sweep (4 eth_calls
            # at the 15s provider timeout each — the quote-leg tail) and
            # fast-fail. FAIL-SAFE: a missed quote is a missed fill, never a
            # bad one. Default "seq" = pre-2026-07-13 behavior.
            if str(os.environ.get("RH_QUOTE_FALLBACK", "seq")).strip().lower() \
                    == "none":
                return None
            by_fee = {}
            for fee in FEE_TIERS:
                out = self._quote_single(token_in, token_out, amount_in, fee)
                if out:
                    by_fee[fee] = out
        best = None
        for fee, out in by_fee.items():  # insertion order == FEE_TIERS order
            if best is None or out > best[1]:
                best = (fee, out)
        if best is None:
            return None
        return best[0], best[1], by_fee

    def quote_buy(self, token_addr: str, eth_amount_wei: int) -> Optional[RhQuote]:
        """Best WETH->token quote. None when no V3 pool answers (caller treats
        as no-route; FAIL-CLOSED for trading — we never buy unquoted)."""
        r = self._best_quote(WETH9, token_addr, eth_amount_wei)
        if r is None:
            return None
        fee, out, by_fee = r
        dec = self.token_decimals(token_addr)
        mid = (eth_amount_wei / 1e18) / (out / (10 ** dec)) if out else None
        return RhQuote(token=token_addr, side="buy", amount_in=eth_amount_wei,
                       amount_out=out, fee=fee, mid_price_eth_per_token=mid,
                       quotes_by_fee=by_fee)

    def quote_sell(self, token_addr: str, token_amount: int) -> Optional[RhQuote]:
        """Best token->WETH quote. None when no V3 pool answers."""
        r = self._best_quote(token_addr, WETH9, token_amount)
        if r is None:
            return None
        fee, out, by_fee = r
        dec = self.token_decimals(token_addr)
        mid = (out / 1e18) / (token_amount / (10 ** dec)) if token_amount else None
        return RhQuote(token=token_addr, side="sell", amount_in=token_amount,
                       amount_out=out, fee=fee, mid_price_eth_per_token=mid,
                       quotes_by_fee=by_fee)

    def quote_roundtrip_batched(self, token_addr: str, eth_amount_wei: int,
                                est_token_out: int) -> Optional[tuple]:
        """Buy quote + RT-cost sell quote in ONE batched POST (~½ the round
        trips of quote_buy-then-quote_sell). Returns (RhQuote buy, eth_back_wei)
        where eth_back_wei is the best sell output for est_token_out (the
        rt-cost numerator), or None on ANY problem so the caller falls back to
        the exact two-quote path. The buy RhQuote is EXACT (real pool state) —
        only est_token_out (the rt-cost gate input) is an estimate. FAIL-OPEN."""
        if not est_token_out or int(est_token_out) <= 0:
            return None
        try:
            import requests
            if self._batch_session is None:
                self._batch_session = requests.Session()
            payload = build_roundtrip_quote_batch(
                token_addr, int(eth_amount_wei), int(est_token_out))
            r = self._batch_session.post(
                self.rpc_url, json=payload, timeout=_quote_timeout_s(),
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                return None
            parsed = parse_roundtrip_quote_batch(r.json())
            if parsed is None:
                return None
            buy_by_fee, sell_by_fee = parsed
            if not buy_by_fee:
                return None
            bfee, bout = max(buy_by_fee.items(), key=lambda kv: kv[1])
            dec = self.token_decimals(token_addr)
            mid = ((eth_amount_wei / 1e18) / (bout / (10 ** dec))
                   if bout else None)
            buy_q = RhQuote(token=token_addr, side="buy",
                            amount_in=int(eth_amount_wei), amount_out=bout,
                            fee=bfee, mid_price_eth_per_token=mid,
                            quotes_by_fee=buy_by_fee)
            eth_back = max(sell_by_fee.values()) if sell_by_fee else 0
            return buy_q, int(eth_back)
        except Exception:
            return None

    # ── optional 1inch v6 routing (keyed; falls back to direct V3) ───────
    def _oneinch_swap_tx(self, src: str, dst: str, amount: int,
                         max_slippage_bps: int) -> Optional[dict]:
        """Ask 1inch v6 for routed calldata. Returns their tx dict or None on
        ANY failure (no key, 401, unsupported chain, timeout) — the caller
        falls back to direct V3. FAIL-OPEN by design: 1inch is an optimization,
        never a dependency."""
        key = os.environ.get("ONEINCH_API_KEY")
        if not key or self.paper_only:
            return None
        try:
            import urllib.parse
            import urllib.request
            params = urllib.parse.urlencode({
                "src": src, "dst": dst, "amount": str(int(amount)),
                "from": self.wallet_address,
                "slippage": max_slippage_bps / 100.0,  # 1inch wants percent
                "disableEstimate": "false",
            })
            req = urllib.request.Request(
                f"{ONEINCH_BASE}/{RH_CHAIN_ID}/swap?{params}",
                headers={"Authorization": f"Bearer {key}",
                         "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode())
            tx = body.get("tx") or {}
            if not tx.get("to") or not tx.get("data"):
                return None
            return {"to": Web3.to_checksum_address(tx["to"]),
                    "data": tx["data"], "value": int(tx.get("value") or 0),
                    "dst_amount": int(body.get("dstAmount") or 0)}
        except Exception as e:
            logger.info("[rh-exec] 1inch route unavailable (%s) — direct V3", e)
            return None

    # ── tx assembly / signing ────────────────────────────────────────────
    def _build_tx(self, call: dict, gas_fallback: int = 600_000) -> dict:
        """EIP-1559 tx from {to,data,value}: pending nonce, 2x-base maxFee.
        Gas estimation FAIL-OPEN to gas_fallback (estimate failures on fresh
        Orbit nodes are common; the cap bounds the cost)."""
        w3 = self._require_w3()
        frm = self.wallet_address or ZERO_ADDRESS
        try:
            base = int(w3.eth.get_block("latest").get("baseFeePerGas") or 0)
        except Exception:
            base = 0
        if base <= 0:
            base = int(w3.eth.gas_price)
        try:
            tip = int(w3.eth.max_priority_fee)
        except Exception:
            tip = 0  # Orbit sequencers generally ignore tips
        tx = {
            "chainId": RH_CHAIN_ID,
            "from": Web3.to_checksum_address(frm),
            "to": Web3.to_checksum_address(call["to"]),
            "data": call["data"],
            "value": int(call.get("value") or 0),
            "type": 2,
            "maxFeePerGas": 2 * base + tip,
            "maxPriorityFeePerGas": tip,
            "nonce": w3.eth.get_transaction_count(
                Web3.to_checksum_address(frm), "pending"),
        }
        try:
            tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.25)
        except Exception as e:
            logger.debug("[rh-exec] estimate_gas failed (%s); fallback %s",
                         e, gas_fallback)
            tx["gas"] = gas_fallback
        return tx

    def _sign_and_send(self, tx: dict) -> str:
        """Sign + broadcast. FAIL-CLOSED: paper-only mode raises a clear
        RhPaperModeError — building works without a key, sending never does."""
        if self.paper_only:
            raise RhPaperModeError(
                "RH_PRIVATE_KEY not set — paper-only mode: build_*/quote_* "
                "work, sign/send are disabled")
        w3 = self._require_w3()
        signed = self._account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:  # web3 6.x
            raw = signed.rawTransaction
        return w3.eth.send_raw_transaction(raw).hex()

    def _wait_receipt(self, tx_hash: str):
        w3 = self._require_w3()
        return w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=self.receipt_timeout_s)

    def _ensure_allowance(self, token_addr: str, amount: int) -> Optional[str]:
        """Approve SwapRouter02 for `token` when allowance < amount (max
        approve, one tx, waits for the receipt). Returns the approve tx hash
        or None when allowance already suffices. FAIL-CLOSED on errors."""
        w3 = self._require_w3()
        token = Web3.to_checksum_address(token_addr)
        c = w3.eth.contract(address=token, abi=ERC20_ABI)
        current = int(c.functions.allowance(
            Web3.to_checksum_address(self.wallet_address), SWAP_ROUTER02).call())
        if current >= amount:
            return None
        data = _encode_abi(_ERC20_CODEC, "approve", [SWAP_ROUTER02, MAX_UINT256])
        tx = self._build_tx({"to": token, "data": data, "value": 0},
                            gas_fallback=120_000)
        tx_hash = self._sign_and_send(tx)
        rcpt = self._wait_receipt(tx_hash)
        if int(rcpt.get("status", 0)) != 1:
            raise RhSwapError(f"approve reverted tx={tx_hash}")
        logger.info("[rh-exec] approved router for %s tx=%s", token, tx_hash)
        return tx_hash

    # ── public swap API ──────────────────────────────────────────────────
    def quote_and_swap_buy(self, token_addr: str, eth_amount: float,
                           max_slippage_bps: int) -> dict:
        """BUY: quote (1inch when keyed, else QuoterV2) -> build -> sign ->
        send -> receipt -> effective fill from logs. Returns the telemetry
        record (also appended to rh_live_swaps.jsonl).

        FAIL-CLOSED on: no route, chain mismatch, paper mode at the sign step,
        reverted tx (raises RhSwapError with the record already logged)."""
        token = Web3.to_checksum_address(token_addr)
        wei = int(eth_amount * 1e18)
        decision_mono = time.monotonic()

        route = "uniswap_v3_direct"
        oneinch = self._oneinch_swap_tx(ONEINCH_NATIVE, token, wei,
                                        max_slippage_bps)
        quote = self.quote_buy(token, wei)
        dec = self.token_decimals(token)
        if oneinch is not None:
            route = "1inch_v6"
            call = oneinch
            quoted_out = oneinch["dst_amount"] or (quote.amount_out if quote else 0)
            fee = quote.fee if quote else None
            mid = ((wei / 1e18) / (quoted_out / 10 ** dec)) if quoted_out else (
                quote.mid_price_eth_per_token if quote else None)
            min_out = min_out_after_slippage(quoted_out, max_slippage_bps) if quoted_out else None
        else:
            if quote is None:
                raise RhSwapError(f"no V3 route for buy {token} "
                                  f"(fee tiers {FEE_TIERS} all unquoted)")
            quoted_out = quote.amount_out
            fee = quote.fee
            mid = quote.mid_price_eth_per_token
            min_out = min_out_after_slippage(quoted_out, max_slippage_bps)
            call = build_buy_calldata(token, wei, min_out,
                                      self.wallet_address or ZERO_ADDRESS, fee)
        return self._execute_and_record(
            side="buy", token=token, call=call, route=route, fee=fee,
            amount_in=wei, quoted_out=quoted_out, min_out=min_out,
            decision_mid=mid, token_decimals=dec, decision_mono=decision_mono,
            size_eth=eth_amount)

    def swap_sell(self, token_addr: str, token_amount="all",
                  max_slippage_bps: int = 300) -> dict:
        """SELL token -> native ETH. token_amount: atomic int or 'all'
        (reads the live balance). Ensures the router allowance first.
        FAIL-CLOSED same as quote_and_swap_buy."""
        token = Web3.to_checksum_address(token_addr)
        decision_mono = time.monotonic()
        if token_amount == "all":
            token_amount = self.token_balance(token)
        amount = int(token_amount)
        if amount <= 0:
            raise RhSwapError(f"nothing to sell: balance/amount={amount} {token}")

        dec = self.token_decimals(token)
        route = "uniswap_v3_direct"
        oneinch = self._oneinch_swap_tx(token, ONEINCH_NATIVE, amount,
                                        max_slippage_bps)
        quote = self.quote_sell(token, amount)
        if oneinch is not None:
            route = "1inch_v6"
            call = oneinch
            quoted_out = oneinch["dst_amount"] or (quote.amount_out if quote else 0)
            fee = quote.fee if quote else None
            mid = ((quoted_out / 1e18) / (amount / 10 ** dec)) if quoted_out else (
                quote.mid_price_eth_per_token if quote else None)
            min_out = min_out_after_slippage(quoted_out, max_slippage_bps) if quoted_out else None
            # 1inch spender approval differs from SwapRouter02 — their /swap
            # with disableEstimate=false fails loudly on missing allowance;
            # we still fall back to direct V3 in that case (oneinch=None).
        else:
            if quote is None:
                raise RhSwapError(f"no V3 route for sell {token} "
                                  f"(fee tiers {FEE_TIERS} all unquoted)")
            quoted_out = quote.amount_out
            fee = quote.fee
            mid = quote.mid_price_eth_per_token
            min_out = min_out_after_slippage(quoted_out, max_slippage_bps)
            self._ensure_allowance(token, amount)
            call = build_sell_calldata(token, amount, min_out,
                                       self.wallet_address or ZERO_ADDRESS, fee)
        return self._execute_and_record(
            side="sell", token=token, call=call, route=route, fee=fee,
            amount_in=amount, quoted_out=quoted_out, min_out=min_out,
            decision_mid=mid, token_decimals=dec, decision_mono=decision_mono,
            size_eth=(quoted_out / 1e18) if quoted_out else None)

    # ── shared execute + instrument path ─────────────────────────────────
    def _execute_and_record(self, *, side: str, token: str, call: dict,
                            route: str, fee, amount_in: int, quoted_out,
                            min_out, decision_mid, token_decimals: int,
                            decision_mono: float, size_eth) -> dict:
        order_start_mono = time.monotonic()
        tx_hash = None
        success = False
        error_text = None
        fill = None
        receipt = None
        try:
            with self._send_lock:
                tx = self._build_tx(call)
                tx_hash = self._sign_and_send(tx)
            receipt = self._wait_receipt(tx_hash)
            success = int(receipt.get("status", 0)) == 1
            if not success:
                error_text = "revert (status=0)"
            else:
                fill = effective_fill_from_receipt(
                    receipt, wallet=self.wallet_address, token=token,
                    side=side, token_decimals=token_decimals)
        except RhPaperModeError:
            raise  # clear signal, no telemetry noise for paper callers
        except Exception as e:
            error_text = str(e)
        confirmed_mono = time.monotonic()

        real_fill = fill["fill_price_eth_per_token"] if fill else None
        record = {
            "side": side,
            "token_address": token,
            "wallet": self.wallet_address,
            "route": route,
            "fee_tier": fee,
            "size_eth": size_eth,
            "amount_in": amount_in,
            "quoted_out": quoted_out,
            "min_out": min_out,
            "amount_out": fill["token_amount_atomic"] if (fill and side == "buy")
                          else (fill["eth_amount_wei"] if fill else None),
            "decimals": token_decimals,
            "decision_ts": decision_mono,
            "order_start_ts": order_start_mono,
            "confirmed_ts": confirmed_mono,
            "total_latency_ms": round((confirmed_mono - decision_mono) * 1000, 1),
            "decision_mid_price": decision_mid,
            "real_fill_price": real_fill,
            "fill_vs_mid_slippage_pct": fill_slippage_pct(decision_mid, real_fill, side),
            "gas_cost_eth": fill.get("gas_cost_eth") if fill else None,
            "tx_signature": tx_hash,
            "success": success,
            "failure_reason": None,  # normalized by log_rh_swap
            "error_text": error_text,
            "live_mode": not self.paper_only,
            "paper": self.paper_only,
        }
        log_rh_swap(**record)
        if not success:
            raise RhSwapError(
                f"{side} {token} failed: {error_text or 'unknown'} tx={tx_hash}")
        return record
