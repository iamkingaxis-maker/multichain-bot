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
        self.slip_caps = []          # captured slippage_bps per Ultra call
        self.sol_reserve_ok = True   # M3: gas-reserve gate
        self.decimals = 6            # M10: override to test decimals mismatch
        self.legacy_quote = None     # M2: legacy fallback quote (None = fallback fails)
        self.legacy_ok = True
        self._last_realized_slippage_pct = 0.3
        self.balances = []  # M7: popped [pre, post] for orphan-adoption tests
    async def _usd_to_sol(self, usd):  # $200/SOL
        return usd / 200.0
    async def _sol_to_usd(self, sol):
        return sol * 200.0
    async def _get_token_decimals(self, mint):
        return self.decimals
    async def _check_sol_reserve(self, token_symbol="?"):
        return self.sol_reserve_ok
    async def _execute_swap_ultra(self, inp, out, amount, slippage_bps=None, buy_context=False):
        self.calls.append((inp, out, amount)); self.slip_caps.append(slippage_bps)
        return self._swap
    async def _get_quote(self, inp, out, amount, slippage_bps=100):
        return self.legacy_quote
    async def _execute_swap(self, quote):
        return self.legacy_ok
    async def _get_token_balance_atomic(self, mint):
        return self.balances.pop(0) if self.balances else 0


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
    trader.balances = [50_000_000]   # E1a: real on-chain balance (50 tokens @6dec)
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
    trader.balances = [50_000_000]   # E1a: real on-chain balance (50 tokens @6dec)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    assert asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, 1.25)) is None


# ── probe red-team safety fixes (M3 gas, M6 slip cap, M10 decimals, M2 sell fallback) ──
def test_buy_live_gas_check_aborts_before_spending():
    trader = StubTrader({"success": True, "out_amount": 48_000_000})
    trader.sol_reserve_ok = False                      # M3: insufficient gas
    ds, pm = _ds(trader), StubPM()
    assert asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0)) is None
    assert trader.calls == []                          # no swap attempted -> no money spent
    assert pm.opened is None


def test_buy_live_passes_slippage_cap():
    trader = StubTrader({"success": True, "out_amount": 48_000_000, "route": "m", "signature": "S"})
    ds, pm = _ds(trader), StubPM()
    asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert trader.slip_caps and trader.slip_caps[0] == 400   # M6: explicit cap passed


def test_buy_live_decimals_mismatch_falls_back_to_mid_flagged():
    # token is really 6-decimals (48e6 atomic = 48 tokens) but lookup returns 9 ->
    # out_tokens 0.048 -> entry ~$1041 vs mid $1.0 (implausible) -> M10 fallback to mid.
    trader = StubTrader({"success": True, "out_amount": 48_000_000, "route": "m", "signature": "S"})
    trader.decimals = 9
    ds, pm = _ds(trader), StubPM()
    r = asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert r is not None
    assert r["entry_price"] == 1.0                     # fell back to mid (not the $1041 phantom)
    assert r["instrument"]["live_entry_suspect"] is True
    assert pm.opened.entry_price == 1.0                # position tracked, no phantom


def test_sell_live_ultra_fail_legacy_fallback_exits():
    trader = StubTrader({"success": False, "reason": "ultra_down"})
    trader.legacy_quote = {"outAmount": 300_000_000}   # legacy quote: 0.30 SOL = $60
    trader.legacy_ok = True
    ds = _ds(trader)
    trader.balances = [50_000_000]   # E1a: real on-chain balance (50 tokens @6dec)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    r = asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, current_mid=1.25))
    assert r is not None                               # M2: exited via fallback, not stranded
    assert abs(r["exit_price"] - 1.20) < 1e-9          # $60 / 50 tokens
    assert r["instrument"]["live_route"] == "legacy_fallback"


def test_sell_live_both_routes_fail_stays_open():
    trader = StubTrader({"success": False, "reason": "ultra_down"})
    trader.legacy_quote = {"outAmount": 300_000_000}; trader.legacy_ok = False   # legacy also fails
    ds = _ds(trader)
    trader.balances = [50_000_000]   # E1a: real on-chain balance (50 tokens @6dec)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    assert asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, 1.25)) is None  # stays open to retry


# ── audit ship-blocker fixes (2026-06-02): E1a balance-sourced sell, E1b spent sentinel ──
def test_sell_live_E1a_sources_amount_from_onchain_balance():
    # suspect/paper entry would mis-size (size/entry=500k tokens); the BALANCE is truth.
    trader = StubTrader({"success": True, "out_amount": 300_000_000, "route": "m", "signature": "S"})
    trader.balances = [50_000_000]                      # real balance = 50 tokens @6dec
    ds = _ds(trader)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 0.0001   # paper math = 500k tokens (wrong)
    r = asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, current_mid=1.25))
    assert r is not None
    assert trader.calls[0][2] == 50_000_000             # swapped the BALANCE, not paper 500k


def test_buy_live_E1b_spent_sentinel_when_open_fails():
    # swap LANDED (money spent) but open_position raises -> return a SPENT sentinel (NOT None),
    # so the caller does not double-refund the (really gone) capital.
    trader = StubTrader({"success": True, "out_amount": 48_000_000, "route": "m", "signature": "SIG"})
    ds, pm = _ds(trader), StubPM()
    def _raise(*a, **k):
        raise ValueError("max_concurrent")
    pm.open_position = _raise
    r = asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert isinstance(r, dict) and r.get("spent") is True and r.get("signature") == "SIG"


# ── probe must-fixes round 2: M7 orphan-adoption, D1 local_low ──
import os

def test_buy_live_m7_adopts_orphan_when_swap_reports_fail_but_lands():
    # Ultra reports failure, but the token balance grew (timed-out-but-landed) -> ADOPT.
    os.environ["PROBE_ADOPT_WAIT_S"] = "0"
    trader = StubTrader({"success": False, "reason": "execute_error", "signature": "S"})
    trader.balances = [0, 48_000_000]      # pre=0, post=48e6 (tokens arrived)
    ds, pm = _ds(trader), StubPM()
    r = asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert r is not None                    # adopted, not orphaned
    assert abs(r["entry_price"] - 50.0/48.0) < 1e-9
    assert r["instrument"]["live_route"] == "adopted_orphan"
    os.environ.pop("PROBE_ADOPT_WAIT_S", None)


def test_buy_live_m7_real_failure_no_balance_returns_none():
    os.environ["PROBE_ADOPT_WAIT_S"] = "0"
    trader = StubTrader({"success": False, "reason": "no_route"})
    trader.balances = [0, 0]               # nothing landed
    ds, pm = _ds(trader), StubPM()
    assert asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0)) is None
    assert pm.opened is None
    os.environ.pop("PROBE_ADOPT_WAIT_S", None)


def test_buy_live_d1_populates_entry_vs_local_low():
    # decision carries local_low=0.95; entry ~1.0417 -> (1.0417-0.95)/0.95*100 ~= +9.65%
    trader = StubTrader({"success": True, "out_amount": 48_000_000, "route": "m", "signature": "S"})
    ds, pm = _ds(trader), StubPM()
    r = asyncio.run(ds._execute_bot_buy_live(_decision(), pm, 50.0))
    assert r["instrument"]["live_entry_vs_local_low_pct"] is not None
    assert r["instrument"]["live_entry_vs_local_low_pct"] > 9.0


# ── phantom-state fix (2026-06-14): a position with no on-chain tokens must CLOSE, not
#    retry forever (the cap-clog bug). Distinguish a confirmed-0 balance (close) from a
#    FAILED balance read (transient -> keep retrying; never strand a real position). ──
def test_sell_live_confirmed_zero_balance_signals_empty_not_retry():
    # balance read SUCCEEDS and returns 0 -> no real tokens -> signal CLOSE ({"empty": True}),
    # and do NOT attempt a swap on an empty balance.
    trader = StubTrader({"success": True, "out_amount": 1, "route": "m", "signature": "S"})
    trader.balances = [0]                       # confirmed 0 on-chain
    ds = _ds(trader)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    r = asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, current_mid=1.25))
    assert isinstance(r, dict) and r.get("empty") is True   # close it (phantom / already-exited)
    assert trader.calls == []                               # never swapped an empty balance


def test_sell_live_balance_read_failure_returns_none_to_retry():
    # balance read FAILS (returns None) -> transient -> return None (retry next tick),
    # must NOT be treated as empty (would wrongly close a real position on an RPC hiccup).
    trader = StubTrader({"success": True, "out_amount": 1, "route": "m", "signature": "S"})
    async def _none_balance(mint):
        return None
    trader._get_token_balance_atomic = _none_balance
    ds = _ds(trader)
    pos = StubPos(); pos.size_usd = 50.0; pos.entry_price = 1.0
    r = asyncio.run(ds._execute_bot_sell_live("T", StubPM(), pos, 1.0, current_mid=1.25))
    assert r is None                                        # retry, do NOT close
    assert trader.calls == []                               # no swap on a failed read
