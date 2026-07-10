# tests/test_decimals_neg_cache.py
"""Regression: the 2026-07-10 429 storm / fleet drought.

Failed decimals lookups were never cached ('retry next time'), so a restart
with a big armed set made the every-tick prewarm hammer getAccountInfo at
~100 req/s -> permanent 429s -> HTTP stack starved -> fast-watch polled=0 ->
no fills fleet-wide. Failures must be negative-cached (DECIMALS_NEG_TTL_SECS)
so one bad episode cannot self-sustain."""
import asyncio

from core.trader import Trader


def _bare_trader():
    t = Trader.__new__(Trader)          # no __init__: unit-test the method only
    t._token_decimals_cache = {}
    t.rpc_urls = ["http://unit.test"]
    return t


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class TestDecimalsNegCache:
    def test_failure_is_negative_cached(self):
        t = _bare_trader()
        calls = []

        async def failing_rpc(payload, total_timeout=10.0):
            calls.append(payload)
            return None                  # all RPCs 429/down

        t._post_rpc = failing_rpc
        assert _run(t._get_token_decimals("MintA")) == 6
        assert _run(t._get_token_decimals("MintA")) == 6
        assert _run(t._get_token_decimals("MintA")) == 6
        assert len(calls) == 1           # ONE RPC attempt, then neg-cache

    def test_success_still_caches_permanently(self):
        t = _bare_trader()
        calls = []

        async def ok_rpc(payload, total_timeout=10.0):
            calls.append(payload)
            return {"result": {"value": {"data": {
                "parsed": {"info": {"decimals": 9}}}}}}

        t._post_rpc = ok_rpc
        assert _run(t._get_token_decimals("MintB")) == 9
        assert _run(t._get_token_decimals("MintB")) == 9
        assert len(calls) == 1

    def test_neg_ttl_expiry_retries(self, monkeypatch):
        t = _bare_trader()
        calls = []

        async def failing_rpc(payload, total_timeout=10.0):
            calls.append(payload)
            return None

        t._post_rpc = failing_rpc
        monkeypatch.setenv("DECIMALS_NEG_TTL_SECS", "0")   # expire instantly
        assert _run(t._get_token_decimals("MintC")) == 6
        assert _run(t._get_token_decimals("MintC")) == 6
        assert len(calls) == 2           # TTL 0 -> allowed to retry

    def test_failure_does_not_poison_other_mints(self):
        t = _bare_trader()

        async def rpc(payload, total_timeout=10.0):
            mint = payload["params"][0]
            if mint == "BadMint":
                return None
            return {"result": {"value": {"data": {
                "parsed": {"info": {"decimals": 5}}}}}}

        t._post_rpc = rpc
        assert _run(t._get_token_decimals("BadMint")) == 6
        assert _run(t._get_token_decimals("GoodMint")) == 5
