from core.onchain_price import decode_bonding_curve, bonding_curve_pda, price_sol_from_curve


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
