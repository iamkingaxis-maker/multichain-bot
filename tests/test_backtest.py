"""Unified exit backtester (#4.4): replays a BotConfig over OHLCV via production tick().
Verifies hard_stop, the TP1/TP2 ladder, the never-runner exit, and the aggregate."""
from core.bot_config import BotConfig
from scripts.backtest import replay_exits, backtest


def _cfg(**ov):
    base = dict(bot_id="bt", display_name="BT", tp1_pct=5.0, tp1_sell_fraction=0.75,
                tp2_pct=7.0, tp2_sell_fraction=0.25, trail_pp=3.0, hard_stop_pct=-15.0,
                slow_bleed_minutes=60, slow_bleed_pnl_threshold=-8.0)
    base.update(ov)
    return BotConfig(**base)


def _c(minute, o, h, l, cl, v=100):
    return [minute * 60_000, o, h, l, cl, v]


def test_replay_hard_stop():
    # entry 1.0, candle drops to -16% -> hard_stop (-15%) fires
    cfg = _cfg()
    candles = [_c(5, 1.0, 1.0, 0.84, 0.85)]
    r = replay_exits(cfg, 1.0, candles)
    assert r["exit_reason"] == "HARD_STOP"
    assert r["blended_pnl_pct"] < -10


def test_replay_tp_ladder_runner():
    # rises to +6% then +8% -> TP1 (0.75 @ +6) + TP2 (0.25 @ +8) -> blended 6.5
    cfg = _cfg()
    candles = [_c(1, 1.0, 1.06, 1.0, 1.06), _c(2, 1.06, 1.08, 1.06, 1.08)]
    r = replay_exits(cfg, 1.0, candles)
    assert r["n_legs"] == 2
    assert abs(r["blended_pnl_pct"] - 6.5) < 0.5


def test_replay_never_runner_timebox():
    # flat ~-1% (never green, never -6) for >60min -> never-runner time-arm exits ~-1%
    cfg = _cfg(never_runner_exit_enabled=True, never_runner_peak_max=3.0,
               never_runner_loss_floor=-6.0, never_runner_minutes=60)
    candles = [_c(m, 0.99, 0.995, 0.985, 0.99) for m in (0, 20, 40, 61)]
    r = replay_exits(cfg, 1.0, candles)
    assert r["exit_reason"] == "NEVER_RUNNER"
    assert -3 < r["blended_pnl_pct"] < 0


def test_replay_never_runner_off_rides_to_resolve():
    # same flat path, never-runner OFF -> no early exit, resolves at last close (~-1%)
    cfg = _cfg()  # never_runner_exit_enabled defaults False
    candles = [_c(m, 0.99, 0.995, 0.985, 0.99) for m in (0, 20, 40, 61)]
    r = replay_exits(cfg, 1.0, candles)
    assert r["exit_reason"] == "RESOLVE"


def test_backtest_aggregate_and_heldout():
    cfg = _cfg()
    winner = [_c(1, 1.0, 1.06, 1.0, 1.06), _c(2, 1.06, 1.08, 1.06, 1.08)]
    loser = [_c(5, 1.0, 1.0, 0.84, 0.85)]
    dataset = [
        {"token": "A", "entry_price": 1.0, "ohlcv_after": winner},
        {"token": "B", "entry_price": 1.0, "ohlcv_after": loser},
        {"token": "C", "entry_price": 1.0, "ohlcv_after": winner},
        {"token": "D", "entry_price": 1.0, "ohlcv_after": loser},
    ]
    out = backtest(cfg, dataset)
    assert out["n"] == 4 and out["n_tokens"] == 4
    assert out["wr_pct"] == 50.0
    assert out["heldout_train_mean"] is not None and out["heldout_test_mean"] is not None
