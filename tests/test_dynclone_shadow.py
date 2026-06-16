# -*- coding: utf-8 -*-
"""Dynamic-clone SHADOW (chameleon mission 2026-06-16).

The design+backtest (wf_b292581a) was NO-GO on enforcing a 15-min best-bot clone (it loses to
running the best static bot). So we ship a SHADOW logger only: fleet_meta_bus.best_live_bot ranks
the best ELIGIBLE bot, meta_chameleon logs the would-clone pick (no mutation, enforce unbuilt).
These cover the per-bot ranker + the eligibility filter."""
import time
from core import fleet_meta_bus as bus
from core.meta_chameleon import _clone_eligible


def test_clone_eligible_excludes_the_right_bots():
    # real strategy bots: eligible
    assert _clone_eligible("badday_flush_conviction") is True
    assert _clone_eligible("badday_flush_convex") is True
    assert _clone_eligible("champion_defender_v4") is True
    # self / probes / live / A-B clones: excluded
    assert _clone_eligible("meta_chameleon") is False
    assert _clone_eligible("meta_chameleon_x") is False
    assert _clone_eligible("timebox_probe") is False        # probe substring
    assert _clone_eligible("young_probe_late") is False
    assert _clone_eligible("badday_flush_conviction_live") is False   # live substring
    assert _clone_eligible("badday_flush_nf15") is False    # A/B clone suffix
    assert _clone_eligible("champion_defender_v3_pch1le5") is False   # A/B clone suffix


def test_best_live_bot_ranks_eligible_and_respects_min_n():
    bus._ring.clear()
    now = time.time()
    # botA +5/tr, botB +2/tr (both >= MIN_N), meta_chameleon +99 (must be excluded by predicate)
    for _ in range(10):
        bus._ring.append((now - 60, "fam", 5.0, "botA"))
        bus._ring.append((now - 60, "fam", 2.0, "botB"))
        bus._ring.append((now - 60, "fam", 99.0, "meta_chameleon"))
    b = bus.best_live_bot(now, lambda x: not x.startswith("meta_chameleon"))
    assert b is not None and b[0] == "botA", b   # +5 beats +2; chameleon filtered out
    assert b[1] == 5.0
    # MIN_N gate: a single-trade bot is not rankable
    bus._ring.clear()
    bus._ring.append((now - 60, "fam", 100.0, "botC"))
    assert bus.best_live_bot(now) is None
    bus._ring.clear()
