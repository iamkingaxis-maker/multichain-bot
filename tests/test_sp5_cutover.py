import json
import pytest
from pathlib import Path
from scripts.sp5_cutover import perform_cutover, build_new_baseline


def _baseline_dict():
    return {
        "bot_id": "baseline_v1",
        "display_name": "Baseline (current production)",
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


def _champion_dict():
    d = _baseline_dict()
    d["bot_id"] = "champion_proposal"
    d["display_name"] = "Champion proposal (synthesized 2026-05-30)"
    d["enabled"] = False
    d["alpha_multiplier"] = 1.0
    d["hard_stop_pct"] = -10.0
    d["filters_disabled"] = ["filter_topping", "filter_low_volatility"]
    return d


def test_build_new_baseline_preserves_bot_id():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert new_baseline["bot_id"] == "baseline_v1"


def test_build_new_baseline_copies_champion_fields():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert new_baseline["alpha_multiplier"] == 1.0
    assert new_baseline["hard_stop_pct"] == -10.0
    assert new_baseline["filters_disabled"] == ["filter_topping", "filter_low_volatility"]


def test_build_new_baseline_enabled_true():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert new_baseline["enabled"] is True


def test_build_new_baseline_updates_display_name():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert "post-cutover" in new_baseline["display_name"].lower()


def test_perform_cutover_dry_run_does_not_modify_files(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))
    original_baseline_content = baseline_path.read_text()
    original_champion_content = champion_path.read_text()
    backup_path, new_baseline = perform_cutover(baseline_path, champion_path, confirm=False)
    assert baseline_path.read_text() == original_baseline_content
    assert champion_path.read_text() == original_champion_content
    assert new_baseline["alpha_multiplier"] == 1.0


def test_perform_cutover_confirm_creates_backup(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))
    backup_path, new_baseline = perform_cutover(baseline_path, champion_path, confirm=True)
    assert backup_path is not None
    assert backup_path.exists()
    backup_content = json.loads(backup_path.read_text())
    assert backup_content["alpha_multiplier"] == 1.5


def test_perform_cutover_confirm_writes_new_baseline(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))
    perform_cutover(baseline_path, champion_path, confirm=True)
    new_baseline = json.loads(baseline_path.read_text())
    assert new_baseline["bot_id"] == "baseline_v1"
    assert new_baseline["alpha_multiplier"] == 1.0
    assert new_baseline["hard_stop_pct"] == -10.0


def test_perform_cutover_confirm_disables_champion(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))
    perform_cutover(baseline_path, champion_path, confirm=True)
    new_champion = json.loads(champion_path.read_text())
    assert new_champion["enabled"] is False
