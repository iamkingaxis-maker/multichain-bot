# tests/test_hl_ws_pump.py — WS-tick pump for HL-confirm (2026-07-06)
from core.fast_watch import pump_ws_ticks, hl_confirm_state


def test_consumes_only_new_ticks():
    prices = {"A": (1.0, 100.0)}
    hl, seen = {}, {}
    n1 = pump_ws_ticks(["A"], lambda a: prices.get(a), hl, seen, now_mono=10.0)
    assert n1 == 1 and hl["A"]["low"] == 1.0
    # same ts again -> not consumed
    n2 = pump_ws_ticks(["A"], lambda a: prices.get(a), hl, seen, now_mono=11.0)
    assert n2 == 0
    # newer tick, lower price -> low updates
    prices["A"] = (0.9, 101.0)
    n3 = pump_ws_ticks(["A"], lambda a: prices.get(a), hl, seen, now_mono=12.0)
    assert n3 == 1 and hl["A"]["low"] == 0.9


def test_confirm_flows_from_ws_ticks():
    hl, seen = {}, {}
    ticks = [(1.0, 1.0, 0.0), (1.02, 2.0, 10.0), (1.02, 3.0, 200.0)]
    for usd, ts, mono in ticks:
        pump_ws_ticks(["A"], lambda a, u=usd, t=ts: (u, t), hl, seen, mono)
    assert hl_confirm_state(hl["A"], 205.0, hold_secs=120, stale_secs=30) == "CONFIRMED"


def test_missing_price_and_errors_ignored():
    hl, seen = {}, {}
    def boom(a):
        raise RuntimeError("rpc")
    assert pump_ws_ticks(["A"], boom, hl, seen, 0.0) == 0
    assert pump_ws_ticks(["A"], lambda a: None, hl, seen, 0.0) == 0
    assert hl == {}


def test_prunes_disarmed_when_large():
    hl, seen = {}, {k: float(i) for i, k in enumerate(f"m{i}" for i in range(600))}
    pump_ws_ticks(["m1"], lambda a: None, hl, seen, 0.0)
    assert len(seen) <= 512 and "m1" in seen


class TestBucketSecsParam:
    """30s-bucket variant (price-trough validation 2026-07-06): pump_ws_ticks
    must thread bucket_secs through to hl_confirm_update so the alt map's
    higher-lows rotate on 30s boundaries."""

    def test_bucket_secs_reaches_state(self):
        from core.fast_watch import pump_ws_ticks, hl_confirm_state
        hl, seen = {}, {}
        ticks = [(1.00, 1.0), (0.90, 31.0), (0.95, 62.0), (0.96, 63.0)]
        # 30s buckets: bucket ids at now=100.. rotate per call below
        now = [0.0]
        i = [0]

        def lookup(addr):
            p, ts = ticks[i[0]]
            return (p, ts)

        # feed 4 ticks at nows 10, 40, 70, 71 -> 30s buckets 0,1,2,2
        for n, (p, ts) in zip([10.0, 40.0, 70.0, 71.0], ticks):
            i[0] = ticks.index((p, ts))
            pump_ws_ticks(["a"], lookup, hl, seen, n, bucket_secs=30.0)
        st = hl["a"]
        # bucket 2 (low .95/.96) > bucket 1 (low .90) -> higher low formed
        assert st["prev_bkt_low"] == 0.90 and st["bkt_low"] == 0.95
        assert hl_confirm_state(st, 72.0) == "CONFIRMED"

    def test_default_bucket_unchanged_60s(self):
        from core.fast_watch import pump_ws_ticks
        hl, seen = {}, {}
        vals = iter([(1.0, 1.0), (0.9, 2.0)])

        def lookup(addr):
            return next(vals)

        # 60s default: nows 10 and 40 land in the SAME bucket -> no rotation
        pump_ws_ticks(["a"], lookup, hl, seen, 10.0)
        pump_ws_ticks(["a"], lookup, hl, seen, 40.0)
        assert hl["a"].get("prev_bkt_low") is None
