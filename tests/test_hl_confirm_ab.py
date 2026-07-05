# tests/test_hl_confirm_ab.py — HL-confirm A/B jersey integrity (2026-07-05)
import json, pathlib
from core.bot_config import BotConfig


def _cfg(name):
    return BotConfig(**json.loads(
        pathlib.Path(f"config/bots/{name}.json").read_text()))


def test_clone_matches_flush_except_hl():
    h, f = _cfg("badday_flush_hlconfirm_ab"), _cfg("badday_flush")
    assert h.enabled is True and not getattr(h, "live_probe", None)
    assert h.hl_confirm_entry is True and f.hl_confirm_entry is False
    # A/B integrity: identical entry gates + exits; own exclusion pool
    assert [tuple(x) for x in h.entry_gate] == [tuple(x) for x in f.entry_gate]
    assert (h.tp1_pct, h.tp2_pct, h.hard_stop_pct) == (f.tp1_pct, f.tp2_pct, f.hard_stop_pct)
    assert h.exclusion_pool == "badday_flush_hlconfirm_ab"
    assert h.entry_gate_require_data == f.entry_gate_require_data


def test_default_off_everywhere_else():
    for name in ("badday_flush", "badday_young_absorb", "badday_allday"):
        assert _cfg(name).hl_confirm_entry is False
