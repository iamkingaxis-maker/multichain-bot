"""Live validation for task #493 (migrated-token AMM vault coverage).

Runs the FULL resolution chain the WS feed uses -- through the SHIPPED
decoders in core.onchain_amm / core.onchain_price -- against the free public
Solana RPC, on real currently-migrated pump.fun tokens found via DexScreener:

    mint -> bonding curve (expect kind='migrated')
         -> canonical PumpSwap pool PDA (expect == DexScreener pairAddress,
            owner == pAMMBay...)
         -> pool decode (base/quote mint + vault pubkeys at verified offsets)
         -> vault decode (amount u64 LE @64) + base mint decimals (u8 @44)
         -> price_sol = (quote/1e9) / (base/10**decimals)
         -> compare vs DexScreener priceNative (PASS if within 2%)

READ-ONLY public RPC + DexScreener; no keys, no secrets, no state mutation.

Usage:  python scripts/validate_ws_migrated.py [N_TOKENS]
"""

import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from core.onchain_amm import (
    PUMP_AMM_PROGRAM_ID,
    WSOL_MINT,
    decode_mint_decimals,
    decode_pumpswap_pool,
    decode_token_account,
    price_sol_from_vaults,
    pumpswap_pool_pda,
)
from core.onchain_price import bonding_curve_pda, resolve_price_account

RPC = os.environ.get("SOLANA_RPC_URL") or "https://api.mainnet-beta.solana.com"
DS_SEARCH = "https://api.dexscreener.com/latest/dex/search?q=pumpswap"
TOLERANCE_PCT = 2.0


def _rpc(method, params):
    r = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                 "params": params}, timeout=15)
    r.raise_for_status()
    return r.json().get("result")


def _get_account(addr):
    """(raw_bytes, owner_str) or (None, None)."""
    res = _rpc("getAccountInfo", [addr, {"encoding": "base64",
                                         "commitment": "confirmed"}])
    v = (res or {}).get("value")
    if not v:
        return None, None
    return base64.b64decode(v["data"][0]), v.get("owner")


def _find_migrated_candidates():
    """Currently-migrated pump.fun tokens (PumpSwap, WSOL quote) from DexScreener."""
    r = requests.get(DS_SEARCH, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out = []
    for p in (r.json().get("pairs") or []):
        if (p.get("dexId") == "pumpswap" and p.get("chainId") == "solana"
                and (p.get("quoteToken") or {}).get("address") == WSOL_MINT
                and (p.get("baseToken") or {}).get("address", "").endswith("pump")):
            out.append({
                "symbol": (p.get("baseToken") or {}).get("symbol"),
                "mint": p["baseToken"]["address"],
                "pair": p["pairAddress"],
                "price_native": float(p.get("priceNative") or 0),
            })
    return out


def validate_one(cand):
    mint, ds_pair = cand["mint"], cand["pair"]
    print(f"\n=== {cand['symbol']} mint={mint}")

    # 1. bonding curve must classify as migrated
    curve_raw, _ = _get_account(bonding_curve_pda(mint))
    kind = resolve_price_account(mint, curve_raw)["kind"]
    print(f"  curve kind          = {kind} (expect migrated)")
    if kind != "migrated":
        return False

    # 2. canonical pool PDA == DexScreener pairAddress
    pool_addr = pumpswap_pool_pda(mint)
    print(f"  canonical pool PDA  = {pool_addr}")
    print(f"  dexscreener pair    = {ds_pair}  match={pool_addr == ds_pair}")
    if pool_addr != ds_pair:
        return False

    # 3. pool account: owner + decode at verified offsets
    pool_raw, owner = _get_account(pool_addr)
    print(f"  pool owner          = {owner} (expect {PUMP_AMM_PROGRAM_ID})")
    if owner != PUMP_AMM_PROGRAM_ID:
        return False
    pool = decode_pumpswap_pool(pool_raw)
    ok_mints = (pool and pool["base_mint"] == mint
                and pool["quote_mint"] == WSOL_MINT)
    print(f"  base_mint@43 ok     = {pool and pool['base_mint'] == mint}")
    print(f"  quote_mint@75 ok    = {pool and pool['quote_mint'] == WSOL_MINT}")
    print(f"  base_vault@139      = {pool and pool['base_vault']}")
    print(f"  quote_vault@171     = {pool and pool['quote_vault']}")
    if not ok_mints:
        return False

    # 4. vault balances + base mint decimals
    braw, _ = _get_account(pool["base_vault"])
    qraw, _ = _get_account(pool["quote_vault"])
    mraw, _ = _get_account(mint)
    btok, qtok = decode_token_account(braw), decode_token_account(qraw)
    dec = decode_mint_decimals(mraw)
    if not btok or not qtok or dec is None:
        print("  vault/mint decode FAILED")
        return False
    ok_vault_mints = (btok["mint"] == mint and qtok["mint"] == WSOL_MINT)
    print(f"  vault mint fields ok= {ok_vault_mints}")
    print(f"  base_amount         = {btok['amount']} (decimals={dec})")
    print(f"  quote_amount        = {qtok['amount']} lamports")
    if not ok_vault_mints:
        return False

    # 5. price vs DexScreener priceNative
    price_sol = price_sol_from_vaults(btok["amount"], qtok["amount"], dec)
    ds = cand["price_native"]
    diff_pct = (price_sol - ds) / ds * 100.0 if ds else float("nan")
    verdict = abs(diff_pct) <= TOLERANCE_PCT
    print(f"  computed price_sol  = {price_sol:.10g}")
    print(f"  ds priceNative      = {ds:.10g}")
    print(f"  diff                = {diff_pct:+.3f}%  "
          f"({'PASS' if verdict else 'FAIL'} @ {TOLERANCE_PCT}% tolerance)")
    return verdict


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    cands = _find_migrated_candidates()
    print(f"DexScreener migrated pump.fun candidates: {len(cands)} "
          f"(validating first {n})")
    if not cands:
        print("NO CANDIDATES -- DexScreener search returned nothing usable")
        return 1
    results = [validate_one(c) for c in cands[:n]]
    passed = sum(results)
    print(f"\nRESULT: {passed}/{len(results)} tokens PASSED")
    return 0 if passed == len(results) and results else 1


if __name__ == "__main__":
    sys.exit(main())
