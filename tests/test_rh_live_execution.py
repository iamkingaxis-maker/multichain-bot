# tests/test_rh_live_execution.py
"""core/rh_live_execution.py — the RH LIVE policy layer, PARKED DORMANT.

DORMANCY TESTS FIRST: with no key and no flags (the shipped default) every
live function must refuse and the paper lane must behave byte-identically.
Then offline unit tests: revert decoding, slippage/containment math, canary
state machine + probe, daily-loss store, wallet-truth, gas cap. NO network,
NO real keys, NEVER a real tx (executors are MagicMocks throughout)."""
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from eth_abi import encode as abi_encode

import core.rh_live_execution as rl
from core.rh_live_execution import (
    CANARY_BLOCK_REASON,
    CANARY_GRACE_SECS,
    DEFAULT_DAILY_STOP_USD,
    DEFAULT_MAX_POSITION_USD,
    SLIPPAGE_BPS_CEILING,
    GasCappedExecutor,
    RhCanaryHaltError,
    RhContainmentError,
    RhDailyPnl,
    RhLiveExecutor,
    RhLiveGateError,
    RhSellCanary,
    canary_mode_on,
    decode_revert_data,
    enforce_gas_cap,
    explain_swap_error,
    fetch_revert_reason,
    probe_exit_quotes,
    rh_canary_entry_block,
    rh_live_gate,
    rh_paper_mode,
    rh_wallet_rebase,
    rh_wallet_truth,
)
from core.rh_execution import RH_CHAIN_ID, WETH9, RhSwapError

TOKEN = "0x1111111111111111111111111111111111111111"
WALLET = "0xAaAaAaAaaAaAaAaAaAaaAaAaAaAaAaAaAaAaAaAa"
# throwaway env value for gate tests — never parsed as a real key here
FAKE_KEY = "0x" + "11" * 32

RH_ENV_KEYS = (
    "RH_PAPER_MODE", "RH_LIVE_CONFIRMED", "RH_PRIVATE_KEY", "RH_SELL_CANARY",
    "RH_CANARY_FLAG_PATH", "RH_LIVE_STATE_DIR", "RH_LIVE_MAX_POSITION_USD",
    "RH_LIVE_DAILY_STOP_USD", "RH_LIVE_SLIPPAGE_BPS", "RH_CANARY_MAX_FAILS",
    "RH_CANARY_INTERVAL_S", "RH_WALLET_ADDRESS", "RH_LIVE_MAX_GAS_COST_ETH",
)


@pytest.fixture(autouse=True)
def _clean_rh_env(monkeypatch, tmp_path):
    """Shipped-default env (all RH_* gates unset) + state dir in tmp so no
    test writes into the repo scratchpad. conftest restores env after."""
    for k in RH_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RH_LIVE_STATE_DIR", str(tmp_path))
    yield


def _open_gates(monkeypatch):
    monkeypatch.setenv("RH_LIVE_CONFIRMED", "true")
    monkeypatch.setenv("RH_PAPER_MODE", "false")
    monkeypatch.setenv("RH_PRIVATE_KEY", FAKE_KEY)
    # live mode auto-enables the canary; pin it OFF here so containment tests
    # don't depend on the module boot-grace clock — canary tests turn it ON.
    monkeypatch.setenv("RH_SELL_CANARY", "off")


def _mock_executor(**kw):
    ex = MagicMock()
    ex.paper_only = False
    ex.w3 = None                       # explain_swap_error stays pass-through
    ex.wallet_address = WALLET
    ex.quote_and_swap_buy.return_value = {"success": True, "side": "buy"}
    ex.swap_sell.return_value = {"success": True, "side": "sell"}
    for k, v in kw.items():
        setattr(ex, k, v)
    return ex


# ═══════════════════════════ DORMANCY FIRST ═══════════════════════════════
class TestDormancy:
    def test_gate_closed_by_default(self):
        ok, reason = rh_live_gate()
        assert ok is False
        # every missing leg is named (an operator must see ALL of them)
        assert "RH_LIVE_CONFIRMED" in reason
        assert "RH_PAPER_MODE" in reason
        assert "RH_PRIVATE_KEY" in reason

    @pytest.mark.parametrize("confirmed,paper_false,key", [
        (True, True, False),   # no key
        (True, False, True),   # paper mode still on
        (False, True, True),   # not confirmed
        (True, False, False), (False, True, False), (False, False, True),
        (False, False, False),
    ])
    def test_any_missing_leg_stays_closed(self, monkeypatch, confirmed,
                                          paper_false, key):
        if confirmed:
            monkeypatch.setenv("RH_LIVE_CONFIRMED", "true")
        if paper_false:
            monkeypatch.setenv("RH_PAPER_MODE", "false")
        if key:
            monkeypatch.setenv("RH_PRIVATE_KEY", FAKE_KEY)
        assert rh_live_gate()[0] is False

    def test_all_three_legs_open_the_gate(self, monkeypatch):
        _open_gates(monkeypatch)
        assert rh_live_gate()[0] is True

    def test_paper_mode_default_is_paper(self):
        assert rh_paper_mode() is True    # unset -> paper

    def test_paper_mode_needs_literal_false(self, monkeypatch):
        for v in ("0", "no", "off", "False ", "FALSE"):
            monkeypatch.setenv("RH_PAPER_MODE", v)
            # only trimmed case-insensitive "false" opens the leg
            assert rh_paper_mode() is (v.strip().lower() != "false")

    def test_live_buy_refuses_dormant_no_network(self):
        ex = _mock_executor()
        live = RhLiveExecutor(executor=ex)
        with pytest.raises(RhLiveGateError):
            live.live_buy(TOKEN, 10.0, 4000.0)
        ex.quote_and_swap_buy.assert_not_called()
        ex.connect.assert_not_called()

    def test_live_sell_refuses_dormant_no_network(self):
        ex = _mock_executor()
        live = RhLiveExecutor(executor=ex)
        with pytest.raises(RhLiveGateError):
            live.live_sell(TOKEN)
        ex.swap_sell.assert_not_called()

    def test_gates_open_but_executor_paper_only_refused(self, monkeypatch):
        """Belt-and-braces: env says live but the executor holds no key."""
        _open_gates(monkeypatch)
        ex = _mock_executor(paper_only=True)
        live = RhLiveExecutor(executor=ex)
        with pytest.raises(RhLiveGateError):
            live.live_buy(TOKEN, 10.0, 4000.0)
        ex.quote_and_swap_buy.assert_not_called()

    def test_canary_mode_off_in_paper_default(self):
        assert canary_mode_on() is False
        assert rh_canary_entry_block() is None

    def test_canary_off_ignores_even_a_red_flag_file(self, monkeypatch,
                                                     tmp_path):
        """Paper default byte-identity: a leftover red flag must not gate."""
        p = tmp_path / "flag.json"
        c = RhSellCanary(max_fails=1)
        c.record(False)
        c.write_flag(str(p))
        monkeypatch.setenv("RH_CANARY_FLAG_PATH", str(p))
        assert rh_canary_entry_block() is None

    def test_canary_auto_on_when_live_attempted(self, monkeypatch):
        monkeypatch.setenv("RH_PAPER_MODE", "false")
        assert canary_mode_on() is True
        monkeypatch.setenv("RH_SELL_CANARY", "off")   # explicit off allowed
        assert canary_mode_on() is False

    def test_paper_lane_canary_tick_noop_when_off(self):
        """Byte-identity: with canary mode off (paper default) the lane's
        _canary_tick touches NOTHING — no executor call, no flag file."""
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "..", "scripts"))
        from rh_paper_lane import PaperLane
        lane = PaperLane.__new__(PaperLane)   # no attrs needed: returns first
        lane.ex = MagicMock()
        lane._canary_tick(time.time())        # must not raise, must not touch
        lane.ex.assert_not_called()
        assert not os.path.exists(rl.rh_canary_flag_path())

    def test_paper_lane_never_calls_swap_methods_static(self):
        """2026-07-12 LIVE FILL PROBE update: the lane may now reach
        live_buy/live_sell, but ONLY through the four-condition routing glue
        (triple gate + RH_LIVE_PROBE_BOTS opt-in). The raw RhExecutor money
        methods are still never referenced; .live_buy( has exactly ONE call
        site (inside _live_buy_leg, whose single caller sits directly behind
        live_route_open) and .live_sell( exactly ONE (inside _paper_sell,
        behind meta.get("live") — set only by a live-routed buy)."""
        p = os.path.join(os.path.dirname(__file__), "..", "scripts",
                         "rh_paper_lane.py")
        src = open(p, encoding="utf-8").read()
        assert "quote_and_swap_buy" not in src
        assert "swap_sell" not in src           # raw executor sell: never
        # ONE live_buy call site, inside _live_buy_leg
        assert src.count(".live_buy(") == 1
        leg = src[src.index("def _live_buy_leg"):]
        assert ".live_buy(" in leg[:leg.index("\n    def ")]
        # _live_buy_leg's ONLY caller is gated by live_route_open
        callers = [i for i in range(len(src))
                   if src.startswith("self._live_buy_leg(", i)]
        assert len(callers) == 1
        assert "if live_route_open(st.bot.bot_id):" in \
            src[callers[0] - 400:callers[0]]
        # THREE sanctioned live_sell sites, each behind a live gate: position
        # EXIT (meta["live"]), one-shot ORPHAN recovery (RH_SELL_ORPHAN), and the
        # periodic DUST-SWEEP of orphaned bags (rh_live_gate). Order-independent.
        sites = [k for k in range(len(src)) if src.startswith(".live_sell(", k)]
        assert len(sites) == 3
        _GATES = ('if meta.get("live"):', 'RH_SELL_ORPHAN', 'rh_live_gate(')
        for s in sites:
            assert any(g in src[max(0, s - 2500):s] for g in _GATES)

    def test_wallet_truth_dormant_never_arms_baseline(self, tmp_path):
        ex = _mock_executor()
        ex.eth_balance.return_value = 1.0
        ex.token_balance.return_value = 0
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is True and out["live_gate"] is False
        assert "delta_eth" not in out
        assert not os.path.exists(str(tmp_path / "rh_live_wallet_baseline.json"))


# ═══════════════════════════ revert decoding ═══════════════════════════════
def _error_string_hex(msg: str) -> str:
    return "0x08c379a0" + abi_encode(["string"], [msg]).hex()


class TestRevertDecoding:
    def test_error_string(self):
        assert decode_revert_data(_error_string_hex("Too little received")) \
            == "revert: Too little received"

    def test_panic_overflow(self):
        data = "0x4e487b71" + format(0x11, "064x")
        assert "0x11" in decode_revert_data(data)
        assert "overflow" in decode_revert_data(data)

    def test_empty_and_bytes_input(self):
        assert decode_revert_data("") == "revert (no data)"
        assert decode_revert_data(None) == "revert (no data)"
        assert decode_revert_data(
            bytes.fromhex(_error_string_hex("STF")[2:])) == "revert: STF"

    def test_unknown_selector_falls_back_to_hex(self):
        out = decode_revert_data("0xdeadbeef" + "00" * 8)
        assert out.startswith("revert data 0xdeadbeef")

    def test_fetch_revert_reason_decodes_call_exception(self):
        w3 = MagicMock()
        w3.eth.get_transaction.return_value = {
            "from": WALLET, "to": TOKEN, "input": "0x00", "value": 0,
            "gas": 100000, "blockNumber": 7}
        err = Exception("execution reverted")
        err.data = _error_string_hex("Too little received")
        w3.eth.call.side_effect = err
        assert fetch_revert_reason(w3, "0x" + "aa" * 32) \
            == "revert: Too little received"

    def test_fetch_revert_reason_fail_open(self):
        w3 = MagicMock()
        w3.eth.get_transaction.side_effect = Exception("boom")
        # any internal failure -> a string or None, NEVER a raise
        assert fetch_revert_reason(w3, "0x" + "aa" * 32) in (None, "boom")

    def test_explain_swap_error_without_hash_passthrough(self):
        e = RhSwapError("no V3 route for buy 0x11")
        assert explain_swap_error(None, e) == "no V3 route for buy 0x11"


# ═══════════════════════════ gas cap ════════════════════════════════════════
class TestGasCap:
    def test_no_cap_is_noop(self):
        enforce_gas_cap(10 ** 6, 10 ** 12, None)

    def test_under_cap_passes(self):
        enforce_gas_cap(600_000, 10 ** 9, int(0.001 * 1e18))  # 0.0006 ETH

    def test_over_cap_raises(self):
        with pytest.raises(RhSwapError, match="gas-cost cap"):
            enforce_gas_cap(600_000, 10 ** 10, int(0.001 * 1e18))  # 0.006 ETH

    def test_gas_capped_executor_enforces_pre_sign(self):
        tx = {"gas": 600_000, "maxFeePerGas": 10 ** 10}
        with patch("core.rh_live_execution.RhExecutor._build_tx",
                   return_value=tx):
            ex = GasCappedExecutor.__new__(GasCappedExecutor)
            ex.max_gas_cost_wei = int(0.001 * 1e18)
            with pytest.raises(RhSwapError, match="gas-cost cap"):
                ex._build_tx({"to": TOKEN, "data": "0x", "value": 0})
            ex.max_gas_cost_wei = None       # cap off -> byte-identical
            assert ex._build_tx({"to": TOKEN, "data": "0x", "value": 0}) is tx


# ═══════════════════════════ canary state machine ══════════════════════════
class TestCanaryStateMachine:
    def test_boot_grace_then_fail_closed(self):
        t0 = 1_000_000.0
        c = RhSellCanary(interval_secs=60, max_fails=3, spawned_at=t0)
        assert c.healthy(t0 + 10) is True                 # inside grace
        assert c.healthy(t0 + CANARY_GRACE_SECS + 1) is False  # never probed

    def test_n_consecutive_fails_halts_and_ok_resets(self):
        t0 = 1_000_000.0
        c = RhSellCanary(interval_secs=60, max_fails=3, spawned_at=t0)
        c.record(False, t0 + 1)
        c.record(False, t0 + 2)
        assert c.healthy(t0 + 3) is True                  # debounce: 2 < 3
        c.record(False, t0 + 3)
        assert c.healthy(t0 + 4) is False                 # 3rd consecutive
        c.record(True, t0 + 5)
        assert c.healthy(t0 + 6) is True                  # success resets

    def test_stale_success_ages_out(self):
        t0 = 1_000_000.0
        c = RhSellCanary(interval_secs=60, max_fails=3, spawned_at=t0)
        c.record(True, t0 + 1)
        assert c.healthy(t0 + 100) is True
        assert c.healthy(t0 + 1 + 60 * 4 + 1) is False    # wedged loop

    def test_state_roundtrip_via_flag_file(self, tmp_path):
        t0 = 1_000_000.0
        c = RhSellCanary(interval_secs=45, max_fails=2, spawned_at=t0)
        c.record(False, t0 + 5)
        c.record(False, t0 + 6)
        p = str(tmp_path / "flag.json")
        c.write_flag(p)
        c2 = RhSellCanary.from_state(json.load(open(p)))
        assert c2.consecutive_fails == 2 and c2.max_fails == 2
        assert c2.healthy(t0 + 7) is False

    def test_entry_block_reads_flag_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RH_SELL_CANARY", "on")
        p = str(tmp_path / "flag.json")
        monkeypatch.setenv("RH_CANARY_FLAG_PATH", p)
        now = time.time()
        c = RhSellCanary(interval_secs=60, max_fails=1, spawned_at=now)
        c.record(True, now)
        c.write_flag(p)
        assert rh_canary_entry_block(now) is None
        c.record(False, now + 1)
        c.write_flag(p)
        assert rh_canary_entry_block(now + 2) == CANARY_BLOCK_REASON

    def test_entry_block_missing_file_grace_then_closed(self, monkeypatch,
                                                        tmp_path):
        monkeypatch.setenv("RH_SELL_CANARY", "on")
        monkeypatch.setenv("RH_CANARY_FLAG_PATH", str(tmp_path / "none.json"))
        monkeypatch.setattr(rl, "_MODULE_SPAWNED_AT", time.time())
        assert rh_canary_entry_block() is None            # inside boot grace
        monkeypatch.setattr(rl, "_MODULE_SPAWNED_AT",
                            time.time() - CANARY_GRACE_SECS - 1)
        assert rh_canary_entry_block() == CANARY_BLOCK_REASON

    def test_entry_block_garbage_flag_fails_closed(self, monkeypatch,
                                                   tmp_path):
        monkeypatch.setenv("RH_SELL_CANARY", "on")
        p = tmp_path / "flag.json"
        p.write_text('{"consecutive_fails": "not-a-number"}')
        monkeypatch.setenv("RH_CANARY_FLAG_PATH", str(p))
        assert rh_canary_entry_block() == CANARY_BLOCK_REASON


class TestCanaryProbe:
    def test_open_positions_all_quotable_passes(self):
        ex = MagicMock()
        ex._quote_all_tiers_batched.return_value = {}     # transport alive
        q = MagicMock()
        q.amount_out = 12345
        ex.quote_sell.return_value = q
        assert probe_exit_quotes(ex, [(TOKEN, 10 ** 18)]) is True
        ex.quote_sell.assert_called_once_with(TOKEN, 10 ** 18)

    def test_dead_bag_with_transport_ok_does_not_halt(self):
        # 2026-07-14 incident fix: transport ALIVE + one unquotable (dead/rug)
        # holding must NOT freeze the lane — a dead bag is a write-off, not a
        # broken sell path. Was False (froze all live buys on a GOATAI rug).
        ex = MagicMock()
        ex._quote_all_tiers_batched.return_value = {}     # transport alive
        good = MagicMock()
        good.amount_out = 1
        ex.quote_sell.side_effect = [good, None]          # 2nd holding is dead
        assert probe_exit_quotes(
            ex, [(TOKEN, 10 ** 18), (WETH9, 5)]) is True

    def test_unquotable_holding_but_transport_down_still_fails(self):
        # transport DEAD is the real disaster -> RED regardless of holdings.
        ex = MagicMock()
        ex._quote_all_tiers_batched.return_value = None
        w3 = MagicMock()
        w3.eth.chain_id = 1                               # wrong chain = pipe dead
        ex._require_w3.return_value = w3
        assert probe_exit_quotes(ex, [(TOKEN, 10 ** 18)]) is False

    def test_no_positions_wellformed_batch_passes(self):
        ex = MagicMock()
        ex._quote_all_tiers_batched.return_value = {}   # all-revert = pipe OK
        assert probe_exit_quotes(ex, []) is True

    def test_no_positions_transport_down_chain_id_fallback(self):
        ex = MagicMock()
        ex._quote_all_tiers_batched.return_value = None
        w3 = MagicMock()
        w3.eth.chain_id = RH_CHAIN_ID
        ex._require_w3.return_value = w3
        assert probe_exit_quotes(ex, []) is True
        w3.eth.chain_id = 1                              # wrong chain = fail
        assert probe_exit_quotes(ex, []) is False

    def test_probe_never_raises(self):
        ex = MagicMock()
        ex.quote_sell.side_effect = Exception("rpc down")
        assert probe_exit_quotes(ex, [(TOKEN, 1)]) is False


# ═══════════════════════════ daily loss store ═══════════════════════════════
class TestDailyPnl:
    def test_accumulates_and_persists(self, tmp_path):
        p = str(tmp_path / "pnl.json")
        d = RhDailyPnl(p)
        t = time.time()
        d.record(-10.0, t)
        d.record(-6.5, t)
        assert RhDailyPnl(p).today_usd(t) == pytest.approx(-16.5)

    def test_fresh_state_is_zero_not_halt(self, tmp_path):
        assert RhDailyPnl(str(tmp_path / "nope.json")).today_usd() == 0.0

    def test_utc_day_rollover_resets(self, tmp_path):
        p = str(tmp_path / "pnl.json")
        d = RhDailyPnl(p)
        t = time.time()
        d.record(-30.0, t)
        assert d.today_usd(t + 2 * 86400) == 0.0

    def test_unreadable_existing_state_is_none(self, tmp_path):
        p = tmp_path / "pnl.json"
        p.write_text("{corrupt json")
        assert RhDailyPnl(str(p)).today_usd() is None     # caller halts


# ═══════════════════════════ containment (gates OPEN, mocked exec) ══════════
class TestContainment:
    def test_position_cap_refuses_before_any_network(self, monkeypatch):
        _open_gates(monkeypatch)
        ex = _mock_executor()
        live = RhLiveExecutor(executor=ex)
        with pytest.raises(RhContainmentError, match="position cap"):
            live.live_buy(TOKEN, DEFAULT_MAX_POSITION_USD + 1, 4000.0)
        ex.quote_and_swap_buy.assert_not_called()

    def test_position_cap_env_override(self, monkeypatch):
        _open_gates(monkeypatch)
        monkeypatch.setenv("RH_LIVE_MAX_POSITION_USD", "50")
        live = RhLiveExecutor(executor=_mock_executor())
        assert live.max_position_usd == 50.0
        live.live_buy(TOKEN, 40.0, 4000.0)                # allowed under 50

    def test_buy_converts_usd_to_eth_and_passes_bps(self, monkeypatch):
        _open_gates(monkeypatch)
        ex = _mock_executor()
        live = RhLiveExecutor(executor=ex)
        live.live_buy(TOKEN, 20.0, 4000.0, max_slippage_bps=250)
        ex.quote_and_swap_buy.assert_called_once_with(TOKEN, 0.005, 250)

    def test_default_slippage_and_ceiling(self, monkeypatch):
        _open_gates(monkeypatch)
        ex = _mock_executor()
        live = RhLiveExecutor(executor=ex)
        live.live_buy(TOKEN, 10.0, 4000.0)                # default bps
        assert ex.quote_and_swap_buy.call_args[0][2] == 300
        for bad in (0, -5, SLIPPAGE_BPS_CEILING + 1):
            with pytest.raises(RhContainmentError, match="slippage"):
                live.live_buy(TOKEN, 10.0, 4000.0, max_slippage_bps=bad)

    def test_bad_eth_price_refused(self, monkeypatch):
        _open_gates(monkeypatch)
        live = RhLiveExecutor(executor=_mock_executor())
        for bad in (0, None, -1.0):
            with pytest.raises(RhContainmentError):
                live.live_buy(TOKEN, 10.0, bad)

    def test_daily_stop_halts_buys_not_sells(self, monkeypatch, tmp_path):
        _open_gates(monkeypatch)
        ex = _mock_executor()
        d = RhDailyPnl(str(tmp_path / "pnl.json"))
        live = RhLiveExecutor(executor=ex, daily=d)
        live.record_realized(-DEFAULT_DAILY_STOP_USD - 0.01)
        assert "daily_loss_stop" in (live.buys_halted() or "")
        with pytest.raises(RhContainmentError, match="daily_loss_stop"):
            live.live_buy(TOKEN, 10.0, 4000.0)
        ex.quote_and_swap_buy.assert_not_called()
        live.live_sell(TOKEN)                             # sells NEVER gated
        ex.swap_sell.assert_called_once()

    def test_unreadable_daily_state_halts_buys(self, monkeypatch, tmp_path):
        _open_gates(monkeypatch)
        p = tmp_path / "pnl.json"
        p.write_text("{corrupt")
        live = RhLiveExecutor(executor=_mock_executor(),
                              daily=RhDailyPnl(str(p)))
        assert live.buys_halted() == "daily_pnl_unreadable"

    def test_canary_halt_blocks_buys_not_sells(self, monkeypatch, tmp_path):
        _open_gates(monkeypatch)
        monkeypatch.setenv("RH_SELL_CANARY", "on")
        p = str(tmp_path / "flag.json")
        monkeypatch.setenv("RH_CANARY_FLAG_PATH", p)
        c = RhSellCanary(max_fails=1)
        c.record(False)
        c.write_flag(p)
        ex = _mock_executor()
        live = RhLiveExecutor(executor=ex)
        with pytest.raises(RhCanaryHaltError):
            live.live_buy(TOKEN, 10.0, 4000.0)
        ex.quote_and_swap_buy.assert_not_called()
        live.live_sell(TOKEN)                             # exits free to try
        ex.swap_sell.assert_called_once()

    def test_swap_error_reraised_enriched(self, monkeypatch):
        _open_gates(monkeypatch)
        ex = _mock_executor()
        ex.quote_and_swap_buy.side_effect = RhSwapError("buy failed tx=None")
        live = RhLiveExecutor(executor=ex)
        with pytest.raises(RhSwapError, match="buy failed"):
            live.live_buy(TOKEN, 10.0, 4000.0)


# ═══════════════════════════ wallet truth ═══════════════════════════════════
class TestWalletTruth:
    def _ex(self, eth=1.5, weth_wei=25 * 10 ** 16):
        ex = _mock_executor()
        ex.eth_balance.return_value = eth
        ex.token_balance.return_value = weth_wei
        return ex

    def test_live_arms_baseline_then_delta(self, monkeypatch, tmp_path):
        _open_gates(monkeypatch)
        ex = self._ex(eth=1.5, weth_wei=int(0.25 * 1e18))
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is True and out["live_gate"] is True
        assert out["total_eth"] == pytest.approx(1.75)
        assert out["delta_eth"] == pytest.approx(0.0)     # armed this call
        ex2 = self._ex(eth=1.4, weth_wei=int(0.25 * 1e18))
        out2 = rh_wallet_truth(executor=ex2, eth_price_usd=4000.0)
        assert out2["delta_eth"] == pytest.approx(-0.10)
        assert out2["delta_usd"] == pytest.approx(-400.0)
        # status file shipped for the uploader/dashboard
        status = json.load(open(os.path.join(
            str(tmp_path), "rh_wallet_truth.json")))
        assert status["delta_eth"] == pytest.approx(-0.10)

    def test_paper_mode_stamps_total_usd_without_baseline(self, tmp_path):
        # PAPER mode (gate closed): no baseline/delta arms, but the snapshot
        # still carries total_usd from the passed ETH price so the dashboard
        # wallet card renders the USD balance NOW, pre-live (2026-07-13).
        ex = self._ex(eth=1.5, weth_wei=int(0.25 * 1e18))
        out = rh_wallet_truth(executor=ex, eth_price_usd=1600.0)
        assert out["ok"] is True and out["live_gate"] is False
        assert out["total_eth"] == pytest.approx(1.75)
        assert out["eth_price_usd"] == 1600.0
        assert out["total_usd"] == pytest.approx(2800.0)      # 1.75 * 1600
        assert "delta_eth" not in out                         # baseline unarmed

    def test_read_error_never_fabricates_or_arms(self, monkeypatch, tmp_path):
        _open_gates(monkeypatch)
        ex = _mock_executor()
        ex.eth_balance.side_effect = Exception("rpc 502")
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is False and "rpc 502" in out["error"]
        assert "delta_eth" not in out and "total_eth" not in out
        assert not os.path.exists(os.path.join(
            str(tmp_path), "rh_live_wallet_baseline.json"))

    def test_keyless_watch_via_env_address(self, monkeypatch):
        monkeypatch.setenv("RH_WALLET_ADDRESS", WALLET)
        ex = self._ex()
        ex.wallet_address = None                          # no key loaded
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is True
        ex.eth_balance.assert_called_once_with(WALLET)

    def test_no_wallet_at_all_reports_not_raises(self):
        ex = _mock_executor()
        ex.wallet_address = None
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is False and "no wallet" in out["error"]

    def test_deliberate_rebase_overwrites(self, monkeypatch, tmp_path):
        _open_gates(monkeypatch)
        rh_wallet_truth(executor=self._ex(eth=1.0, weth_wei=0))  # arm at 1.0
        base = rh_wallet_rebase(executor=self._ex(eth=3.0, weth_wei=0))
        assert base["total_eth"] == pytest.approx(3.0)
        out = rh_wallet_truth(executor=self._ex(eth=3.0, weth_wei=0))
        assert out["delta_eth"] == pytest.approx(0.0)


# ═══════════════════════════ lane wiring (offline) ══════════════════════════
class TestLaneWiring:
    def _lane(self):
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "..", "scripts"))
        from rh_paper_lane import PaperLane
        lane = PaperLane.__new__(PaperLane)
        lane._canary = None
        lane._last_canary_ts = 0.0
        return lane

    def test_canary_tick_probes_and_writes_flag(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RH_SELL_CANARY", "on")
        flag = str(tmp_path / "flag.json")
        monkeypatch.setenv("RH_CANARY_FLAG_PATH", flag)
        lane = self._lane()
        ex = MagicMock()
        ex._quote_all_tiers_batched.return_value = {}     # pipe OK
        lane._executor = lambda: ex
        lane._held_pools = lambda: {}
        lane._canary_tick(time.time())
        blob = json.load(open(flag))
        assert blob["consecutive_fails"] == 0
        assert rh_canary_entry_block() is None            # healthy -> no block

    def test_canary_tick_respects_interval(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RH_SELL_CANARY", "on")
        monkeypatch.setenv("RH_CANARY_FLAG_PATH", str(tmp_path / "f.json"))
        lane = self._lane()
        ex = MagicMock()
        ex._quote_all_tiers_batched.return_value = {}
        lane._executor = lambda: ex
        lane._held_pools = lambda: {}
        t = time.time()
        lane._canary_tick(t)
        lane._canary_tick(t + 1)                          # inside interval
        assert ex._quote_all_tiers_batched.call_count == 1

    def test_entry_path_reads_halt_flag_static(self):
        """The lane's _consider_entries checks rh_canary_entry_block FIRST
        and _manage_exits never does (sells free) — source-level wiring."""
        p = os.path.join(os.path.dirname(__file__), "..", "scripts",
                         "rh_paper_lane.py")
        src = open(p, encoding="utf-8").read()
        entries = src[src.index("def _consider_entries"):]
        entries = entries[:entries.index("\n    def ")]
        assert "rh_canary_entry_block" in entries
        exits = src[src.index("def _manage_exits"):]
        exits = exits[:exits.index("\n    def ")]
        assert "rh_canary_entry_block" not in exits
        loop = src[src.index("def strategy_loop"):]
        assert "_canary_tick" in loop[:loop.index("\n    def ")]
