# tests/test_rpc_budget.py
"""RPC-quota circuit breaker + sell-path balance-read retry (2026-07-10
mogdog postmortem: background prewarm burned the shared free-RPC quota and a
profitable live exit's balance read 429'd across the whole rotation)."""
import asyncio

from core.rpc_budget import RpcBudget
from core.trader import Trader

T0 = 1_000_000.0


class TestBreaker:
    def test_starts_closed(self):
        b = RpcBudget()
        assert b.background_allowed(T0) is True

    def test_trips_on_burst(self):
        b = RpcBudget(window_secs=30, trip_429s=8, cooldown_secs=60)
        for i in range(8):
            b.report_429(T0 + i)
        assert b.tripped(T0 + 8) is True
        assert b.background_allowed(T0 + 8) is False

    def test_below_threshold_stays_closed(self):
        b = RpcBudget(window_secs=30, trip_429s=8)
        for i in range(7):
            b.report_429(T0 + i)
        assert b.background_allowed(T0 + 7) is True

    def test_slow_drip_outside_window_never_trips(self):
        b = RpcBudget(window_secs=30, trip_429s=8)
        for i in range(20):
            b.report_429(T0 + i * 10)   # one per 10s: max 3 in any 30s window
        assert b.background_allowed(T0 + 200) is True

    def test_recovers_after_cooldown(self):
        b = RpcBudget(window_secs=30, trip_429s=8, cooldown_secs=60)
        for i in range(8):
            b.report_429(T0 + i)
        assert b.tripped(T0 + 10) is True
        # last 429 lands at T0+7 -> cooldown runs to T0+67
        assert b.tripped(T0 + 7 + 59.9) is True     # still inside cooldown
        assert b.tripped(T0 + 7 + 60.1) is False    # 60s quiet -> recovered
        assert b.background_allowed(T0 + 7 + 60.1) is True

    def test_429_during_cooldown_extends_it(self):
        b = RpcBudget(window_secs=30, trip_429s=8, cooldown_secs=60)
        for i in range(8):
            b.report_429(T0 + i)
        b.report_429(T0 + 50)                        # storm still alive
        assert b.tripped(T0 + 8 + 61) is True        # clock restarts from t=50
        assert b.tripped(T0 + 50 + 61) is False

    def test_env_kill_switch(self, monkeypatch):
        b = RpcBudget(trip_429s=1)
        b.report_429(T0)
        monkeypatch.setenv("RPC_BUDGET_BREAKER", "off")
        assert b.background_allowed(T0 + 1) is True


def _bare_trader():
    t = Trader.__new__(Trader)
    t._token_decimals_cache = {}
    t.private_key = "x"                       # live-mode gate for balance read
    t._get_public_key = lambda: "OwnerPubkey11111111111111111111111111111111"
    return t


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _ok_response(amount):
    return {"result": {"value": [{"account": {"data": {"parsed": {"info": {
        "tokenAmount": {"amount": str(amount)}}}}}}]}}


class TestBalanceReadRetry:
    def test_transient_failure_retried_in_call(self):
        t = _bare_trader()
        calls = []

        async def rpc(payload, total_timeout=10.0):
            calls.append(1)
            if len(calls) == 1:
                return None                    # first rotation: all 429
            return _ok_response(52737780907)

        t._post_rpc = rpc
        assert _run(t._get_token_balance_atomic("Mint")) == 52737780907
        assert len(calls) == 2                 # failed once, retried, succeeded

    def test_persistent_failure_returns_minus_one(self):
        t = _bare_trader()
        calls = []

        async def rpc(payload, total_timeout=10.0):
            calls.append(1)
            return None

        t._post_rpc = rpc
        assert _run(t._get_token_balance_atomic("Mint")) == -1
        assert len(calls) == 3                 # capped attempts, then honest fail

    def test_genuine_zero_not_retried(self):
        t = _bare_trader()
        calls = []

        async def rpc(payload, total_timeout=10.0):
            calls.append(1)
            return {"result": {"value": []}}   # success: genuinely no account

        t._post_rpc = rpc
        assert _run(t._get_token_balance_atomic("Mint")) == 0
        assert len(calls) == 1


class TestPrewarmGated:
    def test_prewarm_skipped_while_tripped(self, monkeypatch):
        import core.rpc_budget as rb
        t = _bare_trader()
        calls = []

        async def rpc(payload, total_timeout=10.0):
            calls.append(1)
            return None

        t._post_rpc = rpc
        monkeypatch.setattr(rb, "GLOBAL", RpcBudget(trip_429s=1))
        rb.GLOBAL.report_429()                 # trip immediately
        _run(t.prewarm_decimals("ColdMint"))
        assert calls == []                     # background consumer paused
