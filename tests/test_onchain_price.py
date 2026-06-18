import struct

from core.onchain_price import (
    decode_bonding_curve,
    bonding_curve_pda,
    price_sol_from_curve,
    resolve_price_account,
)


def _curve_bytes(vtr, vsr, complete):
    disc = b"\x00" * 8
    body = struct.pack("<QQQQQ", vtr, vsr, 800_000_000_000, 5_000_000_000, 1_000_000_000_000)
    flag = b"\x01" if complete else b"\x00"
    return disc + body + flag


_MINT = "9h66V2NiHU3PpviwceSg4KZ7xqStLTDej58o5pdhpump"


def test_decode_struct():
    import struct
    disc = b"\x00"*8
    body = struct.pack("<QQQQQ", 1_000_000_000_000, 30_000_000_000, 800_000_000_000, 5_000_000_000, 1_000_000_000_000)
    flag = b"\x01"
    acct = disc + body + flag
    d = decode_bonding_curve(acct)
    assert d["virtual_token_reserves"] == 1_000_000_000_000
    assert d["virtual_sol_reserves"] == 30_000_000_000
    assert d["complete"] is True


def test_price_sol():
    # price_sol = (vSOL/1e9) / (vTOK/1e6)
    d = {"virtual_sol_reserves": 30_000_000_000, "virtual_token_reserves": 1_000_000_000_000_000, "complete": False}
    p = price_sol_from_curve(d)
    assert abs(p - ((30_000_000_000/1e9)/(1_000_000_000_000_000/1e6))) < 1e-18


def test_migrated_returns_none():
    d = {"virtual_token_reserves": 0, "complete": True, "virtual_sol_reserves": 0}
    assert price_sol_from_curve(d) is None   # migrated -> bonding curve dead


def test_pda_is_deterministic():
    a = bonding_curve_pda("9h66V2NiHU3PpviwceSg4KZ7xqStLTDej58o5pdhpump")
    b = bonding_curve_pda("9h66V2NiHU3PpviwceSg4KZ7xqStLTDej58o5pdhpump")
    assert a == b and isinstance(a, str)


def test_resolve_bonding_live():
    # LIVE curve: complete=False, vtr>0 -> kind='bonding', usable decoded + price.
    acct = _curve_bytes(vtr=1_000_000_000_000_000, vsr=30_000_000_000, complete=False)
    res = resolve_price_account(_MINT, acct)
    assert res["kind"] == "bonding"
    assert res["decoded"] is not None
    assert res["price_sol"] is not None
    # price_sol matches the pure curve math.
    assert res["price_sol"] == price_sol_from_curve(res["decoded"])
    assert res["price_sol"] > 0


def test_resolve_migrated():
    # complete=True, vtr=0 -> classified migrated, NOT decoded as a (wrong) price.
    acct = _curve_bytes(vtr=0, vsr=0, complete=True)
    res = resolve_price_account(_MINT, acct)
    assert res["kind"] == "migrated"
    assert res["price_sol"] is None


def test_resolve_unknown_or_missing():
    # Account not found / empty / too short -> kind='unknown', no exception.
    for acct in (None, b"", b"\x00" * 4):
        res = resolve_price_account(_MINT, acct)
        assert res["kind"] == "unknown"
        assert res["price_sol"] is None
        assert res["decoded"] is None
