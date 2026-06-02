"""Live-execution bridge (piece 1b): _execute_bot_buy_live / _execute_bot_sell_live.

Mocked-swap TDD of the real-money ACCOUNTING — no network. Verifies real-fill entry/exit
pricing, instrumentation stamping, fail-soft on swap failure, and the pre-check that a
confirmed swap can't strand money in an un-trackable position.
"""
import asyncio
import types
from feeds.dip_scanner import DipScanner


class StubTrader:
    def __init__(self, swap_result):
        self.private_key = "live-key"
        self._swap = swap_result
        self.calls = []
    async def _usd_to_sol(self, usd):  # $200/SOL
        return usd / 200.0
    async def _sol_to_usd(self, sol):
        return sol * 200.0
    async def _get_token_decimals(self, mint):
        return 6
    async def _execute_swap_ultra(self, inp, out, amount, slippage_bps=None):
        self.calls.append((inp, out, amount))
        return self._swap


class StubPos:
    def __init__(self):
        self.state_blob = {}
        self.address = "addr"
        self.pair_address = "pair"
        self.size_usd = 0.0
        self.entry_price = 0.0


class StubPM:
    def __init__(self, maxc=2, existing=None, open_count=0):
        self.config = types.SimpleNamespace(max_concurrent_positions=maxc,
                                            live_probe=True, size_sweep_usd=())
        self.open_count = open_count
        self._existing = existing
        self.opened = None
    def get_position(self, token):
        return self._existing
    def open_position(self, token, entry_price, size_usd, entry_time, address, pair_address):
        p = StubPos()
        p.entry_price = entry_price; p.size_usd = size_usd
        p.address = address; p.pair_address = pair_address
        self.opened = p
        return p


def _ds(trader):
    ds = DipScanner.__new__(DipScanner)
    ds.trader = trader
    return ds


def _decision():
    return types.SimpleNamespace(token="T", address="addr", entry_price=1.0,
                                 pair_address="pair", local_low=0.95)


# ── BUY live ──
def test_buy_live_success_real_fill_and_instrument():
    # size $50, swap returns 48 tokens (atomic 48e6 @ 6 decimals) -> entry 50/48 = 1.0417
    trader = StubTrader({"success": True, "out_amount": 48_000_000, "route": "metis",
                         "realized_slippage_pct": 0.5, "signature": "SIG"})
    ds, pm = _ds(trader), StubPM()
    r = asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert r is not None
    assert abs(r["entry_price"] - 50.0/48.0) < 1e-9
    assert pm.opened is not None and pm.opened.size_usd == 50.0
    inst = r["instrument"]
    assert inst["live_route"] == "metis" and inst["live_signature"] == "SIG"
    assert inst["live_size_usd"] == 50.0
    # buy slippage adverse-positive: paid 1.0417 vs mid 1.0 -> ~+4.17%
    assert inst["live_slippage_pct"] > 4.0
    # swap was SOL->token for the $50 -> 0.25 SOL -> 250_000_000 lamports
    assert trader.calls[0][2] == 250_000_000


def test_buy_live_swap_failure_returns_none_no_open():
    trader = StubTrader({"success": False, "reason": "no_route"})
    ds, pm = _ds(trader), StubPM()
    assert asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0)) is None
    assert pm.opened is None


def test_buy_live_precheck_blocks_before_spending():
    trader = StubTrader({"success": True, "out_amount": 48_000_000})
    # already-open token -> must NOT swap
    ds, pm = _ds(trader), StubPM(existing=StubPos())
    assert asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0)) is None
    assert trader.calls == []   # no swap attempted -> no money spent
    # max_concurrent reached -> must NOT swap
    ds2, pm2 = _ds(StubTrader({"success": True, "out_amount": 1})), StubPM(open_count=2, maxc=2)
    assert asyncio.run(ds2._execute_bot_buy_live(_decision(), pm2, 50.0)) is None
    assert ds2.trader.calls == []


# ── SELL live ──
def test_sell_live_success_real_exit_and_instrument():
    # position: $50 @ entry 1.0 -> 50 tokens held; sell 100%. swap returns 0.30 SOL
    # (out_amount 300_000_000 lamports) -> proceeds $60 -> exit 60/50 = 1.20
    trader = StubTrader({"success": True, "out_amount": 300_000_000, "route": "jupiterz",
                         "realized_slippage_pct": 0.4, "signature": "SELLSIG"})
    ds = _ds(trader)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    r = asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, current_mid=1.25))
    assert r is not None
    assert abs(r["exit_price"] - 1.20) < 1e-9
    inst = r["instrument"]
    assert inst["live_proceeds_usd"] == 60.0 and inst["live_signature"] == "SELLSIG"
    # sell slippage adverse-positive: got 1.20 vs mid 1.25 -> +4% (received less)
    assert inst["live_slippage_pct"] == 4.0
    # swap was token->SOL of 50 tokens @ 6 decimals = 50_000_000 atomic
    assert trader.calls[0][2] == 50_000_000


def test_sell_live_swap_failure_returns_none():
    trader = StubTrader({"success": False, "reason": "execute_error"})
    ds = _ds(trader)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    assert asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, 1.25)) is None
