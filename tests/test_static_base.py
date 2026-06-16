# -*- coding: utf-8 -*-
"""CHAMELEON_STATIC_BASE (chameleon mission 2026-06-16).

Phase A+B backtests: the dynamic AND slow best-bot TRACKERS both LOSE to running the best
STATIC bot; the robust static best is badday_flush (+3.88/+4.80 both windows, 0% catastrophe)
vs chameleon regime-mode's per-dollar-worst + 6% catastrophe. So CHAMELEON_STATIC_BASE lets the
chameleon statically ADOPT a proven bot's population+geometry (default OFF, instant-reversible,
JSON untouched). These cover the reader, the applier (incl the zero-fire-trap asserts), the
size/concurrency/capital freeze invariant, and the entries_allowed bypass."""
from core.bot_config import BotConfig
from core import meta_chameleon as mc

CHAM = "config/bots/meta_chameleon.json"


def test_static_base_reader(monkeypatch):
    monkeypatch.delenv("CHAMELEON_STATIC_BASE", raising=False)
    assert mc.static_base() is None
    monkeypatch.setenv("CHAMELEON_STATIC_BASE", "badday_flush")
    assert mc.static_base() == "badday_flush"
    monkeypatch.setenv("CHAMELEON_STATIC_BASE", "   ")
    assert mc.static_base() is None


def test_apply_static_base_copies_shape_and_population():
    cfg = BotConfig.from_json(CHAM)
    mc._apply_static_base(cfg, "badday_flush")
    # POPULATION mandate (the two zero-fire traps the applier asserts)
    assert cfg.microcap_mandate is True
    assert cfg.mcap_min == 50000.0 and cfg.mcap_max == 500000.0
    # GEOMETRY adopted from badday_flush
    assert cfg.hard_stop_pct == -12.0
    assert cfg.tp1_pct == 6.0
    # ENTRY GATE includes badday_flush's buyer-quality gates (intentional, kept)
    feats = [c[0] for c in cfg.entry_gate]
    assert "unique_buyers_n" in feats
    assert "n_recurring_buyers_3plus" in feats
    assert "pc_h1" in feats


def test_apply_static_base_freezes_size_concurrency_capital():
    cfg = BotConfig.from_json(CHAM)
    sz, conc, cap = cfg.base_position_usd, cfg.max_concurrent_positions, cfg.paper_capital_usd
    mc._apply_static_base(cfg, "badday_flush")
    assert cfg.base_position_usd == sz          # size FROZEN (chameleon contract)
    assert cfg.max_concurrent_positions == conc
    assert cfg.paper_capital_usd == cap


def test_entries_allowed_bypasses_standby_when_static(monkeypatch):
    monkeypatch.delenv("META_CHAMELEON", raising=False)   # enabled() defaults on
    monkeypatch.setenv("CHAMELEON_STATIC_BASE", "badday_flush")
    allowed, reason = mc.entries_allowed("meta_chameleon")
    assert allowed is True
    assert "STATIC_BASE" in reason


def test_no_static_base_default_off(monkeypatch):
    # Flag unset -> static_base None -> chameleon keeps normal behavior (no bypass reason).
    monkeypatch.delenv("CHAMELEON_STATIC_BASE", raising=False)
    assert mc.static_base() is None
