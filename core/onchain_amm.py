"""Pure PumpSwap AMM pool + SPL vault decoders (migrated pump.fun tokens).

Task #493: tokens that MIGRATE off the pump.fun bonding curve move to the
PumpSwap AMM. Their price then lives in the pool's two token VAULTS:
    price_sol = (quote_vault_amount / 1e9) / (base_vault_amount / 10**base_decimals)

DELIBERATELY dependency-light like core.onchain_price: struct decode + price
math + PDA derivation. NO network, NO asyncio -- trivially unit-testable.
The network/resolution/subscription side lives in core.onchain_ws_feed.

VERIFIED LIVE 2026-07-10 against the FREE public RPC
(https://api.mainnet-beta.solana.com) via scripts/validate_ws_migrated.py on
three currently-migrated pump.fun tokens found through DexScreener
(ANSEM 9cRC..pump, ANIF HcFU..pump, ARROW G6cH..pump):
  - canonical pool PDA derivation matched the DexScreener pairAddress 3/3:
      pool_authority = find_program_address([b"pool-authority", mint],
                                            PUMP_PROGRAM 6EF8rre...)
      pool = find_program_address([b"pool", u16le(0), pool_authority,
                                   base_mint, WSOL], PUMP_AMM_PROGRAM pAMMBay...)
  - pool account (owner pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA, 301 bytes):
      [0-7]     discriminator
      [8]       pool_bump (u8)
      [9-10]    index (u16 LE)
      [11-42]   creator pubkey
      [43-74]   base_mint pubkey        <- byte-scan for the mint found offset 43
      [75-106]  quote_mint pubkey       <- byte-scan for WSOL found offset 75
      [107-138] lp_mint pubkey
      [139-170] pool_base_token_account  (base vault)
      [171-202] pool_quote_token_account (quote vault; verified == ATA(pool, WSOL))
  - vault accounts: mint pubkey @0, amount u64 LE @64. NOTE: the base vault is
    a Token-2022 account (owner TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb) --
    the base token-account layout (mint@0/owner@32/amount@64) is IDENTICAL, so
    the same decode works; the quote vault is classic SPL (Tokenkeg...).
  - base mint decimals u8 @44 of the mint account = 6 on all three.
  - computed price_sol vs DexScreener priceNative: +0.17% / -1.64% / +0.93%.
"""

import struct

from solders.pubkey import Pubkey

from core.onchain_price import PUMP_PROGRAM

# PumpSwap AMM (post-graduation pump.fun pools)
PUMP_AMM_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMP_AMM_PROGRAM = Pubkey.from_string(PUMP_AMM_PROGRAM_ID)

WSOL_MINT = "So11111111111111111111111111111111111111112"
_WSOL_PUBKEY = Pubkey.from_string(WSOL_MINT)

# Pool account offsets -- LIVE-VERIFIED, see module docstring.
_POOL_BASE_MINT_OFFSET = 43
_POOL_QUOTE_MINT_OFFSET = 75
_POOL_BASE_VAULT_OFFSET = 139
_POOL_QUOTE_VAULT_OFFSET = 171
_POOL_MIN_LEN = _POOL_QUOTE_VAULT_OFFSET + 32  # 203

# SPL token-account offsets (classic SPL and Token-2022 share this prefix).
_TOKEN_ACCT_MINT_OFFSET = 0
_TOKEN_ACCT_AMOUNT_OFFSET = 64
_TOKEN_ACCT_MIN_LEN = _TOKEN_ACCT_AMOUNT_OFFSET + 8  # 72

# SPL mint account: decimals u8 @44 (after mint_authority COption(4+32) + supply u64).
_MINT_DECIMALS_OFFSET = 44
_MINT_MIN_LEN = _MINT_DECIMALS_OFFSET + 1  # 45

# pump.fun tokens are 6 decimals (live-verified); fallback when mint fetch fails.
DEFAULT_BASE_DECIMALS = 6


def pumpswap_pool_pda(mint_str):
    """Derive the CANONICAL PumpSwap pool address (str) for a migrated
    pump.fun mint (quote = WSOL, index 0). Raises on an invalid mint string;
    callers on the feed path catch (fail-open)."""
    mint = Pubkey.from_string(mint_str)
    pool_authority, _bump = Pubkey.find_program_address(
        [b"pool-authority", bytes(mint)],
        PUMP_PROGRAM,
    )
    pool, _bump = Pubkey.find_program_address(
        [
            b"pool",
            (0).to_bytes(2, "little"),
            bytes(pool_authority),
            bytes(mint),
            bytes(_WSOL_PUBKEY),
        ],
        PUMP_AMM_PROGRAM,
    )
    return str(pool)


def _pubkey_at(account_bytes, offset):
    """Base58-encode the 32 bytes at `offset` (str)."""
    return str(Pubkey.from_bytes(bytes(account_bytes[offset:offset + 32])))


def decode_pumpswap_pool(account_bytes):
    """Decode a PumpSwap pool account.

    Returns {base_mint, quote_mint, base_vault, quote_vault} (base58 strs) or
    None if the buffer is missing/too short/undecodable. Pure; never raises.
    """
    if account_bytes is None:
        return None
    try:
        if len(account_bytes) < _POOL_MIN_LEN:
            return None
        return {
            "base_mint": _pubkey_at(account_bytes, _POOL_BASE_MINT_OFFSET),
            "quote_mint": _pubkey_at(account_bytes, _POOL_QUOTE_MINT_OFFSET),
            "base_vault": _pubkey_at(account_bytes, _POOL_BASE_VAULT_OFFSET),
            "quote_vault": _pubkey_at(account_bytes, _POOL_QUOTE_VAULT_OFFSET),
        }
    except Exception:
        return None


def decode_token_account(account_bytes):
    """Decode an SPL / Token-2022 token account's {mint, amount}.

    amount is the raw u64 balance (int). Returns None on missing/short/bad
    input. Pure; never raises.
    """
    if account_bytes is None:
        return None
    try:
        if len(account_bytes) < _TOKEN_ACCT_MIN_LEN:
            return None
        mint = _pubkey_at(account_bytes, _TOKEN_ACCT_MINT_OFFSET)
        (amount,) = struct.unpack_from(
            "<Q", account_bytes, _TOKEN_ACCT_AMOUNT_OFFSET)
        return {"mint": mint, "amount": amount}
    except Exception:
        return None


def decode_mint_decimals(account_bytes):
    """Decimals (int) from a raw SPL mint account, or None. Pure; never raises."""
    if account_bytes is None:
        return None
    try:
        if len(account_bytes) < _MINT_MIN_LEN:
            return None
        return int(account_bytes[_MINT_DECIMALS_OFFSET])
    except Exception:
        return None


def price_sol_from_vaults(base_amount, quote_amount,
                          base_decimals=DEFAULT_BASE_DECIMALS):
    """Pool spot price in SOL from raw vault balances, or None.

    price_sol = (quote/1e9) / (base/10**base_decimals). None when either
    balance is missing/non-positive (empty pool, partial state). Pure.
    """
    try:
        if not base_amount or not quote_amount:
            return None
        if base_amount <= 0 or quote_amount <= 0:
            return None
        dec = int(base_decimals) if base_decimals is not None else DEFAULT_BASE_DECIMALS
        return (quote_amount / 1e9) / (base_amount / (10 ** dec))
    except Exception:
        return None
