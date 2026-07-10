"""Unit tests for core.onchain_amm -- pure PumpSwap pool/vault decoders (task #493).

NO network. The pool-PDA derivation test pins values that were LIVE-VERIFIED
2026-07-10 (canonical pool PDA == DexScreener pairAddress, 3/3 tokens) so any
seed/program regression is caught deterministically.
"""

import struct

from solders.pubkey import Pubkey

from core.onchain_amm import (
    DEFAULT_BASE_DECIMALS,
    PUMP_AMM_PROGRAM_ID,
    WSOL_MINT,
    decode_mint_decimals,
    decode_pumpswap_pool,
    decode_token_account,
    price_sol_from_vaults,
    pumpswap_pool_pda,
)

# Live-verified fixtures (2026-07-10, scripts/validate_ws_migrated.py):
_ANSEM_MINT = "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump"
_ANSEM_POOL = "FnzKY6x7entQ1eR3D225dQyT7ybfka4PskBMQhb8L3CC"
_ANIF_MINT = "HcFUgXHEJWjZfDFvyFXDfVRkq5VTzJCfXNpJtcQ3pump"
_ANIF_POOL = "CLMWpDYwQBkbQ9prSUj3dBkxqXigf6jTrnfbR8vwrQ6e"


def _pk_bytes(seed_byte):
    """Deterministic 32-byte pubkey material."""
    return bytes([seed_byte] * 32)


def _pool_bytes(base_mint_b, quote_mint_b, base_vault_b, quote_vault_b):
    """Synthetic PumpSwap pool account at the live-verified offsets."""
    buf = bytearray(203)
    buf[43:75] = base_mint_b
    buf[75:107] = quote_mint_b
    buf[139:171] = base_vault_b
    buf[171:203] = quote_vault_b
    return bytes(buf)


def _token_account_bytes(mint_b, amount):
    """Synthetic SPL token account (mint@0, amount u64 LE @64)."""
    buf = bytearray(165)
    buf[0:32] = mint_b
    struct.pack_into("<Q", buf, 64, amount)
    return bytes(buf)


# --- pool PDA derivation ------------------------------------------------------

def test_pool_pda_matches_live_verified_values():
    assert pumpswap_pool_pda(_ANSEM_MINT) == _ANSEM_POOL
    assert pumpswap_pool_pda(_ANIF_MINT) == _ANIF_POOL


def test_pool_pda_deterministic():
    a = pumpswap_pool_pda(_ANSEM_MINT)
    b = pumpswap_pool_pda(_ANSEM_MINT)
    assert a == b and isinstance(a, str)


# --- pool account decode ------------------------------------------------------

def test_decode_pool_extracts_mints_and_vaults():
    bm, qm = _pk_bytes(1), bytes(Pubkey.from_string(WSOL_MINT))
    bv, qv = _pk_bytes(3), _pk_bytes(4)
    d = decode_pumpswap_pool(_pool_bytes(bm, qm, bv, qv))
    assert d is not None
    assert d["base_mint"] == str(Pubkey.from_bytes(bm))
    assert d["quote_mint"] == WSOL_MINT
    assert d["base_vault"] == str(Pubkey.from_bytes(bv))
    assert d["quote_vault"] == str(Pubkey.from_bytes(qv))


def test_decode_pool_bad_input():
    assert decode_pumpswap_pool(None) is None
    assert decode_pumpswap_pool(b"") is None
    assert decode_pumpswap_pool(b"\x00" * 202) is None   # one byte short
    assert decode_pumpswap_pool("not-bytes") is None


# --- token account decode -----------------------------------------------------

def test_decode_token_account():
    mb = _pk_bytes(9)
    d = decode_token_account(_token_account_bytes(mb, 12_849_662_961_937))
    assert d is not None
    assert d["mint"] == str(Pubkey.from_bytes(mb))
    assert d["amount"] == 12_849_662_961_937


def test_decode_token_account_min_len_boundary():
    # exactly 72 bytes (amount ends at 72) is decodable
    buf = bytearray(72)
    struct.pack_into("<Q", buf, 64, 5)
    assert decode_token_account(bytes(buf))["amount"] == 5
    assert decode_token_account(bytes(buf[:71])) is None


def test_decode_token_account_bad_input():
    assert decode_token_account(None) is None
    assert decode_token_account(b"short") is None


# --- mint decimals ------------------------------------------------------------

def test_decode_mint_decimals():
    buf = bytearray(82)
    buf[44] = 6
    assert decode_mint_decimals(bytes(buf)) == 6
    buf[44] = 9
    assert decode_mint_decimals(bytes(buf)) == 9


def test_decode_mint_decimals_bad_input():
    assert decode_mint_decimals(None) is None
    assert decode_mint_decimals(b"\x00" * 44) is None


# --- price math ----------------------------------------------------------------

def test_price_sol_from_vaults_live_verified_math():
    # ANSEM live snapshot: quote=12_849_662_961_937 lamports, base=4_743_833_075_294
    # (6 dec) -> 0.00270870891... SOL (DexScreener priceNative 0.002704, +0.17%)
    p = price_sol_from_vaults(4_743_833_075_294, 12_849_662_961_937, 6)
    assert abs(p - 0.0027087089191351107) < 1e-15


def test_price_sol_from_vaults_default_decimals():
    assert DEFAULT_BASE_DECIMALS == 6
    p6 = price_sol_from_vaults(1_000_000, 2_000_000_000)
    # 1 token (6 dec) vs 2 SOL -> 2 SOL/token
    assert abs(p6 - 2.0) < 1e-12


def test_price_sol_from_vaults_guards():
    assert price_sol_from_vaults(0, 1_000) is None
    assert price_sol_from_vaults(1_000, 0) is None
    assert price_sol_from_vaults(None, 1_000) is None
    assert price_sol_from_vaults(1_000, None) is None
    assert price_sol_from_vaults(-5, 1_000) is None


def test_program_id_constant():
    # guard against typo'd program id (would silently break owner checks)
    assert PUMP_AMM_PROGRAM_ID == "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
