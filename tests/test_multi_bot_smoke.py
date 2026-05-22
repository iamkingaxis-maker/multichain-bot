"""Integration smoke: 3 bots running in-memory against a stream of
FeatureBundles. Verifies independent state, isolation, and persistence."""
import json
from pathlib import Path
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator
from core.bot_manager import BotManager
from core.bot_registry import BotRegistry
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager
from core.multi_bot_persistence import MultiBotTradeStore


def _seed_smoke_configs(tmp_dir):
    """Write the 3 SP1 smoke bot configs into a temporary directory.

    Decouples this test from the growing production catalog so adding bots
    to config/bots/ never breaks the SP1 harness validation.
    """
    bots_dir = tmp_dir / "config_bots"
    bots_dir.mkdir(exist_ok=True)
    base = {
        "bot_id": "baseline_v1",
        "display_name": "Baseline",
        "enabled": True,
        "paper_capital_usd": 2000.0,
        "base_position_usd": 20.0,
        "max_concurrent_positions": 3,
        "alpha_multiplier": 1.5,
        "macro_up_multiplier": 1.5,
        "premium_runner_multiplier": 3.0,
        "marginal_multiplier": 0.5,
        "sol_macro_h6_block_threshold": -0.3,
        "sol_macro_h1_block_threshold": -0.7,
        "btc_macro_h1_block_threshold": None,
        "pc_h24_max": None, "pc_h24_min": None, "pc_h1_max": None,
        "age_h_min": None, "age_h_max": None,
        "mcap_min": None, "mcap_max": None, "vol_h1_min": 1000.0,
        "filters_enforced": None, "filters_disabled": [],
        "triggers_allowed": None, "triggers_disabled": [],
        "min_triggers_to_fire": 1, "require_alpha_trigger": False,
        "mcap_psych_pc_h24_max": 80.0,
        "tp1_pct": 5.0, "tp1_sell_fraction": 0.75,
        "tp2_pct": 10.0, "tp2_sell_fraction": 0.25,
        "trail_pp": 3.0, "hard_stop_pct": -15.0,
        "pre_stop_bail_pnl_pct": -3.0, "pre_stop_bail_vol_m5_max": 500.0,
        "slow_bleed_minutes": 60, "slow_bleed_pnl_threshold": -8.0,
        "trading_hour_utc_start": 0, "trading_hour_utc_end": 24,
    }
    (bots_dir / "baseline_v1.json").write_text(json.dumps(base))
    nsg = dict(base, bot_id="no_sol_gate", display_name="No SOL gate",
               sol_macro_h6_block_threshold=None,
               sol_macro_h1_block_threshold=None)
    (bots_dir / "no_sol_gate.json").write_text(json.dumps(nsg))
    nf = dict(base, bot_id="no_filters", display_name="No filters",
              filters_enforced=[])
    (bots_dir / "no_filters.json").write_text(json.dumps(nf))
    return bots_dir


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
    config_dir = _seed_smoke_configs(tmp_path)
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
    config_dir = _seed_smoke_configs(tmp_path)
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
