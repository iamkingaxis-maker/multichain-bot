# tests/test_young_tape_shadow.py
"""Young tape shadow metrics (2026-07-03 launch-arc mine).

Trough signals: recoverers keep a live tape (15/15 minute-bars printing)
while corpses go silent (1/15); depth is anti-predictive (0/9 troughs deeper
than -85% recovered). Pure function; fail-soft on malformed input.
"""
from core.young_token_probe import tape_absorption_metrics, tape_shadow_mode

NOW = 1_000_000.0


def bars(n_recent, price=1.0, peak_high=None, spread_mins=15):
    """n_recent bars inside the last spread_mins, newest last."""
    rows = []
    for i in range(n_recent):
        ts = NOW - (n_recent - 1 - i) * (spread_mins * 60 / max(1, n_recent))
        rows.append([ts, price, peak_high or price, price, price, 100.0])
    return rows


class TestBarsPrinted:
    def test_live_tape_counts_15(self):
        m = tape_absorption_metrics(bars(15), NOW)
        assert m["bars_printed_15"] == 15
        assert m["tape_dead"] is False

    def test_dead_tape_flagged(self):
        # one old bar + one recent = silent trough
        rows = [[NOW - 3600, 1, 1, 1, 1, 5], [NOW - 60, 1, 1, 1, 1, 5]]
        m = tape_absorption_metrics(rows, NOW)
        assert m["bars_printed_15"] == 1
        assert m["tape_dead"] is True

    def test_threshold_boundary(self):
        m = tape_absorption_metrics(bars(8), NOW)
        assert m["tape_dead"] is False
        m = tape_absorption_metrics(bars(7), NOW)
        assert m["tape_dead"] is True


class TestRugFloor:
    def test_deep_drawdown_flagged(self):
        # peak high 1.0, last close 0.10 -> -90%
        rows = [[NOW - 3000, 1.0, 1.0, 0.9, 1.0, 50]] + \
               [[NOW - 60, 0.1, 0.12, 0.1, 0.10, 10]]
        m = tape_absorption_metrics(rows, NOW)
        assert m["dd_from_peak_pct"] <= -85
        assert m["rug_floor"] is True

    def test_normal_dip_passes(self):
        rows = [[NOW - 3000, 1.0, 1.0, 0.9, 1.0, 50]] + \
               [[NOW - 60, 0.6, 0.62, 0.6, 0.60, 10]]
        m = tape_absorption_metrics(rows, NOW)
        assert m["rug_floor"] is False


class TestFailSoft:
    def test_empty_and_garbage(self):
        assert tape_absorption_metrics([], NOW) == {}
        assert tape_absorption_metrics(None, NOW) == {}
        assert tape_absorption_metrics([["x"], [1]], NOW) == {}

    def test_zero_prices_ignored(self):
        rows = [[NOW - 60, 0, 0, 0, 0, 0]]
        m = tape_absorption_metrics(rows, NOW)
        # bar counts as printed but no dd computable
        assert m["bars_printed_15"] == 1
        assert "dd_from_peak_pct" not in m


def test_mode_default_on(monkeypatch):
    monkeypatch.delenv("YOUNG_TAPE_SHADOW_MODE", raising=False)
    assert tape_shadow_mode() == "on"
    monkeypatch.setenv("YOUNG_TAPE_SHADOW_MODE", "off")
    assert tape_shadow_mode() == "off"
