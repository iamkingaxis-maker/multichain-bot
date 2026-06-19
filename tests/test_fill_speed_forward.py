# tests/test_fill_speed_forward.py
"""Pure-logic tests for the forward fill-speed joiner (scripts/fill_speed_forward.py).
Read-only join math; no network/file IO is exercised here."""
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts import fill_speed_forward as fsf


def _rec(addr, ts, fast, sweep):
    return {"token_address": addr, "ts": ts, "fast_price": fast, "sweep_price": sweep}


def _sell(addr, entry_ts, exit_price):
    return {"address": addr, "entry_ts": entry_ts, "exit_price": exit_price}


def test_match_trade_address_keyed_nearest_entry():
    by_addr = {
        "aaa": [_sell("AAA", 1000.0, 5.0), _sell("AAA", 2000.0, 6.0)],
    }
    # record captured near the 2000 trade
    r = _rec("AAA", "1970-01-01T00:31:50+00:00", 1.0, 1.1)  # 1910s -> nearest 2000
    m = fsf.match_trade(r, by_addr, max_skew_secs=600)
    assert m is not None
    assert m["entry_ts"] == 2000.0


def test_match_trade_respects_skew_and_address():
    by_addr = {"aaa": [_sell("AAA", 1000.0, 5.0)]}
    # capture ts is 5000s -> 4000s from the only trade -> outside 600s skew
    far = _rec("AAA", "1970-01-01T01:23:20+00:00", 1.0, 1.1)
    assert fsf.match_trade(far, by_addr, max_skew_secs=600) is None
    # wrong address -> no join
    wrong = _rec("BBB", "1970-01-01T00:16:40+00:00", 1.0, 1.1)
    assert fsf.match_trade(wrong, by_addr, max_skew_secs=600) is None


def test_match_trade_case_insensitive_addr():
    by_addr = {"aaa": [_sell("AAA", 1000.0, 5.0)]}
    r = _rec("AaA", "1970-01-01T00:16:40+00:00", 1.0, 1.1)  # 1000s
    assert fsf.match_trade(r, by_addr, max_skew_secs=600) is not None


def test_summarize_side_basic_and_empty():
    s = fsf.summarize_side([10.0, -5.0, 20.0, None])
    assert s["n"] == 3
    assert abs(s["wr"] - (100.0 * 2 / 3)) < 1e-9
    assert s["median"] == 10.0
    assert s["sum"] == 25.0
    empty = fsf.summarize_side([None, None])
    assert empty["n"] == 0
    assert empty["wr"] is None
    assert empty["sum"] == 0.0


def test_build_pairs_applies_same_exit_to_both_sides():
    # fast entry cheaper (100) than sweep (110); same exit 120 from the joined trade
    by_addr = {"aaa": [_sell("AAA", 1000.0, 120.0)]}
    records = [_rec("AAA", "1970-01-01T00:16:40+00:00", 100.0, 110.0)]  # ts=1000
    fast, sweep, edges, n_rec, n_join = fsf.build_pairs(records, by_addr)
    assert n_rec == 1 and n_join == 1
    assert round(fast[0], 6) == 20.0                       # 120/100-1
    assert round(sweep[0], 6) == round((120 / 110 - 1) * 100, 6)
    assert edges[0] > 0                                    # cheaper fast -> +edge


def test_build_pairs_skips_unjoinable_and_bad_prices():
    by_addr = {"aaa": [_sell("AAA", 1000.0, 120.0)]}
    records = [
        _rec("ZZZ", "1970-01-01T00:16:40+00:00", 100.0, 110.0),   # no matching addr
        _rec("AAA", "1970-01-01T00:16:40+00:00", 0.0, 110.0),     # bad fast price
        _rec("AAA", "1970-01-01T00:16:40+00:00", 100.0, 110.0),   # good
    ]
    fast, sweep, edges, n_rec, n_join = fsf.build_pairs(records, by_addr)
    assert n_rec == 3
    assert n_join == 1            # only the last record joins + has valid prices
