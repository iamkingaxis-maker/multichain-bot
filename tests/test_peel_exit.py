# tests/test_peel_exit.py — conditional peel ladder (2026-07-06 TP-peel replay)
"""Fill <+12 at TP1 -> remainder is an uncapped 5pp-giveback runner (TP2
skipped). Fill >=+12 (soft-cap wick) -> standard ladder unchanged — the
unconditional peel LOSES -59.6pp on wick fills."""
import json
import pathlib

from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _pm(**over):
    base = dict(bot_id="t", display_name="t", tp1_pct=6.0, tp1_sell_fraction=0.5,
                tp2_pct=12.0, tp2_sell_fraction=0.25, trail_pp=2.0,
                hard_stop_pct=-12.0, peel_exit=True)
    base.update(over)
    return PerBotPositionManager(BotConfig(**base))


def _open(pm, price=1.0):
    pm.open_position(token="TOK", entry_price=price, size_usd=25.0,
                     entry_time=900.0, address="mintTOK", pair_address="pairTOK")
    return pm.get_position("TOK")


def _tick(pm, pnl_pct, now=1000.0):
    px = 1.0 * (1 + pnl_pct / 100.0)
    return pm.tick("TOK", px, now)


class TestPeelLow:
    def test_normal_fill_activates_peel_and_skips_tp2(self):
        pm = _pm()
        p = _open(pm)
        d1 = _tick(pm, 6.5)                      # TP1 at +6.5 (<12 -> peel)
        assert [x.kind for x in d1] == ["TP1"]
        assert p.state_blob.get("peel_active") is True
        d2 = _tick(pm, 13.0)                     # crosses tp2_pct -> NO TP2
        assert d2 == []
        d3 = _tick(pm, 25.0)                     # runner runs (peak 25)
        assert d3 == []
        d4 = _tick(pm, 19.5)                     # 25-5=20 -> peel trail fires
        assert [x.kind for x in d4] == ["POST_TP1_TRAIL"]
        assert "peel-runner" in d4[0].reason

    def test_runner_not_stopped_by_tight_trail(self):
        pm = _pm()
        _open(pm)
        _tick(pm, 6.5)
        # -3pp from peak would fire the old 2pp trail; peel needs 5pp
        assert _tick(pm, 8.0) == []              # peak 8
        assert _tick(pm, 5.5) == []              # 8-2.5: tight would fire, peel holds
        d = _tick(pm, 2.9)                       # 8-5.1 -> fires
        assert [x.kind for x in d] == ["POST_TP1_TRAIL"]


class TestPeelWick:
    def test_wick_fill_keeps_standard_ladder(self):
        pm = _pm()
        p = _open(pm)
        d1 = _tick(pm, 33.0)                     # soft-cap fill >=12 -> NO peel
        assert p.state_blob.get("peel_active") is None
        kinds = [x.kind for x in d1]
        assert "TP1" in kinds and "TP2" in kinds  # standard ladder intact


class TestPeelOff:
    def test_default_bots_byte_identical(self):
        pm = _pm(peel_exit=False)
        p = _open(pm)
        _tick(pm, 6.5)
        assert p.state_blob.get("peel_active") is None
        d = _tick(pm, 13.0)
        assert [x.kind for x in d] == ["TP2"]    # TP2 fires as always


def test_jersey_integrity():
    cfg = BotConfig(**json.loads(pathlib.Path(
        "config/bots/badday_flush_peel_ab.json").read_text()))
    flush = BotConfig(**json.loads(pathlib.Path(
        "config/bots/badday_flush.json").read_text()))
    assert cfg.enabled is True and cfg.peel_exit is True
    assert cfg.tp1_sell_fraction == 0.5 and cfg.hard_stop_pct == -12.0
    assert [tuple(x) for x in cfg.entry_gate] == [tuple(x) for x in flush.entry_gate]
    assert cfg.exclusion_pool == "badday_flush_peel_ab"
    # wideexit slot retired
    w = BotConfig(**json.loads(pathlib.Path(
        "config/bots/badday_flush_wideexit_ab.json").read_text()))
    assert w.enabled is False
