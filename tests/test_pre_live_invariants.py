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
    def is_daily_limit_hit(self, *a, **kw): return False   # buy path queries this; stub: limit not hit


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

@_t("Only the deliberate go-live set carries live_probe (no accidental live bot)")
def test_no_enabled_live_probe_bot():
    # FAIL-CLOSED: going live must require a DELIBERATE config enable. The ONLY
    # enabled bots permitted to carry live_probe are the explicit go-live set
    # below. Any OTHER enabled bot with live_probe is an accident that would route
    # real money. (2026-06-13: badday_flush_conviction_LIVE added — a dedicated
    # live CLONE of the validated badday_flush_conviction (n=181 / +$2.24/tr paper),
    # float-sized ($15 base, 2x->$30, maxconc 1). The paper original stays paper for
    # the conviction-vs-flat A/B; only the clone routes live.)
    import glob
    from core.bot_config import BotConfig
    # 2026-06-20: live bot swapped to badday_flush_nf15_live (FLAT $100 + net_flow_15s>=0
    # demand gate) — best drawdown of the badday family (worst -$18 within AxiS's -$21 pain
    # line), real-selection edge (not leverage). badday_flush_conviction_live RETIRED to
    # paper (live_probe=false, still enabled for the conviction-vs-flat A/B) so it no longer
    # routes money; it stays in this set harmlessly (the test only flags enabled+live_probe).
    # 2026-06-24: tiny LIVE FILL-ACCURACY PROBE sanctioned. badday_fill_probe_live ($5,
    # daily-loss $10, max-2-buys/token) is the SOLE live_probe=true+enabled bot — it measures
    # decision->landed-fill (landing latency + MEV residual) on $5 swaps held to the normal
    # badday exit. nf15_live was set live_probe=false (neutralized to paper) so the $100 bot no
    # longer routes money; only the $5 probe does.
    INTENDED_LIVE = {"badday_fill_probe_live",
                     "badday_flush_nf15_live",
                     "badday_flush_conviction_live", "badday_flush_live",
                     "deepflush_timebox_live", "timebox_probe_5mgreen_live"}
    offenders = []
    for p in glob.glob("config/bots/*.json"):
        c = BotConfig.from_json(p)
        if (getattr(c, "live_probe", False) and c.enabled
                and c.bot_id not in INTENDED_LIVE):
            offenders.append(c.bot_id)
    assert not offenders, (f"ENABLED bot(s) with live_probe outside the go-live set "
                           f"(would route live!): {offenders}")


@_t("Probe configs exist, are dormant, fixed-size, and capped")
def test_probe_configs_dormant_with_caps():
    from core.bot_config import BotConfig
    sizes = {20.0: (2, 30.0), 50.0: (2, 50.0), 100.0: (1, 75.0)}
    for sz in (20, 50, 100):
        c = BotConfig.from_json(f"config/bots/probe_tightexit_live_{sz}.json")
        assert c.enabled is False, f"probe_{sz} must be DORMANT (enabled=false)"
        assert c.live_probe is True, f"probe_{sz} must declare live_probe intent"
        assert c.base_position_usd == float(sz) and c.base_position_usd <= 100, \
            f"probe_{sz} must be FIXED size <= $100"
        # multipliers neutralized -> size truly fixed
        assert (c.alpha_multiplier == c.macro_up_multiplier == c.premium_runner_multiplier
                == c.marginal_multiplier == 1.0), f"probe_{sz} multipliers must be 1.0"
        exp_maxc, exp_dl = sizes[float(sz)]
        assert c.daily_loss_limit_usd == exp_dl and c.daily_loss_limit_usd <= 100, \
            f"probe_{sz} needs a tight daily-loss halt"
        assert c.max_concurrent_positions == exp_maxc, f"probe_{sz} concurrent cap"


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


# ── Single-authoritative-live-gate invariants (2026-06-04 live-execution audit) ──

@_t("force_paper routes a live-key buy to PAPER, never the live quote [C1]")
def test_force_paper_routes_paper():
    async def go():
        trader = _make_trader(private_key="fake-key")  # live key present
        trader._dashboard_paused = False
        os.environ.pop("TRADING_PAUSED", None)
        _old = os.environ.get("STRATEGY_ALLOWLIST")
        os.environ["STRATEGY_ALLOWLIST"] = "dip_buy"  # ensure allowlist doesn't pre-block
        called = {"q": False}

        async def spy(*a, **kw):
            called["q"] = True
            return None
        trader._get_quote = spy
        try:
            await trader.buy(token_address=BURNIE_MINT, token_symbol="T",
                             reason="r", strategy="dip_buy", force_paper=True)
        except Exception:
            pass
        finally:
            if _old is None:
                os.environ.pop("STRATEGY_ALLOWLIST", None)
            else:
                os.environ["STRATEGY_ALLOWLIST"] = _old
        # A broken C1 (force_paper ignored) would fall through to the LIVE branch and
        # call _get_quote. force_paper MUST keep a live-key buy on the paper path.
        assert not called["q"], "force_paper=True reached the LIVE quote path (C1 broken)"
    asyncio.run(go())


@_t("Single live gate: all direct trader.buy/sell callers are allowlist-gated [C1-C6]")
def test_single_live_gate_static():
    """The invariant: with a key present, the ONLY route to a real order is the
    should_route_live (live_probe) fleet path. Every direct trader.buy/sell caller must
    be force_paper'd or allowlist-gated. Static source check — robust + key-free."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def src(p):
        with open(os.path.join(root, p), encoding="utf-8") as f:
            return f.read()
    tr = src("core/trader.py")
    assert "force_paper: bool = False" in tr, "C1: force_paper kwarg missing on trader"
    assert tr.count("if not self.private_key or force_paper:") >= 2, \
        "C1: force_paper not honored in BOTH buy and sell paper branches"
    assert "STRATEGY_ALLOWLIST unset (fail-closed)" in tr, \
        "C6: STRATEGY_ALLOWLIST does not fail CLOSED in live"
    assert "force_paper=bool(self.trader.private_key)" in src("feeds/dip_scanner.py"), \
        "C2: legacy dip-buy (dip_scanner.py:15549) not force_paper'd in live"
    assert "force_paper=True" in src("core/multi_source_scanner.py"), \
        "C3: legacy MSS buy not force_paper'd"
    assert src("core/scalper.py").count("force_paper=True") >= 2, \
        "C4: scalper buy+sell not force_paper'd"
    assert "force_paper=True" in src("feeds/graduation_sniper.py"), \
        "C5: graduation-sniper buy not force_paper'd"


# ── 2026-06-13 pre-live audit fixes (#1/#3/#5) ───────────────────────────────

@_t("LIVE_CONFIRMED required: a key present without it stays PAPER [fail-to-paper, #3]")
def test_live_confirmed_required_for_live_key():
    from utils.config import Config, _apply_env_overrides
    keys = ("SOLANA_PRIVATE_KEY", "SCALPER_SOLANA_PRIVATE_KEY", "PAPER_MODE", "LIVE_CONFIRMED")
    _saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["SOLANA_PRIVATE_KEY"] = "fakekey123"
        os.environ.pop("PAPER_MODE", None)
        os.environ.pop("LIVE_CONFIRMED", None)
        c = Config(); _apply_env_overrides(c)
        assert c.solana_private_key == "", \
            "key present + no LIVE_CONFIRMED must FAIL TO PAPER (the dangerous default)"
        os.environ["LIVE_CONFIRMED"] = "true"           # explicit ack -> live key kept
        c2 = Config(); _apply_env_overrides(c2)
        assert c2.solana_private_key == "fakekey123", "LIVE_CONFIRMED=true must allow the key"
        os.environ["PAPER_MODE"] = "true"               # PAPER_MODE still wins
        c3 = Config(); _apply_env_overrides(c3)
        assert c3.solana_private_key == "", "PAPER_MODE=true must override LIVE_CONFIRMED"
    finally:
        for k, v in _saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@_t("ScalperWallet refuses a live key without explicit ack [dead-route guard, #5]")
def test_scalper_wallet_fail_closed_on_live_key():
    from execution.scalper_wallet import ScalperWallet
    _saved = os.environ.get("SCALPER_WALLET_LIVE_ACK")
    try:
        os.environ.pop("SCALPER_WALLET_LIVE_ACK", None)
        w = ScalperWallet("sol", "Solana", "", "rpc", "weth", is_solana=True)  # paper OK
        assert w.paper_mode is True
        raised = False
        try:
            ScalperWallet("sol", "Solana", "REALKEY", "rpc", "weth", is_solana=True)
        except RuntimeError:
            raised = True
        assert raised, "ScalperWallet took a live key without the ack (ungated live route!)"
        os.environ["SCALPER_WALLET_LIVE_ACK"] = "true"     # conscious re-wire -> allowed
        w2 = ScalperWallet("sol", "Solana", "REALKEY", "rpc", "weth", is_solana=True)
        assert w2.paper_mode is False
    finally:
        if _saved is None:
            os.environ.pop("SCALPER_WALLET_LIVE_ACK", None)
        else:
            os.environ["SCALPER_WALLET_LIVE_ACK"] = _saved


@_t("Profit-sweep state (floor-HWM + interval) survives a restart [deploy-amnesia, #1]")
def test_sweep_state_persists_across_restart():
    d = tempfile.mkdtemp()
    _saved = os.environ.get("DATA_DIR")
    try:
        os.environ["DATA_DIR"] = d
        tr = Trader.__new__(Trader)        # no live deps; persistence uses only self + DATA_DIR
        tr._floor_hwm_usd = 2000.0
        tr._last_sweep_ts = 1234567.0
        tr._persist_sweep_state()
        tr2 = Trader.__new__(Trader)       # simulate a redeploy: fresh instance, cold attrs
        tr2._load_sweep_state_once()
        assert tr2._floor_hwm_usd == 2000.0, \
            "floor high-water lost on restart — the fat-finger drop guard would be defeated"
        assert tr2._last_sweep_ts == 1234567.0, \
            "sweep interval lost on restart — would re-fire immediately post-deploy"
    finally:
        os.environ.pop("DATA_DIR", None) if _saved is None else os.environ.__setitem__("DATA_DIR", _saved)
        shutil.rmtree(d, ignore_errors=True)


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
