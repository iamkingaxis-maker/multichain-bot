"""
Chain Configuration
Defines blockchain-specific settings for Solana.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChainConfig:
    name: str                        # Display name
    chain_id: str                    # Internal ID
    native_token: str                # e.g. SOL, ETH, BNB
    native_token_coingecko_id: str  # For price lookups
    rpc_url: str                     # RPC endpoint
    dexscreener_chain: str          # DexScreener chain slug
    router_address: str             # DEX router for swaps
    weth_address: str               # Wrapped native token address
    usdc_address: str               # USDC address on this chain
    block_explorer: str             # For TX links
    tx_fee_usd_estimate: float      # Estimated gas cost per trade
    min_liquidity_usd: float        # Min liquidity to trade safely
    is_evm: bool = True             # EVM vs Solana
    swap_api: str = ""              # Swap aggregator API


# ─── SOLANA ────────────────────────────────────────────────────────────────────
SOLANA = ChainConfig(
    name="Solana",
    chain_id="solana",
    native_token="SOL",
    native_token_coingecko_id="solana",
    rpc_url="https://mainnet.helius-rpc.com/?api-key=YOUR_HELIUS_API_KEY",
    dexscreener_chain="solana",
    router_address="",  # Jupiter handles routing
    weth_address="So11111111111111111111111111111111111111112",
    usdc_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    block_explorer="https://solscan.io/tx",
    tx_fee_usd_estimate=0.001,
    min_liquidity_usd=10_000,
    is_evm=False,
    swap_api="https://quote-api.jup.ag/v6"
)

# All supported chains
ALL_CHAINS = {
    "solana": SOLANA,
}

def get_chain(chain_id: str) -> Optional[ChainConfig]:
    return ALL_CHAINS.get(chain_id.lower())
