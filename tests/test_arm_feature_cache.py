"""Arm-feature cache (#484 eval->fire latency cut): the chart-fingerprint memo
must (a) be deterministic, (b) change when the last candle's OHLC moves (no stale
reuse of an in-progress candle), (c) reuse on hit in 'on' mode, (d) always rebuild
in 'off' mode (byte-identical), (e) detect mismatches in 'verify' mode."""
import types
from collections import OrderedDict
from types import SimpleNamespace
from feeds.dip_scanner import DipScanner


def _candle(ot, c, h=None, l=None):
    return SimpleNamespace(open_time=ot, close=c, high=h if h is not None else c,
                           low=l if l is not None else c)


def _chart(c1=None, c5=None, c15=None):
    return SimpleNamespace(candles_1m=c1 or [], candles_5m=c5 or [], candles_15m=c15 or [])


def _host():
    h = SimpleNamespace()
    h._chart_feat_cache = OrderedDict()
    h._chart_feat_cache_max = 10
    h._chart_feat_hits = 0
    h._chart_feat_misses = 0
    # bind the real methods so _arm_feat_memo's internal self._chart_fp(...) resolves
    h._chart_fp = types.MethodType(DipScanner._chart_fp, h)
    h._arm_feat_memo = types.MethodType(DipScanner._arm_feat_memo, h)
    return h


def _fp(host, chart):
    return host._chart_fp(chart)


def _memo(host, addr, chart, tag, builder, mode):
    return host._arm_feat_memo(addr, chart, tag, builder, mode)


def test_fingerprint_deterministic_and_ohlc_sensitive():
    h = _host()
    ch_a = _chart(c5=[_candle(100, 1.0), _candle(160, 1.5)])
    ch_b = _chart(c5=[_candle(100, 1.0), _candle(160, 1.5)])      # identical
    ch_c = _chart(c5=[_candle(100, 1.0), _candle(160, 1.7)])      # last close moved (in-progress)
    ch_d = _chart(c5=[_candle(100, 1.0), _candle(160, 1.5), _candle(220, 1.6)])  # new candle
    assert _fp(h, ch_a) == _fp(h, ch_b)        # deterministic
    assert _fp(h, ch_a) != _fp(h, ch_c)        # OHLC move -> different fp (no stale reuse)
    assert _fp(h, ch_a) != _fp(h, ch_d)        # new candle -> different fp
    assert _fp(h, _chart()) is None            # no chart -> None


def test_off_mode_always_builds():
    h = _host()
    ch = _chart(c5=[_candle(100, 1.0)])
    calls = {"n": 0}
    def b():
        calls["n"] += 1
        return {"x": calls["n"]}
    _memo(h, "A", ch, "t2", b, "off")
    _memo(h, "A", ch, "t2", b, "off")
    assert calls["n"] == 2                       # never cached when off (byte-identical)
    assert len(h._chart_feat_cache) == 0


def test_on_mode_reuses_on_hit():
    h = _host()
    ch = _chart(c5=[_candle(100, 1.0)])
    calls = {"n": 0}
    def b():
        calls["n"] += 1
        return {"x": 42}
    r1 = _memo(h, "A", ch, "t2", b, "on")
    r2 = _memo(h, "A", ch, "t2", b, "on")        # same fp -> cache hit, no rebuild
    assert calls["n"] == 1
    assert r1 == r2 == {"x": 42}
    assert h._chart_feat_hits == 1 and h._chart_feat_misses == 1
    # returned dict is a COPY (caller .update() must not mutate the cache)
    r2["x"] = 999
    r3 = _memo(h, "A", ch, "t2", b, "on")
    assert r3["x"] == 42


def test_on_mode_rebuilds_when_chart_changes():
    h = _host()
    calls = {"n": 0}
    def b():
        calls["n"] += 1
        return {"x": calls["n"]}
    _memo(h, "A", _chart(c5=[_candle(100, 1.0)]), "t2", b, "on")
    _memo(h, "A", _chart(c5=[_candle(100, 1.2)]), "t2", b, "on")   # close moved -> rebuild
    assert calls["n"] == 2


def test_distinct_tags_and_addrs_isolated():
    h = _host()
    ch = _chart(c5=[_candle(100, 1.0)])
    _memo(h, "A", ch, "t2", lambda: {"v": "a2"}, "on")
    _memo(h, "A", ch, "t3", lambda: {"v": "a3"}, "on")
    _memo(h, "B", ch, "t2", lambda: {"v": "b2"}, "on")
    assert _memo(h, "A", ch, "t2", lambda: {"v": "X"}, "on") == {"v": "a2"}
    assert _memo(h, "A", ch, "t3", lambda: {"v": "X"}, "on") == {"v": "a3"}
    assert _memo(h, "B", ch, "t2", lambda: {"v": "X"}, "on") == {"v": "b2"}


def test_no_chart_never_caches():
    h = _host()
    calls = {"n": 0}
    def b():
        calls["n"] += 1
        return {"x": 1}
    _memo(h, "A", _chart(), "t2", b, "on")       # fp None -> always build
    _memo(h, "A", _chart(), "t2", b, "on")
    assert calls["n"] == 2
    assert len(h._chart_feat_cache) == 0


def test_lru_bound():
    h = _host()
    h._chart_feat_cache_max = 3
    for i in range(6):
        ch = _chart(c5=[_candle(100, float(i))])
        _memo(h, f"tok{i}", ch, "t2", lambda i=i: {"x": i}, "on")
    assert len(h._chart_feat_cache) <= 3
