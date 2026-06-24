# tests/test_live_swap_wiring.py
"""(d) End-to-end wiring: a simulated LIVE buy/sell populates the COMPLETE
live-swap record. Stubs _execute_swap_ultra to return a known order/execute
result and asserts the JSONL record captures fill price, slippage, tx sig,
latency, success, 429 counts, and cost reconciliation. No network, no money."""
import asyncio
import json
import os
import sys
import types

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from feeds.dip_scanner import DipScanner
from core import live_swap_log as lsl


class StubTrader:
    def __init__(self, swap_result):
        self.private_key = "live-key"
        self._swap = swap_result
        self.calls = []
        self.sol_reserve_ok = True
        self.decimals = 6
        self.legacy_quote = None
        self.legacy_ok = True
        self._last_realized_slippage_pct = 0.5
        self.balances = []
        self._sol_seq = [1.0, 0.74]  # before, after (buy spent 0.25 SOL + fees)

    async def _usd_to_sol(self, usd):
        return usd / 200.0

    async def _sol_to_usd(self, sol):
        return sol * 200.0

    async def _get_token_decimals(self, mint):
        return self.decimals

    async def _check_sol_reserve(self, token_symbol="?"):
        return self.sol_reserve_ok

    async def _get_sol_balance(self, force=False):
        return self._sol_seq.pop(0) if self._sol_seq else 0.5

    async def _execute_swap_ultra(self, inp, out, amount, slippage_bps=None, buy_context=False):
        self.calls.append((inp, out, amount))
        return dict(self._swap)

    async def _get_quote(self, inp, out, amount, slippage_bps=100):
        return self.legacy_quote

    async def _execute_swap(self, quote):
        return self.legacy_ok

    async def _get_token_balance_atomic(self, mint):
        return self.balances.pop(0) if self.balances else 0

    async def _get_current_price_for(self, *a, **k):
        return None


class StubPos:
    def __init__(self):
        self.state_blob = {}
        self.address = "MINTADDR"
        self.pair_address = "PAIR"
        self.size_usd = 0.0
        self.entry_price = 0.0
        self.strategy = "probe_tightexit_live_50"
        self.remaining_fraction = 1.0


class StubPM:
    def __init__(self, maxc=3, existing=None, open_count=0):
        self.config = types.SimpleNamespace(max_concurrent_positions=maxc)
        self.open_count = open_count
        self._existing = existing
        self.opened = None

    def get_position(self, token):
        return self._existing

    def open_position(self, token, entry_price, size_usd, entry_time, address, pair_address):
        p = StubPos()
        p.entry_price = entry_price
        p.size_usd = size_usd
        p.address = address
        p.pair_address = pair_address
        self.opened = p
        return p


def _ds(trader):
    ds = DipScanner.__new__(DipScanner)
    ds.trader = trader
    return ds


def _decision():
    return types.SimpleNamespace(token="TOK", address="MINTADDR", entry_price=1.0,
                                 pair_address="PAIR", local_low=0.95,
                                 reason="deep_dip", liquidity_usd=29000.0,
                                 market_cap=124000.0, bot_id="probe_tightexit_live_50")


def _last_record(tmp_path):
    p = tmp_path / lsl.LOG_BASENAME
    assert p.exists(), "live_swaps.jsonl was not written"
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    return json.loads(lines[-1])


def test_live_buy_populates_complete_record(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    monkeypatch.setenv("BUY_REPRICE_MODE", "off")  # skip reprice fetch for determinism
    # $50 buy -> 48 tokens (48e6 @6dec) -> entry 50/48 ; ultra slip 200bps; cap 400
    swap = {"success": True, "out_amount": 48_000_000, "in_amount": 250_000_000,
            "route": "metis", "realized_slippage_pct": 2.0, "signature": "BUYSIG",
            "slippage_cap_bps": 400, "ultra_slippage_bps": 200,
            "order_duration_ms": 150.0, "sign_duration_ms": 8.0,
            "execute_duration_ms": 2200.0, "order_attempts": 1,
            "order_429_count": 0, "execute_429_count": 1, "backoff_total_ms": 0.0,
            "raw_order_response": {"outAmount": 48_000_000, "slippageBps": 200},
            "raw_execute_response": {"status": "Success", "signature": "BUYSIG"}}
    trader = StubTrader(swap)
    ds, pm = _ds(trader), StubPM()
    r = asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert r is not None and r["entry_price"] > 0
    rec = _last_record(tmp_path)
    # completeness
    for k in lsl.REQUIRED_FIELDS:
        assert k in rec, f"missing {k}"
    # identity/context
    assert rec["side"] == "buy"
    assert rec["token_address"] == "MINTADDR"
    assert rec["paper"] is False and rec["live_mode"] is True
    assert rec["jupiter_api_base"] in ("https://lite-api.jup.ag", "https://api.jup.ag")
    assert rec["liquidity_usd"] == 29000.0
    assert rec["lamports"] == 250_000_000
    # outcome
    assert rec["success"] is True and rec["failure_reason"] == "ok"
    assert rec["tx_signature"] == "BUYSIG"
    assert rec["out_amount"] == 48_000_000 and rec["in_amount"] == 250_000_000
    assert rec["decimals"] == 6
    # fidelity: real fill ~1.0417 vs mid 1.0 -> adverse positive
    assert abs(rec["real_fill_price"] - 50.0 / 48.0) < 1e-6
    assert rec["fill_vs_mid_slippage_pct"] > 4.0
    assert rec["ultra_reported_slippage_bps"] == 200 and rec["slippage_cap_bps"] == 400
    assert rec["cap_bound"] is False  # 200 < 0.9*400
    # latency
    assert rec["execute_duration_ms"] == 2200.0
    assert rec["total_latency_ms"] is not None and rec["total_latency_ms"] >= 0
    # rate-limit
    assert rec["execute_429_count"] == 1
    # cost reconciliation (1.0 -> 0.74 SOL)
    assert rec["sol_before"] == 1.0 and rec["sol_after"] == 0.74
    assert abs(rec["sol_spent"] - 0.26) < 1e-9
    assert abs(rec["tokens_received"] - 48.0) < 1e-9
    # known gap surfaced as null
    assert rec["priority_fee_lamports"] is None
    # trimmed debug present
    assert rec["raw_order_response"]["slippageBps"] == 200


def test_live_buy_stamps_entry_liquidity_on_position_and_record(tmp_path, monkeypatch):
    """Part 1: the REAL entry liquidity/mcap (resolved upstream as _ar_liq) is
    plumbed into the buy via explicit args, written to the buy live-swap record,
    AND stamped onto the opened position's state_blob so the SELL leg can read it."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    monkeypatch.setenv("BUY_REPRICE_MODE", "off")
    swap = {"success": True, "out_amount": 48_000_000, "in_amount": 250_000_000,
            "route": "metis", "realized_slippage_pct": 2.0, "signature": "BUYSIG",
            "slippage_cap_bps": 400, "ultra_slippage_bps": 200,
            "order_duration_ms": 150.0, "sign_duration_ms": 8.0,
            "execute_duration_ms": 2200.0, "order_attempts": 1,
            "order_429_count": 0, "execute_429_count": 0, "backoff_total_ms": 0.0}
    trader = StubTrader(swap)
    ds, pm = _ds(trader), StubPM()
    # decision carries NO liquidity (mirrors reality: it comes through None on the
    # decision); the real value arrives via the explicit entry_liquidity_usd arg.
    dec = types.SimpleNamespace(token="TOK", address="MINTADDR", entry_price=1.0,
                                pair_address="PAIR", local_low=0.95,
                                reason="deep_dip", bot_id="badday_flush_live")
    r = asyncio.run(ds._execute_bot_buy_live(dec, pm, 50.0,
                                             entry_liquidity_usd=41234.0,
                                             entry_mcap=555000.0))
    assert r is not None
    rec = _last_record(tmp_path)
    assert rec["side"] == "buy"
    assert rec["liquidity_usd"] == 41234.0
    assert rec["mcap"] == 555000.0
    # position carries entry liquidity (the SELL leg reads from here)
    pos = r["pos"]
    assert pos.state_blob.get("entry_liquidity_usd") == 41234.0
    assert pos.state_blob.get("entry_mcap") == 555000.0


def test_live_sell_reads_entry_liquidity_from_position(tmp_path, monkeypatch):
    """Part 1: the SELL leg stamps the ENTRY liquidity (carried on the position's
    state_blob from the buy) onto its live-swap record — NOT a literal None."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    swap = {"success": True, "out_amount": 300_000_000, "route": "jupiterz",
            "realized_slippage_pct": 0.4, "signature": "SELLSIG",
            "slippage_cap_bps": 400, "ultra_slippage_bps": 40,
            "order_duration_ms": 120.0, "sign_duration_ms": 7.0,
            "execute_duration_ms": 1800.0, "order_attempts": 1,
            "order_429_count": 0, "execute_429_count": 0, "backoff_total_ms": 0.0,
            "in_amount": 50_000_000}
    trader = StubTrader(swap)
    trader._sol_seq = [0.5, 0.8]
    trader.balances = [50_000_000]
    ds = _ds(trader)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    pos.state_blob = {"entry_liquidity_usd": 41234.0, "entry_mcap": 555000.0}
    r = asyncio.run(ds._execute_bot_sell_live("TOK", StubPM(), pos, 1.0, current_mid=1.25))
    assert r is not None
    rec = _last_record(tmp_path)
    assert rec["side"] == "sell"
    assert rec["liquidity_usd"] == 41234.0  # entry liq, read from the position
    assert rec["mcap"] == 555000.0


def test_live_buy_failure_writes_record_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    monkeypatch.setenv("BUY_REPRICE_MODE", "off")
    swap = {"success": False, "reason": "order HTTP 429", "order_429_count": 3,
            "order_attempts": 3, "execute_429_count": 0}
    trader = StubTrader(swap)
    trader.balances = [0]  # M7 adoption check sees no tokens
    ds, pm = _ds(trader), StubPM()
    out = asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert out is None
    rec = _last_record(tmp_path)
    assert rec["side"] == "buy" and rec["success"] is False
    assert rec["failure_reason"] == "rate_limit"   # classified from "429"
    assert rec["order_429_count"] == 3


def test_live_sell_populates_complete_record(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    swap = {"success": True, "out_amount": 300_000_000, "route": "jupiterz",
            "realized_slippage_pct": 0.4, "signature": "SELLSIG",
            "slippage_cap_bps": 400, "ultra_slippage_bps": 40,
            "order_duration_ms": 120.0, "sign_duration_ms": 7.0,
            "execute_duration_ms": 1800.0, "order_attempts": 1,
            "order_429_count": 0, "execute_429_count": 0, "backoff_total_ms": 0.0,
            "in_amount": 50_000_000}
    trader = StubTrader(swap)
    trader._sol_seq = [0.5, 0.8]  # before, after (received SOL)
    trader.balances = [50_000_000]  # 50 tokens @6dec on-chain
    ds = _ds(trader)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    r = asyncio.run(ds._execute_bot_sell_live("TOK", StubPM(), pos, 1.0, current_mid=1.25))
    assert r is not None and abs(r["exit_price"] - 1.20) < 1e-9
    rec = _last_record(tmp_path)
    for k in lsl.REQUIRED_FIELDS:
        assert k in rec, f"missing {k}"
    assert rec["side"] == "sell" and rec["success"] is True
    assert rec["tx_signature"] == "SELLSIG"
    assert rec["token_address"] == "MINTADDR"
    # sell adverse: 1.20 vs mid 1.25 -> +4%
    assert rec["fill_vs_mid_slippage_pct"] == 4.0
    assert rec["execute_duration_ms"] == 1800.0
    assert rec["sol_before"] == 0.5 and rec["sol_after"] == 0.8
    assert rec["proceeds_usd"] == 60.0  # forward-compat extra field
