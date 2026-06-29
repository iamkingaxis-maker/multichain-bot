# tests/test_gt_decliner.py
from feeds.gecko_ohlcv import GeckoTerminalClient as G


def _pair(addr, h1, liq):
    return {"baseToken": {"address": addr, "symbol": addr},
            "priceChange": {"h1": h1}, "liquidity": {"usd": liq}}


def test_keeps_only_dip_band():
    pairs = [
        _pair("DIP", -20.0, 50_000),     # in band
        _pair("PUMP", +35.0, 50_000),    # pump -> drop
        _pair("FLAT", -2.0, 50_000),     # too shallow -> drop
        _pair("CRASH", -80.0, 50_000),   # below band (corpse) -> drop
        _pair("EDGE_LO", -45.0, 50_000), # inclusive low edge -> keep
        _pair("EDGE_HI", -8.0, 50_000),  # inclusive high edge -> keep
    ]
    out = G._filter_decliners(pairs, h1_min=-45.0, h1_max=-8.0, min_liq_usd=0.0)
    addrs = {p["baseToken"]["address"] for p in out}
    assert addrs == {"DIP", "EDGE_LO", "EDGE_HI"}


def test_min_liq_floor():
    pairs = [_pair("THIN", -20.0, 5_000), _pair("OK", -20.0, 20_000)]
    out = G._filter_decliners(pairs, min_liq_usd=15_000)
    assert [p["baseToken"]["address"] for p in out] == ["OK"]


def test_dedup_by_address_keeps_first():
    pairs = [_pair("A", -20.0, 50_000), _pair("A", -30.0, 50_000)]
    out = G._filter_decliners(pairs)
    assert len(out) == 1 and out[0]["priceChange"]["h1"] == -20.0


def test_malformed_pairs_skipped_no_raise():
    pairs = [
        {"baseToken": {"address": ""}, "priceChange": {"h1": -20.0}},  # no addr
        {"baseToken": {"address": "X"}, "priceChange": {"h1": "bad"}},  # bad h1
        {"baseToken": {"address": "Y"}},                                # no priceChange -> h1=0 -> drop
        _pair("Z", -15.0, 30_000),                                      # valid
    ]
    out = G._filter_decliners(pairs, min_liq_usd=0.0)
    assert [p["baseToken"]["address"] for p in out] == ["Z"]


def test_empty_and_none_inputs():
    assert G._filter_decliners([]) == []
    assert G._filter_decliners(None) == []
