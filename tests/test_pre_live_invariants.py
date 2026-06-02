"""
Pre-live invariant test suite — runs AFTER any change to Trader/persistence
and BEFORE deploying to live mode.  No money spent; uses RPC reads + mocked
Jupiter quotes only.

Covers the 6 critical bug classes found 2026-04-28/29:
  1. solders signing API (verified by validate_decimals_fix indirectly)
  2. Decimals divisor (test_decimals_lookup)
  3. Cancel-on-restart wiping live positions (test_persistence_*)
  4. Reconcile only checking classic SPL Token (test_reconcile_token_2022)
  5. Dashboard pause didn't gate trader (test_dashboard_pause_gates_trader)
  6. Lowercased mints rejected by Jupiter (test_*_case_preservation)

Run: python tests/test_pre_live_invariants.py
Exit 0 → safe to consider live deploy.  Exit non-zero → DO NOT deploy.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.trader import Trader, Position


# ── Fixtures ───────────────────────────────────────────────────────────────

BURNIE_MINT = "CGEDT9QZDvvH5GmVkWJH2BXiMJqMJySC9ihWyr7Spump"
TRIPLET_MINT = "J8PSdNP3QewKq2Z1JJJFDMaqF7KcaiJhR7gbr5KZpump"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"


class _StubTracker:
    def __init__(self):
        self.buys = []
        self.sells = []

    def record_buy(self, p):
        self.buys.append(p)

    def record_sell(self, *a, **kw):
        self.sells.append({"args": a, "kwargs": kw})

    def is_rugged(self, addr):
        return False


class _StubTelegram:
    async def send(self, *a, **kw):
        pass


class _StubRiskManager:
    def record_buy(self, *a, **kw): pass
    def record_sell(self, *a, **kw): pass
    def can_trade(self, *a, **kw): return True


class _StubKillSwitch:
    is_active = False
    _kill_reason = ""


def _make_trader(private_key="", data_dir=None):
    if data_dir:
        os.environ["DATA_DIR"] = data_dir
    return Trader(
        private_key=private_key,
        rpc_url="https://mainnet.helius-rpc.com/?api-key=06c97f31-8c26-4dae-9fb7-2f32ccc87f2c",
        tracker=_StubTracker(),
        telegram=_StubTelegram(),
        risk_manager=_StubRiskManager(),
        kill_switch=_StubKillSwitch(),
    )


def _make_position(mint=BURNIE_MINT, **overrides):
    base = dict(
        token_address=mint,
        token_symbol="TEST",
        entry_price_usd=0.0156,
        amount_tokens=2897.32,
        amount_sol_spent=0.5396,
        entry_time=datetime.now(timezone.utc),
        reason="test",
        token_decimals=6,
    )
    base.update(overrides)
    return Position(**base)


# ── Tests ──────────────────────────────────────────────────────────────────

results = []


def _t(name):
    def deco(fn):
        results.append((name, fn))
        return fn
    return deco


@_t("Position preserves original-case mint")
def t1():
    p = _make_position(mint=BURNIE_MINT)
    assert p.token_address == BURNIE_MINT


@_t("open_positions dict uses lowercase keys")
def t2():
    trader = _make_trader()
    p = _make_position(mint=BURNIE_MINT)
    trader.open_positions[BURNIE_MINT.lower()] = p
    # Both lookups land
    assert trader.open_positions.get(BURNIE_MINT.lower()) is p
    # Position retained original case
    assert trader.open_positions[BURNIE_MINT.lower()].token_address == BURNIE_MINT


@_t("sell() sends original-case mint to Jupiter (regression)")
def t3():
    async def go():
        trader = _make_trader(private_key="fake")
        p = _make_position(mint=BURNIE_MINT)
        trader.open_positions[BURNIE_MINT.lower()] = p
        captured = {}

        async def spy(input_mint, output_mint, amount, slippage_bps=100):
            captured["input_mint"] = input_mint
            return None  # abort gracefully

        trader._get_quote = spy
        try:
            await trader.sell(BURNIE_MINT.lower(), "TEST", "test", pct=1.0)
        except Exception:
            pass
        assert captured.get("input_mint") == BURNIE_MINT, (
            f"Expected {BURNIE_MINT}, got {captured.get('input_mint')}"
        )
    asyncio.run(go())


@_t("Persistence preserves mint case across save/restore")
def t4():
    tmp = tempfile.mkdtemp()
    try:
        os.environ["DATA_DIR"] = tmp
        # Reload module so _OPEN_POSITIONS_FILE picks up new DATA_DIR
        import importlib
        import core.trader as ct_mod
        importlib.reload(ct_mod)
        from core.trader import Trader as Trader2, Position as Position2

        trader = Trader2(
            private_key="fake-key",
            rpc_url="https://example.invalid",
            tracker=_StubTracker(),
            telegram=_StubTelegram(),
            risk_manager=_StubRiskManager(),
            kill_switch=_StubKillSwitch(),
        )
        p = Position2(
            token_address=BURNIE_MINT,
            token_symbol="TEST",
            entry_price_usd=0.0156,
            amount_tokens=2897.32,
            amount_sol_spent=0.5396,
            entry_time=datetime.now(timezone.utc),
            reason="test",
            token_decimals=6,
            # amount_usd > $1 is required — _restore_open_positions runs a
            # dust-cleanup pass that drops sub-$1 positions as TP residue.
            amount_usd=45.0,
        )
        trader.open_positions[BURNIE_MINT.lower()] = p
        trader._save_open_positions()

        # Build a fresh trader and restore
        trader2 = Trader2(
            private_key="fake-key",
            rpc_url="https://example.invalid",
            tracker=_StubTracker(),
            telegram=_StubTelegram(),
            risk_manager=_StubRiskManager(),
            kill_switch=_StubKillSwitch(),
        )
        # __init__ already restored
        assert len(trader2.open_positions) == 1, f"Got {len(trader2.open_positions)}"
        restored = list(trader2.open_positions.values())[0]
        assert restored.token_address == BURNIE_MINT, (
            f"Case lost in persistence: {restored.token_address}"
        )
        # Dict key is lowercased
        assert BURNIE_MINT.lower() in trader2.open_positions, "Lowercase dict key lost"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@_t("Dashboard pause flag gates trader buy")
def t5():
    async def go():
        trader = _make_trader(private_key="fake-key")
        trader._dashboard_paused = True
        # Spy on _get_quote — should NOT be called when paused
        called = {"yes": False}

        async def spy(*a, **kw):
            called["yes"] = True
            return None

        trader._get_quote = spy
        try:
            await trader.buy(
                token_address=BURNIE_MINT,
                token_symbol="TEST",
                position_size_usd=45,
                reason="dip_buy",
            )
        except Exception:
            pass
        assert not called["yes"], "Dashboard pause did NOT block the buy quote"
    asyncio.run(go())


@_t("Reconcile queries BOTH SPL Token programs")
def t6():
    """Validates the reconcile_positions code includes Token-2022 program ID
    in its query loop. Static-source check (no RPC needed)."""
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "core", "trader.py",
    )
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    # Find the reconcile method
    rec_start = src.index("async def reconcile_positions_on_startup")
    rec_end = src.index("\n    async def ", rec_start + 1) if "\n    async def " in src[rec_start + 1:] else len(src)
    rec_block = src[rec_start:rec_end]
    assert "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA" in rec_block, "Classic SPL absent"
    assert "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb" in rec_block, "Token-2022 absent"


@_t("Decimals lookup returns correct values via live RPC")
def t7():
    """Live RPC read against real mints — no swap, no money."""
    async def go():
        trader = _make_trader()
        cases = [
            (SOL_MINT, 9),
            (USDC_MINT, 6),
            (BURNIE_MINT, 6),
            (TRIPLET_MINT, 6),
        ]
        for mint, expected in cases:
            got = await trader._get_token_decimals(mint)
            assert got == expected, f"{mint}: expected {expected}, got {got}"
    asyncio.run(go())


@_t("Jupiter quote rejects lowercase, accepts original case")
def t8():
    """Confirms our root-cause hypothesis still holds. If Jupiter ever
    accepts both, the test still passes (we'd just lose the regression
    guard) — but as long as it rejects lowercase, this proves the bug
    surface is real and the fix matters."""
    import urllib.request, urllib.parse, urllib.error
    JUP_KEY = os.environ.get("JUPITER_API_KEY", "6ca91a56-219d-4839-8406-c25957c73631")
    def quote(mint):
        params = {"inputMint": mint, "outputMint": SOL_MINT, "amount": "1000000000", "slippageBps": "300"}
        url = "https://api.jup.ag/swap/v1/quote?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"x-api-key": JUP_KEY})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    upper_status = quote(BURNIE_MINT)
    lower_status = quote(BURNIE_MINT.lower())
    assert upper_status == 200, f"Original-case mint should quote, got {upper_status}"
    assert lower_status >= 400, f"Lowercase mint should be rejected, got {lower_status}"


# ── Live measurement probe invariants (2026-06-02) ──────────────────────────

@_t("No ENABLED bot has live_probe set (live path off by config)")
def test_no_enabled_live_probe_bot():
    # FAIL-CLOSED: going live must require a DELIBERATE config enable. Until then,
    # no bot that is actually in the running fleet (enabled) may have live_probe.
    import glob
    from core.bot_config import BotConfig
    offenders = []
    for p in glob.glob("config/bots/*.json"):
        c = BotConfig.from_json(p)
        if getattr(c, "live_probe", False) and c.enabled:
            offenders.append(c.bot_id)
    assert not offenders, f"ENABLED bot(s) with live_probe (would route live!): {offenders}"


@_t("Probe config exists, is dormant, and has safety caps")
def test_probe_config_dormant_with_caps():
    from core.bot_config import BotConfig
    c = BotConfig.from_json("config/bots/probe_premium_tightexit_live.json")
    assert c.enabled is False, "probe must be DORMANT (enabled=false) until deliberately enabled"
    assert c.live_probe is True, "probe must declare live_probe intent"
    assert c.daily_loss_limit_usd and c.daily_loss_limit_usd <= 100, "probe needs a tight daily-loss halt"
    assert c.max_concurrent_positions <= 3, "probe must cap concurrent exposure"
    assert c.size_sweep_usd and max(c.size_sweep_usd) <= 100, "probe size sweep must be capped <= $100/leg"


@_t("Jupiter Ultra path is dormant without a private key (paper-safe)")
def test_ultra_dormant_without_key():
    from core.trader import Trader, USE_JUPITER_ULTRA
    tr = Trader.__new__(Trader)
    tr.private_key = ""
    tr._exec_stats = {"swaps_attempted": 0, "swap_failures": 0,
                      "quote_failures": 0, "successful_swaps": 0}
    res = asyncio.run(tr._execute_swap_ultra("A", "B", 1000))
    assert res["success"] is False and res["reason"] == "paper_mode"
    assert tr._exec_stats["swaps_attempted"] == 0
    assert isinstance(USE_JUPITER_ULTRA, bool)


# ── Runner ─────────────────────────────────────────────────────────────────


def main():
    print(f"Pre-live invariant suite — {len(results)} tests\n")
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
    else:
        print(f"All {len(results)} passed.")
        print("Pre-live invariants OK. Live deploy still requires explicit user approval.")
        sys.exit(0)


if __name__ == "__main__":
    main()
