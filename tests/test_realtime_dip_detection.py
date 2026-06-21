import os


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
