"""In-bot hourly regime-pattern miner (#435, 2026-06-14) — deterministic winner-vs-loser
entry-feature separators + regime classification from the fleet's closed trades."""
from datetime import datetime, timezone

import core.regime_pattern_miner as rpm


def _t(typ, addr, t, em=None, pnl=None):
    d = {"type": typ, "address": addr, "pair_address": addr + "p", "time": t,
         "token": addr.upper(), "bot_id": "b"}
    if em is not None:
        d["entry_meta"] = em
    if pnl is not None:
        d["pnl"] = pnl
    return d


def test_build_snapshot_separates_winners_from_losers():
    now = datetime(2026, 6, 14, 23, 0, 0, tzinfo=timezone.utc)
    trades = []
    # 4 winners enter DEEP dips (-25%); 4 losers enter SHALLOW (-5%) — the separator must catch it
    for i in range(4):
        a = f"win{i}"
        trades.append(_t("buy", a, f"2026-06-14T22:0{i}:00",
                         em={"shape_90m_drawdown_from_max_pct": -25.0, "mcap": 120000, "pc_h1": -22.0}))
        trades.append(_t("sell", a, f"2026-06-14T22:3{i}:00", pnl=5.0))
    for i in range(4):
        a = f"los{i}"
        trades.append(_t("buy", a, f"2026-06-14T22:0{i}:00",
                         em={"shape_90m_drawdown_from_max_pct": -5.0, "mcap": 60000, "pc_h1": -3.0}))
        trades.append(_t("sell", a, f"2026-06-14T22:3{i}:00", pnl=-3.0))
    snap = rpm.build_snapshot(trades, now_dt=now)
    assert snap["n_closed"] == 8
    assert snap["wins"] == 4
    assert snap["wr"] == 0.5
    assert snap["regime"] == "deep-dip"          # winners' median dip -25 <= -20
    sep = snap["all_separators"]["shape_90m_drawdown_from_max_pct"]
    assert sep["win_med"] == -25.0 and sep["loss_med"] == -5.0   # winners enter deeper
    assert sep["n_win"] == 4 and sep["n_loss"] == 4
    # top_separators is ranked + non-empty
    assert snap["top_separators"] and snap["top_separators"][0]["feature"] in snap["all_separators"]


def test_momentum_up_regime():
    now = datetime(2026, 6, 14, 23, 0, 0, tzinfo=timezone.utc)
    trades = []
    for i in range(3):
        a = f"mom{i}"
        trades.append(_t("buy", a, f"2026-06-14T22:0{i}:00",
                         em={"shape_90m_drawdown_from_max_pct": -2.0}))   # near highs = momentum
        trades.append(_t("sell", a, f"2026-06-14T22:3{i}:00", pnl=4.0))
    snap = rpm.build_snapshot(trades, now_dt=now)
    assert snap["regime"] == "momentum-up"        # winners enter near highs (dd > -8)


def test_empty_and_window_safe():
    snap = rpm.build_snapshot([])
    assert snap["n_closed"] == 0 and snap["wr"] is None and snap["regime"] == "unknown"
    # a buy OUTSIDE the 3h window is excluded
    now = datetime(2026, 6, 14, 23, 0, 0, tzinfo=timezone.utc)
    old = [_t("buy", "x", "2026-06-14T05:00:00", em={"mcap": 100000}),
           _t("sell", "x", "2026-06-14T05:30:00", pnl=1.0)]
    assert rpm.build_snapshot(old, now_dt=now)["n_closed"] == 0
