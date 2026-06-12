"""Meta-allocator SHADOW (2026-06-12) — V1 state→family table + snapshot I/O.

Measure-only by construction: nothing in the buy path imports this module's
proposal; these tests pin the pre-registered table so it can't drift without
a deliberate (forward-evidence) change.
"""
import json

from core import meta_allocator as ma


def test_family_of():
    assert ma.family_of("young_probe_thinliq") == "young"
    assert ma.family_of("badday_flush_conviction") == "badday"
    assert ma.family_of("momentum_shadow") == "momentum"
    assert ma.family_of("timebox_probe") == "timebox"
    assert ma.family_of("smart_follow_k2") == "follow"
    assert ma.family_of("no_filters") is None
    assert ma.family_of(None) is None


def test_propose_green_tape_favors_momentum():
    m = ma.propose(sol_h24=2.3, breadth_neg=0.68)
    assert m["momentum"] == 1.5
    assert m["young"] == 1.0
    assert m["badday"] == 1.0


def test_propose_red_tape_narrow_breadth_favors_young_cuts_momentum():
    m = ma.propose(sol_h24=-2.0, breadth_neg=0.60)
    assert m["young"] == 1.5
    assert m["momentum"] == 0.5
    assert m["badday"] == 1.0


def test_propose_broad_red_favors_badday_cuts_young():
    m = ma.propose(sol_h24=-2.0, breadth_neg=0.80)
    assert m["badday"] == 1.5
    assert m["young"] == 0.5      # broad-red = dip edge OFF (49-day study)
    assert m["momentum"] == 0.5


def test_propose_unknown_state_is_flat():
    m = ma.propose(sol_h24=None, breadth_neg=None)
    assert all(v == 1.0 for v in m.values())


def test_shadow_snapshot_roundtrip(tmp_path):
    p = str(tmp_path / "shadow.jsonl")
    sh = ma.MetaAllocatorShadow(path=p)
    sh.SNAPSHOT_SECS = 0.0   # snapshot immediately
    sh.observe_cycle(sol_h24=2.0, breadth_neg=0.6, flush_count=4, launch_count=7)
    rows = [json.loads(l) for l in open(p)]
    assert len(rows) == 1
    r = rows[0]
    assert r["sol_h24"] == 2.0 and r["breadth_neg_h1"] == 0.6
    assert r["flush_envelope_per_cycle"] == 4 and r["launch_candidates"] == 7
    assert r["proposal"]["momentum"] == 1.5
    # accumulators reset after snapshot
    assert sh._sols == [] and sh._negs == []


def test_observe_never_raises_on_garbage():
    sh = ma.MetaAllocatorShadow(path="Z:/definitely/not/writable/x.jsonl")
    sh.SNAPSHOT_SECS = 0.0
    sh.observe_cycle(sol_h24="bad", breadth_neg=object(), flush_count="x",
                     launch_count=None)   # must not raise
