"""Watchlist auto-pruner (core/watchlist_pruner.find_dead) — dead-token detection.
2026-06-03: auto-remove DEAD tokens from the user watchlist (rugged / no liquidity /
dried-up volume), fail-OPEN on missing data so a token we can't assess is never pruned."""
from core.watchlist_pruner import find_dead, find_adds, is_rugged


def _t(addr, liq=None, vol=None, mcap=None):
    return {"address": addr, "liq_usd": liq, "vol_h24": vol, "mcap": mcap}


def _a(addr, liq=80000, vol=120000, pc_h1=15, age_h=3):
    return {"address": addr, "liq_usd": liq, "vol_h24": vol, "pc_h1": pc_h1, "age_h": age_h}


# defaults mirror config: max_total, min_liq, min_vol, min_pc_h1, max_age_h, max_per_run
def _adds(tokens, current=(), denylist=(), max_total=150):
    return find_adds(tokens, list(current), list(denylist), max_total, 40000, 75000, 8, 24, 10)


def test_rugged_is_dead():
    # mcap 0 or liq 0 = rugged/delisted
    assert find_dead([_t("a", liq=0, vol=0, mcap=0)], 25000, 20000) == ["a"]
    assert find_dead([_t("b", liq=0, vol=50000, mcap=500000)], 25000, 20000) == ["b"]


def test_low_liquidity_is_dead():
    assert find_dead([_t("c", liq=19000, vol=50000, mcap=500000)], 25000, 20000) == ["c"]


def test_dried_up_volume_is_dead():
    assert find_dead([_t("d", liq=80000, vol=24000, mcap=500000)], 25000, 20000) == ["d"]


def test_is_rugged_instant():
    # rugged = liq or mcap <= 0 -> removed instantly (no strike wait)
    assert is_rugged(_t("a", liq=0, vol=0, mcap=0)) is True
    assert is_rugged(_t("b", liq=80000, vol=120000, mcap=0)) is True
    assert is_rugged(_t("c", liq=0, vol=50000, mcap=500000)) is True
    # dried-up but tradeable (not rugged) -> needs strikes, not instant
    assert is_rugged(_t("d", liq=80000, vol=10000, mcap=500000)) is False


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


# ---- auto-ADD ----
def test_add_fresh_live_mover():
    assert _adds([_a("good")]) == ["good"]


def test_add_skips_already_on_list():
    assert _adds([_a("dup")], current=["dup"]) == []


def test_add_skips_denylisted_manual_removal():
    # a fresh live mover the user MANUALLY removed must NEVER be auto-re-added
    assert _adds([_a("banned")], denylist=["banned"]) == []
    # but a non-banned mover still adds
    assert _adds([_a("ok"), _a("banned")], denylist=["banned"]) == ["ok"]


def test_add_skips_stale_token():
    assert _adds([_a("old", age_h=48)]) == []          # too old (>24h)


def test_add_skips_weak():
    assert _adds([_a("lowliq", liq=10000)]) == []        # liq below floor
    assert _adds([_a("lowvol", vol=20000)]) == []        # vol below floor
    assert _adds([_a("flat", pc_h1=2)]) == []            # not rising enough


def test_add_requires_full_evidence():
    # missing any of liq/vol/pc_h1 -> not added (stricter than prune)
    assert _adds([{"address": "x", "liq_usd": 80000, "vol_h24": 120000, "age_h": 3}]) == []


def test_add_ranks_by_volume_and_caps_room():
    toks = [_a("v1", vol=300000), _a("v2", vol=200000), _a("v3", vol=100000)]
    # cap total at len(current)+2 -> only top-2 by volume added
    assert find_adds(toks, ["x"], [], 3, 40000, 75000, 8, 24, 10) == ["v1", "v2"]


def test_add_respects_max_per_run():
    toks = [_a(f"t{i}", vol=100000 + i) for i in range(20)]
    assert len(find_adds(toks, [], [], 150, 40000, 75000, 8, 24, 5)) == 5
