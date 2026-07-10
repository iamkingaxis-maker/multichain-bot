"""Unit tests for the migrated-token AMM vault coverage in OnchainWsFeed
(task #493, ONCHAIN_WS_MIGRATED_MODE=off/shadow/enforce).

PURE sync logic only -- NO network, NO sockets. Mirrors test_onchain_ws_feed.py
patterns: synthetic account bytes, env via monkeypatch, exception-safety.
"""

import base64
import math
import struct

from solders.pubkey import Pubkey

from core.onchain_amm import WSOL_MINT
from core.onchain_ws_feed import OnchainWsFeed


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _feed(sol_usd=150.0):
    return OnchainWsFeed(get_sol_usd=lambda: sol_usd)


def _migrated_curve_bytes():
    """pump.fun curve with complete=True + vtr=0 -> kind='migrated'."""
    disc = b"\x00" * 8
    body = struct.pack("<QQQQQ", 0, 0, 0, 0, 1_000_000_000_000)
    return disc + body + b"\x01"


def _token_account_bytes(mint_str, amount):
    buf = bytearray(165)
    buf[0:32] = bytes(Pubkey.from_string(mint_str))
    struct.pack_into("<Q", buf, 64, amount)
    return bytes(buf)


_MINT = "9cRCn9rGT8V2imeM2BaKs13yhMEais3ruM3rPvTGpump"
_POOL = "FnzKY6x7entQ1eR3D225dQyT7ybfka4PskBMQhb8L3CC"
_BASE_VAULT = "BmCXK8QFCHgjiqGm7peAtBbZpFPJNsp5fYP5rSRazMS8"
_QUOTE_VAULT = "DaXhQ3pfN3J5dQnXxVU8YqW9bwA3RUVxXvq2iBjTDVt4"


def _pool_dict(base_mint=_MINT, quote_mint=WSOL_MINT,
               base_vault=_BASE_VAULT, quote_vault=_QUOTE_VAULT):
    return {"base_mint": base_mint, "quote_mint": quote_mint,
            "base_vault": base_vault, "quote_vault": quote_vault}


def _registered_feed(monkeypatch, mode="shadow", sol_usd=150.0,
                     base_amount=None, quote_amount=None):
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", mode)
    feed = _feed(sol_usd=sol_usd)
    assert feed._register_amm_pool(_MINT, _POOL, _pool_dict(),
                                   base_decimals=6,
                                   base_amount=base_amount,
                                   quote_amount=quote_amount)
    return feed


# --- mode parsing -------------------------------------------------------------

def test_migrated_mode_default_off(monkeypatch):
    monkeypatch.delenv("ONCHAIN_WS_MIGRATED_MODE", raising=False)
    assert OnchainWsFeed._migrated_mode() == "off"


def test_migrated_mode_values(monkeypatch):
    for v, want in (("shadow", "shadow"), ("ENFORCE", "enforce"),
                    (" off ", "off"), ("bogus", "off"), ("on", "off")):
        monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", v)
        assert OnchainWsFeed._migrated_mode() == want


# --- migrated detection -> pending queue --------------------------------------

def test_mode_off_migrated_only_counts(monkeypatch):
    monkeypatch.delenv("ONCHAIN_WS_MIGRATED_MODE", raising=False)
    feed = _feed()
    feed._handle_account_data(_MINT, _b64(_migrated_curve_bytes()))
    assert feed.migrated_skips == 1
    assert feed._amm_pending == {}          # off: pre-#493 behavior exactly


def test_shadow_migrated_push_queues_mint(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    feed = _feed()
    feed._handle_account_data(_MINT, _b64(_migrated_curve_bytes()))
    assert feed.migrated_skips == 1
    # queued with ORIGINAL case preserved (RPC needs case-sensitive base58)
    assert feed._amm_pending == {_MINT.lower(): _MINT}
    assert _MINT.lower() in feed._amm_checked


def test_note_migrated_skips_resolved_and_unsupported(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    feed = _feed()
    feed._amm_pools[_MINT.lower()] = {"mint": _MINT}
    feed._note_migrated(_MINT)
    assert feed._amm_pending == {}
    feed2 = _feed()
    feed2._amm_unsupported.add(_MINT.lower())
    feed2._note_migrated(_MINT)
    assert feed2._amm_pending == {}


# --- pool registration ----------------------------------------------------------

def test_register_amm_pool_routes_vaults(monkeypatch):
    feed = _registered_feed(monkeypatch)
    ml = _MINT.lower()
    assert feed._amm_pools[ml]["base_vault"] == _BASE_VAULT
    assert feed._amm_pools[ml]["base_decimals"] == 6
    assert feed._amm_vault_route[_BASE_VAULT] == (ml, "base")
    assert feed._amm_vault_route[_QUOTE_VAULT] == (ml, "quote")
    assert ml not in feed._amm_pending


def test_register_amm_pool_rejects_non_wsol_quote(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    feed = _feed()
    ok = feed._register_amm_pool(
        _MINT, _POOL, _pool_dict(quote_mint=_MINT), base_decimals=6)
    assert ok is False
    assert _MINT.lower() in feed._amm_unsupported
    assert feed._amm_pools == {}


def test_register_amm_pool_rejects_base_mint_mismatch(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    feed = _feed()
    ok = feed._register_amm_pool(
        _MINT, _POOL, _pool_dict(base_mint=WSOL_MINT), base_decimals=6)
    assert ok is False
    assert _MINT.lower() in feed._amm_unsupported


def test_register_amm_pool_bad_decimals_falls_back_to_6(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    feed = _feed()
    assert feed._register_amm_pool(_MINT, _POOL, _pool_dict(),
                                   base_decimals=None)
    assert feed._amm_pools[_MINT.lower()]["base_decimals"] == 6


# --- vault push -> price ---------------------------------------------------------

def test_vault_pushes_compute_price(monkeypatch):
    sol_usd = 150.0
    feed = _registered_feed(monkeypatch, sol_usd=sol_usd)
    # ANSEM live snapshot (2026-07-10): price_sol = 0.0027087089...
    base_amt, quote_amt = 4_743_833_075_294, 12_849_662_961_937
    feed._handle_amm_vault_data(
        _BASE_VAULT, _b64(_token_account_bytes(_MINT, base_amt)))
    assert feed.get_amm_price(_MINT) is None       # one leg only: no price yet
    feed._handle_amm_vault_data(
        _QUOTE_VAULT, _b64(_token_account_bytes(WSOL_MINT, quote_amt)))
    got = feed.get_amm_price(_MINT)
    assert got is not None
    usd, ts = got
    expected = ((quote_amt / 1e9) / (base_amt / 1e6)) * sol_usd
    assert math.isclose(usd, expected, rel_tol=1e-12)
    assert ts > 0
    assert feed.amm_prices == 1


def test_seeded_balances_yield_price_after_single_push(monkeypatch):
    # Resolver seeds both balances at registration -> the FIRST WS push
    # (either vault) refreshes a complete price.
    feed = _registered_feed(monkeypatch, base_amount=4_743_833_075_294,
                            quote_amount=12_849_662_961_937)
    assert feed.get_amm_price(_MINT) is not None   # seeded price at registration
    before = feed.amm_prices
    feed._handle_amm_vault_data(
        _QUOTE_VAULT, _b64(_token_account_bytes(WSOL_MINT, 13_000_000_000_000)))
    assert feed.amm_prices == before + 1


def test_vault_push_sol_gate_zero_writes_nothing(monkeypatch):
    feed = _registered_feed(monkeypatch, sol_usd=0.0,
                            base_amount=1_000_000, quote_amount=1_000_000_000)
    assert feed.amm_price_cache == {}


def test_vault_push_malformed_never_raises(monkeypatch):
    feed = _registered_feed(monkeypatch)
    feed._handle_amm_vault_data(_BASE_VAULT, "!!!not-base64!!!")
    feed._handle_amm_vault_data(_BASE_VAULT, _b64(b"short"))
    feed._handle_amm_vault_data(_BASE_VAULT, None)
    feed._handle_amm_vault_data("UnknownVault", _b64(b"\x00" * 165))
    assert feed.amm_price_cache == {}


# --- get_price serving by mode ---------------------------------------------------

def _feed_with_amm_price(monkeypatch, mode):
    feed = _registered_feed(monkeypatch, mode=mode,
                            base_amount=4_743_833_075_294,
                            quote_amount=12_849_662_961_937)
    assert feed.amm_price_cache      # sanity: price computed
    return feed


def test_get_price_shadow_never_serves_amm(monkeypatch):
    feed = _feed_with_amm_price(monkeypatch, "shadow")
    assert feed.get_price(_MINT) is None
    assert feed.get_amm_price(_MINT) is not None   # shadow read path works


def test_get_price_enforce_serves_amm(monkeypatch):
    feed = _feed_with_amm_price(monkeypatch, "enforce")
    got = feed.get_price(_MINT)
    assert got is not None
    usd, ts = got
    assert usd == feed.amm_price_cache[_MINT.lower()]
    assert ts == feed.amm_ts[_MINT.lower()]


def test_get_price_enforce_curve_price_wins(monkeypatch):
    feed = _feed_with_amm_price(monkeypatch, "enforce")
    feed.price_cache[_MINT.lower()] = 42.0
    feed.ts[_MINT.lower()] = 123.0
    assert feed.get_price(_MINT) == (42.0, 123.0)


def test_get_price_off_ignores_amm_cache(monkeypatch):
    feed = _registered_feed(monkeypatch, mode="shadow",
                            base_amount=1_000_000, quote_amount=1_000_000_000)
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "off")
    assert feed.get_price(_MINT) is None


# --- refresh pruning --------------------------------------------------------------

def test_apply_refresh_prunes_amm_state_for_dropped(monkeypatch):
    feed = _feed_with_amm_price(monkeypatch, "shadow")
    ml = _MINT.lower()
    feed._tracked = {ml, "keepme"}
    feed._apply_refresh(["KeepMe"])
    assert ml not in feed._amm_pools
    assert ml not in feed.amm_price_cache
    assert ml not in feed.amm_ts
    assert ml not in feed._amm_checked
    assert _BASE_VAULT not in feed._amm_vault_route
    assert _QUOTE_VAULT not in feed._amm_vault_route


# --- connection planning -----------------------------------------------------------

def test_amm_desired_vault_chunks(monkeypatch):
    feed = _registered_feed(monkeypatch)
    chunks = feed._amm_desired_vault_chunks()
    assert len(chunks) == 1
    assert sorted(chunks[0]) == sorted([_BASE_VAULT, _QUOTE_VAULT])
    # empty routing -> no chunks
    feed2 = _feed()
    assert feed2._amm_desired_vault_chunks() == []


# --- seed-triple decode -------------------------------------------------------------

def test_decode_amm_seed():
    def _acct(raw):
        return {"data": [_b64(raw), "base64"]}

    mint_acct = bytearray(82)
    mint_acct[44] = 6
    triple = [
        _acct(_token_account_bytes(_MINT, 111)),
        _acct(_token_account_bytes(WSOL_MINT, 222)),
        _acct(bytes(mint_acct)),
    ]
    assert OnchainWsFeed._decode_amm_seed(triple) == (111, 222, 6)
    # missing legs -> Nones, never raises
    assert OnchainWsFeed._decode_amm_seed([None, None, None]) == (None, None, None)
    assert OnchainWsFeed._decode_amm_seed([]) == (None, None, None)


# --- heartbeat observability ---------------------------------------------------------

def test_heartbeat_line_includes_migrated_fields(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MIGRATED_MODE", "shadow")
    feed = _registered_feed(monkeypatch, base_amount=1_000_000,
                            quote_amount=1_000_000_000)
    line = feed._heartbeat_line()
    assert "mig_mode=shadow" in line
    assert "amm_pools=1" in line
    assert "amm_cached=1" in line
