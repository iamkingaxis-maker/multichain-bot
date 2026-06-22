"""Unit tests for core.top_bots — the curated top-bots scoreboard helper.

TDD: written before/with the implementation. Covers:
  - known trade list -> exact n, realized_usd, pnl_per_tr, wr, median,
    worst_decile, max_loss, enough_n
  - empty input -> a zeroed entry for each requested bot (present, not missing)
  - env override via TOP_BOTS
  - malformed records skipped (fail-open, never raises)
"""
import os
import importlib

import core.top_bots as tb


def test_default_set_and_env_override():
    # default
    importlib.reload(tb)
    assert tb.top_bots_set() == tb.TOP_BOTS_DEFAULT
    assert "badday_flush_conviction" in tb.TOP_BOTS_DEFAULT

    # env override (comma-separated, trimmed)
    old = os.environ.get("TOP_BOTS")
    try:
        os.environ["TOP_BOTS"] = "bot_a, bot_b ,bot_c"
        assert tb.top_bots_set() == ["bot_a", "bot_b", "bot_c"]
    finally:
        if old is None:
            os.environ.pop("TOP_BOTS", None)
        else:
            os.environ["TOP_BOTS"] = old


def test_empty_input_zeroed_entries_present():
    res = tb.compute_top_bots([], ["bot_a", "bot_b"])
    assert set(res.keys()) == {"bot_a", "bot_b"}
    for b in ("bot_a", "bot_b"):
        e = res[b]
        assert e["n"] == 0
        assert e["realized_usd"] == 0
        assert e["pnl_per_tr"] == 0
        assert e["wr"] == 0
        assert e["median_pnl_pct"] == 0
        assert e["worst_decile_pnl_pct"] == 0
        assert e["max_loss_usd"] == 0
        assert e["enough_n"] is False


def test_known_trade_list_aggregation():
    # bot_x: 4 closed sells. pnl_pct: [10, -5, 20, -50]; pnl_usd: [5, -2, 8, -40]
    trades = [
        {"type": "sell", "bot_id": "bot_x", "pnl_pct": 10, "pnl_usd": 5},
        {"type": "sell", "bot_id": "bot_x", "pnl_pct": -5, "pnl_usd": -2},
        {"type": "sell", "bot_id": "bot_x", "pnl_pct": 20, "pnl_usd": 8},
        {"type": "sell", "bot_id": "bot_x", "pnl_pct": -50, "pnl_usd": -40},
        # buys ignored
        {"type": "buy", "bot_id": "bot_x", "pnl_pct": 99, "pnl_usd": 99},
        # other bot ignored for bot_x
        {"type": "sell", "bot_id": "other", "pnl_pct": 1, "pnl_usd": 1},
    ]
    res = tb.compute_top_bots(trades, ["bot_x"])
    e = res["bot_x"]
    assert e["n"] == 4
    assert e["realized_usd"] == 5 - 2 + 8 - 40  # -29
    assert e["pnl_per_tr"] == round((-29) / 4, 2)
    # winners (pnl_pct>0): 2 of 4 = 50%
    assert e["wr"] == 50.0
    # median of [10,-5,20,-50] sorted [-50,-5,10,20] -> (−5+10)/2 = 2.5
    assert e["median_pnl_pct"] == 2.5
    # worst decile (10th pct, nearest-rank) of sorted [-50,-5,10,20] -> -50
    assert e["worst_decile_pnl_pct"] == -50
    # most-negative single pnl_usd
    assert e["max_loss_usd"] == -40
    assert e["enough_n"] is False  # n<30


def test_enough_n_threshold_and_pnl_fallback():
    # 30 closed sells -> enough_n True; uses 'pnl' fallback when 'pnl_usd' absent
    trades = []
    for i in range(30):
        trades.append({"side": "sell", "strategy": "bot_y",
                       "pnl_pct": 1.0, "pnl": 2.0})
    res = tb.compute_top_bots(trades, ["bot_y"])
    e = res["bot_y"]
    assert e["n"] == 30
    assert e["realized_usd"] == 60  # 30 * 2.0
    assert e["enough_n"] is True
    assert e["wr"] == 100.0


def test_malformed_records_skipped_fail_open():
    trades = [
        {"type": "sell", "bot_id": "bot_z", "pnl_pct": 10, "pnl_usd": 5},
        None,                                   # malformed
        "not a dict",                           # malformed
        {"type": "sell", "bot_id": "bot_z", "pnl_pct": None, "pnl_usd": 5},   # pnl_pct None -> skip
        {"type": "sell", "bot_id": "bot_z", "pnl_pct": "bad", "pnl_usd": 5},  # non-numeric -> skip
        {"type": "sell", "bot_id": "bot_z", "pnl_pct": 30, "pnl_usd": 12},
    ]
    # must not raise
    res = tb.compute_top_bots(trades, ["bot_z"])
    e = res["bot_z"]
    assert e["n"] == 2  # only the two valid sells
    assert e["realized_usd"] == 17
    assert e["wr"] == 100.0
