"""Unit tests for the 2026-07-13 quote-leg latency levers.

Attribution: a paper fill's stamped lat_quote_s (median ~1.06s) is TWO
sequential batched QuoterV2 POSTs — quote_buy then the RT-cost quote_sell of
the buy's exact output. These levers (all env-gated, default = prior behavior)
attack that: RH_QUOTE_TIMEOUT_S (fast-fail the RPC tail), RH_QUOTE_FALLBACK
(skip the slow sequential sweep on a batch miss), and RH_RT_COMBINED (fold both
POSTs into one via build_roundtrip_quote_batch). PURE / mocked — no network.
"""
import os
from unittest.mock import MagicMock

import pytest

from core.rh_execution import (
    FEE_TIERS,
    QUOTER_V2,
    WETH9,
    RhExecutor,
    RhQuote,
    _quote_timeout_s,
    build_roundtrip_quote_batch,
    parse_roundtrip_quote_batch,
)

TOKEN = "0x1111111111111111111111111111111111111111"


def _u256(n: int) -> str:
    return "0x" + format(n, "064x")


# ── RH_QUOTE_TIMEOUT_S ───────────────────────────────────────────────────────
class TestQuoteTimeout:
    def test_default_is_current_behavior(self, monkeypatch):
        monkeypatch.delenv("RH_QUOTE_TIMEOUT_S", raising=False)
        assert _quote_timeout_s() == 10.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("RH_QUOTE_TIMEOUT_S", "2.5")
        assert _quote_timeout_s() == 2.5

    def test_garbage_and_nonpositive_fall_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RH_QUOTE_TIMEOUT_S", "not-a-number")
        assert _quote_timeout_s() == 10.0
        monkeypatch.setenv("RH_QUOTE_TIMEOUT_S", "0")
        assert _quote_timeout_s() == 10.0
        monkeypatch.setenv("RH_QUOTE_TIMEOUT_S", "-3")
        assert _quote_timeout_s() == 10.0

    def test_batch_post_uses_env_timeout(self, monkeypatch):
        monkeypatch.setenv("RH_QUOTE_TIMEOUT_S", "3")
        ex = RhExecutor(rpc_url="http://localhost:1")
        sess = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {"id": i, "result": _u256(0)} for i in range(len(FEE_TIERS))]
        sess.post.return_value = resp
        ex._batch_session = sess
        ex._quote_all_tiers_batched(WETH9, TOKEN, 10 ** 16)
        assert sess.post.call_args.kwargs["timeout"] == 3.0


# ── RH_QUOTE_FALLBACK (fast-fail the sequential-sweep tail) ──────────────────
class TestQuoteFallback:
    def _ex_batch_none(self):
        ex = RhExecutor(rpc_url="http://localhost:1")
        ex.w3 = MagicMock()
        ex._quote_all_tiers_batched = lambda a, b, c: None   # batch "unavailable"
        return ex

    def test_default_seq_sweeps_on_batch_miss(self, monkeypatch):
        monkeypatch.delenv("RH_QUOTE_FALLBACK", raising=False)
        ex = self._ex_batch_none()
        calls = []
        ex._quote_single = lambda ti, to, amt, fee: (calls.append(fee) or 123)
        r = ex._best_quote(WETH9, TOKEN, 10 ** 16)
        assert r is not None                 # sequential sweep produced a quote
        assert set(calls) == set(FEE_TIERS)  # all tiers swept

    def test_none_fast_fails_without_sweep(self, monkeypatch):
        monkeypatch.setenv("RH_QUOTE_FALLBACK", "none")
        ex = self._ex_batch_none()
        called = []
        ex._quote_single = lambda ti, to, amt, fee: called.append(fee)
        r = ex._best_quote(WETH9, TOKEN, 10 ** 16)
        assert r is None            # fast-fail -> no quote -> caller books nothing
        assert called == []         # the slow per-tier sweep was skipped


# ── RH_RT_COMBINED: single-POST round trip (buy + RT-cost sell) ──────────────
class TestRoundtripBatch:
    def test_build_ids_and_directions(self):
        payload = build_roundtrip_quote_batch(TOKEN, 10 ** 16, 5 * 10 ** 18)
        n = len(FEE_TIERS)
        assert len(payload) == 2 * n
        assert [p["id"] for p in payload] == list(range(2 * n))
        # every call hits the QuoterV2; all target "latest"
        for p in payload:
            assert p["params"][0]["to"] == QUOTER_V2
            assert p["params"][1] == "latest"
        # buy tiers (ids 0..n-1) and sell tiers (ids n..2n-1) differ in calldata
        assert payload[0]["params"][0]["data"] != payload[n]["params"][0]["data"]

    def test_parse_splits_buys_and_sells(self):
        n = len(FEE_TIERS)
        resp = []
        for i in range(n):
            resp.append({"id": i, "result": _u256(100 + i)})       # buys
        for i in range(n):
            resp.append({"id": n + i, "result": _u256(200 + i)})   # sells
        buys, sells = parse_roundtrip_quote_batch(resp)
        assert buys == {fee: 100 + i for i, fee in enumerate(FEE_TIERS)}
        assert sells == {fee: 200 + i for i, fee in enumerate(FEE_TIERS)}

    def test_parse_error_entry_is_no_pool(self):
        n = len(FEE_TIERS)
        resp = [{"id": i, "result": _u256(10)} for i in range(n)]
        resp += [{"id": n + i, "error": {"message": "reverted"}}
                 for i in range(n)]
        buys, sells = parse_roundtrip_quote_batch(resp)
        assert len(buys) == n and sells == {}   # all sell tiers reverted

    def test_parse_missing_id_returns_none(self):
        n = len(FEE_TIERS)
        resp = [{"id": i, "result": _u256(10)} for i in range(2 * n - 1)]
        assert parse_roundtrip_quote_batch(resp) is None

    def test_parse_bad_shape_returns_none(self):
        assert parse_roundtrip_quote_batch({"not": "a list"}) is None

    def test_quote_roundtrip_batched_returns_exact_buy_and_best_sell(self):
        ex = RhExecutor(rpc_url="http://localhost:1")
        ex.token_decimals = lambda t: 18
        n = len(FEE_TIERS)
        # buy: tier index 1 wins (largest out); sell: tier index 2 wins
        resp = []
        for i in range(n):
            resp.append({"id": i, "result": _u256(1000 + (500 if i == 1 else i))})
        for i in range(n):
            resp.append({"id": n + i, "result": _u256(900 + (400 if i == 2 else i))})
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = resp
        sess.post.return_value = r
        ex._batch_session = sess
        out = ex.quote_roundtrip_batched(TOKEN, 10 ** 16, 5 * 10 ** 18)
        assert out is not None
        buy_q, eth_back = out
        assert isinstance(buy_q, RhQuote) and buy_q.side == "buy"
        assert buy_q.amount_out == 1500         # exact best buy (tier idx 1)
        assert buy_q.fee == FEE_TIERS[1]
        assert eth_back == 1300                 # best sell (tier idx 2 => 900+400)

    def test_quote_roundtrip_batched_bad_estimate_returns_none(self):
        ex = RhExecutor(rpc_url="http://localhost:1")
        assert ex.quote_roundtrip_batched(TOKEN, 10 ** 16, 0) is None
        assert ex.quote_roundtrip_batched(TOKEN, 10 ** 16, -5) is None

    def test_quote_roundtrip_batched_fail_open_on_http(self):
        ex = RhExecutor(rpc_url="http://localhost:1")
        sess = MagicMock()
        r = MagicMock()
        r.status_code = 500
        sess.post.return_value = r
        ex._batch_session = sess
        assert ex.quote_roundtrip_batched(TOKEN, 10 ** 16, 5 * 10 ** 18) is None


# ── lane flag ────────────────────────────────────────────────────────────────
class TestLaneFlag:
    def test_rt_combined_default_off(self, monkeypatch):
        import scripts.rh_paper_lane as lane
        monkeypatch.delenv("RH_RT_COMBINED", raising=False)
        assert lane._rt_combined() is False

    def test_rt_combined_on_values(self, monkeypatch):
        import scripts.rh_paper_lane as lane
        for v in ("1", "true", "on", "yes", "TRUE"):
            monkeypatch.setenv("RH_RT_COMBINED", v)
            assert lane._rt_combined() is True
        for v in ("0", "false", "off", ""):
            monkeypatch.setenv("RH_RT_COMBINED", v)
            assert lane._rt_combined() is False
