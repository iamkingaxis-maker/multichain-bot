from utils.config import Config


def test_scalp_config_has_new_fields():
    c = Config()
    assert c.scalp_impulse_min_pct == 10.0
    assert c.scalp_impulse_max_pct == 30.0
    assert c.scalp_impulse_lookback == 6
    assert c.scalp_pullback_min_pct == 30.0
    assert c.scalp_pullback_max_pct == 60.0
    assert c.scalp_sweep_vol_mult == 1.5
    assert c.scalp_sweep_vol_lookback == 20
    assert c.scalp_tp1_pct == 10.0
    assert c.scalp_tp1_sell == 0.50
    assert c.scalp_tp2_pct == 15.0
    assert c.scalp_tp2_sell == 0.35
    assert c.scalp_stop_pct == 6.0
    assert c.scalp_min_rr == 2.0
    assert c.scalp_time_exit_candles == 4
    assert c.scalp_time_exit_min_pct == 5.0
    assert c.scalp_min_m5_volume_usd == 5_000
    assert c.scalp_min_liquidity_usd == 30_000
    assert c.scalp_min_age_minutes == 5
    assert c.scalp_max_age_hours == 24.0
    assert c.scalp_rug_lp_drop_pct == 10.0
    assert c.scalp_max_deployment_pct == 0.80
    assert c.scalp_gt_rate_per_min == 10
    assert c.scalp_gt_cache_ttl_sec == 180
    assert c.scalp_gt_trending_pages == 1
    assert c.scalp_max_concurrent == 5
