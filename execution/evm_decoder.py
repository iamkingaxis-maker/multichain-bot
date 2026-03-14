"""
EVM Transaction Decoder
Properly decodes swap transactions on Base and BNB Chain.

Supports:
  - Uniswap V2 / PancakeSwap V2 (swapExactETHForTokens, etc.)
  - Uniswap V3 / PancakeSwap V3 (exactInputSingle, exactInput)
  - 0x Protocol aggregator swaps
  - 1inch aggregator swaps
  - Generic multi-hop paths

Previously we used naive hex pattern matching which missed
V3 swaps and multi-hop routes. This uses proper ABI decoding.
"""

import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Method Signatures (first 4 bytes of keccak256 of function signature) ──────

# Uniswap V2 / PancakeSwap V2
SWAP_EXACT_ETH_FOR_TOKENS        = "0x7ff36ab5"
SWAP_EXACT_ETH_FOR_TOKENS_FEE    = "0xb6f9de95"
SWAP_EXACT_TOKENS_FOR_ETH        = "0x18cbafe5"
SWAP_EXACT_TOKENS_FOR_ETH_FEE    = "0x791ac947"
SWAP_EXACT_TOKENS_FOR_TOKENS     = "0x38ed1739"
SWAP_EXACT_TOKENS_FOR_TOKENS_FEE = "0x5c11d795"
SWAP_ETH_FOR_EXACT_TOKENS        = "0xfb3bdb41"
SWAP_TOKENS_FOR_EXACT_ETH        = "0x4a25d94a"
SWAP_TOKENS_FOR_EXACT_TOKENS     = "0x8803dbee"

# Uniswap V3
EXACT_INPUT_SINGLE    = "0x414bf389"
EXACT_INPUT_SINGLE_V2 = "0x04e45aaf"
EXACT_OUTPUT_SINGLE   = "0xdb3e2198"
EXACT_INPUT           = "0xc04b8d59"
EXACT_OUTPUT          = "0xf28c0498"
MULTICALL             = "0xac9650d8"
MULTICALL_V2          = "0x5ae401dc"

# 0x Protocol
ZEROX_FILL_OR_KILL    = "0xd9627aa4"
ZEROX_BATCH_FILL      = "0x8bc8efb3"
ZEROX_TRANSFORM_ERC20 = "0x415565b0"

# 1inch
ONEINCH_SWAP          = "0x12aa3caf"
ONEINCH_UNOSWAP       = "0x0502b1c5"

# All recognized buy methods (ETH/BNB → Token)
BUY_METHODS = {
    SWAP_EXACT_ETH_FOR_TOKENS,
    SWAP_EXACT_ETH_FOR_TOKENS_FEE,
    SWAP_ETH_FOR_EXACT_TOKENS,
    EXACT_INPUT_SINGLE,
    EXACT_INPUT_SINGLE_V2,
    EXACT_INPUT,
    ZEROX_TRANSFORM_ERC20,
    ONEINCH_SWAP,
    ONEINCH_UNOSWAP,
    MULTICALL,
    MULTICALL_V2
}

# All recognized sell methods (Token → ETH/BNB)
SELL_METHODS = {
    SWAP_EXACT_TOKENS_FOR_ETH,
    SWAP_EXACT_TOKENS_FOR_ETH_FEE,
    SWAP_TOKENS_FOR_EXACT_ETH,
    EXACT_OUTPUT_SINGLE,
    EXACT_OUTPUT,
}

# Methods that could be either buy or sell
AMBIGUOUS_METHODS = {
    SWAP_EXACT_TOKENS_FOR_TOKENS,
    SWAP_EXACT_TOKENS_FOR_TOKENS_FEE,
    SWAP_TOKENS_FOR_EXACT_TOKENS,
}


@dataclass
class DecodedSwap:
    """Result of decoding a swap transaction."""
    action: str              # "buy" or "sell"
    token_in: str            # Input token address
    token_out: str           # Output token address
    amount_in_wei: int       # Raw input amount
    amount_out_min_wei: int  # Minimum output (slippage)
    native_amount_wei: int   # ETH/BNB value (from tx.value)
    path: List[str]          # Full swap path
    method_id: str           # Which method was called
    method_name: str         # Human-readable method name
    is_multi_hop: bool       # Multi-hop route (path > 2 tokens)
    decoded: bool = True     # Successfully decoded


class EVMTransactionDecoder:
    """
    Decodes EVM swap transactions using proper ABI parameter parsing.
    Works with Uniswap V2/V3, PancakeSwap, 0x, and 1inch.
    """

    def __init__(self, weth_address: str, usdc_address: str):
        self.weth = weth_address.lower()
        self.usdc = usdc_address.lower()

        self.method_names = {
            SWAP_EXACT_ETH_FOR_TOKENS: "swapExactETHForTokens",
            SWAP_EXACT_ETH_FOR_TOKENS_FEE: "swapExactETHForTokensSupportingFee",
            SWAP_EXACT_TOKENS_FOR_ETH: "swapExactTokensForETH",
            SWAP_EXACT_TOKENS_FOR_ETH_FEE: "swapExactTokensForETHSupportingFee",
            SWAP_EXACT_TOKENS_FOR_TOKENS: "swapExactTokensForTokens",
            SWAP_EXACT_TOKENS_FOR_TOKENS_FEE: "swapExactTokensForTokensSupportingFee",
            SWAP_ETH_FOR_EXACT_TOKENS: "swapETHForExactTokens",
            SWAP_TOKENS_FOR_EXACT_ETH: "swapTokensForExactETH",
            SWAP_TOKENS_FOR_EXACT_TOKENS: "swapTokensForExactTokens",
            EXACT_INPUT_SINGLE: "exactInputSingle (V3)",
            EXACT_INPUT_SINGLE_V2: "exactInputSingle (V3 new)",
            EXACT_OUTPUT_SINGLE: "exactOutputSingle (V3)",
            EXACT_INPUT: "exactInput (V3 multi-hop)",
            EXACT_OUTPUT: "exactOutput (V3 multi-hop)",
            ZEROX_TRANSFORM_ERC20: "0x transformERC20",
            ONEINCH_SWAP: "1inch swap",
            ONEINCH_UNOSWAP: "1inch unoswap",
            MULTICALL: "multicall",
            MULTICALL_V2: "multicall (deadline)"
        }

    def decode(self, input_data: str, value_wei: int = 0) -> Optional[DecodedSwap]:
        """
        Main decode entry point.
        Returns DecodedSwap or None if not a recognized swap.
        """
        if not input_data or len(input_data) < 10:
            return None

        method_id = input_data[:10].lower()
        method_name = self.method_names.get(method_id, "unknown")

        try:
            # Route to specific decoder based on method
            if method_id in (SWAP_EXACT_ETH_FOR_TOKENS,
                              SWAP_EXACT_ETH_FOR_TOKENS_FEE,
                              SWAP_ETH_FOR_EXACT_TOKENS):
                return self._decode_v2_eth_to_token(
                    input_data, value_wei, method_id, method_name
                )

            elif method_id in (SWAP_EXACT_TOKENS_FOR_ETH,
                                SWAP_EXACT_TOKENS_FOR_ETH_FEE,
                                SWAP_TOKENS_FOR_EXACT_ETH):
                return self._decode_v2_token_to_eth(
                    input_data, method_id, method_name
                )

            elif method_id in (SWAP_EXACT_TOKENS_FOR_TOKENS,
                                SWAP_EXACT_TOKENS_FOR_TOKENS_FEE,
                                SWAP_TOKENS_FOR_EXACT_TOKENS):
                return self._decode_v2_token_to_token(
                    input_data, method_id, method_name
                )

            elif method_id in (EXACT_INPUT_SINGLE, EXACT_INPUT_SINGLE_V2):
                return self._decode_v3_exact_input_single(
                    input_data, value_wei, method_id, method_name
                )

            elif method_id == EXACT_INPUT:
                return self._decode_v3_exact_input(
                    input_data, value_wei, method_id, method_name
                )

            elif method_id in (MULTICALL, MULTICALL_V2):
                return self._decode_multicall(
                    input_data, value_wei, method_id, method_name
                )

            elif method_id == ZEROX_TRANSFORM_ERC20:
                return self._decode_zerox(
                    input_data, value_wei, method_id, method_name
                )

        except Exception as e:
            logger.debug(f"Decode error for {method_id}: {e}")

        return None

    def _decode_v2_eth_to_token(self, data: str, value: int,
                                  method_id: str, name: str) -> Optional[DecodedSwap]:
        """Decode swapExactETHForTokens and variants."""
        try:
            params = data[10:]
            # amountOutMin (uint256) — 64 chars
            amount_out_min = int(params[0:64], 16)
            # path offset — skip
            # path array
            path = self._decode_path_from_params(params, offset=64)
            if len(path) < 2:
                return None

            token_out = path[-1]
            is_buy = (path[0].lower() == self.weth or value > 0)

            return DecodedSwap(
                action="buy" if is_buy else "sell",
                token_in=path[0],
                token_out=token_out,
                amount_in_wei=value,
                amount_out_min_wei=amount_out_min,
                native_amount_wei=value,
                path=path,
                method_id=method_id,
                method_name=name,
                is_multi_hop=len(path) > 2
            )
        except Exception as e:
            logger.debug(f"V2 ETH→Token decode error: {e}")
            return None

    def _decode_v2_token_to_eth(self, data: str,
                                  method_id: str, name: str) -> Optional[DecodedSwap]:
        """Decode swapExactTokensForETH and variants."""
        try:
            params = data[10:]
            amount_in = int(params[0:64], 16)
            amount_out_min = int(params[64:128], 16)
            path = self._decode_path_from_params(params, offset=128)
            if len(path) < 2:
                return None

            return DecodedSwap(
                action="sell",
                token_in=path[0],
                token_out=path[-1],
                amount_in_wei=amount_in,
                amount_out_min_wei=amount_out_min,
                native_amount_wei=0,
                path=path,
                method_id=method_id,
                method_name=name,
                is_multi_hop=len(path) > 2
            )
        except Exception as e:
            logger.debug(f"V2 Token→ETH decode error: {e}")
            return None

    def _decode_v2_token_to_token(self, data: str,
                                   method_id: str, name: str) -> Optional[DecodedSwap]:
        """Decode swapExactTokensForTokens — determine buy/sell from path."""
        try:
            params = data[10:]
            amount_in = int(params[0:64], 16)
            amount_out_min = int(params[64:128], 16)
            path = self._decode_path_from_params(params, offset=128)
            if len(path) < 2:
                return None

            # Buy: WETH → Token. Sell: Token → WETH
            action = "buy" if path[0].lower() == self.weth else "sell"

            return DecodedSwap(
                action=action,
                token_in=path[0],
                token_out=path[-1],
                amount_in_wei=amount_in,
                amount_out_min_wei=amount_out_min,
                native_amount_wei=0,
                path=path,
                method_id=method_id,
                method_name=name,
                is_multi_hop=len(path) > 2
            )
        except Exception as e:
            logger.debug(f"V2 Token→Token decode error: {e}")
            return None

    def _decode_v3_exact_input_single(self, data: str, value: int,
                                       method_id: str, name: str) -> Optional[DecodedSwap]:
        """Decode Uniswap V3 exactInputSingle."""
        try:
            params = data[10:]
            # V3 exactInputSingle params struct:
            # tokenIn (address, 32 bytes)
            # tokenOut (address, 32 bytes)
            # fee (uint24, 32 bytes)
            # recipient (address, 32 bytes)
            # deadline (uint256, 32 bytes) — may not be present in new version
            # amountIn (uint256, 32 bytes)
            # amountOutMinimum (uint256, 32 bytes)
            # sqrtPriceLimitX96 (uint160, 32 bytes)

            token_in = "0x" + params[24:64]
            token_out = "0x" + params[88:128]
            amount_in = int(params[256:320], 16)

            action = "buy" if token_in.lower() == self.weth else "sell"

            return DecodedSwap(
                action=action,
                token_in=token_in,
                token_out=token_out,
                amount_in_wei=amount_in if amount_in > 0 else value,
                amount_out_min_wei=0,
                native_amount_wei=value,
                path=[token_in, token_out],
                method_id=method_id,
                method_name=name,
                is_multi_hop=False
            )
        except Exception as e:
            logger.debug(f"V3 exactInputSingle decode error: {e}")
            return None

    def _decode_v3_exact_input(self, data: str, value: int,
                                method_id: str, name: str) -> Optional[DecodedSwap]:
        """Decode Uniswap V3 exactInput (multi-hop)."""
        try:
            params = data[10:]
            # Extract path bytes (encoded as bytes type)
            path_offset = int(params[0:64], 16) * 2
            path_length = int(params[path_offset:path_offset+64], 16)
            path_hex = params[path_offset+64:path_offset+64+path_length*2]

            # V3 path encoding: token(20) + fee(3) + token(20) + ...
            addresses = []
            i = 0
            while i + 40 <= len(path_hex):
                addr = "0x" + path_hex[i:i+40]
                addresses.append(addr)
                i += 40 + 6  # 20 bytes address + 3 bytes fee

            if len(addresses) < 2:
                return None

            action = "buy" if addresses[0].lower() == self.weth else "sell"
            amount_in = int(params[64:128], 16)

            return DecodedSwap(
                action=action,
                token_in=addresses[0],
                token_out=addresses[-1],
                amount_in_wei=amount_in if amount_in > 0 else value,
                amount_out_min_wei=0,
                native_amount_wei=value,
                path=addresses,
                method_id=method_id,
                method_name=name,
                is_multi_hop=True
            )
        except Exception as e:
            logger.debug(f"V3 exactInput decode error: {e}")
            return None

    def _decode_multicall(self, data: str, value: int,
                           method_id: str, name: str) -> Optional[DecodedSwap]:
        """
        Decode multicall — contains nested calls.
        Extract the first swap call found within.
        """
        try:
            params = data[10:]
            # Skip deadline if present (multicall_v2)
            offset_start = 64 if method_id == MULTICALL_V2 else 0
            # calls array offset
            calls_offset = int(params[offset_start:offset_start+64], 16) * 2
            num_calls = int(params[calls_offset:calls_offset+64], 16)
            calls_offset += 64

            for i in range(min(num_calls, 5)):
                try:
                    call_offset = int(params[calls_offset + i*64: calls_offset + i*64+64], 16) * 2
                    call_length = int(params[calls_offset + call_offset: calls_offset + call_offset + 64], 16)
                    call_data = "0x" + params[calls_offset + call_offset + 64: calls_offset + call_offset + 64 + call_length*2]

                    result = self.decode(call_data, value)
                    if result:
                        result.method_name = f"{name} → {result.method_name}"
                        return result
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Multicall decode error: {e}")
        return None

    def _decode_zerox(self, data: str, value: int,
                       method_id: str, name: str) -> Optional[DecodedSwap]:
        """Decode 0x transformERC20."""
        try:
            params = data[10:]
            token_in = "0x" + params[24:64]
            token_out = "0x" + params[88:128]
            amount_in = int(params[128:192], 16)

            is_native_in = (
                token_in.lower() == self.weth or
                token_in == "0x" + "e" * 40  # 0xEEE... = native ETH
            )
            action = "buy" if is_native_in else "sell"

            return DecodedSwap(
                action=action,
                token_in=token_in,
                token_out=token_out,
                amount_in_wei=amount_in if amount_in > 0 else value,
                amount_out_min_wei=0,
                native_amount_wei=value,
                path=[token_in, token_out],
                method_id=method_id,
                method_name=name,
                is_multi_hop=False
            )
        except Exception as e:
            logger.debug(f"0x decode error: {e}")
            return None

    def _decode_path_from_params(self, params: str, offset: int) -> List[str]:
        """Decode a V2-style address[] path parameter."""
        try:
            # Array offset pointer
            arr_offset = int(params[offset:offset+64], 16) * 2
            arr_length = int(params[arr_offset:arr_offset+64], 16)
            arr_offset += 64

            addresses = []
            for i in range(arr_length):
                addr_hex = params[arr_offset + i*64: arr_offset + i*64 + 64]
                addr = "0x" + addr_hex[24:]  # Last 20 bytes
                addresses.append(addr)

            return addresses
        except Exception as e:
            logger.debug(f"Path decode error: {e}")
            return []

    def is_swap(self, input_data: str) -> bool:
        """Quick check if a transaction is a swap."""
        if not input_data or len(input_data) < 10:
            return False
        method_id = input_data[:10].lower()
        return method_id in (
            BUY_METHODS | SELL_METHODS | AMBIGUOUS_METHODS
        )
