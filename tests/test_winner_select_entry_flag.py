"""BotConfig.winner_select_entry flag (patient sleeve, 2026-06-26)."""
from core.bot_config import BotConfig


def test_winner_select_entry_defaults_false():
    assert BotConfig(bot_id="b", display_name="B").winner_select_entry is False


def test_winner_select_entry_from_json_accepted():
    c = BotConfig.from_json({"bot_id": "b", "display_name": "B", "winner_select_entry": True})
    assert c.winner_select_entry is True
