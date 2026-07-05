# tests/test_wickride_ab.py
"""Wickride A/B + adolescent absorb (2026-07-03 current-regime winner decode).

The velocity-bail pre-empt was 83% of the family bleed in the current chop
(77% of bailed tokens hit +6% above the bail within 60m, n=48 thin); regime
winners sit through -7/-12 wicks. velbail_pnl_pct=-8 disables the velocity
leg (below the -7 MAE floor, which stays).
"""
import json
import pathlib

from core.bot_config import BotConfig
from core.bot_evaluator import in_flight_floor_fires


def _cfg(name):
    return BotConfig(**json.loads(
        pathlib.Path(f"config/bots/{name}.json").read_text()))


class TestVelbailOverride:
    def test_default_minus4_fires_velocity(self):
        # fast never-green collapse at -5: default velbail (-4) fires
        fires, why = in_flight_floor_fires(-5.0, 0.5, 10)
        assert fires and "velocity-bail" in why

    def test_minus8_disables_velocity_leg(self):
        # same collapse with velbail_pnl=-8: no velocity fire (pnl > -8)...
        fires, _ = in_flight_floor_fires(-5.0, 0.5, 10, velbail_pnl=-8.0)
        assert not fires

    def test_mae_floor_survives_override(self):
        # ...but the -7 MAE floor still fires at -7.5 regardless
        fires, why = in_flight_floor_fires(-7.5, 0.5, 10, velbail_pnl=-8.0)
        assert fires and "MAE-floor" in why


class TestWickrideConfig:
    def test_clone_matches_flush_except_bail(self):
        w, f = _cfg("badday_flush_wickride_ab"), _cfg("badday_flush")
        assert w.enabled is True and not getattr(w, "live_probe", None)
        assert w.velbail_pnl_pct == -8.0 and f.velbail_pnl_pct is None
        # A/B integrity: identical entry + exits
        assert [tuple(x) for x in w.entry_gate] == [tuple(x) for x in f.entry_gate]
        assert (w.tp1_pct, w.tp2_pct, w.hard_stop_pct) == (f.tp1_pct, f.tp2_pct, f.hard_stop_pct)
        assert w.exclusion_pool == "badday_flush_wickride_ab"


class TestAdolescentConfig:
    def test_pond_and_mechanics(self):
        a, y = _cfg("badday_adolescent_absorb"), _cfg("badday_young_absorb")
        assert a.enabled is True and not getattr(a, "live_probe", None)
        # REQUIRED: YOUNG_TOKEN_MAX_AGE_H=24 makes production bots SKIP all <24h
        # tokens — without the probe flag this bot is dead on arrival. Its own
        # age_h_min=6 keeps it off the fresh launches.
        assert a.young_token_probe is True
        assert (a.age_h_min, a.age_h_max) == (6.0, 24.0)   # the winners' pond
        # hours widened 13-22 -> family 8-3 on 2026-07-05: the daily decode
        # found the pond premise HELD but the hours leg went NEUTRAL, and the
        # 9h window throttled the bot to ~2.5 fires/day.
        assert (a.trading_hour_utc_start, a.trading_hour_utc_end) == (8, 3)
        assert a.velbail_pnl_pct == -8.0             # wick-tolerant
        # same demand/absorption entry as young
        assert [tuple(x) for x in a.entry_gate] == [tuple(x) for x in y.entry_gate]
