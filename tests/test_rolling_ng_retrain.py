"""Rolling NG retrain — FIFO-match trade log -> training set, label correctness, fail-safe."""
import json
import os
import tempfile
from core.rolling_ng_retrain import build_training_set, retrain_and_save


def _buy(bot, addr, t, meta):
    return {"type": "buy", "bot_id": bot, "address": addr, "time": t, "entry_meta": meta}


def _sell(bot, addr, t, pnl, peak, frac=1.0):
    return {"type": "sell", "bot_id": bot, "address": addr, "time": t,
            "pnl_pct": pnl, "peak_pnl_pct": peak, "sell_fraction": frac}


def test_label_never_green_from_episode_peak():
    trades = [
        _buy("b", "AAA", "2026-06-01T00:00", {"pc_h1": 1.0}),
        _sell("b", "AAA", "2026-06-01T01:00", -10, 1.5),   # peak 1.5 < 3 -> never-green
        _buy("b", "BBB", "2026-06-01T00:00", {"pc_h1": 2.0}),
        _sell("b", "BBB", "2026-06-01T01:00", 8, 25.0),     # peak 25 -> green
    ]
    X, y, g = build_training_set(trades)
    assert len(X) == 2
    lbl = dict(zip(g, y))
    assert lbl["AAA"] == 1 and lbl["BBB"] == 0


def test_episode_peak_is_max_over_legs():
    trades = [
        _buy("b", "AAA", "2026-06-01T00:00", {"pc_h1": 1.0}),
        _sell("b", "AAA", "2026-06-01T01:00", 2, 1.0, frac=0.5),
        _sell("b", "AAA", "2026-06-01T02:00", 5, 12.0, frac=0.5),  # max peak 12 -> green
    ]
    X, y, g = build_training_set(trades)
    assert len(X) == 1 and y[0] == 0


def test_partial_close_excluded():
    trades = [
        _buy("b", "AAA", "2026-06-01T00:00", {"pc_h1": 1.0}),
        _sell("b", "AAA", "2026-06-01T01:00", -5, 1.0, frac=0.4),  # only 40% closed
    ]
    X, y, g = build_training_set(trades)
    assert len(X) == 0


def test_retrain_insufficient_data_failsafe():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "trades.json"), "w") as f:
            json.dump([_buy("b", "AAA", "2026-06-01T00:00", {"pc_h1": 1.0}),
                       _sell("b", "AAA", "2026-06-01T01:00", -5, 1.0)], f)
        r = retrain_and_save(d, os.path.join(d, "model"),
                             now_epoch=__import__("calendar").timegm((2026, 6, 1, 2, 0, 0, 0, 0, 0)))
        assert r["trained"] is False  # < 80 rows


def test_retrain_no_log_failsafe():
    with tempfile.TemporaryDirectory() as d:
        r = retrain_and_save(d, os.path.join(d, "model"))
        assert r["trained"] is False and r["reason"] == "no_trade_log"


def test_retrain_trains_on_sufficient_data():
    import calendar
    trades = []
    for i in range(200):
        addr = f"T{i}"
        dud = i % 2
        trades.append(_buy("b", addr, "2026-06-01T00:00",
                           {"pc_h1": 5.0 if dud else -5.0, "lifecycle_age_hours": 300 if dud else 5}))
        trades.append(_sell("b", addr, "2026-06-01T01:00", -8 if dud else 9,
                            1.0 if dud else 30.0))
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "trades.json"), "w") as f:
            json.dump(trades, f)
        r = retrain_and_save(d, os.path.join(d, "model"),
                             now_epoch=calendar.timegm((2026, 6, 1, 2, 0, 0, 0, 0, 0)))
        assert r["trained"] is True and r["n"] == 200
        assert os.path.exists(os.path.join(d, "model.joblib"))
