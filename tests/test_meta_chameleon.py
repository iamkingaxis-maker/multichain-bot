"""Meta chameleon — the fixed dynamic bot (2026-06-12). Geometry-only retune,
quiesce on open positions, clamps, persistence, boot overlay."""
import json
import time
import types

import core.meta_chameleon as ch
from core.bot_config import BotConfig


def _cfg():
    return BotConfig.from_json("config/bots/meta_chameleon.json")


def _pm(cfg, open_positions=0):
    return types.SimpleNamespace(
        config=cfg,
        iter_positions=lambda: [object()] * open_positions,
    )


def _scanner(pm):
    return types.SimpleNamespace(bot_position_managers={"meta_chameleon": pm})


class _FakeSensor:
    def __init__(self, board, geo):
        self._board, self._geo = board, geo

    def scoreboard(self, now=None):
        return {"windows": {"6h": self._board}}

    def archetype_geometry(self, arch, now=None, window_secs=21600, min_n=8):
        return self._geo.get(arch)


def _patch(monkeypatch, tmp_path, sensor):
    monkeypatch.setattr(ch, "_TUNE_FILE", str(tmp_path / "tune.json"))
    monkeypatch.setattr(ch, "_last_check", 0.0)
    import core.meta_sensor as ms
    monkeypatch.setattr(ms, "_SENSOR", sensor)


GEO = {"n": 12, "wr": 0.75, "med_win_pct": 35.0, "med_loss_pct": -28.0,
       "med_hold_secs": 1800, "p75_hold_secs": 5400}


def test_tune_from_geometry_and_clamps():
    t = ch.tune_from_geometry(GEO)
    assert t == {"time_stop_minutes": 90.0, "tp1_pct": 35.0, "hard_stop_pct": -33.6}
    wild = {"med_win_pct": 400.0, "med_loss_pct": -95.0, "p75_hold_secs": 10 * 86400}
    t = ch.tune_from_geometry(wild)
    assert t["tp1_pct"] == 60.0 and t["time_stop_minutes"] == 780.0
    assert t["hard_stop_pct"] == -60.0
    assert ch.tune_from_geometry({"med_win_pct": None, "p75_hold_secs": None}) is None


def test_retune_applies_on_flat_book(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.time_stop_minutes == 90.0
    assert cfg.tp1_pct == 35.0
    assert cfg.hard_stop_pct == -33.6
    st = json.load(open(str(tmp_path / "tune.json")))
    assert st["meta_chameleon"]["archetype"] == "timebox"


def test_quiesce_defers_until_flat(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    pm = _pm(cfg, open_positions=3)
    ch.maybe_retune(_scanner(pm), now=time.time())
    assert cfg.time_stop_minutes == 240.0          # unchanged — book not flat
    st = json.load(open(str(tmp_path / "tune.json")))
    assert st["meta_chameleon"]["pending"]["archetype"] == "surgical"
    # book goes flat -> deferred tune applies on next check
    monkeypatch.setattr(ch, "_last_check", 0.0)
    pm.iter_positions = lambda: []
    ch.maybe_retune(_scanner(pm), now=time.time())
    assert cfg.time_stop_minutes == 90.0


def test_no_qualifying_archetype_holds_current_tune(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 3, "wr": 0.9},        # n too thin
                          "pond": {"n": 20, "wr": 0.4}}, {})     # wr too low
    _patch(monkeypatch, tmp_path, sensor)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.time_stop_minutes == 240.0          # held


def test_cadence_blocks_rapid_retune(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    now = time.time()
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now)
    assert cfg.tp1_pct == 35.0
    # sensor now reports a different geometry 30min later — must NOT churn
    geo2 = dict(GEO, med_win_pct=12.0)
    sensor._geo = {"timebox": geo2}
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now + 1800)
    assert cfg.tp1_pct == 35.0                     # cadence held


def test_boot_overlay_reapplies(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_TUNE_FILE", str(tmp_path / "tune.json"))
    json.dump({"meta_chameleon": {"tune": {"time_stop_minutes": 55.0,
                                           "tp1_pct": 14.0,
                                           "hard_stop_pct": -22.0},
                                  "archetype": "surgical"}},
              open(str(tmp_path / "tune.json"), "w"))
    cfg = _cfg()
    ch.apply_overlay(cfg)
    assert (cfg.time_stop_minutes, cfg.tp1_pct, cfg.hard_stop_pct) == (55.0, 14.0, -22.0)


def test_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("META_CHAMELEON", "off")
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.time_stop_minutes == 240.0
