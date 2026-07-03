# tests/test_swing_latch.py
"""badday_swing_latch paper A/B (2026-07-03 swing-latch study).

Ride-the-streak rule: latch onto a token's swings only while they keep
winning; drop the token on its first losing sell leg. Market-only sim on 216
traction tokens: after-win deep swings = +4.09 gross (~+1.5 net) stable across
time/token splits, but concentrated in serial swingers — the drop rule is the
concentrator.
"""
import json
import pathlib

from core.bot_config import BotConfig


def _cfg():
    p = pathlib.Path("config/bots/badday_swing_latch.json")
    return BotConfig(**json.loads(p.read_text()))


class TestConfig:
    def test_loads_and_is_paper_only(self):
        c = _cfg()
        assert c.bot_id == "badday_swing_latch"
        assert c.enabled is True
        assert not getattr(c, "live_probe", None)   # paper A/B, never live

    def test_streak_latch_flag(self):
        c = _cfg()
        assert c.streak_latch is True
        # immediate re-entry after wins is the point of the latch
        assert not c.reentry_cooldown_secs

    def test_deep_swing_entry(self):
        c = _cfg()
        gate = [tuple(x) for x in c.entry_gate]
        assert ("pc_h1", "<=", -35) in gate          # deep swing, not the -20 family dip
        assert ("liquidity_usd", ">=", 25000) in gate  # fleet anti-rug floor KEPT
        assert ("unique_buyers_n", ">=", 12) in gate  # demand gates stay on

    def test_serial_swinger_pond_v2(self):
        # discriminator study 2026-07-03: serial swingers are YOUNG (median
        # 0.70h; age<=1h cell +28.2 net/token, median-positive, split-stable).
        # v1's age>=6h stack was the anti-pond (-9.98/token).
        c = _cfg()
        assert c.young_token_probe is True           # young lane admission
        assert (c.age_h_min, c.age_h_max) == (0.1, 1.5)
        assert c.mcap_min == 100000.0                # young admission floor

    def test_let_run_exits(self):
        c = _cfg()
        assert c.tp1_pct == 25.0                     # the winning sim cell
        assert c.hard_stop_pct == -12.0
        assert c.slow_bleed_minutes == 90            # ~90min timestop scale

    def test_family_risk_rails(self):
        c = _cfg()
        assert c.daily_loss_limit_usd == 60.0
        assert c.trading_hour_utc_start == 8 and c.trading_hour_utc_end == 3
        assert c.max_concurrent_positions == 2

    def test_default_flag_is_off_for_other_bots(self):
        flush = BotConfig(**json.loads(
            pathlib.Path("config/bots/badday_flush.json").read_text()))
        assert getattr(flush, "streak_latch", False) is False
