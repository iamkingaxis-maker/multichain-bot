"""Tests for core.live_faithful_pnl.compute_live_faithful.

Live-faithful P&L EXCLUDES closed buys whose entry_meta daily_halt_would_block or
reentry_cap_would_block was True (trades a funded live bot would skip), while paper
total INCLUDES them. delta_usd == sum of would-blocked realized $.
"""
import math

from core.live_faithful_pnl import compute_live_faithful


def _buy(bot, addr, t, **meta):
    return {"type": "buy", "bot_id": bot, "address": addr, "time": t,
            "entry_meta": dict(meta) if meta else {}}


def _sell(bot, addr, t, pnl, pnl_pct, frac=1.0):
    return {"type": "sell", "bot_id": bot, "address": addr, "time": t,
            "pnl": pnl, "pnl_pct": pnl_pct, "sell_fraction": frac}


def test_blocked_loser_excluded_from_livefaithful_only():
    # A flagged loser (would_block=True, -$10) and an unflagged winner (+$5).
    records = [
        _buy("botA", "MINT_LOSE", "2026-06-25T00:00:00", daily_halt_would_block=True),
        _sell("botA", "MINT_LOSE", "2026-06-25T00:05:00", pnl=-10.0, pnl_pct=-20.0),
        _buy("botA", "MINT_WIN", "2026-06-25T01:00:00"),
        _sell("botA", "MINT_WIN", "2026-06-25T01:05:00", pnl=5.0, pnl_pct=10.0),
    ]
    res = compute_live_faithful(records)
    fleet = res["fleet"]

    # paper includes both: -10 + 5 = -5
    assert math.isclose(fleet["paper_total_usd"], -5.0, abs_tol=1e-6)
    # live-faithful excludes the blocked loser: +5
    assert math.isclose(fleet["live_faithful_total_usd"], 5.0, abs_tol=1e-6)
    # delta == sum of would-blocked realized $ == -10
    assert math.isclose(fleet["delta_usd"],
                        fleet["paper_total_usd"] - fleet["live_faithful_total_usd"],
                        abs_tol=1e-6)
    assert math.isclose(fleet["delta_usd"], -10.0, abs_tol=1e-6)
    assert math.isclose(fleet["would_block_usd"], -10.0, abs_tol=1e-6)
    assert fleet["would_block_n"] == 1
    assert res["meta"]["n_closed"] == 2


def test_blocked_net_losers_make_livefaithful_higher():
    # would-blocked trades are net LOSERS -> excluding them lifts live_faithful above
    # paper, so delta (paper - live_faithful) is negative.
    records = [
        _buy("botB", "M1", "2026-06-25T00:00:00", reentry_cap_would_block=True),
        _sell("botB", "M1", "2026-06-25T00:05:00", pnl=-8.0, pnl_pct=-15.0),
        _buy("botB", "M2", "2026-06-25T00:10:00", daily_halt_would_block=True),
        _sell("botB", "M2", "2026-06-25T00:15:00", pnl=-4.0, pnl_pct=-9.0),
        _buy("botB", "M3", "2026-06-25T00:20:00"),
        _sell("botB", "M3", "2026-06-25T00:25:00", pnl=3.0, pnl_pct=6.0),
    ]
    res = compute_live_faithful(records)
    fleet = res["fleet"]

    assert fleet["live_faithful_total_usd"] > fleet["paper_total_usd"]
    assert fleet["delta_usd"] < 0
    assert fleet["direction"] == "paper_UNDERSTATES"
    assert fleet["would_block_n"] == 2


def test_blocked_net_winners_make_livefaithful_lower():
    # symmetric direction check: blocked winners -> paper OVERSTATES, delta positive.
    records = [
        _buy("botC", "W1", "2026-06-25T00:00:00", daily_halt_would_block=True),
        _sell("botC", "W1", "2026-06-25T00:05:00", pnl=12.0, pnl_pct=24.0),
        _buy("botC", "W2", "2026-06-25T00:10:00"),
        _sell("botC", "W2", "2026-06-25T00:15:00", pnl=-2.0, pnl_pct=-4.0),
    ]
    res = compute_live_faithful(records)
    fleet = res["fleet"]
    assert fleet["delta_usd"] > 0
    assert fleet["live_faithful_total_usd"] < fleet["paper_total_usd"]
    assert fleet["direction"] == "paper_OVERSTATES"


def test_missing_or_empty_entry_meta_fail_open_not_blocked():
    # missing entry_meta key entirely + explicit empty dict + None -> all treated as
    # NOT blocked, no crash.
    records = [
        {"type": "buy", "bot_id": "botD", "address": "X1", "time": "2026-06-25T00:00:00"},
        _sell("botD", "X1", "2026-06-25T00:05:00", pnl=4.0, pnl_pct=8.0),
        {"type": "buy", "bot_id": "botD", "address": "X2", "time": "2026-06-25T00:10:00",
         "entry_meta": {}},
        _sell("botD", "X2", "2026-06-25T00:15:00", pnl=-1.0, pnl_pct=-2.0),
        {"type": "buy", "bot_id": "botD", "address": "X3", "time": "2026-06-25T00:20:00",
         "entry_meta": None},
        _sell("botD", "X3", "2026-06-25T00:25:00", pnl=2.0, pnl_pct=5.0),
    ]
    res = compute_live_faithful(records)
    fleet = res["fleet"]
    assert fleet["would_block_n"] == 0
    # nothing blocked -> paper == live-faithful, delta 0
    assert math.isclose(fleet["delta_usd"], 0.0, abs_tol=1e-6)
    assert math.isclose(fleet["paper_total_usd"], fleet["live_faithful_total_usd"],
                        abs_tol=1e-6)
    assert fleet["direction"] == "no_would_blocked_closed_trades"


def test_empty_records_no_crash():
    res = compute_live_faithful([])
    assert res["fleet"]["paper_total_usd"] == 0
    assert res["fleet"]["delta_usd"] == 0
    assert res["meta"]["n_closed"] == 0
    assert res["per_bot"] == {}


def test_open_buy_excluded_orphan_sell_excluded():
    # an open buy (no sell) is excluded; an orphan sell (no prior buy) is excluded.
    records = [
        _buy("botE", "O1", "2026-06-25T00:00:00"),  # never sold -> excluded
        _sell("botE", "O2", "2026-06-25T00:05:00", pnl=99.0, pnl_pct=50.0),  # orphan
        _buy("botE", "O3", "2026-06-25T00:10:00"),
        _sell("botE", "O3", "2026-06-25T00:15:00", pnl=1.0, pnl_pct=2.0),
    ]
    res = compute_live_faithful(records)
    assert res["meta"]["n_closed"] == 1
    assert res["meta"]["open_unsold"] == 1
    assert res["meta"]["orphan_sells"] == 1
    # orphan sell P&L must NOT leak into paper total
    assert math.isclose(res["fleet"]["paper_total_usd"], 1.0, abs_tol=1e-6)
