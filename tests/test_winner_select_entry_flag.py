"""BotConfig.winner_select_entry flag (patient sleeve, 2026-06-26)."""
import json
from core.bot_config import BotConfig


def test_winner_select_entry_defaults_false():
    assert BotConfig(bot_id="b", display_name="B").winner_select_entry is False


def test_winner_select_entry_from_json_accepted(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"bot_id": "b", "display_name": "B", "winner_select_entry": True}))
    c = BotConfig.from_json(p)
    assert c.winner_select_entry is True
