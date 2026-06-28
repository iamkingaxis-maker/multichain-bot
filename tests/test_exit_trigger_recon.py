# -*- coding: utf-8 -*-
"""EXIT-TRIGGER REACHABILITY recon (MEASUREMENT ONLY, 2026-06-28).

Paper evaluates ALL exit triggers inside pm.tick at the ~150s-STALE main-scan
snapshot price; LIVE evaluates the SAME pm.tick on the ~2s FRESH fast-watch
price. Same position, different price -> a DIFFERENT trigger can fire at a
different pnl. EXIT_TRIGGER_RECON_MODE=off|shadow (default off) re-runs the EXACT
same trigger logic on the fresh price against a DEEP COPY of the position and
logs whether the fresh decision DIFFERS from the stale one.

CONTRACT: measurement only. NEVER sells, NEVER mutates the real position or the
real manager, NEVER changes an exit. Default off = byte-identical (no records).
"""
import copy

import pytest

from core.fast_watch import rt_mode
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager
from feeds.dip_scanner import DipScanner


def _cfg(**overrides):
    base = dict(bot_id="badday_flush", display_name="Badday Flush")
    base.update(overrides)
    return BotConfig(**base)


def _scanner(records):
    """A bare DipScanner instance wired so the recon writer captures records into
    `records` instead of touching the filesystem."""
    sc = DipScanner.__new__(DipScanner)
    sc._fast_samples = {}
    sc._fast_samples_ts = {}
    sc._append_exit_trigger_recon = lambda rec: records.append(rec)
    return sc


# ---- flag resolver -------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    ("off", "off"), ("shadow", "shadow"),
    ("garbage", "off"), ("SHADOW", "shadow"), ("  Off ", "off"),
    # NO enforce here -- it's measurement only; an "enforce" string is still
    # a valid resolver value, but the recon method never acts on it.
])
def test_flag_resolver(monkeypatch, val, expected):
    monkeypatch.setenv("EXIT_TRIGGER_RECON_MODE", val)
    assert rt_mode("EXIT_TRIGGER_RECON_MODE") == expected


def test_flag_resolver_default_off(monkeypatch):
    monkeypatch.delenv("EXIT_TRIGGER_RECON_MODE", raising=False)
    assert rt_mode("EXIT_TRIGGER_RECON_MODE") == "off"


# ---- default off -> no-op, real position untouched -----------------------

def test_off_is_noop(monkeypatch):
    monkeypatch.delenv("EXIT_TRIGGER_RECON_MODE", raising=False)
    records = []
    sc = _scanner(records)
    pm = PerBotPositionManager(_cfg())
    pm.open_position("TOK", entry_price=1.0, size_usd=20.0, entry_time=0.0,
                     address="mintTOK")
    pos = pm.get_position("TOK")
    # a fresh price that WOULD cross tp1 if recon ran
    from collections import deque
    sc._fast_samples = {"mintTOK": deque([1.07], maxlen=20)}

    before = copy.deepcopy(pos)
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, stale_price=1.03,
                                 stale_decisions=[], now=60.0, vol_m5=None)
    assert records == []
    # real position byte-identical
    assert pos.peak_pnl_pct == before.peak_pnl_pct
    assert pos.tp1_hit == before.tp1_hit
    assert pos.state_blob == before.state_blob


# ---- shadow: fresh crosses tp1, stale does not (disagreement) ------------

def test_shadow_fresh_crosses_tp1_disagree(monkeypatch):
    monkeypatch.setenv("EXIT_TRIGGER_RECON_MODE", "shadow")
    records = []
    sc = _scanner(records)
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, tp1_sell_fraction=0.75))
    pm.open_position("TOK", entry_price=1.0, size_usd=20.0, entry_time=0.0,
                     address="mintTOK")
    pos = pm.get_position("TOK")

    from collections import deque
    sc._fast_samples = {"mintTOK": deque([1.07], maxlen=20)}   # fresh = +7% (> tp1 5%)
    sc._fast_samples_ts = {"mintTOK": 57.0}

    # The REAL stale tick saw +3% (below tp1) -> HOLD (no decision).
    stale_price = 1.03
    stale_decisions = pm.tick(token="TOK", current_price=stale_price, now=60.0)
    assert stale_decisions == []                       # stale = HOLD
    real_peak_after_stale = pos.peak_pnl_pct
    real_tp1_after_stale = pos.tp1_hit
    real_blob_after_stale = copy.deepcopy(pos.state_blob)

    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, stale_price,
                                 stale_decisions, now=60.0, vol_m5=None)

    assert len(records) == 1
    rec = records[0]
    assert rec["stale_reason"] == "HOLD"
    assert rec["fresh_reason"] == "TP1"
    assert rec["agree"] is False
    assert rec["pnl_delta"] > 0                        # fresh (+7) > stale (+3)
    assert rec["fresh_pnl"] == pytest.approx(7.0, abs=0.01)
    assert rec["stale_pnl"] == pytest.approx(3.0, abs=0.01)
    assert rec["secs_stale"] == pytest.approx(3.0, abs=0.01)

    # CRITICAL: the recon eval did NOT move the real position state. The copy
    # absorbed the tp1 mutation -- the real tp1_hit/peak are exactly what the
    # real stale tick left them.
    assert pos.tp1_hit == real_tp1_after_stale is False
    assert pos.peak_pnl_pct == real_peak_after_stale
    assert pos.state_blob == real_blob_after_stale


# ---- shadow: stale and fresh agree --------------------------------------

def test_shadow_agree(monkeypatch):
    monkeypatch.setenv("EXIT_TRIGGER_RECON_MODE", "shadow")
    records = []
    sc = _scanner(records)
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0))
    pm.open_position("TOK", entry_price=1.0, size_usd=20.0, entry_time=0.0,
                     address="mintTOK")
    pos = pm.get_position("TOK")

    from collections import deque
    # fresh ~ +2%, stale ~ +1.8% -> both HOLD (neither crosses tp1 or any stop)
    sc._fast_samples = {"mintTOK": deque([1.02], maxlen=20)}
    sc._fast_samples_ts = {"mintTOK": 58.0}

    stale_price = 1.018
    stale_decisions = pm.tick(token="TOK", current_price=stale_price, now=60.0)
    assert stale_decisions == []

    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, stale_price,
                                 stale_decisions, now=60.0, vol_m5=None)

    assert len(records) == 1
    rec = records[0]
    assert rec["stale_reason"] == "HOLD"
    assert rec["fresh_reason"] == "HOLD"
    assert rec["agree"] is True


# ---- no fresh sample -> skip, no record ---------------------------------

def test_no_fresh_sample_skips(monkeypatch):
    monkeypatch.setenv("EXIT_TRIGGER_RECON_MODE", "shadow")
    records = []
    sc = _scanner(records)
    pm = PerBotPositionManager(_cfg())
    pm.open_position("TOK", entry_price=1.0, size_usd=20.0, entry_time=0.0,
                     address="mintTOK")
    pos = pm.get_position("TOK")
    # no _fast_samples entry for this address
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, stale_price=1.03,
                                 stale_decisions=[], now=60.0, vol_m5=None)
    assert records == []


# ---- throttle/dedup: same transition within window logs once ------------

def test_dedup_same_transition_throttled(monkeypatch):
    monkeypatch.setenv("EXIT_TRIGGER_RECON_MODE", "shadow")
    monkeypatch.setenv("EXIT_TRIGGER_RECON_THROTTLE_SECS", "300")
    records = []
    sc = _scanner(records)
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0))
    pm.open_position("TOK", entry_price=1.0, size_usd=20.0, entry_time=0.0,
                     address="mintTOK")
    pos = pm.get_position("TOK")

    from collections import deque
    sc._fast_samples = {"mintTOK": deque([1.07], maxlen=20)}
    sc._fast_samples_ts = {"mintTOK": 57.0}

    # same (HOLD, TP1) transition twice, 10s apart -> only ONE record
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, 1.03, [],
                                 now=60.0, vol_m5=None)
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, 1.03, [],
                                 now=70.0, vol_m5=None)
    assert len(records) == 1
