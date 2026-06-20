import asyncio
import pytest
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator, BuyDecision
from core.bot_manager import BotManager


def _bundle():
    return FeatureBundle(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1.0, price_usd=0.001, mcap_usd=4_000_000.0, age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("vol_breakout",),  # non-alpha so std size
        triggers_shadow=(), filters_block=(), filters_pass=(), filters_shadow=(),
        raw_meta={},
    )


def test_bot_manager_fans_out_to_all_bots():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1"),
        BotConfig(bot_id="b2", display_name="B2"),
        BotConfig(bot_id="b3", display_name="B3"),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 3
    assert {d.bot_id for d in decisions} == {"b1", "b2", "b3"}


def test_bot_manager_isolates_exceptions_in_one_bot():
    class _BoomEvaluator(BotEvaluator):
        def evaluate(self, b):
            raise RuntimeError("boom")
    cfgs = [
        BotConfig(bot_id="ok", display_name="OK"),
        BotConfig(bot_id="boom", display_name="Boom"),
        BotConfig(bot_id="also_ok", display_name="Also OK"),
    ]
    evaluators = [
        BotEvaluator(cfgs[0]),
        _BoomEvaluator(cfgs[1]),
        BotEvaluator(cfgs[2]),
    ]
    mgr = BotManager(evaluators=evaluators)
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 2
    assert {d.bot_id for d in decisions} == {"ok", "also_ok"}


def test_bot_manager_skips_disabled_bots():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1", enabled=True),
        BotConfig(bot_id="b2", display_name="B2", enabled=False),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 1
    assert decisions[0].bot_id == "b1"


def test_bot_manager_enabled_bot_ids():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1", enabled=True),
        BotConfig(bot_id="b2", display_name="B2", enabled=False),
        BotConfig(bot_id="b3", display_name="B3", enabled=True),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    assert mgr.enabled_bot_ids() == ["b1", "b3"]


# ─────────────────────────────────────────────────────────────────────────────
# CAPSTONE FILL-SPEED (2026-06-20): evaluate_all_async (yield every K) +
# has_momentum_bot (prescreen helper). Money-path: async variant MUST return the
# SAME decisions as the sync version (identical set/order); the only added
# behaviour is cooperative yields so the event loop breathes between K bots.
# ─────────────────────────────────────────────────────────────────────────────

def test_evaluate_all_async_identical_to_sync():
    cfgs = [BotConfig(bot_id=f"b{i}", display_name=f"B{i}") for i in range(20)]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    b = _bundle()
    sync = mgr.evaluate_all(b)

    async def _go():
        return await mgr.evaluate_all_async(b, yield_every=5)

    asy = asyncio.run(_go())
    # Identical decisions, identical ORDER (deterministic fan-out).
    assert [d.bot_id for d in asy] == [d.bot_id for d in sync]
    assert {d.bot_id for d in asy} == {f"b{i}" for i in range(20)}


def test_evaluate_all_async_yields_every_k():
    cfgs = [BotConfig(bot_id=f"b{i}", display_name=f"B{i}") for i in range(31)]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    b = _bundle()

    sleeps = {"n": 0}
    real_sleep = asyncio.sleep

    async def _counting_sleep(secs, *a, **k):
        if secs == 0:
            sleeps["n"] += 1
        return await real_sleep(secs, *a, **k)

    async def _go():
        import core.bot_manager as bm
        orig = bm.asyncio.sleep
        bm.asyncio.sleep = _counting_sleep
        try:
            return await mgr.evaluate_all_async(b, yield_every=10)
        finally:
            bm.asyncio.sleep = orig

    decisions = asyncio.run(_go())
    # 31 bots, yield every 10 -> yields after bot 10, 20, 30 = 3 yields.
    assert sleeps["n"] == 3, sleeps
    assert len(decisions) == 31


def test_evaluate_all_async_respects_allowlist():
    cfgs = [BotConfig(bot_id=f"b{i}", display_name=f"B{i}") for i in range(5)]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])

    async def _go():
        return await mgr.evaluate_all_async(
            _bundle(), bot_allowlist={"b1", "b3"}, yield_every=2)

    decisions = asyncio.run(_go())
    assert {d.bot_id for d in decisions} == {"b1", "b3"}


def test_has_momentum_bot_detects_momentum_mode():
    cfgs = [
        BotConfig(bot_id="dip1", display_name="D1"),
        BotConfig(bot_id="mom1", display_name="M1", momentum_mode=True),
        BotConfig(bot_id="dip2", display_name="D2"),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    assert mgr.has_momentum_bot() is True
    # Scoped to allowlist: only dip bots -> no momentum.
    assert mgr.has_momentum_bot({"dip1", "dip2"}) is False
    # Allowlist includes the momentum bot.
    assert mgr.has_momentum_bot({"dip1", "mom1"}) is True


def test_has_momentum_bot_false_when_none():
    cfgs = [BotConfig(bot_id="d1", display_name="D1"),
            BotConfig(bot_id="d2", display_name="D2")]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    assert mgr.has_momentum_bot() is False


def test_has_momentum_bot_ignores_disabled():
    cfgs = [
        BotConfig(bot_id="d1", display_name="D1"),
        BotConfig(bot_id="mom_off", display_name="MOFF",
                  momentum_mode=True, enabled=False),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    # A DISABLED momentum bot can never fire -> prescreen must not be defeated by it.
    assert mgr.has_momentum_bot() is False


def _bundle_no_triggers():
    b = _bundle()
    # FeatureBundle is a frozen dataclass-style record; rebuild with empty triggers.
    import dataclasses
    return dataclasses.replace(b, triggers_fired=())


def test_prescreen_invariant_no_trigger_no_momentum_yields_no_buys():
    """The pre-screen skips the heavy fan-out iff NO bot could buy this tick:
    no dip triggers fired AND no momentum bot present. This test PROVES the
    buy-preservation invariant: under exactly that condition, evaluate_all itself
    returns NO decisions (dip bots require >=1 trigger), so skipping it drops no
    buy. (If a momentum bot is present, the prescreen does NOT skip — see below.)"""
    cfgs = [BotConfig(bot_id="dip1", display_name="D1"),
            BotConfig(bot_id="dip2", display_name="D2")]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    b = _bundle_no_triggers()
    # Prescreen WOULD skip: no triggers + no momentum bot.
    assert mgr.has_momentum_bot() is False
    # ...and the fan-out it would skip produces zero buys anyway -> safe to skip.
    assert mgr.evaluate_all(b) == []


def test_prescreen_does_not_skip_when_triggers_present():
    """A token WITH a trigger can fire a dip bot -> prescreen must NOT skip, and
    the fan-out actually produces a buy (the 'same buys still fire' half)."""
    cfgs = [BotConfig(bot_id="dip1", display_name="D1")]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    decisions = mgr.evaluate_all(_bundle())  # _bundle has triggers_fired=("vol_breakout",)
    assert len(decisions) == 1 and decisions[0].bot_id == "dip1"
