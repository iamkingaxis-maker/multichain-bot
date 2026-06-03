"""nf60 forward-shadow pure-function tests (scripts/nf60_forward_shadow.py).
2026-06-03: read-only fleet shadow of net_flow_60s as an entry block gate."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "nf60_forward_shadow",
    Path(__file__).parent.parent / "scripts" / "nf60_forward_shadow.py",
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
build_episodes = mod.build_episodes
shadow_stats = mod.shadow_stats


def _buy(bot, addr, t, nf60, amt=20.0, sym="X"):
    return {"type": "buy", "bot_id": bot, "address": addr, "token": sym, "time": t,
            "amount_usd": amt, "entry_meta": {"net_flow_60s_imbalance": nf60}}


def _sell(bot, addr, t, pnl, frac=1.0, peak=0.0):
    return {"type": "sell", "bot_id": bot, "address": addr, "time": t,
            "pnl_pct": pnl, "sell_fraction": frac, "peak_pnl_pct": peak}


def test_fifo_single_leg_episode():
    trades = [_buy("b1", "a", "2026-06-04T00:00:00", -0.5),
              _sell("b1", "a", "2026-06-04T00:05:00", -12.0)]
    eps = build_episodes(trades)
    assert len(eps) == 1
    assert eps[0]["win"] == 0
    assert eps[0]["nf60"] == -0.5
    assert eps[0]["pnl_pct"] == -12.0


def test_fifo_blended_partial_legs():
    # two partial sells: +30% on half, -10% on half -> blended +10%
    trades = [_buy("b1", "a", "2026-06-04T00:00:00", 0.4),
              _sell("b1", "a", "2026-06-04T00:05:00", 30.0, frac=0.5),
              _sell("b1", "a", "2026-06-04T00:09:00", -10.0, frac=0.5)]
    eps = build_episodes(trades)
    assert len(eps) == 1
    assert abs(eps[0]["pnl_pct"] - 10.0) < 1e-6
    assert eps[0]["win"] == 1


def test_phantom_excluded():
    trades = [_buy("b1", "a", "2026-06-04T00:00:00", 0.1),
              _sell("b1", "a", "2026-06-04T00:05:00", 5000.0)]
    assert build_episodes(trades) == []


def test_buy_without_nf60_skipped():
    trades = [{"type": "buy", "bot_id": "b", "address": "a", "time": "2026-06-04T00:00:00",
               "amount_usd": 20, "entry_meta": {}},
              _sell("b", "a", "2026-06-04T00:05:00", -5.0)]
    assert build_episodes(trades) == []


def test_open_position_not_closed():
    # buy with no matching sell -> not an episode yet
    assert build_episodes([_buy("b1", "a", "2026-06-04T00:00:00", -0.5)]) == []


def test_shadow_kill_ratio_and_dollars():
    eps = [
        {"address": "t1", "nf60": -0.5, "win": 0, "pnl_pct": -10.0, "amount_usd": 100},  # blocked loser
        {"address": "t1", "nf60": -0.4, "win": 1, "pnl_pct": 5.0, "amount_usd": 100},   # blocked winner
        {"address": "t2", "nf60": 0.5, "win": 1, "pnl_pct": 20.0, "amount_usd": 100},   # kept winner
        {"address": "t2", "nf60": 0.1, "win": 0, "pnl_pct": -8.0, "amount_usd": 100},   # kept loser
    ]
    s = shadow_stats(eps, -0.2)
    assert s["n_blocked"] == 2
    assert s["blocked_wins"] == 1 and s["blocked_losses"] == 1
    assert s["kill_ratio"] == 1.0  # 1 winner per 1 loser
    # blocked dollars: -10 (loser) + 5 (winner) = -$5 net saved
    assert abs(s["blocked_dollars"] - (-5.0)) < 1e-6


def test_shadow_fail_open_on_missing_nf60():
    # nf60 None must never be blocked
    eps = [{"address": "t1", "nf60": None, "win": 0, "pnl_pct": -10.0, "amount_usd": 100}]
    s = shadow_stats(eps, -0.2)
    assert s["n_blocked"] == 0
