import json
import pytest
from core.bot_config import BotConfig
from core.bot_registry import BotRegistry


def test_registry_loads_all_bots_from_directory(tmp_path):
    (tmp_path / "b1.json").write_text(json.dumps({
        "bot_id": "b1", "display_name": "Bot 1"
    }))
    (tmp_path / "b2.json").write_text(json.dumps({
        "bot_id": "b2", "display_name": "Bot 2", "enabled": False
    }))
    reg = BotRegistry.from_directory(tmp_path)
    assert len(reg.configs) == 2
    by_id = {c.bot_id: c for c in reg.configs}
    assert by_id["b1"].enabled is True
    assert by_id["b2"].enabled is False


def test_registry_skips_malformed_config_files(tmp_path, caplog):
    (tmp_path / "ok.json").write_text(json.dumps({
        "bot_id": "ok", "display_name": "OK"
    }))
    (tmp_path / "bad.json").write_text("not json")
    with caplog.at_level("WARNING"):
        reg = BotRegistry.from_directory(tmp_path)
    assert len(reg.configs) == 1
    assert reg.configs[0].bot_id == "ok"
    assert any("bad.json" in r.message for r in caplog.records)


def test_registry_rejects_duplicate_bot_ids(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({
        "bot_id": "dup", "display_name": "First"
    }))
    (tmp_path / "b.json").write_text(json.dumps({
        "bot_id": "dup", "display_name": "Second"
    }))
    with pytest.raises(ValueError, match="duplicate bot_id"):
        BotRegistry.from_directory(tmp_path)


def test_registry_returns_empty_for_missing_dir(tmp_path):
    reg = BotRegistry.from_directory(tmp_path / "nonexistent")
    assert reg.configs == []


from pathlib import Path


def test_smoke_configs_present_and_loadable():
    config_dir = Path(__file__).parent.parent / "config" / "bots"
    reg = BotRegistry.from_directory(config_dir)
    by_id = {c.bot_id: c for c in reg.configs}
    assert "baseline_v1" in by_id
    assert "no_sol_gate" in by_id
    assert "no_filters" in by_id

    base = by_id["baseline_v1"]
    assert base.sol_macro_h6_block_threshold == -0.3
    assert base.mcap_psych_pc_h24_max == 80.0
    assert base.hard_stop_pct == -15.0

    nsg = by_id["no_sol_gate"]
    assert nsg.sol_macro_h6_block_threshold is None
    assert nsg.sol_macro_h1_block_threshold is None

    nf = by_id["no_filters"]
    assert nf.filters_enforced == ()
