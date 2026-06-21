import os

import math
from core.fast_watch import reprice_change_pct


def test_reprice_identity_when_price_unchanged():
    # P_fresh == P_snap -> fresh_pc == snapshot_pc (inversion fallback property)
    assert reprice_change_pct(-20.0, 0.1521, 0.1521) == -20.0


def test_reprice_recovers_toward_high():
    # Snapshot: price 0.1521 is -20% off the 1h high => ref = 0.1521/0.8 = 0.190125
    # Fresh price 0.1998 => fresh_pc = (0.1998/0.190125 - 1)*100 = +5.09%
    out = reprice_change_pct(-20.0, 0.1521, 0.1998)
    assert math.isclose(out, 5.0855, abs_tol=0.01)


def test_reprice_deeper_dip_when_price_falls_further():
    # Fresh price BELOW snapshot => deeper negative pc
    out = reprice_change_pct(-20.0, 0.1521, 0.1300)
    assert out < -20.0


def test_reprice_none_on_bad_prices():
    assert reprice_change_pct(-20.0, 0.0, 0.1998) is None
    assert reprice_change_pct(-20.0, 0.1521, 0.0) is None
    assert reprice_change_pct(-20.0, 0.1521, -1.0) is None


def test_scan_yield_every_default_is_tight(monkeypatch):
    # The redesign tightens the cooperative-yield default from 8 to 4 so the
    # sync sweep cannot block the loop long enough to starve a ~3s fast tick.
    monkeypatch.delenv("SCAN_YIELD_EVERY", raising=False)
    import feeds.dip_scanner as ds
    # The default is read inline; assert the literal default in source is 4.
    import inspect
    # Scan the module source for the default.
    msrc = inspect.getsource(ds)
    assert 'os.environ.get("SCAN_YIELD_EVERY", "4")' in msrc
