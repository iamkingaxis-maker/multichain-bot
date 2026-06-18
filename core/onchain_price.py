"""Pure pump.fun bonding-curve decoder + price math.

Foundation of the on-chain ~1-2s price feed (Part B of the free-realtime-price-feeds
plan). DELIBERATELY dependency-light: struct decode + price math + PDA derivation.
NO network, NO asyncio -- trivially unit-testable.

MEASURED facts (free-feed bakeoff RPC probe):
- pump.fun price lives in the bonding-curve PDA:
  seeds = [b"bonding-curve", mint_pubkey_bytes]
  program = 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P
- Account layout: 8-byte discriminator + 5 u64 LE
  (virtual_token_reserves, virtual_sol_reserves, real_token_reserves,
   real_sol_reserves, token_total_supply) + 1 bool (complete).
- price_sol = (virtual_sol_reserves / 1e9) / (virtual_token_reserves / 1e6)
- complete == 1 AND virtual_token_reserves == 0  => MIGRATED (curve dead) => price None.
"""

import struct

from solders.pubkey import Pubkey

# pump.fun bonding-curve program
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_PROGRAM = Pubkey.from_string(PUMP_PROGRAM_ID)

# 8-byte discriminator + 5 u64 LE = 48 bytes of body; bool flag at offset 48.
_DISCRIMINATOR_LEN = 8
_BODY_FMT = "<QQQQQ"
_BODY_LEN = struct.calcsize(_BODY_FMT)  # 40
_COMPLETE_OFFSET = _DISCRIMINATOR_LEN + _BODY_LEN  # 48
_MIN_LEN = _COMPLETE_OFFSET + 1  # 49


def decode_bonding_curve(account_bytes):
    """Decode a pump.fun bonding-curve account.

    Returns a dict with the 5 u64 reserve fields + ``complete`` bool, or None
    if ``account_bytes`` is too short / not bytes-like.
    """
    if account_bytes is None:
        return None
    try:
        if len(account_bytes) < _MIN_LEN:
            return None
        (
            virtual_token_reserves,
            virtual_sol_reserves,
            real_token_reserves,
            real_sol_reserves,
            token_total_supply,
        ) = struct.unpack_from(_BODY_FMT, account_bytes, _DISCRIMINATOR_LEN)
        complete = bool(account_bytes[_COMPLETE_OFFSET])
    except (TypeError, struct.error):
        return None
    return {
        "virtual_token_reserves": virtual_token_reserves,
        "virtual_sol_reserves": virtual_sol_reserves,
        "real_token_reserves": real_token_reserves,
        "real_sol_reserves": real_sol_reserves,
        "token_total_supply": token_total_supply,
        "complete": complete,
    }


def price_sol_from_curve(decoded):
    """Price in SOL from a decoded bonding curve, or None.

    None when:
      - decoded is falsy
      - MIGRATED: complete is True AND virtual_token_reserves == 0
      - any needed reserve is missing or 0 (would divide by zero / no price)
    """
    if not decoded:
        return None

    vtr = decoded.get("virtual_token_reserves")
    vsr = decoded.get("virtual_sol_reserves")
    complete = bool(decoded.get("complete"))

    # Migrated => bonding curve dead.
    if complete and (vtr == 0 or vtr is None):
        return None

    if not vtr or not vsr:
        return None

    return (vsr / 1e9) / (vtr / 1e6)


def bonding_curve_pda(mint_str):
    """Derive the bonding-curve PDA address (str) for a pump.fun mint."""
    mint = Pubkey.from_string(mint_str)
    pda, _bump = Pubkey.find_program_address(
        [b"bonding-curve", bytes(mint)],
        PUMP_PROGRAM,
    )
    return str(pda)
