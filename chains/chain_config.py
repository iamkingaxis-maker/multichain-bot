"""
Chain Configuration
Defines all blockchain-specific settings for Solana, Base, and BNB Chain.
Add new chains here without touching the rest of the bot.
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

# ─── BASE ──────────────────────────────────────────────────────────────────────
BASE = ChainConfig(
    name="Base",
    chain_id="base",
    native_token="ETH",
    native_token_coingecko_id="ethereum",
    rpc_url="https://mainnet.base.org",
    dexscreener_chain="base",
    router_address="0x2626664c2603336E57B271c5C0b26F421741e481",  # Uniswap V3 on Base
    weth_address="0x4200000000000000000000000000000000000006",
    usdc_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    block_explorer="https://basescan.org/tx",
    tx_fee_usd_estimate=0.01,
    min_liquidity_usd=15_000,
    is_evm=True,
    swap_api="https://api.0x.org/swap/v1"
)

# ─── BNB CHAIN ─────────────────────────────────────────────────────────────────
BNB = ChainConfig(
    name="BNB Chain",
    chain_id="bsc",
    native_token="BNB",
    native_token_coingecko_id="binancecoin",
    rpc_url="https://bsc-dataseed1.binance.org",
    dexscreener_chain="bsc",
    router_address="0x10ED43C718714eb63d5aA57B78B54704E256024E",  # PancakeSwap V2
    weth_address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    usdc_address="0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    block_explorer="https://bscscan.com/tx",
    tx_fee_usd_estimate=0.05,
    min_liquidity_usd=20_000,
    is_evm=True,
    swap_api="https://api.0x.org/swap/v1"
)

# All supported chains
ALL_CHAINS = {
    "solana": SOLANA,
    "base": BASE,
    "bnb": BNB,
}

def get_chain(chain_id: str) -> Optional[ChainConfig]:
    return ALL_CHAINS.get(chain_id.lower())
