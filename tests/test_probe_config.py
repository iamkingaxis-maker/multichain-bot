"""Live-probe scaffolding: BotConfig live_probe field + the three dormant fixed-size
probe configs (probe_tightexit_live_{20,50,100}).

Safety invariants: live_probe defaults OFF (fleet stays paper), each probe config is
DORMANT (enabled=false) with fixed size + neutralized multipliers + its own daily-loss
halt, and every existing config still loads (unknown-field check).
"""
import glob
import pytest
from core.bot_config import BotConfig

PROBES = [
    ("config/bots/probe_tightexit_live_20.json", 20.0, 2, 30.0),
    ("config/bots/probe_tightexit_live_50.json", 50.0, 2, 50.0),
    ("config/bots/probe_tightexit_live_100.json", 100.0, 1, 75.0),
]


def test_live_probe_defaults_off():
    cfg = BotConfig(bot_id="x", display_name="x")
    assert cfg.live_probe is False             # default OFF -> fleet stays paper
    assert not hasattr(cfg, "size_sweep_usd")  # size-sweep mechanism removed


@pytest.mark.parametrize("path,size,maxc,daily", PROBES)
def test_probe_config_dormant_fixed_size(path, size, maxc, daily):
    c = BotConfig.from_json(path)
    assert c.enabled is False                 # DORMANT — not in the running fleet
    assert c.live_probe is True
    assert c.base_position_usd == size        # FIXED size per bot (clean size axis)
    assert c.max_concurrent_positions == maxc
    assert c.daily_loss_limit_usd == daily    # own tight halt
    assert c.max_token_buys_per_day == 2
    # multipliers neutralized so size is truly fixed (no re-entanglement)
    assert c.alpha_multiplier == 1.0 and c.macro_up_multiplier == 1.0
    assert c.premium_runner_multiplier == 1.0 and c.marginal_multiplier == 1.0
    # strategy IDENTICAL to the candidate (so it measures the same thing)
    cand = BotConfig.from_json("config/bots/champion_premium_tightexit.json")
    for f in ("tp1_pct", "tp2_pct", "trail_pp", "hard_stop_pct", "filters_enforced",
              "triggers_allowed", "entry_gate", "slow_bleed_pnl_threshold"):
        assert getattr(c, f) == getattr(cand, f), f"probe {f} must match candidate"
    assert hash(c) is not None


def test_three_probe_sizes_distinct():
    sizes = sorted(BotConfig.from_json(p).base_position_usd for p, *_ in PROBES)
    assert sizes == [20.0, 50.0, 100.0]       # the $20/$50/$100 size axis


def test_candidate_unaffected():
    cand = BotConfig.from_json("config/bots/champion_premium_tightexit.json")
    assert cand.live_probe is False


def test_all_configs_still_load():
    n = 0
    for p in glob.glob("config/bots/*.json"):
        BotConfig.from_json(p); n += 1
    assert n >= 50
