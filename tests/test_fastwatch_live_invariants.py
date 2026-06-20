# -*- coding: utf-8 -*-
"""Fast-watch x LIVE pre-live invariants (the untested fast-watch x live gap).

These guard the MONEY path the live fan-out uses (fast-watch survivors ->
_fast_route_decisions -> _execute_bot_buy -> _execute_bot_buy_live ->
_execute_swap_ultra). They assert, with the slow ~10-40s Ultra confirm SIMULATED:

  1. The _buy_fire_lock is held ACROSS the full live execute, so two concurrent
     fast-watch survivors (same token AND different tokens) cannot produce
     overlapping live buys / a double-buy.
  2. The 4% slippage cap (PROBE_ULTRA_SLIPPAGE_BPS) is actually passed into the
     Ultra order params (build_ultra_order_params).
  3. NO_FAST_PRICE_GATE_MODE=enforce blocks an unpriceable armed token on the live
     route; =shadow does not.
  4. BUY-REPRICE=enforce aborts a >5% run-up; =shadow does not.  (Driven here via
     the real _execute_bot_buy_live, complementing tests/test_buy_reprice.py.)

Run: python tests/test_fastwatch_live_invariants.py   (also collected by pytest)
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace as NS

from feeds.dip_scanner import DipScanner
from core.trader import build_ultra_order_params


results = []


def _t(name):
    def deco(fn):
        results.append((name, fn))
        return fn
    return deco


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 1. Lock held across the (slow) live execute: no overlap / no double-buy ──

def _lock_scanner():
    """A DipScanner skeleton whose _execute_bot_buy is replaced by a SLOW async
    that records start/end timestamps + per-token buy counts, simulating the
    10-40s Ultra confirm window. Drives the REAL _fast_route_decisions (which
    holds self._buy_fire_lock across the whole _execute_bot_buy call)."""
    sc = DipScanner.__new__(DipScanner)
    sc._buy_fire_lock = asyncio.Lock()
    sc._fast_armed = {}
    sc.trader = NS(private_key="")
    state = {"in_flight": 0, "max_concurrent": 0, "by_token": {}, "order": []}

    async def _slow_buy(decision, bundle):
        state["in_flight"] += 1
        state["max_concurrent"] = max(state["max_concurrent"], state["in_flight"])
        state["order"].append(("start", decision.token))
        # Simulate the long live-execute window (Ultra /execute confirm).
        await asyncio.sleep(0.05)
        state["by_token"][decision.token] = state["by_token"].get(decision.token, 0) + 1
        state["order"].append(("end", decision.token))
        state["in_flight"] -= 1

    sc._execute_bot_buy = _slow_buy
    sc._fw_record_hit = lambda *a, **k: None
    return sc, state


@_t("Lock serializes concurrent SAME-token fast-watch buys (no double-buy under latency)")
def t_same_token_no_overlap():
    sc, state = _lock_scanner()
    d1 = NS(token="TOK", address="mintTOK", bot_id="b1")
    d2 = NS(token="TOK", address="mintTOK", bot_id="b2")

    async def go():
        # Two concurrent survivors fire the SAME token while the execute is slow.
        await asyncio.gather(
            sc._fast_route_decisions([d1], None, [], False, "TOK"),
            sc._fast_route_decisions([d2], None, [], False, "TOK"),
        )
    _run(go())
    # The buy-fire lock must have serialized them: never two in flight at once.
    assert state["max_concurrent"] == 1, f"overlapping live buys! max_concurrent={state['max_concurrent']}"
    # Order must be fully nested (start,end,start,end) — never interleaved.
    assert state["order"] == [("start", "TOK"), ("end", "TOK"),
                              ("start", "TOK"), ("end", "TOK")], state["order"]


@_t("Lock serializes concurrent DIFFERENT-token fast-watch buys (no overlap)")
def t_diff_token_no_overlap():
    sc, state = _lock_scanner()
    d1 = NS(token="AAA", address="mintAAA", bot_id="b1")
    d2 = NS(token="BBB", address="mintBBB", bot_id="b2")

    async def go():
        await asyncio.gather(
            sc._fast_route_decisions([d1], None, [], False, "AAA"),
            sc._fast_route_decisions([d2], None, [], False, "BBB"),
        )
    _run(go())
    assert state["max_concurrent"] == 1, f"overlapping live buys across tokens! {state['max_concurrent']}"
    # No interleave: each token's start is immediately followed by its own end.
    for i in range(0, len(state["order"]), 2):
        assert state["order"][i][0] == "start" and state["order"][i + 1][0] == "end"
        assert state["order"][i][1] == state["order"][i + 1][1]


# ── 2. The 4% slippage cap reaches the Ultra order params ────────────────────

@_t("PROBE_ULTRA_SLIPPAGE_BPS (4%) is passed into build_ultra_order_params")
def t_slip_cap_in_order_params():
    # 400 bps = 4%; the live buy passes this as slippage_bps into the Ultra order.
    p = build_ultra_order_params("So111", "MINT", 1_000_000, "TAKER", 400)
    assert p.get("slippageBps") == 400, p
    # Omitted -> Ultra estimates its own (RTSE); the key must be absent, not 0.
    p2 = build_ultra_order_params("So111", "MINT", 1_000_000, "TAKER", None)
    assert "slippageBps" not in p2, p2


@_t("Live buy passes the slip cap THROUGH to _execute_swap_ultra")
def t_live_buy_passes_slip_cap():
    os.environ["PROBE_ULTRA_SLIPPAGE_BPS"] = "400"
    os.environ["BUY_REPRICE_MODE"] = "off"
    captured = {}

    sc = DipScanner.__new__(DipScanner)
    sc._recent_low_sync = lambda pair: None

    class _Tr:
        async def _usd_to_sol(self, usd): return usd / 100.0
        async def _check_sol_reserve(self, token): return True
        async def _get_token_balance_atomic(self, mint): return 0
        async def _get_token_decimals(self, mint): return 6
        async def _execute_swap_ultra(self, src, dst, lamports, slippage_bps=None, buy_context=False):
            captured["slippage_bps"] = slippage_bps
            captured["buy_context"] = buy_context
            return {"success": True, "out_amount": 1_000_000, "route": "t",
                    "signature": "SIG", "realized_slippage_pct": 0.1}
    sc.trader = _Tr()

    pm = NS(config=NS(max_concurrent_positions=3), open_count=0)
    pm.get_position = lambda token: None
    pm.open_position = lambda **kw: NS(state_blob={}, **{k: kw[k] for k in
                                       ("token", "entry_price", "size_usd")})
    dec = NS(token="TOK", address="mintTOK", pair_address="p", entry_price=1.0, local_low=None)
    r = _run(sc._execute_bot_buy_live(dec, pm, 30.0))
    assert r is not None
    assert captured.get("slippage_bps") == 400, captured
    # And the buy uses the short-backoff buy_context path.
    assert captured.get("buy_context") is True, captured


# ── 3. NO_FAST_PRICE gate: enforce blocks an unpriceable armed token ─────────

class _SentinelGate(dict):
    """A _buy_gate stand-in placed JUST AFTER the NO-FAST-PRICE gate. Reaching the
    regime buy-gate (_bg.get('block')) means the NO-FAST-PRICE gate did NOT return."""
    def __init__(self, flag):
        super().__init__(block=False)   # block=False -> regime gate is inert
        self._flag = flag
    def get(self, *a, **k):
        self._flag["v"] = True          # sentinel: we got past the NO-FAST-PRICE gate
        return super().get(*a, **k)


def _nfp_scanner(has_fresh):
    """Drives the REAL _execute_bot_buy through the NO-FAST-PRICE gate with valid
    capital+pm, detecting whether the gate returned via a sentinel _buy_gate placed
    immediately after it. The regime gate is inert (block=False) so the buy would
    proceed normally if not blocked."""
    sc = DipScanner.__new__(DipScanner)
    sc._fast_armed = {"mintTOK": {}}            # token IS armed (we are polling it)
    sc._addr_by_token = {"TOK": "mintTOK"}
    sc._has_fresh_fast_price = lambda addr: has_fresh
    proceeded = {"v": False}
    # Valid capital + pm so _execute_bot_buy does NOT early-return before the gate.

    class _Cap:
        def reserve_for_buy(self, usd):
            raise ValueError("stop-here-after-gate")   # halt cleanly well past the gate
    pm = NS(config=NS(momentum_mode=False))
    sc.bot_capitals = {"b1": _Cap()}
    sc.bot_position_managers = {"b1": pm}
    sc._buy_gate = _SentinelGate(proceeded)
    return sc, proceeded


@_t("NO_FAST_PRICE=enforce blocks an unpriceable armed token (no proceed)")
def t_nfp_enforce_blocks():
    os.environ["NO_FAST_PRICE_GATE_MODE"] = "enforce"
    sc, proceeded = _nfp_scanner(has_fresh=False)
    dec = NS(token="TOK", address="mintTOK", bot_id="b1")
    _run(sc._execute_bot_buy(dec, None))
    assert proceeded["v"] is False, "enforce did NOT block — proceeded past the gate"


def _drive_buy_to_gate(sc, dec):
    """Run _execute_bot_buy; the sentinel records gate-passage. Any later exception
    (from the stubbed-thin scanner past the gate) is irrelevant — the flag already set."""
    try:
        _run(sc._execute_bot_buy(dec, None))
    except Exception:
        pass


@_t("NO_FAST_PRICE=shadow does NOT block an unpriceable armed token")
def t_nfp_shadow_allows():
    os.environ["NO_FAST_PRICE_GATE_MODE"] = "shadow"
    sc, proceeded = _nfp_scanner(has_fresh=False)
    _drive_buy_to_gate(sc, NS(token="TOK", address="mintTOK", bot_id="b1"))
    assert proceeded["v"] is True, "shadow blocked the buy — must only log"


@_t("NO_FAST_PRICE=enforce allows a PRICEABLE armed token")
def t_nfp_enforce_allows_priceable():
    os.environ["NO_FAST_PRICE_GATE_MODE"] = "enforce"
    sc, proceeded = _nfp_scanner(has_fresh=True)   # fresh price present -> not blocked
    _drive_buy_to_gate(sc, NS(token="TOK", address="mintTOK", bot_id="b1"))
    assert proceeded["v"] is True, "enforce wrongly blocked a priceable token"


# ── 4. BUY-REPRICE enforce vs shadow on the live route ───────────────────────

def _reprice_scanner(fresh_price):
    sc = DipScanner.__new__(DipScanner)
    sc._recent_low_sync = lambda pair: None

    async def _fresh(token, address="", pair_address=""):
        return fresh_price
    sc._get_current_price_for = _fresh

    class _Tr:
        def __init__(self): self.swap_called = False
        async def _usd_to_sol(self, usd): return usd / 100.0
        async def _check_sol_reserve(self, token): return True
        async def _get_token_balance_atomic(self, mint): return 0
        async def _get_token_decimals(self, mint): return 6
        async def _execute_swap_ultra(self, src, dst, lamports, slippage_bps=None, buy_context=False):
            self.swap_called = True
            return {"success": True, "out_amount": 1_000_000, "route": "t",
                    "signature": "SIG", "realized_slippage_pct": 0.1}
    sc.trader = _Tr()
    return sc


def _pm():
    pm = NS(config=NS(max_concurrent_positions=3), open_count=0)
    pm.get_position = lambda token: None
    pm.open_position = lambda **kw: NS(state_blob={}, **{k: kw[k] for k in
                                       ("token", "entry_price", "size_usd")})
    return pm


@_t("BUY-REPRICE=enforce aborts a >5% run-up (swap never fires)")
def t_reprice_enforce_aborts():
    os.environ["BUY_REPRICE_MODE"] = "enforce"
    os.environ.pop("BUY_REPRICE_MAX_RUNUP", None)
    sc = _reprice_scanner(fresh_price=1.10)         # +10% > 5%
    dec = NS(token="TOK", address="mintTOK", pair_address="p", entry_price=1.0, local_low=None)
    r = _run(sc._execute_bot_buy_live(dec, _pm(), 30.0))
    assert r is None and sc.trader.swap_called is False, "enforce did not abort the run-up"


@_t("BUY-REPRICE=shadow does NOT abort a >5% run-up (swap fires)")
def t_reprice_shadow_allows():
    os.environ["BUY_REPRICE_MODE"] = "shadow"
    os.environ.pop("BUY_REPRICE_MAX_RUNUP", None)
    sc = _reprice_scanner(fresh_price=1.50)         # +50% would-abort, but shadow only logs
    dec = NS(token="TOK", address="mintTOK", pair_address="p", entry_price=1.0, local_low=None)
    r = _run(sc._execute_bot_buy_live(dec, _pm(), 30.0))
    assert r is not None and sc.trader.swap_called is True, "shadow wrongly aborted"


# ── pytest shims ─────────────────────────────────────────────────────────────

def _make_pytest(name, fn):
    def _wrapped():
        fn()
    _wrapped.__name__ = "test_" + name
    return _wrapped


for _i, (_name, _fn) in enumerate(results):
    globals()[f"test_inv_{_i}"] = _make_pytest(_name, _fn)


def main():
    print(f"Fast-watch x live invariant suite — {len(results)} tests\n")
    failed = []
    for name, fn in results:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"  FAIL  {name}\n        {e}")
        except Exception as e:
            failed.append(name)
            print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} FAILED — DO NOT DEPLOY LIVE")
        sys.exit(1)
    print(f"All {len(results)} passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
