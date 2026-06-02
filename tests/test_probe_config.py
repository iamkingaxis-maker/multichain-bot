"""Live-probe scaffolding (piece 2/4): BotConfig probe fields + the dormant probe config.

Safety invariants: the new fields default to OFF (fleet stays paper), the probe config
is DORMANT (enabled=false), and every existing config still loads (unknown-field check).
"""
import glob
from core.bot_config import BotConfig


def test_new_probe_fields_default_off():
    cfg = BotConfig(bot_id="x", display_name="x")
    assert cfg.live_probe is False            # default OFF -> fleet stays paper
    assert cfg.size_sweep_usd == ()           # default empty -> fixed size


def test_probe_config_loads_and_is_dormant():
    cfg = BotConfig.from_json("config/bots/probe_premium_tightexit_live.json")
    assert cfg.bot_id == "probe_premium_tightexit_live"
    assert cfg.enabled is False               # DORMANT — not in the running fleet
    assert cfg.live_probe is True             # declares intent (read by the bridge once enabled)
    assert cfg.size_sweep_usd == (20.0, 50.0, 100.0)
    assert cfg.daily_loss_limit_usd == 50.0   # tiny tuition cap
    assert cfg.max_token_buys_per_day == 2
    assert cfg.max_concurrent_positions == 2  # exposure cap
    # strategy IDENTICAL to the candidate (so it measures the same thing)
    cand = BotConfig.from_json("config/bots/champion_premium_tightexit.json")
    for f in ("tp1_pct", "tp2_pct", "trail_pp", "hard_stop_pct", "filters_enforced",
              "triggers_allowed", "entry_gate", "slow_bleed_pnl_threshold"):
        assert getattr(cfg, f) == getattr(cand, f), f"probe {f} must match candidate"
    assert hash(cfg) is not None              # frozen/hashable (size_sweep tuple-normalized)


def test_candidate_unaffected():
    cand = BotConfig.from_json("config/bots/champion_premium_tightexit.json")
    assert cand.live_probe is False and cand.size_sweep_usd == ()


def test_all_configs_still_load():
    n = 0
    for p in glob.glob("config/bots/*.json"):
        BotConfig.from_json(p); n += 1
    assert n >= 50
