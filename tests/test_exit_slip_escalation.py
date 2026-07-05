# tests/test_exit_slip_escalation.py
"""Escalating exit slippage (2026-07-05 tail audit).

A reverted urgent exit at a flat 3% cap re-quotes at the same cap while the
token crashes; after 3 failures the position rides the dump. The schedule
escalates urgent retries (300->800->1500 bps) so the LAST resort lands.
Also: bails/floors/velocity exits are now classified urgent (they sold at
the 1% cap into crashes before).
"""
from core.trader import exit_slippage_bps_for_attempt as f


class TestSchedule:
    def test_urgent_escalates(self, monkeypatch):
        monkeypatch.delenv("EXIT_SLIP_ESCALATION", raising=False)
        assert [f(True, a) for a in (0, 1, 2)] == [300, 800, 1500]

    def test_normal_stays_tight(self, monkeypatch):
        monkeypatch.delenv("EXIT_SLIP_ESCALATION", raising=False)
        assert [f(False, a) for a in (0, 1, 2)] == [100, 100, 300]

    def test_clamps_past_schedule(self):
        assert f(True, 7) == 1500
        assert f(False, 7) == 300

    def test_garbage_attempt_is_first_step(self):
        assert f(True, None) == 300
        assert f(True, -3) == 300

    def test_kill_switch_restores_legacy(self, monkeypatch):
        monkeypatch.setenv("EXIT_SLIP_ESCALATION", "off")
        assert f(True, 2) == 300
        assert f(False, 2) == 100


def test_urgent_classification_covers_bails():
    # the classifier lives inline; assert the source carries the new classes
    import pathlib
    src = pathlib.Path("core/trader.py").read_text(encoding="utf-8")
    seg = src[src.find("_is_urgent_exit = "):][:400]
    for word in ('"bail"', '"floor"', '"velocity"', '"stop"', '"manual"'):
        assert word in seg, word
