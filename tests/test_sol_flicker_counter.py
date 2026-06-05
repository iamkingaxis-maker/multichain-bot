"""SOL-flicker counter (2026-06-05 flicker-gate tune): causal flk_1h trailing
count of SOL-gate clear->block flips. The hard entry BLOCK was tuned + REJECTED
(2-day chop artifact); flk_1h drives a measure-only shadow + a winner-safe scale-in
deferral. These exercise the pure counter logic via unbound methods (the methods
touch only self._sol_flip_log / self._sol_blocked_prev)."""
from types import SimpleNamespace
from feeds.dip_scanner import DipScanner

upd = DipScanner._update_sol_flip_log
flk = DipScanner._sol_flk_1h


def test_flk_zero_when_uninitialized():
    assert flk(SimpleNamespace(), 1000) == 0


def test_flip_appended_on_clear_to_block():
    s = SimpleNamespace()
    upd(s, {"sol_pc_h6": 0.1}, 1000)      # clear
    assert flk(s, 1000) == 0
    upd(s, {"sol_pc_h6": -0.5}, 1100)     # clear -> block: 1 flip
    assert flk(s, 1100) == 1


def test_no_double_append_while_blocked():
    s = SimpleNamespace()
    upd(s, {"sol_pc_h6": -0.5}, 1000)     # default-clear -> block: flip
    upd(s, {"sol_pc_h6": -0.6}, 1100)     # still blocked: no new flip
    upd(s, {"sol_pc_h6": -0.7}, 1200)
    assert flk(s, 1200) == 1


def test_second_flip_after_unblock():
    s = SimpleNamespace()
    upd(s, {"sol_pc_h6": -0.5}, 1000)     # flip 1
    upd(s, {"sol_pc_h6": 0.2}, 1100)      # block -> clear
    upd(s, {"sol_pc_h6": -0.5}, 1200)     # clear -> block: flip 2
    assert flk(s, 1200) == 2


def test_each_gate_dimension_triggers_block():
    s1 = SimpleNamespace(); upd(s1, {"sol_pc_h1": -0.8}, 1000); assert flk(s1, 1000) == 1   # h1<-0.7
    s2 = SimpleNamespace(); upd(s2, {"sol_pc_m5": -1.5}, 1000); assert flk(s2, 1000) == 1   # m5<-1.0
    s3 = SimpleNamespace()  # all above thresholds -> no block
    upd(s3, {"sol_pc_h6": -0.2, "sol_pc_h1": -0.5, "sol_pc_m5": -0.5}, 1000)
    assert flk(s3, 1000) == 0


def test_trailing_window_excludes_old_flips():
    s = SimpleNamespace()
    upd(s, {"sol_pc_h6": -0.5}, 1000)     # flip @1000
    upd(s, {"sol_pc_h6": 0.2}, 1100)
    upd(s, {"sol_pc_h6": -0.5}, 2000)     # flip @2000
    # at now=4601, cut=1001 -> the 1000 flip is just outside the hour, 2000 inside
    assert flk(s, 4601) == 1


def test_prune_bounds_memory():
    s = SimpleNamespace()
    upd(s, {"sol_pc_h6": -0.5}, 1000)
    upd(s, {"sol_pc_h6": 0.2}, 1100)
    upd(s, {"sol_pc_h6": -0.5}, 5700)     # >1h after the 1000 flip -> 1000 pruned on this update
    assert s._sol_flip_log == [5700]
    assert flk(s, 5700) == 1


def test_empty_or_none_features_are_noop():
    s = SimpleNamespace()
    upd(s, {"sol_pc_h6": -0.5}, 1000)     # block, flip 1
    upd(s, {}, 1100)                       # empty -> no-op (preserve prev)
    upd(s, None, 1200)                     # None -> no-op
    upd(s, {"sol_pc_h6": -0.6}, 1300)     # still blocked (prev preserved) -> no new flip
    assert flk(s, 1300) == 1
