# Bot Hypothesis Registry

What each bot in the fleet is testing, what dimension it isolates, and what to compare it against. Updated 2026-05-23.

**Purpose:** The fleet is a measurement instrument, not a portfolio. Every bot answers a specific question. This registry keeps the question paired with the bot so attribution work doesn't drift into "why does X have realized $Y" without context.

**Usage:**
- When opening any attribution comparison, find both bots here first.
- When proposing a new bot, add the row before shipping the JSON.
- When retiring a bot, mark `retired: YYYY-MM-DD` but keep the row.

## Reference

| bot_id | hypothesis | compare vs |
|---|---|---|
| `baseline_v1` | Current production design — default filter chain, $20 × 3 positions, 5%/10% TP, -15% stop. Every other bot is a single-knob deviation from this. | (reference) |

## Single-filter ablations (which filters add EV?)

Each disables ONE enforced filter. If the ablation outperforms baseline → that filter is net-negative and should be reverted to SHADOW.

| bot_id | filter disabled | hypothesis | compare vs |
|---|---|---|---|
| `no_1m_steep_fall` | filter_1m_steep_fall | This 1m-fall blocker is net-negative; killing it captures more winners | baseline_v1 |
| `no_above_vwap_chase` | filter_above_vwap_chase | VWAP-chase block kills good runners | baseline_v1 |
| `no_blowoff_top` | filter_blowoff_top | Blowoff-top filter blocks real winners | baseline_v1 |
| `no_bs_m5_weak` | filter_bs_m5_weak | bs_m5 weak filter is over-cautious | baseline_v1 |
| `no_low_volatility` | filter_low_volatility | Low-vol filter blocks recoverable bottoms | baseline_v1 |
| `no_negative_net_flow_5m` | filter_negative_net_flow_5m | 5m flow filter is noisy | baseline_v1 |
| `no_seller_imbalance` | filter_seller_imbalance | Seller-imbalance blocker too strict | baseline_v1 |
| `no_topping` | filter_topping | Topping filter blocks legit dips | baseline_v1 |
| `no_turn` | filter_turn | Turn filter (catching knife) over-blocks | baseline_v1 |
| `no_vp_poc` | filter_vp_poc | Volume-profile POC block is noisy | baseline_v1 |

## Filter category disables (which group adds EV?)

Each disables an entire category to test category-level value.

| bot_id | disables | hypothesis | compare vs |
|---|---|---|---|
| `no_filters` | ALL enforced filters | Filters net-cost EV; just use triggers + exits | baseline_v1 |
| `no_chart_pattern_filters` | chart-pattern filter family | Chart-pattern filters add no EV | baseline_v1 |
| `no_flow_filters` | flow-related filters | Flow filters are noisy | baseline_v1 |
| `no_liquidity_filters` | liquidity-band filters | Liquidity filters too strict | baseline_v1 |
| `no_macro_filters` | macro-gate filters (non-sol/btc) | Macro filters don't help intraday | baseline_v1 |
| `no_structural_filters` | structural (sr, lower_low, etc) | Structural filters block legit dips | baseline_v1 |
| `no_timing_filters` | timing-window filters | Timing windows aren't needed | baseline_v1 |
| `no_pc_h24_ceiling` | pc_h24-ceiling block | h24 ceiling rejects winners | baseline_v1 |
| `no_sol_gate` | SOL macro h6/h1 blocks | SOL macro blocks cost EV | baseline_v1 |

## Trigger restrictions (which trigger families are highest-EV?)

Each restricts to ONE trigger family.

| bot_id | triggers_allowed | hypothesis | compare vs |
|---|---|---|---|
| `chart_pattern_only` | chart-pattern triggers | Chart triggers alone are sufficient | baseline_v1 |
| `cnn_cluster_only` | cnn_cluster_* | CNN clusters are the dominant alpha | baseline_v1 |
| `flow_only` | flow-related triggers | Flow signals carry the EV | baseline_v1 |
| `one_sec_only` | 1s_capit_reversal | 1s features are the best entry signal | baseline_v1 |
| `whales_only` | whale_conviction + related | Concentrated buyers > everything else | baseline_v1 |
| `scalp_only` | scalp triggers | Scalping niche is profitable in isolation | baseline_v1 |
| `deep_dip_only` | deep_1h_dip | Deep dip is the only alpha | baseline_v1 |
| `runner_tilt_aggressive` | runner-tilt triggers | Big runners are where money is made | baseline_v1 |
| `triggers_classic_dip` | classic dip family | Old-school dip-buy strategy holds up | baseline_v1 |
| `triggers_sweep_demand` | sweep + demand_bottom_compound | Sweep+demand compound is the winner | baseline_v1 |
| `triggers_3d_only` | 19 `3d_*` triggers | 3d-compound family is alpha | baseline_v1 (DEAD — no fires ever) |
| `triggers_compound_combo` | 9 multi-feature compounds | Multi-feature compounds beat singletons | baseline_v1 (mostly DEAD — 4/9 triggers commented out in code) |
| `triggers_overnight_only` | 11 overnight triggers | Overnight cohort is high-EV | baseline_v1 (just un-blocked 2026-05-23) |
| `strict_alpha_only` | require_alpha_trigger=true | Alpha triggers carry all the EV | baseline_v1 |

## Capital / concurrency variants

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `narrow_concurrent` | max_concurrent=1 | Concentrate capital, take fewer better trades | baseline_v1 |
| `wide_concurrent` | max_concurrent=5 | More positions = more diversification = better EV | baseline_v1 |
| `no_alpha_sizing` | alpha_multiplier=1.0 | Alpha sizing bump is over-paying | baseline_v1 |

## Sizing variants — compounding (2026-05-23)

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `compound_linear` | compound_mode=linear | Bigger bankroll → bigger bet, symmetric | baseline_v1 |
| `compound_winners_only` | compound_mode=winners_only | Grow only on wins, never shrink | compound_linear |
| `compound_threshold` | compound_mode=threshold | Discrete steps beat continuous compounding | compound_linear |

## Time-of-day variants

| bot_id | window | hypothesis | compare vs |
|---|---|---|---|
| `tod_morning` | CT morning only | Morning hours are higher-EV | baseline_v1 |
| `tod_afternoon` | CT afternoon only | Afternoon hours are higher-EV | baseline_v1 |
| `tod_evening` | CT evening only | Evening hours are higher-EV | baseline_v1 |
| `tod_overnight` | CT overnight only | Overnight hours are higher-EV | baseline_v1 |

## Exit ladder variants

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `exit_aggressive` | tighter TP/trail | Capture faster, lose less | baseline_v1 |
| `exit_no_trail` | trail disabled | Trailing stop is over-tight, costs runners | baseline_v1 |
| `exit_patient` | wider trail | Let positions breathe | baseline_v1 |
| `exit_runner_hold` | larger TP2, looser trail | Big runners deserve more rope | baseline_v1 |
| `exit_tight_trail` | tighter trail | Sharper trail catches reversals faster | baseline_v1 |

## Stop variants

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `tight_stop` | hard_stop_pct=-10 | Tighter stops cut losers faster | baseline_v1 (-15%) |
| `wide_stop` | hard_stop_pct=-20 | Wider stops let recoveries happen | baseline_v1 (-15%) |

## Volume threshold variants

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `vol_min_500` | vol_h1_min=500 | Lower vol gate catches more setups | baseline_v1 (1000) |
| `vol_min_5k` | vol_h1_min=5000 | Higher vol gate filters dud tokens | baseline_v1 (1000) |
| `vol_min_10k` | vol_h1_min=10000 | High vol = real activity | baseline_v1 (1000) |

## SOL macro variants

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `sol_h6_tight` | sol_macro_h6=-0.1 | Stricter SOL gate avoids macro dumps | baseline_v1 (-0.3) |
| `sol_h6_loose` | sol_macro_h6=-1.0 | Loose SOL gate doesn't sacrifice trades | baseline_v1 (-0.3) |
| `sol_h6_extreme` | sol_macro_h6=-5.0 | Effectively disable SOL gate | no_sol_gate |
| `regime_aware_bullish` | macro_up_multiplier raised | Size up when SOL is green | baseline_v1 |

## Mcap psych variants

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `psych_h24_50` | mcap_psych_pc_h24_max=50 | Demote psych at lower pc_h24 | baseline_v1 (80) |
| `psych_h24_100` | mcap_psych_pc_h24_max=100 | Looser psych demote | baseline_v1 (80) |
| `psych_h24_150` | mcap_psych_pc_h24_max=150 | Very loose / effectively never demote | baseline_v1 (80) |

## Mcap / token-class variants

| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `microcap_specialist` | mcap_max=1M | Microcaps are higher-EV | baseline_v1 |
| `midcap_specialist` | mcap 5-25M | Midcap is the sweet spot | baseline_v1 |
| `mature_token_only` | age_h_min=168 | Mature tokens behave more predictably | baseline_v1 |
| `early_token_only` | age_h_max=72 (raised 24→72 2026-05-23) | Fresh tokens have higher upside | baseline_v1 |

## Synthesis target

| bot_id | role |
|---|---|
| `champion_proposal` | Synthesized successor candidate. Combines winning components from the rest of the fleet. Re-synthesized whenever attribution settles enough to update component picks. Currently at $0 (unwired). |

## Scheduled (not yet shipped — see [[project_bot_handoff]] for timing)

### Deploy A — TP ladder (3 bots)
| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `tp_aggressive` | TP1=3, TP2=7 | Faster capture beats letting winners run | baseline_v1, exit_aggressive |
| `tp_runner` | TP1=8, TP2=20 | Letting winners run beats fast capture | baseline_v1, exit_runner_hold |
| `tp_single_target` | TP1=5 sell 100%, no TP2 | Single-target simpler and better | baseline_v1 |

### Deploy B — Config-only batch (7 bots)
| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `concentrated_50` | base=$50, max=1 | Concentrated capital beats spray | baseline_v1, spray_10 |
| `spray_10` | base=$10, max=6 | Diversified spray beats concentration | baseline_v1, concentrated_50 |
| `stop_8` | hard_stop_pct=-8 | Tighter than tight_stop is better | tight_stop, baseline_v1 |
| `stop_25` | hard_stop_pct=-25 | Deeper than wide_stop is better | wide_stop, baseline_v1 |
| `bleed_30min` | slow_bleed_minutes=30 | Bail underwater positions faster | baseline_v1 (60min) |
| `bleed_120min` | slow_bleed_minutes=120 | Patience beats fast bail | baseline_v1 (60min) |
| `no_bleed` | slow_bleed disabled | Slow-bleed knob is net-negative | baseline_v1 |

### Deploy C — Code-required batch (3 bots)
| bot_id | knob | hypothesis | compare vs |
|---|---|---|---|
| `drawdown_freeze` | pause buying when realized <= -$100 | Discipline beats always-on | baseline_v1 |
| `reentry_after_stop` | re-buy same token N min after stop | Choppy-recovery capture | baseline_v1 |
| `macro_conditional` | size scales with sol_pc_h6 | Macro is gradient, not gate | baseline_v1, no_sol_gate |
