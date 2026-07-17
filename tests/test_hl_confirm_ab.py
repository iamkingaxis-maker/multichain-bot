# tests/test_hl_confirm_ab.py — RETIREMENT PIN (2026-07-17)
# Originally the HL-confirm A/B jersey-integrity tests (2026-07-05). The
# flush/knife family — including both sides of that A/B — was RETIRED by AxiS
# ("yes retire") after the 3.4-day family x regime mine: -$2,217 across BOTH
# regimes on 947 entries (~80% of the SOL bleed, half the fleet's volume, no
# green exception among the 10 measured bleeders). Configs archived as
# .json.off; these tests now pin the retirement so it can't silently undo.
# (The surviving liq-floor semantics they also covered are pinned on the
# still-active young_absorb probe twins.)
import json
import pathlib

from core.bot_config import BotConfig

RETIRED = [
    "badday_flush", "badday_flush_hlconfirm_ab", "badday_flush_nf15",
    "badday_flush_rsi_ab", "badday_flush_peel_ab", "badday_flush_runner_ab",
    "badday_flush_wickride_ab", "badday_allday", "badday_pump_dip_ab",
    "badday_young_pump_dip_ab",
]


def _cfg(name):
    return BotConfig(**json.loads(
        pathlib.Path(f"config/bots/{name}.json").read_text()))


def test_flush_knife_family_retired():
    for name in RETIRED:
        live = pathlib.Path(f"config/bots/{name}.json")
        archived = pathlib.Path(f"config/bots/{name}.json.off")
        assert not live.exists(), f"{name} resurrected — retirement violated"
        assert archived.exists(), f"{name} archive missing"


def test_liq_floor_still_enforced_on_probe_twins():
    assert _cfg("badday_young_absorb").liq_exit_floor_enforce is True
    assert _cfg("badday_young_absorb_live").liq_exit_floor_enforce is True
