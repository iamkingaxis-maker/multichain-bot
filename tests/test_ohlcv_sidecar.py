"""OHLCV-capture sidecar (#4.4 follow-up): per-cycle price path -> persisted -> replayable
through the backtester deterministically."""
import os
from core import ohlcv_sidecar as oc
from core.bot_config import BotConfig
from scripts.backtest import backtest


def test_accumulate_samples_and_caps():
    sb = {}
    oc.accumulate_point(sb, 0.0, 1.0)
    oc.accumulate_point(sb, 5.0, 1.1)      # within min_gap (20s) -> dropped
    oc.accumulate_point(sb, 25.0, 1.2)     # past gap -> kept
    assert sb["ohlcv_path"] == [[0.0, 1.0], [25.0, 1.2]]
    # cap
    sb2 = {}
    for i in range(10):
        oc.accumulate_point(sb2, i * 30.0, 1.0 + i, max_points=3)
    assert len(sb2["ohlcv_path"]) == 3


def test_accumulate_ignores_bad_price():
    sb = {}
    oc.accumulate_point(sb, 0.0, 0.0)
    oc.accumulate_point(sb, 0.0, None)
    assert sb.get("ohlcv_path", []) == []


def test_path_to_candles():
    cands = oc.path_to_candles([[0.0, 1.0], [60.0, 1.05]], entry_time_ms=1_000_000)
    assert cands[0] == [1_000_000, 1.0, 1.0, 1.0, 1.0, 0]
    assert cands[1] == [1_060_000, 1.05, 1.05, 1.05, 1.05, 0]


def test_append_load_roundtrip(tmp_path):
    store = str(tmp_path / "sidecar.jsonl")
    oc.append_episode(store, {"bot_id": "b", "token": "T", "entry_price": 1.0, "path": [[0.0, 1.0]]})
    oc.append_episode(store, {"bot_id": "b", "token": "U", "entry_price": 2.0, "path": [[0.0, 2.0]]})
    eps = oc.load_episodes(store)
    assert len(eps) == 2 and eps[1]["token"] == "U"


def test_load_missing_returns_empty(tmp_path):
    assert oc.load_episodes(str(tmp_path / "nope.jsonl")) == []


def test_tick_accumulates_path_when_enabled(monkeypatch):
    monkeypatch.setenv("OHLCV_CAPTURE_SIDECAR", "1")
    from core.per_bot_position_manager import PerBotPositionManager
    pm = PerBotPositionManager(BotConfig(bot_id="b", display_name="B"))
    pm.open_position("T", 1.0, 100.0, entry_time=0.0)
    pm.tick("T", 1.00, 0.0)       # +0%
    pm.tick("T", 1.02, 30.0)      # +2%, past the 20s sample gap
    path = pm.get_position("T").state_blob.get("ohlcv_path")
    assert path and len(path) >= 2


def test_tick_no_path_when_disabled(monkeypatch):
    monkeypatch.delenv("OHLCV_CAPTURE_SIDECAR", raising=False)
    from core.per_bot_position_manager import PerBotPositionManager
    pm = PerBotPositionManager(BotConfig(bot_id="b", display_name="B"))
    pm.open_position("T", 1.0, 100.0, entry_time=0.0)
    pm.tick("T", 1.02, 30.0)
    assert "ohlcv_path" not in (pm.get_position("T").state_blob or {})


def test_episode_replays_through_backtester(tmp_path):
    # a runner episode (price climbs +6% then +8%) persisted -> backtester books the TP ladder
    store = str(tmp_path / "sidecar.jsonl")
    path = [[0.0, 1.0], [60.0, 1.06], [120.0, 1.08]]
    oc.append_episode(store, {"bot_id": "b", "token": "RUN", "address": "addr",
                              "entry_price": 1.0, "entry_time_ms": 0, "path": path})
    ds = oc.episodes_to_backtest_dataset(oc.load_episodes(store))
    cfg = BotConfig(bot_id="bt", display_name="BT", tp1_pct=5.0, tp1_sell_fraction=0.75,
                    tp2_pct=7.0, tp2_sell_fraction=0.25, trail_pp=3.0)
    out = backtest(cfg, ds)
    assert out["n"] == 1 and out["mean_pnl_pct"] > 5   # captured the +6/+8 ladder
