"""Gap-capture re-pricing (honest paper-EV for gappy TP fills)."""
from core.gap_capture import realistic_exit_pnl_pct as r


def test_tp1_gapped_above_trigger_is_haircut():
    # TP1 trigger +5%, booked +47% (gapped). gap_capture 0.5 -> 5 + 0.5*42 = 26
    assert r("TP1 pnl=47.0% >= 5.0", 47.0, 5.0, 7.0, gap_capture=0.5) == 26.0
    # gap_capture 0.0 -> fill AT trigger
    assert r("TP1", 47.0, 5.0, 7.0, gap_capture=0.0) == 5.0
    # gap_capture 1.0 -> unchanged (current paper assumption)
    assert r("TP1", 47.0, 5.0, 7.0, gap_capture=1.0) == 47.0


def test_tp2_uses_tp2_trigger():
    # TP2 trigger +7%, booked +20% -> 7 + 0.5*13 = 13.5
    assert r("TP2 pnl=20% >= 7.0", 20.0, 5.0, 7.0, gap_capture=0.5) == 13.5


def test_tp_filled_near_trigger_unchanged():
    # booked +5.1% on a +5% trigger -> tiny gap, barely haircut
    assert r("TP1", 5.1, 5.0, 7.0, gap_capture=0.0) == 5.0   # capped at trigger
    assert r("TP1", 5.0, 5.0, 7.0, gap_capture=0.5) == 5.0   # exactly at trigger, no gap


def test_non_tp_exits_unchanged():
    for reason in ("trail pullback 2pp", "slow_bleed hold=70min", "hard stop pnl=-15%",
                   "stall_exit", "open_at_resolve", None):
        assert r(reason, -8.3, 5.0, 7.0, gap_capture=0.0) == -8.3   # losses untouched
    assert r("trail", 12.0, 5.0, 7.0, gap_capture=0.0) == 12.0       # trail win untouched (no fixed trigger)


def test_clamp_and_failsoft():
    assert r("TP1", 47.0, 5.0, 7.0, gap_capture=5.0) == 47.0   # clamp >1 -> 1.0 (unchanged)
    assert r("TP1", 47.0, 5.0, 7.0, gap_capture=-1.0) == 5.0   # clamp <0 -> 0.0 (trigger)
    assert r("TP1", None, 5.0, 7.0) is None                     # bad pnl -> passthrough
    assert r("TP1", 47.0, None, 7.0) == 47.0                    # bad trigger -> unchanged
