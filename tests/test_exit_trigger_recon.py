# -*- coding: utf-8 -*-
"""EXIT-TRIGGER REACHABILITY recon (MEASUREMENT ONLY, 2026-06-28).

Paper evaluates ALL exit triggers inside pm.tick at the ~150s-STALE main-scan
snapshot price; LIVE evaluates the SAME pm.tick on the ~2s FRESH fast-watch
price. Same position, different price -> a DIFFERENT trigger can fire at a
different pnl. EXIT_TRIGGER_RECON_MODE=off|shadow (default off) re-runs the EXACT
same trigger logic on the fresh price against a DEEP COPY of the position taken
BEFORE the real tick (pre-tick state) and logs whether the fresh decision DIFFERS
from the stale one.

CONTRACT: measurement only. NEVER sells, NEVER mutates the real position or the
real manager, NEVER changes an exit. Default off = byte-identical (no records).
"""
from collections import deque
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
    # NO enforce path here -- it's measurement only; the recon method never acts
    # on a decision regardless of the resolved value.
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
    sc._fast_samples = {"mintTOK": deque([1.07], maxlen=20)}  # would cross tp1

    before = copy.deepcopy(pos)
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, copy.deepcopy(pos),
                                 stale_price=1.03, stale_decisions=[],
                                 now=60.0, vol_m5=None)
    assert records == []
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

    sc._fast_samples = {"mintTOK": deque([1.07], maxlen=20)}   # fresh = +7% (> tp1 5%)
    sc._fast_samples_ts = {"mintTOK": 57.0}

    stale_price = 1.03
    snap = copy.deepcopy(pos)                          # PRE-TICK snapshot
    stale_decisions = pm.tick(token="TOK", current_price=stale_price, now=60.0)
    assert stale_decisions == []                       # stale = HOLD (+3% < tp1)
    real_peak_after_stale = pos.peak_pnl_pct
    real_tp1_after_stale = pos.tp1_hit
    real_blob_after_stale = copy.deepcopy(pos.state_blob)

    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, snap, stale_price,
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

    # CRITICAL: the recon eval did NOT move the real position state. The snapshot
    # copy absorbed the tp1 mutation; the real tp1_hit/peak/state_blob are exactly
    # what the real stale tick left them.
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

    # fresh ~ +2%, stale ~ +1.8% -> both HOLD (neither crosses tp1 or any stop)
    sc._fast_samples = {"mintTOK": deque([1.02], maxlen=20)}
    sc._fast_samples_ts = {"mintTOK": 58.0}

    stale_price = 1.018
    snap = copy.deepcopy(pos)
    stale_decisions = pm.tick(token="TOK", current_price=stale_price, now=60.0)
    assert stale_decisions == []

    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, snap, stale_price,
                                 stale_decisions, now=60.0, vol_m5=None)

    assert len(records) == 1
    rec = records[0]
    assert rec["stale_reason"] == "HOLD"
    assert rec["fresh_reason"] == "HOLD"
    assert rec["agree"] is True


# ---- pre-tick state: fresh eval must NOT see the peak the stale tick advanced --

def test_fresh_eval_uses_pre_tick_state(monkeypatch):
    """The stale tick advances the REAL peak (10% -> 20%); the post-TP1 trail
    threshold is peak - trail_pp. The fresh re-eval must start from the PRE-TICK
    peak (10 -> threshold 7), so a fresh pnl of +8% is ABOVE the trail and HOLDs.
    If the recon (wrongly) used the post-tick peak (20 -> threshold 17), +8% would
    be at/under the trail and fire POST_TP1_TRAIL. Asserting fresh_reason==HOLD
    proves the pre-tick snapshot (not the contaminated post-tick state) was used.
    """
    monkeypatch.setenv("EXIT_TRIGGER_RECON_MODE", "shadow")
    records = []
    sc = _scanner(records)
    # tp2_pct high so the stale +20% can't fire TP2 and muddy stale_reason.
    pm = PerBotPositionManager(_cfg(tp1_pct=5.0, trail_pp=3.0, tp2_pct=99.0))
    pm.open_position("TOK", entry_price=1.0, size_usd=20.0, entry_time=0.0,
                     address="mintTOK")
    pos = pm.get_position("TOK")
    pos.tp1_hit = True               # post-TP1 so the trailing stop is live
    pos.peak_pnl_pct = 10.0          # pre-tick peak
    pos.peak_pnl_at_secs = 0

    sc._fast_samples = {"mintTOK": deque([1.08], maxlen=20)}   # fresh = +8%
    sc._fast_samples_ts = {"mintTOK": 58.0}

    stale_price = 1.20               # +20% advances the real peak to 20
    snap = copy.deepcopy(pos)        # PRE-TICK snapshot (peak still 10)
    stale_decisions = pm.tick(token="TOK", current_price=stale_price, now=60.0)
    assert pos.peak_pnl_pct == pytest.approx(20.0)   # real peak advanced
    assert stale_decisions == []                     # +20% > trail(20-3=17) -> HOLD

    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, snap, stale_price,
                                 stale_decisions, now=60.0, vol_m5=None)

    assert len(records) == 1
    rec = records[0]
    # fresh +8% vs PRE-TICK peak 10 -> trail threshold 7 -> 8 > 7 -> HOLD.
    # (post-tick peak 20 would give threshold 17 -> 8 <= 17 -> TRAIL.)
    assert rec["fresh_reason"] == "HOLD"
    assert rec["stale_reason"] == "HOLD"
    # real position peak unchanged by the recon (still the stale-tick value 20)
    assert pos.peak_pnl_pct == pytest.approx(20.0)


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
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, copy.deepcopy(pos),
                                 stale_price=1.03, stale_decisions=[],
                                 now=60.0, vol_m5=None)
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

    sc._fast_samples = {"mintTOK": deque([1.07], maxlen=20)}
    sc._fast_samples_ts = {"mintTOK": 57.0}

    # same (HOLD, TP1) transition twice, 10s apart -> only ONE record. Fresh
    # snapshot each call (mirrors production: a new deepcopy per tick).
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, copy.deepcopy(pos),
                                 1.03, [], now=60.0, vol_m5=None)
    sc._maybe_exit_trigger_recon("badday_flush", pm, pos, copy.deepcopy(pos),
                                 1.03, [], now=70.0, vol_m5=None)
    assert len(records) == 1
