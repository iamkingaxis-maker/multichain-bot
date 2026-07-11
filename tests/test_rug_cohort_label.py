"""rug_cohort_label timestamp parsing (2026-07-11 adversarial review).

/api/trades `time` is an ISO-8601 STRING; the original float() parse raised
for every trade, so entry_ts was None across the whole cohort and the 24h
maturation gate never applied — the day-one 198-mint cohort was labeled
IMMEDIATELY (premature permanent labels). These tests pin the fix.
"""
from scripts.rug_cohort_label import _ts_float


def test_iso_string_parses():
    # the exact shape /api/trades returns
    from datetime import datetime
    ts = _ts_float("2026-07-01T19:28:22.765486+00:00")
    want = datetime.fromisoformat("2026-07-01T19:28:22.765486+00:00").timestamp()
    assert ts == want


def test_iso_z_suffix_parses():
    assert _ts_float("2026-07-01T19:28:22Z") is not None


def test_epoch_number_passthrough():
    assert _ts_float(1782847702.5) == 1782847702.5
    assert _ts_float("1782847702.5") == 1782847702.5


def test_junk_and_none_return_none():
    assert _ts_float(None) is None
    assert _ts_float("not-a-date") is None
    assert _ts_float({}) is None
