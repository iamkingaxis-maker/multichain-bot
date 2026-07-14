# tests/test_rh_pre_live_invariants.py
"""RH-chain PRE-LIVE invariant suite — mirror of tests/test_pre_live_invariants.py.

Run BEFORE ever setting RH_PAPER_MODE=false with a real RH_PRIVATE_KEY:

    python tests/test_rh_pre_live_invariants.py

Exit 0 -> safe to CONSIDER the live flip (AxiS approval still required).
Exit non-zero -> DO NOT go live.

Asserts the four load-bearing disciplines (all from Solana incident history):
  1. TRIPLE GATE   — RH_LIVE_CONFIRMED=true AND RH_PAPER_MODE=false AND
                     RH_PRIVATE_KEY present; any missing leg refuses; paper
                     mode wins over everything (fail-to-paper).
  2. CANARY WIRED  — never-buys-while-sells-broken: the lane's entry path
                     reads the halt flag, the probe tick is in the strategy
                     loop, canary defaults ON in live mode, a red canary
                     refuses live_buy but NEVER gates live_sell.
  3. CAPS PRESENT  — per-position USD cap + daily loss halt + slippage
                     ceiling + gas-cost cap, enforced in the live path.
  4. WALLET-TRUTH  — the on-chain balance reader is reachable (real RPC
                     read when networked) and never fabricates on error.

Offline by default under pytest; the standalone runner (__main__) also runs
the real-RPC reachability checks (read-only, no key, no tx). Force them
under pytest with RH_PRELIVE_NETWORK=1.
"""
import json
import os
import sys
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.rh_live_execution as rl
from core.rh_execution import RH_CHAIN_ID, RhChainMismatchError, RhExecutor
from core.rh_live_execution import (
    CANARY_BLOCK_REASON,
    DEFAULT_DAILY_STOP_USD,
    DEFAULT_MAX_POSITION_USD,
    SLIPPAGE_BPS_CEILING,
    RhCanaryHaltError,
    RhContainmentError,
    RhDailyPnl,
    RhLiveExecutor,
    RhLiveGateError,
    RhSellCanary,
    canary_mode_on,
    rh_live_gate,
    rh_wallet_truth,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_KEY = "0x" + "11" * 32
TOKEN = "0x1111111111111111111111111111111111111111"

_RH_KEYS = ("RH_PAPER_MODE", "RH_LIVE_CONFIRMED", "RH_PRIVATE_KEY",
            "RH_SELL_CANARY", "RH_CANARY_FLAG_PATH", "RH_LIVE_STATE_DIR",
            "RH_LIVE_MAX_POSITION_USD", "RH_LIVE_DAILY_STOP_USD",
            "RH_WALLET_ADDRESS")


@contextmanager
def _env(**kw):
    """Set/unset env (None = delete), restore on exit (standalone-safe)."""
    saved = {k: os.environ.get(k) for k in set(list(kw) + list(_RH_KEYS))}
    try:
        for k in _RH_KEYS:
            os.environ.pop(k, None)
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _mock_ex():
    ex = MagicMock()
    ex.paper_only = False
    ex.w3 = None
    ex.wallet_address = "0xAaAaAaAaaAaAaAaAaAaaAaAaAaAaAaAaAaAaAaAa"
    ex.quote_and_swap_buy.return_value = {"success": True}
    ex.swap_sell.return_value = {"success": True}
    return ex


def _network_enabled():
    return (os.environ.get("RH_PRELIVE_NETWORK") == "1"
            or os.environ.get("_RH_PRELIVE_MAIN") == "1")


# ── registry (Solana-suite style) ────────────────────────────────────────────
results = []


def _t(name):
    def deco(fn):
        results.append((name, fn))
        return fn
    return deco


# ═══ 1. TRIPLE GATE ══════════════════════════════════════════════════════════
@_t("Triple gate: shipped default (no flags, no key) is fully dormant")
def test_gate_dormant_by_default():
    with _env():
        ok, reason = rh_live_gate()
        assert ok is False, "gate must be CLOSED with a clean env"
        for leg in ("RH_LIVE_CONFIRMED", "RH_PAPER_MODE", "RH_PRIVATE_KEY"):
            assert leg in reason, f"gate reason must name missing leg {leg}"
        live = RhLiveExecutor(executor=_mock_ex())
        for call in (lambda: live.live_buy(TOKEN, 5.0, 4000.0),
                     lambda: live.live_sell(TOKEN)):
            try:
                call()
                raise AssertionError("live path executed while gate closed!")
            except RhLiveGateError:
                pass


@_t("Triple gate: every single leg is required (no two-leg bypass)")
def test_gate_every_leg_required():
    combos = [
        {"RH_LIVE_CONFIRMED": "true", "RH_PAPER_MODE": "false"},
        {"RH_LIVE_CONFIRMED": "true", "RH_PRIVATE_KEY": FAKE_KEY},
        {"RH_PAPER_MODE": "false", "RH_PRIVATE_KEY": FAKE_KEY},
    ]
    for combo in combos:
        with _env(**combo):
            assert rh_live_gate()[0] is False, f"two legs must NOT open: {combo}"
    with _env(RH_LIVE_CONFIRMED="true", RH_PAPER_MODE="false",
              RH_PRIVATE_KEY=FAKE_KEY):
        assert rh_live_gate()[0] is True, "all three legs must open the gate"


@_t("Triple gate: RH_PAPER_MODE=true beats confirmed+key (fail-to-paper)")
def test_paper_mode_wins():
    with _env(RH_LIVE_CONFIRMED="true", RH_PAPER_MODE="true",
              RH_PRIVATE_KEY=FAKE_KEY):
        assert rh_live_gate()[0] is False, "PAPER_MODE=true must always win"
    # and an UNSET paper mode is paper (the dangerous value must be explicit)
    with _env(RH_LIVE_CONFIRMED="true", RH_PRIVATE_KEY=FAKE_KEY):
        assert rh_live_gate()[0] is False, "unset RH_PAPER_MODE must be PAPER"


@_t("Chain-id mismatch fails CLOSED before any order (wrong-RPC guard)")
def test_chain_id_fail_closed():
    with _env(), patch("core.rh_execution.Web3") as W:
        inst = MagicMock()
        inst.eth.chain_id = 1          # mainnet Ethereum, not RH 4663
        W.return_value = inst
        W.HTTPProvider.return_value = object()
        ex = RhExecutor(rpc_url="http://wrong-chain.invalid", private_key=None)
        try:
            ex.connect()
            raise AssertionError("connect() accepted a wrong-chain RPC!")
        except RhChainMismatchError:
            pass


# ═══ 2. CANARY WIRED ═════════════════════════════════════════════════════════
@_t("Canary: lane entry path reads the halt flag; exits never do (static)")
def test_canary_wired_into_lane():
    src = _src(os.path.join("scripts", "rh_paper_lane.py"))
    entries = src[src.index("def _consider_entries"):]
    entries = entries[:entries.index("\n    def ")]
    assert "rh_canary_entry_block" in entries, \
        "_consider_entries must check the sell-canary halt flag"
    exits = src[src.index("def _manage_exits"):]
    exits = exits[:exits.index("\n    def ")]
    assert "rh_canary_entry_block" not in exits, \
        "exits must NEVER be canary-gated (always free to try)"
    loop = src[src.index("def strategy_loop"):]
    loop = loop[:loop.index("\n    def ")]
    assert "_canary_tick" in loop, "strategy loop must run the canary probe"


@_t("Canary: defaults ON when live is attempted (RH_PAPER_MODE=false)")
def test_canary_default_on_in_live():
    with _env(RH_PAPER_MODE="false"):
        assert canary_mode_on() is True, \
            "canary must default ON in live mode (07-10 incident mandate)"
    with _env():
        assert canary_mode_on() is False, "paper default keeps the lane byte-identical"


@_t("Canary: red canary refuses live_buy, never gates live_sell")
def test_canary_halts_buys_only():
    import tempfile
    d = tempfile.mkdtemp()
    flag = os.path.join(d, "flag.json")
    c = RhSellCanary(max_fails=1)
    c.record(False)
    c.write_flag(flag)
    with _env(RH_LIVE_CONFIRMED="true", RH_PAPER_MODE="false",
              RH_PRIVATE_KEY=FAKE_KEY, RH_SELL_CANARY="on",
              RH_CANARY_FLAG_PATH=flag, RH_LIVE_STATE_DIR=d):
        ex = _mock_ex()
        live = RhLiveExecutor(executor=ex)
        assert (live.buys_halted() or "").startswith(CANARY_BLOCK_REASON)
        try:
            live.live_buy(TOKEN, 5.0, 4000.0)
            raise AssertionError("live_buy executed under a RED canary!")
        except RhCanaryHaltError:
            pass
        assert not ex.quote_and_swap_buy.called
        live.live_sell(TOKEN)          # sells always free to try
        assert ex.swap_sell.called


@_t("Canary: a never-started probe loop fails CLOSED after boot grace")
def test_canary_missing_flag_fails_closed():
    import tempfile
    d = tempfile.mkdtemp()
    with _env(RH_SELL_CANARY="on",
              RH_CANARY_FLAG_PATH=os.path.join(d, "never_written.json")):
        old = rl._MODULE_SPAWNED_AT
        try:
            rl._MODULE_SPAWNED_AT = time.time() - rl.CANARY_GRACE_SECS - 1
            assert rl.rh_canary_entry_block() == CANARY_BLOCK_REASON, \
                "missing canary state past grace must HALT buys"
        finally:
            rl._MODULE_SPAWNED_AT = old


# ═══ 3. CAPS PRESENT ═════════════════════════════════════════════════════════
@_t("Caps: defaults $25 position / $25 daily stop; ceiling + gas cap set")
def test_cap_defaults():
    with _env():
        live = RhLiveExecutor(executor=_mock_ex())
        assert live.max_position_usd == DEFAULT_MAX_POSITION_USD == 25.0
        assert live.daily_stop_usd == DEFAULT_DAILY_STOP_USD == 25.0
        assert 0 < live.default_slippage_bps <= SLIPPAGE_BPS_CEILING
        assert live.max_gas_cost_eth > 0, "gas-cost cap must be armed"


@_t("Caps: position cap + daily loss halt ENFORCED in the live buy path")
def test_caps_enforced():
    import tempfile
    d = tempfile.mkdtemp()
    with _env(RH_LIVE_CONFIRMED="true", RH_PAPER_MODE="false",
              RH_PRIVATE_KEY=FAKE_KEY, RH_SELL_CANARY="off",
              RH_LIVE_STATE_DIR=d):
        ex = _mock_ex()
        live = RhLiveExecutor(executor=ex,
                              daily=RhDailyPnl(os.path.join(d, "pnl.json")))
        try:
            live.live_buy(TOKEN, live.max_position_usd + 0.01, 4000.0)
            raise AssertionError("position cap not enforced!")
        except RhContainmentError:
            pass
        live.record_realized(-live.daily_stop_usd - 1)
        try:
            live.live_buy(TOKEN, 5.0, 4000.0)
            raise AssertionError("daily loss halt not enforced!")
        except RhContainmentError:
            pass
        assert not ex.quote_and_swap_buy.called
        live.live_sell(TOKEN)          # exits unaffected by the daily stop
        assert ex.swap_sell.called


@_t("Paper lane money reach is GATE-WRAPPED (static)")
def test_lane_never_swaps():
    """2026-07-12 LIVE FILL PROBE update: the lane may reach live_buy/
    live_sell, but ONLY through the four-condition routing glue (triple gate
    + RH_LIVE_PROBE_BOTS opt-in). The raw RhExecutor money methods and
    _sign_and_send stay unreferenced; each live_* has exactly ONE call site
    sitting behind its gate (live_route_open / meta['live'])."""
    src = _src(os.path.join("scripts", "rh_paper_lane.py"))
    for forbidden in ("quote_and_swap_buy", "swap_sell", "_sign_and_send"):
        assert forbidden not in src, \
            f"paper lane must never reference {forbidden}"
    assert src.count(".live_buy(") == 1, "exactly ONE live_buy call site"
    leg = src[src.index("def _live_buy_leg"):]
    assert ".live_buy(" in leg[:leg.index("\n    def ")], \
        "live_buy must live inside _live_buy_leg"
    callers = [i for i in range(len(src))
               if src.startswith("self._live_buy_leg(", i)]
    assert len(callers) == 1 and "if live_route_open(st.bot.bot_id):" in \
        src[callers[0] - 400:callers[0]], \
        "_live_buy_leg's only caller must sit behind live_route_open"
    # TWO sanctioned live_sell sites: the position-EXIT path (behind
    # meta["live"]) and the one-shot ORPHAN recovery (behind RH_SELL_ORPHAN +
    # rh_live_gate, added 2026-07-14 to clear a stranded live position).
    assert src.count(".live_sell(") == 2, \
        "exactly TWO sanctioned live_sell sites (exit + orphan-recovery)"
    i = src.index(".live_sell(")                          # 1st = exit path
    assert 'if meta.get("live"):' in src[i - 2000:i], \
        "the exit live_sell must sit behind the position's live flag"
    j = src.index(".live_sell(", i + 1)                   # 2nd = orphan recovery
    assert 'RH_SELL_ORPHAN' in src[j - 1500:j], \
        "the orphan-recovery live_sell must sit behind RH_SELL_ORPHAN"


# ═══ 4. WALLET-TRUTH ═════════════════════════════════════════════════════════
@_t("Wallet-truth: read errors report, never fabricate, never arm baseline")
def test_wallet_truth_honest_on_error():
    import tempfile
    d = tempfile.mkdtemp()
    with _env(RH_LIVE_CONFIRMED="true", RH_PAPER_MODE="false",
              RH_PRIVATE_KEY=FAKE_KEY, RH_LIVE_STATE_DIR=d):
        ex = _mock_ex()
        ex.eth_balance.side_effect = Exception("rpc down")
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is False and "rpc down" in out.get("error", "")
        assert "delta_eth" not in out and "total_eth" not in out
        assert not os.path.exists(
            os.path.join(d, "rh_live_wallet_baseline.json")), \
            "baseline must NOT arm on a failed read"


@_t("Wallet-truth: baseline arms ONLY while the triple gate is open")
def test_wallet_truth_baseline_gated():
    import tempfile
    d = tempfile.mkdtemp()
    ex = _mock_ex()
    ex.eth_balance.return_value = 1.0
    ex.token_balance.return_value = 0
    with _env(RH_LIVE_STATE_DIR=d):                      # dormant
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is True and "delta_eth" not in out
        assert not os.path.exists(os.path.join(d, "rh_live_wallet_baseline.json"))
    with _env(RH_LIVE_CONFIRMED="true", RH_PAPER_MODE="false",
              RH_PRIVATE_KEY=FAKE_KEY, RH_LIVE_STATE_DIR=d):  # live
        out = rh_wallet_truth(executor=ex)
        assert out["delta_eth"] == 0.0
        assert os.path.exists(os.path.join(d, "rh_live_wallet_baseline.json"))


@_t("Wallet-truth REACHABLE: real RPC balance read (read-only, no key)")
def test_wallet_truth_reachable_network():
    if not _network_enabled():
        print("        (skipped: set RH_PRELIVE_NETWORK=1 or run standalone)")
        return
    import tempfile
    d = tempfile.mkdtemp()
    from core.rh_execution import WETH9
    with _env(RH_LIVE_STATE_DIR=d, RH_WALLET_ADDRESS=WETH9):
        ex = RhExecutor()              # keyless, official RPC
        out = rh_wallet_truth(executor=ex)
        assert out["ok"] is True, f"wallet-truth unreachable: {out.get('error')}"
        assert out["chain_id"] == RH_CHAIN_ID
        assert isinstance(out["total_eth"], float)


@_t("Sell-path canary probe REACHABLE: quote pipe answers (read-only)")
def test_canary_probe_reachable_network():
    if not _network_enabled():
        print("        (skipped: set RH_PRELIVE_NETWORK=1 or run standalone)")
        return
    from core.rh_live_execution import probe_exit_quotes
    ex = RhExecutor()                  # keyless
    assert probe_exit_quotes(ex, []) is True, \
        "canary transport probe failed against the live RPC"


# ── runner (Solana-suite style) ──────────────────────────────────────────────
def main():
    os.environ["_RH_PRELIVE_MAIN"] = "1"   # standalone = network checks ON
    print(f"RH pre-live invariant suite — {len(results)} checks\n")
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
        print(f"{len(failed)} of {len(results)} FAILED — DO NOT GO LIVE ON RH")
        sys.exit(1)
    print(f"All {len(results)} passed.")
    print("RH pre-live invariants OK. The live flip STILL requires explicit "
          "AxiS approval + the sell-path end-to-end dust test.")
    sys.exit(0)


if __name__ == "__main__":
    main()
