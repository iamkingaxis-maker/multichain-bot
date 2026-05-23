# Filter categorization — 2026-05-23

Each ENFORCED filter assigned to exactly one of 6 categories. Used by
SP3 Block 1 group-level filter test bots.

## Verification target
Total filters: 40 (matches SP2 inventory ENFORCED count).
Each filter appears in EXACTLY ONE category.

## macro
SOL/BTC macro context, market-wide regime checks, and time-of-session /
day-of-week regime gates (6 filters).

- filter_sol_macro_down
- filter_blowoff_top
- filter_post_pump_corpse
- filter_dead_5m_eve_wknd
- filter_sat_eve_midliq
- filter_morning_dead_zone

## chart_pattern
Candle-shape or short-window price-action patterns — 1m/5m/1h candle
morphology, pump/dump shapes, fake reversals, composite candle rules
(11 filters).

- filter_fake_bounce
- filter_round_trip
- filter_1m_steep_fall
- filter_chasing_bounce
- filter_topping
- filter_above_vwap_chase
- filter_1h_v_bottom_fake_recovery
- filter_cluster_19_rug
- filter_zero_winner_compound
- filter_lazy_fade_buy
- filter_premium_shallow_dip

## structural
Multi-timeframe alignment, support/resistance levels, V-bottoms, trend
topology, lifecycle state, VWAP/POC relative position (9 filters).

- filter_mtf_strong_downtrend
- filter_turn
- filter_vp_poc
- filter_lower_low
- filter_no_signatures
- filter_solo_decay
- filter_reviving_lifecycle
- filter_knife_catch_peak
- filter_stale_h1_peak

## timing
1-minute or sub-minute freshness/staleness checks, sweep recency,
candle confirmation, trigger-quality / trigger-confirmation gates
(4 filters).

- filter_clean_break_p90
- filter_high_regime_buyvol
- filter_solo_dropouts
- filter_premium_required

## flow
Buy/sell ratios, net flow, big trade size, seller imbalance,
order-book asymmetry (4 filters).

- filter_bs_m5_weak
- filter_seller_imbalance
- filter_negative_net_flow_5m
- filter_quote_asymmetry

## liquidity
Liquidity profile checks, LP behavior, microcap traps, dev rugs,
volatility floor, DEX compatibility (6 filters).

- filter_dev_rugged
- filter_meteora_dex
- filter_orca_dex
- filter_lp_drain
- filter_low_volatility
- filter_microcap_trap

## Notes on edge cases

### filter_dead_5m_eve_wknd → macro (not flow or timing)
Checks bs_m5 < 0.8 AND hour ∈ [17,22) AND weekend. The bs_m5 component
is a flow metric but the hour+weekend gate makes this a market-session
regime filter. The session context is the discriminating signal (17.1% WR
specifically on that session). Placed in macro. Alternate: flow (bs_m5 is
the primary mechanism). Chose macro because bs_m5 < 0.8 alone is not
blocked — the session gate is the defining predicate.

### filter_sat_eve_midliq → macro (not liquidity)
Checks liquidity ∈ [$100k, $250k) AND hour ∈ [17,22) AND Saturday. The
Saturday-evening session is the defining discriminator (3.3% WR n=30).
Placed in macro. Alternate: liquidity. Chose macro because the liquidity
band is a secondary shaping predicate — the session is the primary gate.

### filter_morning_dead_zone → macro (not timing)
Checks CT hour ∈ {7,8,9} AND age > 24h. Hourly session regime gate.
"Timing" in the category definitions means 1m/sub-minute freshness/
staleness. This operates at hourly granularity = market-regime context.

### filter_blowoff_top → macro (not chart_pattern)
Checks pc_h24 >= 500%. Uses 24h rolling price change — same dimension as
sol_pc_h6 — not candle morphology. Placed in macro. Alternate: chart_pattern
(extended pump shape). Chose macro because it captures a market-regime
extension state rather than a candle or short-window pattern.

### filter_post_pump_corpse → macro (not chart_pattern)
Checks pc_h1 >= 500% OR (pc_h24 >= 200% AND buys_per_min <= 2). Captures
post-extreme-pump dying-activity regime state. Placed in macro. Alternate:
chart_pattern. Chose macro because buys_per_min is a velocity/activity
metric and the filter detects dead-token regime state, not candle morphology.

### filter_topping → chart_pattern (not structural)
Checks macro30_pct > +5% (price up >5% in the last 30 minutes). The 30m
window is a short-window candle-range pattern at entry. Placed in
chart_pattern. Alternate: structural (short-window trend direction). Chose
chart_pattern because macro30 measures the immediate entry momentum shape,
not multi-timeframe topology.

### filter_1h_v_bottom_fake_recovery → chart_pattern (not structural)
Checks 1h candle pair morphology: prior 1h red, current 1h green erasing
prior. Inspects individual candle bodies (open/close). Placed in
chart_pattern. Alternate: structural (1h trend reversal). Chose chart_pattern
because the logic is candle-body comparison, not multi-timeframe alignment
or support levels.

### filter_vp_poc → structural (not flow or chart_pattern)
Checks chart_vp_poc_distance_pct > 20 AND 1m_volume_spike < 1.0. Volume
Profile Point of Control is a support/resistance level reference. Placed in
structural. Alternate: flow (volume spike component) or chart_pattern.
Chose structural because POC distance is a price-vs-volume-level metric —
equivalent to price relative to a support/resistance level.

### filter_no_signatures → structural (not flow or macro)
Checks 0-of-6 winner signatures: chart_score, 5m_state, VWAP position,
CHoCH direction, 15m_state, SOL regime. Composite multi-timeframe topology
check. Placed in structural because the majority of the 6 signatures are
structural (chart_score, 5m/15m states, CHoCH). Alternate: macro (regime
component). Chose structural because MTF topology is the dominant signal set.

### filter_turn → structural (not timing)
Checks pct_in_5m_range < 0.5 (current close in the lower half of the 5m
bar's range). Price location relative to the 5m range = support/resistance
envelope positioning. Placed in structural. Alternate: timing (5m candle
position freshness). Chose structural because it measures where price sits
in the current structure, not how fresh/stale a signal event is.

### filter_solo_decay → structural (not timing)
Checks solo clean_break/high_regime + age > 168h + lifecycle_h24_ratio <
0.20. The lifecycle state (age + ratio to 24h peak) is a structural
descriptor of the token's price-topology phase (post-pump decay). Placed
in structural. Alternate: timing. Chose structural because lifecycle phase
is a topology concept, not a signal-freshness check.

### filter_lower_low → structural (not chart_pattern)
Checks hl_delta_pct < -25% (5m swing low 25%+ below prior swing low).
Lower-low is a trend-topology concept (downtrend structure). Placed in
structural. Alternate: chart_pattern (5m candle). Chose structural because
swing-low comparisons define trend structure, not candle shape.

### filter_stale_h1_peak → timing (not structural)
Checks time_since_h1_peak_secs ∈ [3000, 3600) (peak was 50-60 min ago).
Explicitly a time-elapsed-since-event / staleness check. Placed in timing.
Alternate: structural (h1 momentum decay). Chose timing because the filter
is measuring elapsed time since a peak — the staleness notion is dominant.

### filter_premium_required → timing (not flow)
Gates marginal triggers on premium-quality compound (avg_trade_size,
liq_velocity, p90_buy_size). Functions as an entry-confirmation gate at
scan time. Placed in timing. Alternate: flow (trade-size metrics). Chose
timing because the filter's function is trigger-quality confirmation before
acting — analogous to "candle confirmation" in the timing definition.

### filter_solo_dropouts → timing (not structural)
Blocks lone-trigger entries for three specific triggers with poor solo WR.
Trigger-combination confirmation gate. Placed in timing. Alternate:
structural. Chose timing because it checks whether a trigger has confirming
co-evidence — a scan-time confirmation/freshness check.

### filter_high_regime_buyvol → timing (not flow)
Checks high_regime trigger + buyvol_ratio_60m <= 1.0. Trigger-validity
gate — does high_regime have confirming 60m buy-volume? Placed in timing.
Alternate: flow (buyvol_ratio is a flow metric). Chose timing because the
filter demotes a trigger when its flow confirmation is absent — a
trigger-validity/confirmation check rather than a raw flow gate.

### filter_clean_break_p90 → timing (not liquidity or chart_pattern)
Checks clean_break trigger + chart_p90_body_pct <= 5.0. Trigger-quality
gate — does clean_break fire on a token with sufficient candle energy?
Placed in timing. Alternate: liquidity (volatility floor) or chart_pattern
(candle body size). Chose timing because its function is confirming a
specific trigger is valid, not measuring raw volatility or a chart shape.

### filter_cluster_19_rug → chart_pattern (not liquidity)
Uses autoencoder+k-means on 1m/5m/15m candle series to detect Cluster 19
(67% rug rate). Detection mechanism is entirely chart-shape inference.
Placed in chart_pattern. Alternate: liquidity (rug detection). Chose
chart_pattern because the classifier operates on candle-series morphology,
not LP/dev/DEX behavior.

### filter_quote_asymmetry → flow (not liquidity)
Checks quote_asymmetry_pct > 3.5 (sell-side Jupiter quote impact worse
than buy-side by >3.5pp). Measures order-book imbalance between buy and
sell impact — a flow/order-book metric. Placed in flow. Alternate:
liquidity (exit cost). Chose flow because it directly measures buy-vs-sell
impact asymmetry at entry size.

### filter_lp_drain → liquidity (not flow)
Checks lp_delta_15m_pct <= -5% (LP pool shrinking >5% in 15m). LP pool
behavior is explicitly a liquidity dynamic. Placed in liquidity.
