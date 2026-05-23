# Filter block rates - mined 2026-05-23


Sample: 204 buys from production /api/trades.

BLOCK count = number of buy candidates where this filter's verdict was BLOCK
(SHADOW filters block but don't enforce; ENFORCED filters are marked).

| Rank | Filter name | BLOCK count | ENFORCED? |
|---:|---|---:|:---:|
| 1 | filter_dev_dumping | 200 | shadow |
| 2 | filter_stale_watch | 199 | shadow |
| 3 | filter_quad_hi_wr | 180 | shadow |
| 4 | filter_confirmation_candle | 163 | shadow |
| 5 | filter_quad_robust | 157 | shadow |
| 6 | filter_big_trade_size | 150 | shadow |
| 7 | filter_clean_break | 148 | shadow |
| 8 | filter_two_pattern | 135 | shadow |
| 9 | filter_a | 127 | shadow |
| 10 | filter_1m | 123 | shadow |
| 11 | filter_real_dip_5 | 87 | shadow |
| 12 | filter_token_ema | 82 | shadow |
| 13 | filter_bs_m5_low | 81 | shadow |
| 14 | filter_buyer_fomo | 61 | shadow |
| 15 | filter_turn | 60 | yes |
| 16 | filter_negative_net_flow_5m | 52 | yes |
| 17 | filter_1m_dead_vol | 47 | shadow |
| 18 | filter_quad | 47 | shadow |
| 19 | filter_sweep_too_recent | 44 | shadow |
| 20 | filter_seller_imbalance | 35 | yes |
| 21 | filter_weak_bounce | 32 | shadow |
| 22 | filter_trend_score | 32 | shadow |
| 23 | filter_vwap_h24 | 31 | shadow |
| 24 | filter_low_volatility | 30 | yes |
| 25 | filter_vp_poc | 23 | yes |
| 26 | filter_falling_knife | 22 | shadow |
| 27 | filter_topping | 21 | yes |
| 28 | filter_rsi_overbought | 19 | shadow |
| 29 | filter_real_dip_3 | 19 | shadow |
| 30 | filter_weak_bounce_v2 | 19 | shadow |
| 31 | filter_above_vwap_chase | 18 | yes |
| 32 | filter_dead_vol_chart | 17 | shadow |
| 33 | filter_seller_dominant | 17 | shadow |
| 34 | filter_corpse | 17 | shadow |
| 35 | filter_bs_m5_weak | 16 | yes |
| 36 | filter_dip_volume | 16 | shadow |
| 37 | filter_high_activity_fomo | 16 | shadow |
| 38 | filter_dying_volume | 16 | shadow |
| 39 | filter_1h_v_bottom | 13 | shadow |
| 40 | filter_wide_range_entry | 11 | shadow |
| 41 | filter_blowoff_top | 10 | yes |
| 42 | filter_1m_steep_fall | 10 | yes |
| 43 | filter_high_regime_buyvol | 9 | yes |
| 44 | filter_5m_downtrend | 8 | shadow |
| 45 | filter_chasing_bounce | 8 | yes |
| 46 | filter_chart_trendline_1h | 7 | shadow |
| 47 | filter_reviving_lifecycle | 6 | yes |
| 48 | filter_knife_catch_peak | 5 | yes |
| 49 | filter_15s_dump | 4 | shadow |
| 50 | filter_double_bottom | 4 | shadow |
| 51 | filter_chart_trendline_5m | 3 | shadow |
| 52 | filter_lp_drain | 3 | yes |
| 53 | filter_double_bear | 2 | shadow |
| 54 | filter_stairstep | 2 | shadow |
| 55 | filter_stale_h1_peak | 2 | yes |
| 56 | filter_structure | 2 | shadow |
| 57 | filter_combo_v2 | 2 | shadow |
| 58 | filter_fofar | 1 | shadow |
| 59 | filter_already_mooned | 1 | shadow |

## Top 10 ENFORCED filters (use for SP3 Block 2 ablations)

1. `filter_turn` - 60 blocks observed
2. `filter_negative_net_flow_5m` - 52 blocks observed
3. `filter_seller_imbalance` - 35 blocks observed
4. `filter_low_volatility` - 30 blocks observed
5. `filter_vp_poc` - 23 blocks observed
6. `filter_topping` - 21 blocks observed
7. `filter_above_vwap_chase` - 18 blocks observed
8. `filter_bs_m5_weak` - 16 blocks observed
9. `filter_blowoff_top` - 10 blocks observed
10. `filter_1m_steep_fall` - 10 blocks observed