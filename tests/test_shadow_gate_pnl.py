# tests/test_shadow_gate_pnl.py
"""Pure-logic tests for the per-gate shadow would-block P&L attribution joiner
(scripts/shadow_gate_pnl.py).

Read-only join math; no network/file IO is exercised here. ADDRESS-keyed join.
P&L is expressed via pnl_pct (primary); dollar figures are RECONSTRUCTED from
entry-size x pnl_pct (NEVER the corrupted feed `pnl` dollar field)."""
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scripts import shadow_gate_pnl as sg


def _event(gate, bot, addr, ts, symbol="SYM"):
    return {
        "ts": ts, "gate": gate, "bot": bot,
        "token_address": addr, "symbol": symbol, "would_block": True, "ctx": {},
    }


def _trade(bot, addr, entry_ts, pnl_pct, size_usd=10.0):
    """Synthetic CLOSED sell with entry_ts (unix s), pnl_pct, and a reconstructed
    entry size_usd already attached (the joiner reconstructs this from usd_received
    in the live path; here we pass it directly so the math is deterministic)."""
    return {
        "type": "sell", "bot_id": bot, "address": addr,
        "entry_ts": float(entry_ts), "pnl_pct": float(pnl_pct),
        "size_usd": float(size_usd),
    }


# ── match_block_to_trade ────────────────────────────────────────────────────

def test_match_nearest_within_skew_same_bot_and_address():
    trades = [
        _trade("botA", "AAA", 1000.0, 5.0),
        _trade("botA", "AAA", 2000.0, -3.0),
    ]
    by = sg.index_trades(trades)
    # event captured at ~1910s -> nearest is the 2000 trade
    ev = _event("g", "botA", "AAA", "1970-01-01T00:31:50+00:00")
    m = sg.match_block_to_trade(ev, by, max_skew=600)
    assert m is not None
    assert m["entry_ts"] == 2000.0


def test_match_respects_skew_boundary():
    trades = [_trade("botA", "AAA", 1000.0, 5.0)]
    by = sg.index_trades(trades)
    # exactly 600s away -> inside (<=)
    at_edge = _event("g", "botA", "AAA", "1970-01-01T00:26:40+00:00")  # 1600s -> 600 skew
    assert sg.match_block_to_trade(at_edge, by, max_skew=600) is not None
    # 601s away -> outside
    over = _event("g", "botA", "AAA", "1970-01-01T00:26:41+00:00")  # 1601s -> 601 skew
    assert sg.match_block_to_trade(over, by, max_skew=600) is None


def test_match_requires_same_bot_and_address():
    trades = [_trade("botA", "AAA", 1000.0, 5.0)]
    by = sg.index_trades(trades)
    # right addr, wrong bot
    wrong_bot = _event("g", "botB", "AAA", "1970-01-01T00:16:40+00:00")
    assert sg.match_block_to_trade(wrong_bot, by, max_skew=600) is None
    # right bot, wrong addr
    wrong_addr = _event("g", "botA", "BBB", "1970-01-01T00:16:40+00:00")
    assert sg.match_block_to_trade(wrong_addr, by, max_skew=600) is None


def test_match_address_is_case_insensitive():
    trades = [_trade("botA", "AaA", 1000.0, 5.0)]
    by = sg.index_trades(trades)
    ev = _event("g", "botA", "aAa", "1970-01-01T00:16:40+00:00")
    assert sg.match_block_to_trade(ev, by, max_skew=600) is not None


def test_match_none_when_no_trade():
    by = sg.index_trades([])
    ev = _event("g", "botA", "AAA", "1970-01-01T00:16:40+00:00")
    assert sg.match_block_to_trade(ev, by, max_skew=600) is None


# ── gate_attribution ────────────────────────────────────────────────────────

def test_attribution_winner_loser_and_unmatched():
    trades = [
        _trade("botA", "WIN", 1000.0, 20.0, size_usd=10.0),   # winner, +$2.00
        _trade("botA", "LOSS", 1000.0, -40.0, size_usd=10.0),  # loser, -$4.00
    ]
    events = [
        _event("regime_buy_gate", "botA", "WIN", "1970-01-01T00:16:40+00:00"),
        _event("regime_buy_gate", "botA", "LOSS", "1970-01-01T00:16:40+00:00"),
        # unmatched: no trade for this addr
        _event("regime_buy_gate", "botA", "GHOST", "1970-01-01T00:16:40+00:00"),
    ]
    out = sg.gate_attribution(events, trades, max_skew=600)
    g = out["regime_buy_gate"]
    assert g["n_blocked"] == 2
    assert g["n_unmatched_events"] == 1
    assert g["winners_blocked"] == 1
    assert g["losers_blocked"] == 1
    assert g["wr"] == 50.0
    assert g["sum_pnl_pct"] == -20.0          # 20 + (-40)
    assert g["median_pnl_pct"] == -10.0       # median(20,-40)
    # reconstructed $: bleed avoided = -(-40/100*10) = 4.0 ; winners given up = 20/100*10 = 2.0
    assert abs(g["bleed_avoided_usd"] - 4.0) < 1e-9
    assert abs(g["winners_given_up_usd"] - 2.0) < 1e-9
    # net_edge_usd = -(sum size*pnl_pct/100) = -((2.0) + (-4.0)) = +2.0 (enforcing helps)
    assert abs(g["net_edge_usd"] - 2.0) < 1e-9
    # net_edge_pct = -sum_pnl_pct = +20
    assert g["net_edge_pct"] == 20.0


def test_attribution_dedupes_one_trade_across_multiple_events_same_gate():
    trades = [_trade("botA", "AAA", 1000.0, -10.0, size_usd=10.0)]
    # two would-block events of the SAME gate for the SAME trade
    events = [
        _event("g1", "botA", "AAA", "1970-01-01T00:16:40+00:00"),
        _event("g1", "botA", "AAA", "1970-01-01T00:16:45+00:00"),
    ]
    out = sg.gate_attribution(events, trades, max_skew=600)
    g = out["g1"]
    # the single trade must be counted ONCE, not twice
    assert g["n_blocked"] == 1
    assert g["losers_blocked"] == 1
    assert g["sum_pnl_pct"] == -10.0


def test_attribution_separates_gates():
    trades = [
        _trade("botA", "AAA", 1000.0, 30.0, size_usd=10.0),
        _trade("botA", "BBB", 1000.0, -20.0, size_usd=10.0),
    ]
    events = [
        _event("gateX", "botA", "AAA", "1970-01-01T00:16:40+00:00"),
        _event("gateY", "botA", "BBB", "1970-01-01T00:16:40+00:00"),
    ]
    out = sg.gate_attribution(events, trades, max_skew=600)
    assert set(out.keys()) == {"gateX", "gateY"}
    assert out["gateX"]["winners_blocked"] == 1
    assert out["gateX"]["losers_blocked"] == 0
    assert out["gateY"]["winners_blocked"] == 0
    assert out["gateY"]["losers_blocked"] == 1


def test_reconstruct_size_from_usd_received():
    # usd_received / (1 + pnl_pct/100) recovers entry notional
    assert abs(sg.reconstruct_size_usd({"usd_received": 11.0916, "pnl_pct": 10.92}) - 10.0) < 0.01
    assert abs(sg.reconstruct_size_usd({"usd_received": 18.304, "pnl_pct": -8.48}) - 20.0) < 0.01
    # missing -> None (never crash)
    assert sg.reconstruct_size_usd({"pnl_pct": 5.0}) is None
    assert sg.reconstruct_size_usd({"usd_received": 10.0}) is None
