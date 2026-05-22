"""Integration smoke: 3 bots running in-memory against a stream of
FeatureBundles. Verifies independent state, isolation, and persistence."""
from pathlib import Path
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator
from core.bot_manager import BotManager
from core.bot_registry import BotRegistry
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager
from core.multi_bot_persistence import MultiBotTradeStore


def _bundle(token, pc_h24=None, sol_pc_h6=None,
            filters_block=(), triggers=("vol_breakout",)):
    return FeatureBundle(
        token=token, address=f"addr_{token}", pair_address=f"pair_{token}",
        chain="solana", snapshot_ts=1.0, price_usd=0.001, mcap_usd=4_000_000.0,
        age_hours=240.0,
        pc_h24=pc_h24, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=sol_pc_h6, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=triggers,
        triggers_shadow=(),
        filters_block=filters_block, filters_pass=(), filters_shadow=(),
        raw_meta={},
    )


def test_3bot_smoke_independent_state(tmp_path):
    config_dir = Path(__file__).parent.parent / "config" / "bots"
    reg = BotRegistry.from_directory(config_dir)
    evaluators = [BotEvaluator(c) for c in reg.configs]
    mgr = BotManager(evaluators=evaluators)

    capitals = {c.bot_id: PerBotCapital(c.bot_id, c.paper_capital_usd)
                for c in reg.configs}
    position_mgrs = {c.bot_id: PerBotPositionManager(c) for c in reg.configs}
    store = MultiBotTradeStore(data_dir=tmp_path)

    # SCENARIO 1: SOL macro down. baseline+no_filters block, no_sol_gate enters.
    b1 = _bundle(token="A", sol_pc_h6=-1.0)
    decisions = mgr.evaluate_all(b1)
    assert {d.bot_id for d in decisions} == {"no_sol_gate"}
    for d in decisions:
        capitals[d.bot_id].reserve_for_buy(d.size_usd)
        position_mgrs[d.bot_id].open_position(d.token, d.entry_price, d.size_usd, entry_time=1.0)
        store.record_trade({
            "type": "buy", "token": d.token, "entry_price": d.entry_price,
            "amount_usd": d.size_usd, "time": "2026-05-23T10:00:00+00:00",
        }, bot_id=d.bot_id)

    # SCENARIO 2: filter_corpse blocks. baseline+no_sol_gate block, no_filters enters.
    b2 = _bundle(token="B", filters_block=("filter_corpse",))
    decisions = mgr.evaluate_all(b2)
    assert {d.bot_id for d in decisions} == {"no_filters"}
    for d in decisions:
        capitals[d.bot_id].reserve_for_buy(d.size_usd)
        position_mgrs[d.bot_id].open_position(d.token, d.entry_price, d.size_usd, entry_time=2.0)
        store.record_trade({
            "type": "buy", "token": d.token, "entry_price": d.entry_price,
            "amount_usd": d.size_usd, "time": "2026-05-23T10:01:00+00:00",
        }, bot_id=d.bot_id)

    # SCENARIO 3: clean candidate, all 3 enter.
    b3 = _bundle(token="C")
    decisions = mgr.evaluate_all(b3)
    assert {d.bot_id for d in decisions} == {"baseline_v1", "no_sol_gate", "no_filters"}
    for d in decisions:
        capitals[d.bot_id].reserve_for_buy(d.size_usd)
        position_mgrs[d.bot_id].open_position(d.token, d.entry_price, d.size_usd, entry_time=3.0)
        store.record_trade({
            "type": "buy", "token": d.token, "entry_price": d.entry_price,
            "amount_usd": d.size_usd, "time": "2026-05-23T10:02:00+00:00",
        }, bot_id=d.bot_id)

    # ASSERTIONS — independent state
    assert capitals["baseline_v1"].in_flight_usd == 20.0
    assert capitals["no_sol_gate"].in_flight_usd == 40.0
    assert capitals["no_filters"].in_flight_usd == 40.0

    assert position_mgrs["baseline_v1"].open_count == 1
    assert position_mgrs["no_sol_gate"].open_count == 2
    assert position_mgrs["no_filters"].open_count == 2

    # Persistence
    assert len(store.load_trades(bot_id="baseline_v1")) == 1
    assert len(store.load_trades(bot_id="no_sol_gate")) == 2
    assert len(store.load_trades(bot_id="no_filters")) == 2
    assert len(store.load_trades()) == 5


def test_3bot_state_persists_across_save_load(tmp_path):
    config_dir = Path(__file__).parent.parent / "config" / "bots"
    reg = BotRegistry.from_directory(config_dir)
    capitals = {c.bot_id: PerBotCapital(c.bot_id, c.paper_capital_usd)
                for c in reg.configs}
    capitals["baseline_v1"].reserve_for_buy(20.0)
    capitals["no_sol_gate"].reserve_for_buy(40.0)

    store = MultiBotTradeStore(data_dir=tmp_path)
    for c in reg.configs:
        store.save_bot_state(c.bot_id, capitals[c.bot_id].to_dict())

    # Simulate restart
    loaded = {
        c.bot_id: PerBotCapital.from_dict(store.load_bot_state(c.bot_id))
        for c in reg.configs
    }
    assert loaded["baseline_v1"].in_flight_usd == 20.0
    assert loaded["baseline_v1"].balance_usd == 1980.0
    assert loaded["no_sol_gate"].in_flight_usd == 40.0
    assert loaded["no_filters"].in_flight_usd == 0.0
