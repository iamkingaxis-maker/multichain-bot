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
