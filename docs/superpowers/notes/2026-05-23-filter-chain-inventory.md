# Filter chain inventory — feeds/dip_scanner.py

Produced for Sub-project 2 Task 3 (filter chain restructure).

HEAD at time of audit: `fe8ac42`

## All BLOCKED-by-filter log lines

Each entry: filter_name | log_line_number | continue_line_number | block variable

| Filter name | Log line | Continue (approx) | Block variable |
|---|---:|---:|---|
| filter_fake_bounce | 2237 | 2240 | `_filter_fake_bounce_block_reasons` (via `_filter_fake_bounce_verdict`) |
| filter_round_trip | 2304 | 2307 | `_filter_round_trip_block_reasons` (via `_filter_round_trip_verdict`) |
| filter_1m_steep_fall | 2601 | 2604 | `_filter_1m_steep_fall_block_reasons` (via `_filter_1m_steep_fall_verdict`) |
| filter_cluster_19_rug | 2978 | 2983 | `_cluster_19_rug_block` (bool, not list) |
| filter_turn | 3149 | 3161 | `_filter_turn_block_reasons` (via `_filter_turn_verdict`) |
| filter_vp_poc | 3257 | 3262 | `_filter_vp_poc_block_reasons` (via `_filter_vp_poc_verdict`) |
| filter_dev_rugged | 3781 | 3785 | `_dev_pct` (direct numeric check, no list) |
| filter_meteora_dex | 3889 | 3894 | `_grad_dex == "meteora"` (direct string check, inside try/except) |
| filter_orca_dex | 3898 | 3902 | `_grad_dex == "orca"` (direct string check, inside try/except) |
| filter_bs_m5_weak | 3995 | 3999 | `_filter_bs_m5_weak_block_reasons` (via `_filter_bs_m5_weak_verdict`) |
| filter_sol_macro_down | 4166 | 4169 | `_filter_sol_macro_down_block_reasons` (via `_filter_sol_macro_down_verdict`) |
| filter_mtf_strong_downtrend | 7003 | 7006 | `_filter_mtf_dn_block_reasons` (via `_filter_mtf_dn_verdict`) |
| filter_solo_decay | 9310 | 9313 | `_fsd_block_reasons` (via `_fsd_verdict`) |
| filter_no_signatures | 10201 | 10205 | `_filter_no_sig_block_reasons` (via `_filter_no_signatures_verdict`) |
| filter_chasing_bounce | 10267 | 10273 | `_filter_chasing_bounce_block_reasons` (via `_filter_chasing_bounce_verdict`) |
| filter_quote_asymmetry | 10430 | 10433 | `_filter_quote_asymmetry_block_reasons` (via `_filter_quote_asymmetry_verdict`) |
| filter_lower_low | 10581 | 10584 | `_filter_lower_low_block_reasons` (via `_filter_lower_low_verdict`) |
| filter_lp_drain | 10645 | 10648 | `_filter_lp_drain_block_reasons` (via `_filter_lp_drain_verdict`) |
| filter_low_volatility | 10848 | 10852 | `_filter_low_vol_block_reasons` (via `_filter_low_vol_verdict`) |
| filter_dead_5m_eve_wknd | 10897 | 10900 | `_filter_dead_5m_eve_wknd_block_reasons` (via `_filter_dead_5m_eve_wknd_verdict`) |
| filter_sat_eve_midliq | 10928 | 10931 | `_filter_sat_eve_midliq_block_reasons` (via `_filter_sat_eve_midliq_verdict`) |
| filter_microcap_trap | 10971 | 10974 | `_filter_microcap_trap_block_reasons` (via `_filter_microcap_trap_verdict`) |
| filter_clean_break_p90 | 11015 | 11019 | `_filter_cb_p90_block_reasons` (via `_filter_cb_p90_verdict`) |
| filter_high_regime_buyvol | 11070 | 11074 | `_filter_hr_buyvol_block_reasons` (via `_filter_hr_buyvol_verdict`) |
| filter_solo_dropouts | 11110 | 11117 | direct set-membership check (`_triggers_fired[0] in _SOLO_DROPOUT_TRIGGERS`) |
| filter_premium_required | 11197 | 11205 | `_premium_ok` (bool result, no list) |
| filter_morning_dead_zone | 11248 | 11255 | `_mdz_premium_ok` (bool result, no list) |
| filter_blowoff_top | 11350 | 11354 | `_filter_blowoff_block_reasons` (via `_filter_blowoff_top_verdict`) |
| filter_post_pump_corpse | 11456 | 11459 | `_filter_corpse_pump_block_reasons` (via `_filter_post_pump_corpse_verdict`) |
| filter_1h_v_bottom_fake_recovery | 11598 | 11601 | `_filter_v_bottom_block_reasons` (via `_filter_v_bottom_verdict`) |
| filter_topping | 11641 | 11645 | `_filter_topping_block_reasons` (via `_filter_topping_verdict`) |
| filter_seller_imbalance | 11879 | 11882 | `_filter_seller_imbalance_block_reasons` (via `_filter_seller_imbalance_verdict`) |
| filter_negative_net_flow_5m | 11952 | 11955 | `_filter_neg_nf5m_block_reasons` (via `_filter_neg_nf5m_verdict`) |
| filter_above_vwap_chase | 12015 | 12018 | `_filter_avc_block_reasons` (via `_filter_avc_verdict`) |
| filter_knife_catch_peak | 12054 | 12057 | `_filter_kcp_block_reasons` (via `_filter_kcp_verdict`) |
| filter_reviving_lifecycle | 12083 | 12085 | `_filter_rvl_block_reasons` (via `_filter_rvl_verdict`) |
| filter_stale_h1_peak | 12151 | 12154 | `_filter_shp_block_reasons` (via `_filter_shp_verdict`) |
| filter_zero_winner_compound | 13238 | 13244 | `_zwc_fired` (list, fires if non-empty — no verdict var) |
| filter_lazy_fade_buy | 13260 | 13267 | direct numeric guard (no list/verdict pattern) |
| filter_premium_shallow_dip | 13282 | 13289 | direct numeric guard (no list/verdict pattern) |

## Total count

**41 ENFORCED filters** with early-continue pattern.

## Notes

### Anomaly 1 — Watchlist bypass (`_user_watch`) pattern
Seven filters skip the `continue` when `_user_watch` is truthy, using `if not _user_watch: continue` instead of a bare `continue`. These are:

- `filter_vp_poc` (line 3261–3262)
- `filter_meteora_dex` (line 3893–3894)
- `filter_bs_m5_weak` (line 3998–3999)
- `filter_chasing_bounce` (line 10272–10273)
- `filter_low_volatility` (line 10851–10852)
- `filter_solo_dropouts` (line 11116–11117)
- `filter_blowoff_top` (line 11354–11355)
- `filter_topping` (line 11645–11646)

The refactor must preserve the watchlist-bypass guard. The observational pattern for these should be:
```python
if not _user_watch:
    _filters_block.append("filter_X")
    # (or track with a note that watchlist bypass suppressed the block)
```

### Anomaly 2 — Trigger-demoting filters (not pure continues)
Two filters (`filter_clean_break_p90` at line 11011 and `filter_high_regime_buyvol` at line 11066) first mutate `_triggers_fired` (removing the specific trigger from the list), then only call `continue` if no other triggers remain. They also have a secondary path that logs but does NOT continue (trigger removed but others remain).

The refactor needs a two-phase approach for these:
1. Mutation phase: strip the offending trigger from `_triggers_fired` (keep as-is).
2. Block phase: append to `_filters_block` only when `not _triggers_fired`, then continue.

### Anomaly 3 — Boolean/direct-check blocks (no `_block_reasons` list)
Six filters use a direct boolean or numeric check rather than the standard `_block_reasons` list + verdict pattern:

- `filter_cluster_19_rug` — `_cluster_19_rug_block` (bool set inside `try/except`, line 2960)
- `filter_dev_rugged` — `_dev_pct < 2.0` (inline numeric)
- `filter_meteora_dex` / `filter_orca_dex` — `_grad_dex == "..."` inside a shared `try/except`
- `filter_premium_required` — `_premium_ok` bool (no list)
- `filter_morning_dead_zone` — `_mdz_premium_ok` bool (no list), nested inside an outer `if` block
- `filter_zero_winner_compound` — `_zwc_fired` list checked directly (no verdict var)
- `filter_lazy_fade_buy` — inline numeric guard (no list or verdict)
- `filter_premium_shallow_dip` — inline numeric guard (no list or verdict)

These don't have a `_filter_X_verdict` variable to replace. The refactor needs to synthesize the block-var name and use the direct condition as the gate.

### Anomaly 4 — Rescue/carve-out pattern (conditional continues)
Several filters check a rescue condition before the `continue`. The continue only fires if rescue is absent:

- `filter_turn` (line 3146–3161): `if (not _big_buyer_carve_out and not _chart_carve_out and not _pcb_carve_out and not _bs_h6_carve_out): continue`
- `filter_seller_imbalance` (line 11877): `if ... and not _big_buyer_carve_out_si: continue`
- `filter_negative_net_flow_5m` (line 11950): `if ... and not _big_buyer_carve_out_nf and not _nf5m_trig_rescue: continue`
- `filter_above_vwap_chase` (line 12013): `if ... and not _avc_rescued: continue`
- `filter_knife_catch_peak` (line 12052): `if ... and not _kcp_rescued: continue`
- `filter_reviving_lifecycle` (line 12081): `if ... and not _rvl_rescued: continue`
- `filter_stale_h1_peak` (line 12149): `if ... and not _shp_rescued: continue`
- `filter_lp_drain` (line 10622–10648): inside `if/else` tree, continue is inside the `else` branch
- `filter_1h_v_bottom_fake_recovery` (line 11573–11601): continue inside the `else` of a rescue check

The observational refactor should preserve the rescue guard but replace `continue` with `_filters_block.append("filter_X")`.

### Anomaly 5 — `filter_turn` deferred verdict check
`filter_turn` is unique: its verdict is computed early (around line 2393–3119), but the actual block check is deferred to line 3130 after `chart_score` is available. The refactor must not move this check — it must stay at its deferred location.

### Anomaly 6 — `filter_meteora_dex` and `filter_orca_dex` share a single `try/except` block
Both are evaluated inside a single `try/except NameError` block (lines 3885–3904). The refactor for both must stay inside this shared block.

### Anomaly 7 — `filter_morning_dead_zone` is nested
The `continue` at line 11255 is inside nested `if` blocks:
```python
if _flt_h in (7, 8) and pair_age_hours > 24:   # outer guard
    if not _mdz_premium_ok:                      # inner check
        ...
        continue                                  # line 11255
```
The observational pattern needs to flatten or preserve this nesting.
