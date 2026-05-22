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
