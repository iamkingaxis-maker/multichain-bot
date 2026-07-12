"""Variance-lever SHADOW stamps (2026-07-12 variance-reduction mine).

The Solana-side shadow must STAMP the low-variance levers' would-fire moments
onto state_blob but NEVER change the enforced exit — live SOL bots keep their
behaviour byte-for-byte. Provenance: scratchpad/_variance_reduction.md.
"""
import pytest
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _cfg(**ov):
    base = dict(bot_id="b1", display_name="Bot 1")
    base.update(ov)
    return BotConfig(**base)


def test_catastrophe_shadow_stamps_but_does_not_add_exit(monkeypatch):
    # pnl -25% breaches the -20 shadow floor; the ENFORCED exit is the -15 hard
    # stop (unchanged). The shadow must stamp AND the decision stays HARD_STOP.
    monkeypatch.setenv("VARIANCE_SHADOW", "on")
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-15.0))
    pm.open_position("DROP", 1.0, 100.0, entry_time=0.0)
    d = pm.tick(token="DROP", current_price=0.75, now=30.0, vol_m5_usd=None)
    # stamp lands on state_blob before the hard-stop return; enforcement is the
    # -15 hard stop, UNCHANGED — the shadow never adds/replaces a decision.
    assert any(x.kind == "HARD_STOP" for x in d)
    assert not any(x.kind == "VARIANCE_SHADOW" for x in d)


def test_catastrophe_shadow_records_on_blob():
    # hard stop OFF (-99) so the tick returns no exit; verify the stamp itself.
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-99.0, tp1_pct=999.0))
    pm.open_position("BLEED", 1.0, 100.0, entry_time=0.0)
    d = pm.tick(token="BLEED", current_price=0.78, now=30.0, vol_m5_usd=None)
    p = pm.get_position("BLEED")
    assert p is not None                       # not force-closed
    assert p.state_blob.get("varshadow_cat_fired") is True
    assert p.state_blob.get("varshadow_cat_pnl_at_fire") <= -20.0
    assert not any(x.kind == "VARIANCE_SHADOW" for x in d)   # never an exit kind


def test_holdbox_shadow_stamps_after_10min_without_exiting():
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-99.0, tp1_pct=999.0))
    pm.open_position("FLAT", 1.0, 100.0, entry_time=0.0)
    # +1% and held 10min: no enforced exit, box shadow should stamp
    d = pm.tick(token="FLAT", current_price=1.01, now=10 * 60, vol_m5_usd=None)
    p = pm.get_position("FLAT")
    assert p is not None
    assert p.state_blob.get("varshadow_box_fired") is True
    assert p.state_blob.get("varshadow_box_secs") >= 600
    assert d == [] or all(x.kind != "TIME_STOP" for x in d)  # box is not enforced here


def test_shadow_off_disables_stamps(monkeypatch):
    monkeypatch.setenv("VARIANCE_SHADOW", "off")
    pm = PerBotPositionManager(_cfg(hard_stop_pct=-99.0, tp1_pct=999.0))
    pm.open_position("X", 1.0, 100.0, entry_time=0.0)
    pm.tick(token="X", current_price=0.78, now=10 * 60, vol_m5_usd=None)
    p = pm.get_position("X")
    assert not p.state_blob.get("varshadow_cat_fired")
    assert not p.state_blob.get("varshadow_box_fired")
