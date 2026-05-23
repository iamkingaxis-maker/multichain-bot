import json
from pathlib import Path

from scripts.sp4_champion_synthesis import (
    pick_best_from_pair, synthesize_champion, MIN_BASELINE_SAMPLE,
)
from scripts.sp4_common import BotMetrics


def _m(bot_id, n, per_tr):
    return BotMetrics(
        bot_id=bot_id, sample_n=n, total_pnl_usd=n * per_tr,
        pnl_per_trade=per_tr, win_rate=0.5, avg_win_usd=2.0,
        avg_loss_usd=-1.5, best_trade_usd=10.0, worst_trade_usd=-5.0,
        throughput_x_pnl=n * per_tr,
    )


def test_pick_best_from_pair_returns_higher_per_tr():
    a = _m("a", 10, 0.5)
    b = _m("b", 10, 1.0)
    winner = pick_best_from_pair(a, b)
    assert winner.bot_id == "b"


def test_pick_best_from_pair_falls_back_to_first_on_no_data():
    a = BotMetrics(bot_id="a", sample_n=0, total_pnl_usd=0.0,
                   pnl_per_trade=None, win_rate=None, avg_win_usd=None,
                   avg_loss_usd=None, best_trade_usd=0.0,
                   worst_trade_usd=0.0, throughput_x_pnl=0.0)
    b = BotMetrics(bot_id="b", sample_n=0, total_pnl_usd=0.0,
                   pnl_per_trade=None, win_rate=None, avg_win_usd=None,
                   avg_loss_usd=None, best_trade_usd=0.0,
                   worst_trade_usd=0.0, throughput_x_pnl=0.0)
    winner = pick_best_from_pair(a, b)
    assert winner.bot_id == "a"


def test_synthesize_refuses_insufficient_baseline(tmp_path):
    baseline_config = {
        "bot_id": "baseline_v1", "display_name": "Baseline",
        "enabled": True, "paper_capital_usd": 2000.0,
        "base_position_usd": 20.0, "max_concurrent_positions": 3,
        "alpha_multiplier": 1.5, "macro_up_multiplier": 1.5,
        "premium_runner_multiplier": 3.0, "marginal_multiplier": 0.5,
        "sol_macro_h6_block_threshold": -0.3,
        "sol_macro_h1_block_threshold": -0.7,
        "btc_macro_h1_block_threshold": None,
        "pc_h24_max": None, "pc_h24_min": None, "pc_h1_max": None,
        "age_h_min": None, "age_h_max": None, "mcap_min": None,
        "mcap_max": None, "vol_h1_min": 1000.0,
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
    baseline_path = tmp_path / "baseline_v1.json"
    baseline_path.write_text(json.dumps(baseline_config))
    out_path = tmp_path / "champion_proposal.json"
    reasoning_path = tmp_path / "champion_synthesis.md"

    insufficient = _m("baseline_v1", MIN_BASELINE_SAMPLE - 1, 0.5)
    metrics_by_id = {"baseline_v1": insufficient}
    result = synthesize_champion(
        metrics_by_id, baseline_path, out_path, reasoning_path,
    )
    assert result is False
    assert not out_path.exists()
