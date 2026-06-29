# -*- coding: utf-8 -*-
"""LEGACY ENGINE KILL SWITCH — fleet-safety proof.

LEGACY_ENGINE_ENABLED (default "true" = BYTE-IDENTICAL) disables the LEGACY
(non-fleet) paper-trading strategies while leaving the 11-bot fleet untouched.

Legacy commit sites the switch gates (traced 2026-06-28):
  - feeds/dip_scanner.py  : legacy single-bot `dip_buy [<tier>]` commit
                            (self.trader.buy(... strategy="dip_buy" ...))
  - core/multi_source_scanner.py : `_fire_chart_buy_inner` chart-buy commit
  - main.py               : sol_scalper.run() spawn  (config.scalper_enabled)
  - main.py               : GraduationSniper construct/wire (config.graduation_enabled)

The FLEET (BotManager / per_bot / _execute_bot_buy / should_route_live) is on a
SEPARATE branch the switch never touches — proven by the load-bearing
`test_fleet_buy_unaffected_when_legacy_off` below.

Run: python -m pytest tests/test_legacy_engine_switch.py -v
"""
import asyncio
import os
import re
import sys
import time
from types import SimpleNamespace as NS

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling test imports

from utils.config import legacy_engine_enabled
import feeds.dip_scanner as ds_mod
from core.multi_source_scanner import MultiSourceScanner, TokenSignal
import test_arm_only_buy_route_e2e as h   # reuse the real-chain DipScanner harness


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =========================================================================
# 1. The single switch resolves correctly (default true = byte-identical)
# =========================================================================

def test_default_is_true_byte_identical(monkeypatch):
    monkeypatch.delenv("LEGACY_ENGINE_ENABLED", raising=False)
    assert legacy_engine_enabled() is True


@pytest.mark.parametrize("v", ["true", "True", "1", "on", "yes", "TRUE", "anything", "", "  "])
def test_truthy_variants(monkeypatch, v):
    monkeypatch.setenv("LEGACY_ENGINE_ENABLED", v)
    assert legacy_engine_enabled() is True, v


@pytest.mark.parametrize("v", ["0", "false", "off", "no", "False", "OFF", "No", " false "])
def test_falsey_variants(monkeypatch, v):
    monkeypatch.setenv("LEGACY_ENGINE_ENABLED", v)
    assert legacy_engine_enabled() is False, v


# =========================================================================
# 2. MSS legacy buy — behavioral: fires when ON, skipped when OFF
# =========================================================================

def _mss_shell():
    sc = MultiSourceScanner.__new__(MultiSourceScanner)
    buys = []

    async def _buy(**kw):
        buys.append(kw)

    async def _send(*a, **k):
        pass

    sc.chain = NS(name="solana", chain_id="solana")
    sc.trader = NS(open_positions={}, reentry=NS(last_h1_pct={}), buy=_buy)
    sc.telegram = NS(send=_send)
    sc._pending_buys = set()
    sc._mint_map = {}
    sc.min_combined_score = 50
    sc.signals_fired = 0
    sc._last_buy_time = 0.0
    sc._ext_block_reason = None
    return sc, buys


def _mss_signal():
    # passes every pre-commit MSS gate: score>=50, mcap>0, symbol set, not DANGER,
    # vol/mcap=0.1 (healthy), max_price_usd=0 (chase guard skipped).
    return TokenSignal(
        token_address="Abc12345678901234567890pump", token_symbol="WIF2",
        token_name="WIF2", chain_id="solana", mcap=500_000.0, volume_h1=50_000.0,
        combined_score=80, dex_score=70, chart_score=0, price_change_h1=-1.0,
        price_change_h6=-2.0, pair_address="pair1", age_hours=30.0, dex_url="x",
    )


def test_mss_legacy_buy_fires_when_on(monkeypatch):
    monkeypatch.setenv("LEGACY_ENGINE_ENABLED", "true")
    sc, buys = _mss_shell()
    _run(sc._fire_chart_buy(_mss_signal(), "MEDIUM", strategy_tag="scanner"))
    assert len(buys) == 1, "legacy MSS buy must fire when LEGACY_ENGINE_ENABLED=true"


def test_mss_legacy_buy_skipped_when_off(monkeypatch):
    monkeypatch.setenv("LEGACY_ENGINE_ENABLED", "false")
    sc, buys = _mss_shell()
    _run(sc._fire_chart_buy(_mss_signal(), "MEDIUM", strategy_tag="scanner"))
    assert len(buys) == 0, "legacy MSS buy must be SKIPPED when LEGACY_ENGINE_ENABLED=false"


# =========================================================================
# 3. FLEET buy path — LOAD-BEARING: byte-identical whether flag true or false
# =========================================================================

def _drive_main_scan(monkeypatch, legacy_val):
    """Drive the REAL _evaluate_pair (which contains the legacy gate) on the
    main-scan path (MAIN_SCAN_BUY_MODE=on). Returns (fleet_calls, legacy_calls)."""
    monkeypatch.setattr(ds_mod, "MULTI_BOT_ENABLED", True)
    for k, val in {
        "MAIN_SCAN_BUY_MODE": "on", "PAPER_MODE": "true",
        "HEAVY_EVAL_PRESCREEN": "off", "NO_FAST_PRICE_GATE_MODE": "off",
        "RT_TRIGGER_MODE": "off", "RT_ARM_MODE": "off",
        "RT_DEMAND_TURN_MODE": "off", "FAST_WATCH_PINNED_PRICE": "off",
        "LEGACY_ENGINE_ENABLED": legacy_val,
    }.items():
        monkeypatch.setenv(k, val)

    sc = h._make_scanner(h._decision_from_bundle)
    fleet_calls = h._spy_execute_bot_buy(sc)        # spies the FLEET terminal executor
    legacy_calls = []

    async def _legacy_buy(**kw):
        legacy_calls.append(kw)
    sc.trader = NS(private_key="", buy=_legacy_buy)  # spies the LEGACY commit

    pair = h._benign_pair(h.STALE_PX)
    ctx = {
        "now_ms": int(time.time() * 1000), "_regime_n": 0,
        "_regime_dip_breadth_pct": 0.0, "_regime_h1_neg_pct": 0.0,
        "_fast_cache_only_charts": True,
    }
    _run(sc._evaluate_pair(pair, ctx))
    return len(fleet_calls), len(legacy_calls)


def test_fleet_buy_fires_when_legacy_on(monkeypatch):
    fleet, _ = _drive_main_scan(monkeypatch, "true")
    assert fleet == 1, "fleet _execute_bot_buy must fire on the main-scan path"


def test_fleet_buy_unaffected_when_legacy_off(monkeypatch):
    """LOAD-BEARING: the fleet path fires IDENTICALLY with the kill switch OFF —
    the flag touches only legacy commit sites, never the fleet branch."""
    fleet_on, _ = _drive_main_scan(monkeypatch, "true")
    fleet_off, _ = _drive_main_scan(monkeypatch, "false")
    assert fleet_off == fleet_on == 1, (
        f"FLEET path must be byte-identical regardless of LEGACY_ENGINE_ENABLED "
        f"(on={fleet_on} off={fleet_off})"
    )


# =========================================================================
# 4. Dip legacy commit gate — structural: gated between filters_block & commit
#    (the legacy dip_buy commit sits behind ~90 filters; reaching it with a
#    synthetic pair is impractical, so we assert the gate's PLACEMENT in source.)
# =========================================================================

def test_dip_legacy_gate_placed_before_commit():
    src = open(os.path.join(_ROOT, "feeds", "dip_scanner.py"), encoding="utf-8").read()
    commit_idx = src.index('strategy="dip_buy",')   # the actual buy kwarg (trailing comma)
    gate_idx = src.rindex("if not _legacy_engine_on():", 0, commit_idx)
    fb_idx = src.rindex("if _filters_block:", 0, gate_idx)
    # gate is AFTER the filters_block continue and BEFORE the legacy commit
    assert fb_idx < gate_idx < commit_idx
    # gate resolves the SINGLE central switch (not a bespoke env read)
    assert "from utils.config import legacy_engine_enabled as _legacy_engine_on" in src
    # the FLEET fan-out (_execute_bot_buy) sits BEFORE the gate -> not gated
    fanout_idx = src.rindex("_fast_route_decisions", 0, gate_idx)
    assert fanout_idx < gate_idx


# =========================================================================
# 5. Scalper / Graduation spawn guards — structural: gated on the switch
# =========================================================================

def test_main_spawn_guards_reference_switch():
    src = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
    assert "from utils.config import Config, legacy_engine_enabled" in src
    assert re.search(r"config\.scalper_enabled and not legacy_engine_enabled\(\)", src)
    assert re.search(r"config\.graduation_enabled and not legacy_engine_enabled\(\)", src)


# =========================================================================
# 6. Exit safety — the switch NEVER touches sell/exit machinery, so an
#    already-open legacy position still EXITS normally (only NEW entries blocked).
# =========================================================================

def test_exit_machinery_not_gated_by_switch():
    for rel in ("core/trader.py", "core/position_manager.py",
                "core/per_bot_position_manager.py"):
        src = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        assert "legacy_engine_enabled" not in src, rel
        assert "LEGACY_ENGINE_ENABLED" not in src, rel
