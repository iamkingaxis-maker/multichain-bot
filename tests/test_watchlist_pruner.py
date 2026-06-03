"""Watchlist auto-pruner (core/watchlist_pruner.find_dead) — dead-token detection.
2026-06-03: auto-remove DEAD tokens from the user watchlist (rugged / no liquidity /
dried-up volume), fail-OPEN on missing data so a token we can't assess is never pruned."""
from core.watchlist_pruner import find_dead


def _t(addr, liq=None, vol=None, mcap=None):
    return {"address": addr, "liq_usd": liq, "vol_h24": vol, "mcap": mcap}


def test_rugged_is_dead():
    # mcap 0 or liq 0 = rugged/delisted
    assert find_dead([_t("a", liq=0, vol=0, mcap=0)], 25000, 20000) == ["a"]
    assert find_dead([_t("b", liq=0, vol=50000, mcap=500000)], 25000, 20000) == ["b"]


def test_low_liquidity_is_dead():
    assert find_dead([_t("c", liq=19000, vol=50000, mcap=500000)], 25000, 20000) == ["c"]


def test_dried_up_volume_is_dead():
    assert find_dead([_t("d", liq=80000, vol=24000, mcap=500000)], 25000, 20000) == ["d"]


def test_alive_token_kept():
    # healthy liquidity + volume -> not dead
    assert find_dead([_t("e", liq=80000, vol=120000, mcap=900000)], 25000, 20000) == []


def test_fail_open_when_no_data():
    # no liq AND no vol -> no evidence -> never marked dead
    assert find_dead([_t("f")], 25000, 20000) == []
    assert find_dead([_t("g", mcap=500000)], 25000, 20000) == []


def test_thresholds_respected():
    rows = [_t("h", liq=30000, vol=30000, mcap=500000)]
    assert find_dead(rows, 25000, 20000) == []          # above both floors
    assert find_dead(rows, 50000, 20000) == ["h"]        # raise vol floor -> dead
    assert find_dead(rows, 25000, 40000) == ["h"]        # raise liq floor -> dead


def test_mixed_batch():
    rows = [
        _t("alive", liq=80000, vol=120000, mcap=900000),
        _t("rug", liq=0, vol=0, mcap=0),
        _t("thin", liq=80000, vol=10000, mcap=500000),
        _t("nodata"),
    ]
    assert set(find_dead(rows, 25000, 20000)) == {"rug", "thin"}
