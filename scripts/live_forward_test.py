"""
Live forward test: snapshot currently-trending tokens with their filter verdicts,
then resolve outcomes 2.5h+ later by checking actual price moves.

Builds a forward dataset of (snapshot_features → filter_verdicts → outcome) tuples
over many runs, used to compare which filter combinations actually yield positive
WR/PnL on real-time data we couldn't have curve-fit on.

Usage:
  python scripts/live_forward_test.py         # full cycle: resolve old + take new snapshot
  python scripts/live_forward_test.py status  # print accumulated stats per filter combo
  python scripts/live_forward_test.py purge   # clear snapshots older than 7 days

Snapshots stored in: .live_forward_test/{snapshot_id}.json
Aggregated stats in: .live_forward_test/_aggregate.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from curl_cffi import requests as cf_requests  # bot uses this for DS internal API

ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = ROOT / ".live_forward_test"
SNAPSHOT_DIR.mkdir(exist_ok=True)
AGGREGATE_PATH = SNAPSHOT_DIR / "_aggregate.json"
SLIP_HIST_PATH = SNAPSHOT_DIR / "_slip_history.json"

# ── Filter combos to test ─────────────────────────────────────────────────
# Each combo returns True if the candidate would be ALLOWED (PASS), False if BLOCKED.

def scanner_block_reasons(c):
    reasons = []
    if c['vol_h1'] < 10000: reasons.append('vol_h1<10k')
    if c['pc_h24'] <= 0: reasons.append('red_h24')
    if c['pc_m5'] > -3 and c['pc_h1'] > -3: reasons.append('no_real_dip')
    if c['peak_h24_6h_pct'] > 1000: reasons.append('peak>1000')
    return reasons

def turn_block(c):
    return c.get('pct_in_5m_range') is not None and c['pct_in_5m_range'] < 0.5

def variant_b_block(c):
    return c['peak_h24_6h_pct'] > 50 and c.get('candle_5m') == 'bullish_marubozu'

def variant_c_block(c):
    return c['peak_h24_6h_pct'] > 50 and c.get('struct_5m_verdict') in ('TREND_UP', 'TREND_DOWN')

def peak50_block(c):
    return c['peak_h24_6h_pct'] > 50

def chart_score_block(c):
    cs = c.get('chart_score')
    return cs is not None and cs < 40

def weak_bounce_block(c):
    """Shadow: 5m candle body/range < 0.20 (weak commitment)."""
    btr = c.get('body_to_range_5m')
    return btr is not None and btr < 0.20

def regime_panic_block(c):
    """Shadow: cohort h1-red breadth > 70% (broad market bleeding)."""
    r = c.get('regime_h1_neg_pct')
    return r is not None and r > 70.0

def macro_panic_block(c):
    """Phantom mirror for filter_macro_panic SHADOW 2026-05-16.

    Blocks when ANY of:
      - meme_sector_pct_h24 < -10 (sector dump)
      - sol_pc_h1 < -3 (SOL flash crash)
      - sol_pc_h4 < -5 AND btc_pc_h4 < -2 (macro flush)

    Premium-signature carve-out: avg_trade_size_h1>=116 AND
    liq_velocity_h1>=135 AND p90_buy_size>=153 → passes through.

    Fail-open when macro features absent (token universe pre-macro-context).
    """
    msc = c.get('meme_sector_pct_h24')
    sh1 = c.get('sol_pc_h1')
    sh4 = c.get('sol_pc_h4')
    bh4 = c.get('btc_pc_h4')
    panic = False
    if msc is not None and msc < -10.0:
        panic = True
    if sh1 is not None and sh1 < -3.0:
        panic = True
    if (sh4 is not None and sh4 < -5.0
            and bh4 is not None and bh4 < -2.0):
        panic = True
    if not panic:
        return False
    # Premium carve-out
    ats = c.get('avg_trade_size_h1_usd')
    lvh1 = c.get('liq_velocity_h1_usd_per_txn')
    p90 = c.get('p90_buy_size_usd')
    premium = (ats is not None and ats >= 116
               and lvh1 is not None and lvh1 >= 135
               and p90 is not None and p90 >= 153)
    return not premium

def slip_asym_block(c):
    """Shadow: sell-side liquidity hostile (slip_sell>8%, or sell/buy ratio>1.5x)."""
    sb = c.get('slip_buy_5000_pct')
    ss = c.get('slip_sell_5000_pct')
    if sb is None or ss is None:
        return False  # fail-open
    if ss > 8.0:
        return True
    if sb > 0 and (ss / sb) > 1.5:
        return True
    return False

def mtf_textbook_only_pass(c):
    """Pass only if textbook pullback pattern: 15m red AND 5m red AND 1m green."""
    return c.get('mtf_textbook_pullback') == 1

def sweep_too_recent_block(c):
    """Phantom parity for filter_sweep_too_recent (ENFORCED 2026-05-13).

    Production blocks when chart_sweep_5m_low_candles_ago <= 2 (sweep
    still unfolding, knife-catch). Consumes chart_sweep_5m_low_candles_ago
    from candidate snapshot if present; fails open otherwise.

    NOTE: snapshot currently does NOT enrich chart_sweep_* features (would
    require running chart_reader = 4 timeframe fetches per candidate).
    This phantom mirror is currently no-op until chart enrichment is added
    to fetch_snapshot. TODO same as informed_cluster / grad_window_dip.
    """
    v = c.get('chart_sweep_5m_low_candles_ago')
    return v is not None and v <= 2

def mtf_2plus_green_block(c):
    """Block if fewer than 2 of last 1m/5m/15m closed green."""
    g = c.get('mtf_green_count', 0)
    return g < 2

def bs_m5_low_block(c):
    """Block when buy/sell ratio on 5m < 1.40 (sellers dominating)."""
    bs = c.get('bs_m5')
    return bs is not None and bs < 1.40

def big_trade_size_block(c):
    """Block when avg trade size on h1 > $80 (whale-sized trades preceding dip)."""
    ats = c.get('avg_trade_size_h1_usd')
    return ats is not None and ats > 80.0

def slip_velocity_rising_block(c):
    """Block when slip_sell_5k velocity is rising (sell pressure building)."""
    vel = c.get('slip_sell_5k_velocity_pct_per_min')
    return vel is not None and vel > 0.05

def confirmation_candle_block(c):
    """Timing fix: block when 1m bounce isn't confirmed.
    Block if 1m_last_close_pct < +0.3 OR 1m_volume_spike < 1.0 (when present).
    Fail-open if 1m features absent."""
    lcp = c.get('1m_last_close_pct')
    vs = c.get('1m_volume_spike')
    if lcp is not None and lcp < 0.3:
        return True
    if vs is not None and vs < 1.0:
        return True
    return False

def clean_break_block(c):
    """ENFORCED 2026-05-06. Inverse of "first green after red" pattern.
    Block UNLESS: 1m_consec_red==0 AND 1m_red_count_5>=3 AND 1m_last_close_pct>0.
    Fail-open if any 1m feature is missing.

    Gate F added 2026-05-14 PM: also block when lifecycle_h24_ratio in
    [0.80, 0.95) — the dead-zone (mid-retracement-recovery near peak).
    Mining: n=41, 29.3% WR, -$30.50 — largest losing sub-cohort of
    clean_break solo.
    """
    consec = c.get('1m_consec_red')
    red5 = c.get('1m_red_count_5')
    lcp = c.get('1m_last_close_pct')
    if consec is None or red5 is None or lcp is None:
        # Fall through to dead-zone check rather than fail-open
        base_block = False
    else:
        base_block = not (consec == 0 and red5 >= 3 and lcp > 0)
    # Gate F: h24_ratio_to_peak dead zone (independent of base pattern)
    ratio = c.get('lifecycle_h24_ratio')
    if ratio is not None and 0.80 <= ratio < 0.95:
        return True
    return base_block

def double_bear_block(c):
    """ENFORCED 2026-05-06 PM. Block when BOTH bs_m5<0.70 AND p1h<0.10."""
    bs = c.get('bs_m5')
    p1h = c.get('pct_in_1h_range')
    return bs is not None and p1h is not None and bs < 0.70 and p1h < 0.10

def seller_dominant_block(c):
    """ENFORCED 2026-05-06 PM. Block when bs_m5 < 0.50 (5m sellers dominating)."""
    bs = c.get('bs_m5')
    return bs is not None and bs < 0.50

COMBOS = {
    'Z_truly_unfiltered':    lambda c: True,                                    # control: every trending token PASSES
    'A_scanner_baseline':    lambda c: not scanner_block_reasons(c),            # bot's existing scanner gates (vol_h1, red_h24, real_dip, peak1000)
    'B_with_filter_turn':    lambda c: not scanner_block_reasons(c) and not turn_block(c),
    'C_B_plus_var_b':        lambda c: not scanner_block_reasons(c) and not turn_block(c) and not variant_b_block(c),
    'D_B_plus_var_c':        lambda c: not scanner_block_reasons(c) and not turn_block(c) and not variant_c_block(c),
    'E_B_plus_peak50':       lambda c: not scanner_block_reasons(c) and not turn_block(c) and not peak50_block(c),
    'F_relaxed_turn_0.4':    lambda c: not scanner_block_reasons(c) and (c.get('pct_in_5m_range') is None or c['pct_in_5m_range'] >= 0.4),
    'G_relaxed_turn_0.3':    lambda c: not scanner_block_reasons(c) and (c.get('pct_in_5m_range') is None or c['pct_in_5m_range'] >= 0.3),
    # Shadow filters added 2026-05-05 — layered on B (scanner+turn) to test marginal lift.
    'H_B_plus_weak_bounce':  lambda c: not scanner_block_reasons(c) and not turn_block(c) and not weak_bounce_block(c),
    'I_B_plus_regime_panic': lambda c: not scanner_block_reasons(c) and not turn_block(c) and not regime_panic_block(c),
    'J_B_plus_slip_asym':    lambda c: not scanner_block_reasons(c) and not turn_block(c) and not slip_asym_block(c),
    'K_B_plus_all_three':    lambda c: not scanner_block_reasons(c) and not turn_block(c) and not weak_bounce_block(c) and not regime_panic_block(c) and not slip_asym_block(c),
    # Multi-TF momentum stacking (2026-05-05).
    'L_B_plus_mtf_textbook': lambda c: not scanner_block_reasons(c) and not turn_block(c) and mtf_textbook_only_pass(c),
    'M_B_plus_mtf_2green':   lambda c: not scanner_block_reasons(c) and not turn_block(c) and not mtf_2plus_green_block(c),
    # Regret-analysis-derived shadow filters (2026-05-05 PM).
    'N_B_plus_bs_m5_low':    lambda c: not scanner_block_reasons(c) and not turn_block(c) and not bs_m5_low_block(c),
    'O_B_plus_big_trade':    lambda c: not scanner_block_reasons(c) and not turn_block(c) and not big_trade_size_block(c),
    'P_B_plus_slip_vel':     lambda c: not scanner_block_reasons(c) and not turn_block(c) and not slip_velocity_rising_block(c),
    'Q_B_plus_regret_all':   lambda c: not scanner_block_reasons(c) and not turn_block(c) and not bs_m5_low_block(c) and not big_trade_size_block(c) and not slip_velocity_rising_block(c),
    # Timing-fix shadow filter (2026-05-05 PM): require positive 1m
    # confirmation candle (close >= +0.3% AND vol spike >= 1.0).
    'R_B_plus_confirm_candle': lambda c: not scanner_block_reasons(c) and not turn_block(c) and not confirmation_candle_block(c),
    # ─── LIVE PRODUCTION STACK ───
    # 2026-05-07: seller_dominant demoted to SHADOW (forward phantom test
    # showed -$5/cohort drag). S now matches live = clean_break + double_bear.
    'S_live_prod_stack':     lambda c: not scanner_block_reasons(c) and not clean_break_block(c) and not double_bear_block(c) and not sweep_too_recent_block(c),
    # Variant: same but stripped down to just clean_break (sanity check
    # that the additional gates aren't pulling weight on this slice).
    'T_clean_break_only':    lambda c: not scanner_block_reasons(c) and not clean_break_block(c),
    # ─── SHADOW TRACKING — every shadow filter must have a phantom combo ───
    # U: S + seller_dominant (the demoted filter — track if it would have
    # helped or hurt going forward; if forward data flips back positive,
    # promote back to enforced).
    'U_S_with_seller_dom':   lambda c: not scanner_block_reasons(c) and not clean_break_block(c) and not double_bear_block(c) and not seller_dominant_block(c),
    # ─── NEW PARALLEL TRIGGERS — ENFORCED 2026-05-12 ───
    # Phantom mirrors for new orthogonal entries in feeds/dip_scanner.py.
    # Combos return PASS when the live trigger would fire on this candidate.
    # TODO: patient_bottom, informed_cluster, grad_window_dip need
    # pct_above_vwap_1h, top10_buyer_within_60s_count, hours_since_graduation
    # to be added to phantom snapshot enrichment for full parity.
    # 2026-05-13: scoped C — fail-open helper. All new triggers now require
    # net_flow_60s_imbalance >= -0.3 (sellers not actively winning last 60s).
    'V_alpha_buyperscold':   lambda c: (c.get('bs_m5') is not None and c['bs_m5'] >= 3.0
                                        and c.get('pc_h24') is not None and c['pc_h24'] < 50
                                        and (c.get('net_flow_60s_imbalance') is None or c['net_flow_60s_imbalance'] >= -0.3)),
    'W_beta_retailfresh':    lambda c: (c.get('avg_trade_size_h1_usd') is not None and 0 < c['avg_trade_size_h1_usd'] < 60
                                        and c.get('pct_in_5m_range') is not None and c['pct_in_5m_range'] < 0.3
                                        and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] < 40
                                        and (c.get('net_flow_60s_imbalance') is None or c['net_flow_60s_imbalance'] >= -0.3)),
    'X_delta_microcap':      lambda c: (c.get('mcap') is not None and 0 < c['mcap'] < 5_000_000
                                        and c.get('slip_buy_5000_pct') is not None and c['slip_buy_5000_pct'] < 3.0
                                        and c.get('vol_h1') is not None and c['vol_h1'] > 50_000
                                        and (c.get('net_flow_60s_imbalance') is None or c['net_flow_60s_imbalance'] >= -0.3)),
    'Y_seller_exhaustion':   lambda c: (c.get('bs_m5') is not None and c['bs_m5'] >= 1.34
                                        and c.get('slip_sell_5k_velocity_pct_per_min') is not None and c['slip_sell_5k_velocity_pct_per_min'] >= 0.0004
                                        and c.get('slip_sell_5000_pct') is not None and c['slip_sell_5000_pct'] >= 2.25
                                        and (c.get('net_flow_60s_imbalance') is None or c['net_flow_60s_imbalance'] >= -0.3)),
    'AA_deep_dip_bottom':    lambda c: (c.get('pc_h24') is not None and c['pc_h24'] <= -7.48
                                        and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 7.2
                                        and (c.get('net_flow_60s_imbalance') is None or c['net_flow_60s_imbalance'] >= -0.3)),
    # 2026-05-12: patient_bottom_recovery — vwap_1h now phantom-available
    # via compute_rsi_overbought_features. min_since_peak_5m already enriched.
    'BB_patient_bottom':     lambda c: (c.get('pct_above_vwap_1h') is not None and c['pct_above_vwap_1h'] <= -3.0
                                        and c.get('min_since_peak_5m') is not None and c['min_since_peak_5m'] >= 60
                                        and (c.get('net_flow_60s_imbalance') is None or c['net_flow_60s_imbalance'] >= -0.3)),
    # ─── 1s ENFORCED TRIGGERS — 2026-05-13 (Phases 1/3/4) ───
    # Snapshot enrichment for these requires DexScreener 30S bar fetch per
    # candidate (not currently in fetch_snapshot). Phantom mirror PASSES
    # when 1s features present in snapshot; otherwise fails-closed (no fire).
    # TODO: add 1s enrichment to live_forward_test snapshot to close parity.
    'CC_1s_capit_reversal':  lambda c: (c.get('1s_cascade_reversal_detected') is True or
                                        (c.get('1s_vol_decay_120s') is not None and c['1s_vol_decay_120s'] >= 2.0
                                         and c.get('1s_close_pos_60s') is not None and c['1s_close_pos_60s'] >= 0.5
                                         and c.get('1s_cascade_length') is not None and c['1s_cascade_length'] >= 1)),
    'DD_1s_v_bottom_strict': lambda c: (c.get('1s_green_run_end') is not None and c['1s_green_run_end'] >= 2
                                        and c.get('1s_bars_since_low_60s') is not None and 3 <= c['1s_bars_since_low_60s'] <= 10
                                        and c.get('1s_lower_wick_ratio_last') is not None and c['1s_lower_wick_ratio_last'] >= 0.8
                                        and ((c.get('1s_vol_burst_on_reversal_ratio') is not None and c['1s_vol_burst_on_reversal_ratio'] >= 1.5)
                                             or (c.get('1s_vol_decay_120s') is not None and c['1s_vol_decay_120s'] >= 2.0))),
    'EE_1s_bottom_score_70': lambda c: (c.get('1s_bottom_score') is not None and c['1s_bottom_score'] >= 70),
    # ─── demand_bottom_compound — ENFORCED 2026-05-13 ───
    # 3-branch union: post-pump+demand, fresh-grad+demand, sweep+pump+score.
    # Held-out validated 83% WR on VAL set. After current filter stack:
    # 88% WR / 100% WR on marginal new entries.
    'FF_demand_bottom_compound': lambda c: (
        # B1: post-pump + escalating demand
        (c.get('buy_size_max_trend') is not None and c['buy_size_max_trend'] >= 2.0
         and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 500)
        # B2: fresh graduate + escalating demand
        or (c.get('graduation_status') == 'just_graduated'
            and c.get('buy_size_max_trend') is not None and c['buy_size_max_trend'] >= 2.0)
        # B3: bullish sweep + post-pump + chart score
        or (c.get('chart_sweep_5m_verdict') == 'BULLISH_SWEEP'
            and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 500
            and c.get('chart_score') is not None and c['chart_score'] >= 50)
    ),
    # ─── sweep_rejection + reaccum_demand — ENFORCED 2026-05-13 ───
    # GG_sweep_rejection — RETUNED 2026-05-13 PM with vwap_h24 + p5r gates
    # after IDLE loser revealed standalone wick>=4 was insufficient.
    'GG_sweep_rejection':    lambda c: (c.get('chart_sweep_5m_low_wick_pct') is not None
                                        and c['chart_sweep_5m_low_wick_pct'] >= 4.0
                                        and c.get('pct_above_vwap_h24') is not None
                                        and c['pct_above_vwap_h24'] <= 10.0
                                        and c.get('pct_in_5m_range') is not None
                                        and c['pct_in_5m_range'] >= 0.5),
    # HH_reaccum_demand — RETUNED 2026-05-13 PM with h24_ratio_to_peak<0.6
    # gate. Lifetime n=18, 87% WR, +$17.2. TRAIN 92% / VAL 80%.
    'HH_reaccum_demand':     lambda c: (c.get('chart_reaccum_drawdown_pct') is not None
                                        and c['chart_reaccum_drawdown_pct'] >= 50.0
                                        and c.get('buy_size_max_trend') is not None
                                        and c['buy_size_max_trend'] >= 2.0
                                        and c.get('h24_ratio_to_peak') is not None
                                        and c['h24_ratio_to_peak'] < 0.6),
    # ─── extreme_sweep_1m + controlled_greens_5m — ENFORCED 2026-05-13 PM ───
    # Two new triggers from deep candle synthesis (n=86,865 candles, 12 TFs).
    # Both gated by peak_h24 >= 200% (scoped to these triggers only).
    'JJ_extreme_sweep_1m':   lambda c: (c.get('1m_max_wick_body_ratio_last5') is not None
                                        and c['1m_max_wick_body_ratio_last5'] >= 10.0
                                        and c.get('peak_h24_6h_pct') is not None
                                        and c['peak_h24_6h_pct'] >= 200.0),
    'KK_controlled_greens_5m': lambda c: (c.get('5m_n_normal_greens_last8') is not None
                                          and c['5m_n_normal_greens_last8'] >= 4
                                          and c.get('peak_h24_6h_pct') is not None
                                          and c['peak_h24_6h_pct'] >= 200.0
                                          and c.get('last_5m_green') is True),
    # ─── pullback_in_uptrend + vol_surge_recent — ENFORCED 2026-05-13 PM ───
    # From round-2 analysis (n=55 combined, 27W vs 27L).
    'LL_pullback_in_uptrend': lambda c: (c.get('1h_last3_n_green') is not None
                                         and c['1h_last3_n_green'] >= 2
                                         and c.get('5m_last5_n_green') is not None
                                         and c['5m_last5_n_green'] <= 2
                                         and c.get('last_5m_green') is True),
    'MM_vol_surge_recent':    lambda c: (c.get('vol_surge_ratio_recent_prior') is not None
                                         and c['vol_surge_ratio_recent_prior'] >= 3.0),
    # ─── bullish_engulfing_5m — ENFORCED 2026-05-13 PM ───
    # Round-3 pattern, 100% precision on n=55 paired (6W/0L).
    'NN_bullish_engulfing_5m': lambda c: c.get('bullish_engulfing_5m') is True,
    # ─── mtf_aligned_demand — ENFORCED 2026-05-13 PM ───
    # chart_mtf_score and 1s_close_pos_60s already populated by phantom's
    # existing feature computers (mtf_green_count proxies mtf_score in
    # phantom; 1s_close_pos_60s computed in compute_1s_features).
    'OO_mtf_aligned_demand':  lambda c: (c.get('mtf_green_count') is not None
                                         and c['mtf_green_count'] >= 2
                                         and c.get('1s_close_pos_60s') is not None
                                         and c['1s_close_pos_60s'] >= 0.7),
    # ─── round-7 entry_meta-based triggers — ENFORCED 2026-05-13 PM ───
    'PP_liq_velocity_big_buyers': lambda c: (c.get('liq_velocity_h1_usd_per_txn') is not None
                                             and c['liq_velocity_h1_usd_per_txn'] >= 135.0),
    'QQ_net_flow_5m_demand':      lambda c: (c.get('net_flow_5m_usd') is not None
                                             and c['net_flow_5m_usd'] >= 300.0),
    'RR_mcap_psych_level':        lambda c: c.get('mcap_near_psych_level') is True,
    # Filter: should appear in c['blocked_filters'] phantom output (not a positive trigger)
    'SS_filter_mtf_strong_downtrend': lambda c: (c.get('chart_mtf_score') is None
                                                 or c.get('chart_mtf_score') > -2.0),
    # filter_negative_net_flow_5m + filter_seller_imbalance — ENFORCED 2026-05-14
    'TT_pass_net_flow_5m':    lambda c: (c.get('net_flow_5m_usd') is None
                                         or c['net_flow_5m_usd'] >= 0),
    'UU_pass_seller_imbalance': lambda c: (c.get('net_flow_5m_imbalance') is None
                                           or c['net_flow_5m_imbalance'] >= -0.2),
    # filter_fake_bounce carve-out — ENFORCED 2026-05-14
    # Pass if NOT (fake_bounce condition AND NOT calm-tape rescue).
    'VV_pass_fake_bounce_carved': lambda c: not (
        c.get('1m_last_close_pct') is not None
        and c.get('1m_volume_spike') is not None
        and c['1m_last_close_pct'] > 1.75
        and c['1m_volume_spike'] < 0.30
        and not (
            c.get('sells_per_min_recent') is not None
            and c['sells_per_min_recent'] < 20
        )
    ),
    # filter_top10_holder_band — ENFORCED 2026-05-14 PM (Commit A).
    # Block top10_holder_pct ∈ [70, 80) UNLESS liq_velocity_h1>=115.
    'XX_pass_top10_holder_band': lambda c: not (
        c.get('top10_holder_pct') is not None
        and 70.0 <= c['top10_holder_pct'] < 80.0
        and not (
            c.get('liq_velocity_h1_usd_per_txn') is not None
            and c['liq_velocity_h1_usd_per_txn'] >= 115
        )
    ),
    # filter_above_vwap_chase — ENFORCED 2026-05-14 PM (Commit A).
    # Block pct_above_vwap_h24 ∈ [+10, +30) UNLESS liq_velocity_h1>=115.
    'YY_pass_above_vwap_chase': lambda c: not (
        c.get('pct_above_vwap_h24') is not None
        and 10.0 <= c['pct_above_vwap_h24'] < 30.0
        and not (
            c.get('liq_velocity_h1_usd_per_txn') is not None
            and c['liq_velocity_h1_usd_per_txn'] >= 115
        )
    ),
    # filter_knife_catch_peak — ENFORCED 2026-05-14 PM (Commit A).
    # Block h24_ratio_to_peak ∈ [0.85, 1.0) UNLESS liq_velocity_h1>=115.
    # h24_ratio not in phantom snapshot — derive from pc_h24/peak_h24_6h_pct.
    'ZZ_pass_knife_catch_peak': lambda c: not (
        c.get('pc_h24') is not None
        and c.get('peak_h24_6h_pct') is not None
        and c['peak_h24_6h_pct'] > 0
        and 0.85 <= (c['pc_h24'] / c['peak_h24_6h_pct']) < 1.0
        and not (
            c.get('liq_velocity_h1_usd_per_txn') is not None
            and c['liq_velocity_h1_usd_per_txn'] >= 115
        )
    ),
    # ── Commit B: 3 more blockers (smaller samples but stable held-out) ──
    # All with same lvh1>=115 big-buyer carve-out.
    'AB_pass_reviving_lifecycle': lambda c: not (
        c.get('lifecycle_stage') == 'reviving'
        and not (
            c.get('liq_velocity_h1_usd_per_txn') is not None
            and c['liq_velocity_h1_usd_per_txn'] >= 115
        )
    ),
    'AC_pass_already_mooned': lambda c: not (
        c.get('peak_h24_6h_pct') is not None
        and c['peak_h24_6h_pct'] >= 3000
        and not (
            c.get('liq_velocity_h1_usd_per_txn') is not None
            and c['liq_velocity_h1_usd_per_txn'] >= 115
        )
    ),
    'AD_pass_stale_h1_peak': lambda c: not (
        c.get('time_since_h1_peak_secs') is not None
        and 3000 <= c['time_since_h1_peak_secs'] < 3600
        and not (
            c.get('liq_velocity_h1_usd_per_txn') is not None
            and c['liq_velocity_h1_usd_per_txn'] >= 115
        )
    ),
    # trigger_whale_conviction — ENFORCED 2026-05-14 PM (Commit C).
    # Positive trigger: fires when whale_buy_present_2k OR
    # top10_buyer_within_60s_count >= 3. Both features available in phantom.
    # GATE added 2026-05-14 PM: block [0.80, 0.95) h24_ratio_to_peak dead zone.
    # Mining audit (n=155, top10>=3 branch): ratio 0.80-0.95 = 39.4% WR / -$6.44
    # across 33 fires. Live confirmation: RAGEGUY 17:14 dumped -4.9%.
    'AE_trigger_whale_conviction': lambda c: (
        (
            c.get('whale_buy_present_2k') is True
            or (c.get('top10_buyer_within_60s_count') is not None
                and c['top10_buyer_within_60s_count'] >= 3)
        )
        # Gate: skip dead zone (mid-retracement-recovery near peak)
        and not (
            c.get('lifecycle_h24_ratio') is not None
            and 0.80 <= c['lifecycle_h24_ratio'] < 0.95
        )
    ),
    # trigger_strong_uptrend_dip — ENFORCED 2026-05-14 PM (chart Compound D).
    # Phantom approximation using pc_h1+pc_h24 as proxies for 1h candle data
    # (phantom snapshot doesn't have full 1h candle history).
    # Fires when pc_h24 > 30 (proxy for 1h_6h_chg>30) AND pc_h1 > 0
    # (proxy for "no recent 1h breakdown"). Coarser than the production
    # 1h-candle predicate but captures the same shape.
    'AF_strong_uptrend_dip': lambda c: (
        c.get('pc_h24') is not None and c['pc_h24'] > 30
        and c.get('pc_h1') is not None and c['pc_h1'] > 0
    ),
    # filter_quad — PROMOTED to ENFORCED 2026-05-14 with big-buyer carve-out.
    # 4-component OR-block (velocity_verdict==QUIET, stop_cluster band,
    # lp_locked band, 1m_volume_spike band) UNLESS liq_velocity_h1>=115.
    'WW_pass_filter_quad':    lambda c: not (
        (
            c.get('velocity_verdict') == 'QUIET'
            or (c.get('chart_stop_cluster_5m_pct_below') is not None
                and 1.26 <= c['chart_stop_cluster_5m_pct_below'] < 3.78)
            or (c.get('lp_locked_pct') is not None
                and 60.15 <= c['lp_locked_pct'] < 78.90)
            or (c.get('1m_volume_spike') is not None
                and 0.31 <= c['1m_volume_spike'] < 0.80)
        )
        and not (
            c.get('liq_velocity_h1_usd_per_txn') is not None
            and c['liq_velocity_h1_usd_per_txn'] >= 115
        )
    ),
    # TODO: informed_cluster + grad_window_dip still need top10_buyer_within_60s_count
    # and hours_since_graduation in phantom enrichment. Would require recent_trades
    # fetch + graduation_status lookup per candidate (~30 extra GT calls/snap).
    # ─── trigger_modest_pump_deep_retrace — ENFORCED 2026-05-14 PM ───
    # MASCOTS pattern: peak_h24_6h in [50, 150) AND h24_ratio_to_peak < 0.10.
    # Audit n=6, 66.7% WR, +$3.94 (ratio<0.05 tighter: n=5, 80% WR).
    'AK_modest_pump_deep_retrace': lambda c: (
        c.get('peak_h24_6h_pct') is not None
        and 50 <= c['peak_h24_6h_pct'] < 150
        and c.get('h24_ratio_to_peak') is not None
        and c['h24_ratio_to_peak'] < 0.10
    ),
    # ─── trigger_small_pump_shallow_retrace — ENFORCED 2026-05-14 PM ───
    # Highest-EV cohort: peak[25,50) AND ratio[0.60,0.80). Audit n=56, 66.1% WR,
    # +$418.8 total ($7.48/trade avg — far above baseline +$0.11/trade).
    'AL_small_pump_shallow_retrace': lambda c: (
        c.get('peak_h24_6h_pct') is not None
        and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('h24_ratio_to_peak') is not None
        and 0.60 <= c['h24_ratio_to_peak'] < 0.80
    ),
    # ─── 5 exhaustive-mining triggers — ENFORCED 2026-05-14 PM ───
    # All from cross-feature 2D/3D grid mining of .dataset.pkl.
    'AM_shallow_retrace_fresh_pump': lambda c: (
        c.get('peak_h24_6h_pct') is not None and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('h24_ratio_to_peak') is not None and 0.70 <= c['h24_ratio_to_peak'] < 0.85
        and c.get('cycles_seen') is not None and 10 <= c['cycles_seen'] < 30
    ),
    'AN_midcap_quality_accumulation': lambda c: (
        c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('h24_ratio_to_peak') is not None and 0.5 <= c['h24_ratio_to_peak'] < 0.7
    ),
    'AO_fresh_graduate_buyers': lambda c: (
        c.get('hours_since_graduation') is not None and 6 <= c['hours_since_graduation'] < 24
        and c.get('bs_h1') is not None and 1.3 <= c['bs_h1'] < 1.6
    ),
    'AP_small_pump_fresh_cycles': lambda c: (
        c.get('peak_h24_6h_pct') is not None and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('cycles_seen') is not None and 10 <= c['cycles_seen'] < 30
        and c.get('avg_trade_size_h1_usd') is not None and 200 <= c['avg_trade_size_h1_usd'] < 500
    ),
    'AQ_midcap_bigpump_fresh': lambda c: (
        c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 1000
    ),
    # ─── Overnight-edge triggers — phantom parity 2026-05-14 PM ────────
    # mine_overnight_cohorts.py — gate is full overnight band
    # hour_ct in [19, 24) ∪ [0, 7). All 6 mirror production.
    'AR_overnight_modest_pump_consol': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('peak_h24_6h_pct') is not None and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
    ),
    'AS_overnight_quiet_accumulation': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('avg_trade_size_h1_usd') is not None and 60 <= c['avg_trade_size_h1_usd'] < 100
        and c.get('cycles_seen') is not None and 30 <= c['cycles_seen'] < 60
    ),
    'AU_overnight_fresh_small_pump': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('peak_h24_6h_pct') is not None and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('cycles_seen') is not None and 10 <= c['cycles_seen'] < 30
    ),
    'AV_overnight_quality_old': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('hours_since_graduation') is not None
        and c['hours_since_graduation'] >= 720
    ),
    'AW_overnight_micropump_buyers': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('bs_h1') is not None and 0.9 <= c['bs_h1'] < 1.1
        and c.get('peak_h24_6h_pct') is not None and 0 <= c['peak_h24_6h_pct'] < 25
    ),
    'AX_overnight_mature_midcap': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('cycles_seen') is not None and 60 <= c['cycles_seen'] < 150
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
    ),
    # ─── 3D-refined overnight phantom mirrors — 2026-05-14 PM ───
    'AY_overnight_3d_bigpump_fresh_age': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 1000
        and c.get('hours_since_graduation') is not None
        and 6 <= c['hours_since_graduation'] < 24
    ),
    'AZ_overnight_3d_bigpump_midcap': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 1000
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
    ),
    'BA_overnight_3d_midcap_liq_band': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('bs_h1') is not None and 1.1 <= c['bs_h1'] < 1.3
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('liquidity_usd') is not None
        and 100_000 <= c['liquidity_usd'] < 250_000
    ),
    'BB_overnight_3d_bigpump_avgtrade': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 1000
        and c.get('avg_trade_size_h1_usd') is not None
        and 100 <= c['avg_trade_size_h1_usd'] < 200
    ),
    'BC_overnight_3d_midcap_mature_cycles': lambda c: (
        (lambda _h: (19 <= _h < 24) or (0 <= _h < 7))(
            __import__('datetime').datetime.now(
                __import__('zoneinfo').ZoneInfo('America/Chicago')
            ).hour
        )
        and c.get('bs_h1') is not None and 1.1 <= c['bs_h1'] < 1.3
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('cycles_seen') is not None and 60 <= c['cycles_seen'] < 150
    ),
    # ─── 11 full-day 3D phantom mirrors — 2026-05-15 ───
    'BD_3d_balanced_h1_fresh_predawn': lambda c: (
        (lambda _h: 4 <= _h < 8)(__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/Chicago')).hour)
        and c.get('bs_h1') is not None and 0.9 <= c['bs_h1'] < 1.1
        and c.get('cycles_seen') is not None and 10 <= c['cycles_seen'] < 30
    ),
    'BE_3d_small_pump_shallow_fresh': lambda c: (
        c.get('peak_h24_6h_pct') is not None and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('h24_ratio_to_peak') is not None and 0.7 <= c['h24_ratio_to_peak'] < 0.85
        and c.get('cycles_seen') is not None and 10 <= c['cycles_seen'] < 30
    ),
    'BF_3d_active_5m_small_pump_fresh': lambda c: (
        c.get('bs_m5') is not None and 1.5 <= c['bs_m5'] < 2.0
        and c.get('peak_h24_6h_pct') is not None and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('cycles_seen') is not None and 10 <= c['cycles_seen'] < 30
    ),
    'BG_3d_compound_buyers_fresh_age': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('bs_h1') is not None and 1.3 <= c['bs_h1'] < 1.6
        and c.get('hours_since_graduation') is not None and 6 <= c['hours_since_graduation'] < 24
    ),
    'BH_3d_strong_h1_fresh_daytime': lambda c: (
        (lambda _h: 12 <= _h < 17)(__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/Chicago')).hour)
        and c.get('bs_h1') is not None and 1.3 <= c['bs_h1'] < 1.6
        and c.get('hours_since_graduation') is not None and 6 <= c['hours_since_graduation'] < 24
    ),
    'BI_3d_midrange_midcap_predawn': lambda c: (
        (lambda _h: 4 <= _h < 8)(__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/Chicago')).hour)
        and c.get('h24_ratio_to_peak') is not None and 0.5 <= c['h24_ratio_to_peak'] < 0.7
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
    ),
    'BJ_3d_bigpump_midcap_24_7': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 1000
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
    ),
    'BK_3d_compound_midcap_fresh_age': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('hours_since_graduation') is not None and 6 <= c['hours_since_graduation'] < 24
    ),
    'BL_3d_extreme_h1_midliq_predawn': lambda c: (
        (lambda _h: 4 <= _h < 8)(__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/Chicago')).hour)
        and c.get('bs_h1') is not None and c['bs_h1'] >= 2.0
        and c.get('liquidity_usd') is not None and 250_000 <= c['liquidity_usd'] < 1_000_000
    ),
    'BM_3d_compound_strong5m_midtrade': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('bs_m5') is not None and 1.5 <= c['bs_m5'] < 2.0
        and c.get('avg_trade_size_h1_usd') is not None and 200 <= c['avg_trade_size_h1_usd'] < 500
    ),
    'BN_3d_mature_midcap_postmidnight': lambda c: (
        (lambda _h: 0 <= _h < 4)(__import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/Chicago')).hour)
        and c.get('cycles_seen') is not None and 60 <= c['cycles_seen'] < 150
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
    ),
    # ─── 8 deep-mining 3D phantom mirrors (WR>=80%) — 2026-05-15 ───
    'BO_3d_liq_midcap_compound': lambda c: (
        c.get('liquidity_usd') is not None and 100_000 <= c['liquidity_usd'] < 250_000
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('bs_h6') is not None and c.get('bs_h1') is not None
        and 1.3 <= (c['bs_h6'] * c['bs_h1']) < 1.8
    ),
    'BP_3d_h6_fresh_age_compound': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('hours_since_graduation') is not None and 6 <= c['hours_since_graduation'] < 24
        and c.get('bs_h1') is not None
        and 1.3 <= (c['bs_h6'] * c['bs_h1']) < 1.8
    ),
    'BQ_3d_h1_midcap_liq_24_7': lambda c: (
        c.get('bs_h1') is not None and 1.1 <= c['bs_h1'] < 1.3
        and c.get('liquidity_usd') is not None and 100_000 <= c['liquidity_usd'] < 250_000
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
    ),
    'BR_3d_h6_smallpump_midtrade': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('peak_h24_6h_pct') is not None and 25 <= c['peak_h24_6h_pct'] < 50
        and c.get('avg_trade_size_h1_usd') is not None and 200 <= c['avg_trade_size_h1_usd'] < 500
    ),
    'BS_3d_h6_strong5m_old': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('bs_m5') is not None and 1.5 <= c['bs_m5'] < 2.0
        and c.get('hours_since_graduation') is not None and c['hours_since_graduation'] >= 720
    ),
    'BT_3d_h6_midcap_deepdrop': lambda c: (
        c.get('bs_h6') is not None and 1.1 <= c['bs_h6'] < 1.3
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('peak_h24_6h_pct') is not None
        and c.get('h24_ratio_to_peak') is not None
        and ((1.0 - c['h24_ratio_to_peak']) * c['peak_h24_6h_pct']) >= 1000
    ),
    'BU_3d_bigpump_midcap_compound': lambda c: (
        c.get('peak_h24_6h_pct') is not None and c['peak_h24_6h_pct'] >= 1000
        and c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('bs_h6') is not None and c.get('bs_h1') is not None
        and 1.3 <= (c['bs_h6'] * c['bs_h1']) < 1.8
    ),
    'BV_3d_midcap_fresh_age_compound': lambda c: (
        c.get('mcap') is not None and 2_000_000 <= c['mcap'] < 10_000_000
        and c.get('hours_since_graduation') is not None and 6 <= c['hours_since_graduation'] < 24
        and c.get('bs_h6') is not None and c.get('bs_h1') is not None
        and 1.3 <= (c['bs_h6'] * c['bs_h1']) < 1.8
    ),
    # ─── Defensive filter phantom mirrors (would-have-blocked) — 2026-05-15 ───
    # Note: these are FILTERS — predicates return True when the entry WOULD
    # have been blocked (inverse of allow-predicate). Forward audit measures
    # whether blocking these would have helped P&L.
    'FILT_F2_dead_5m_eve_wknd_BLOCK': lambda c: (
        c.get('bs_m5') is not None and c['bs_m5'] < 0.8
        and (lambda _now: 17 <= _now.hour < 22 and _now.weekday() in (5, 6))(
            __import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/Chicago'))
        )
    ),
    'FILT_F4_sat_eve_midliq_BLOCK': lambda c: (
        c.get('liquidity_usd') is not None and 100_000 <= c['liquidity_usd'] < 250_000
        and (lambda _now: 17 <= _now.hour < 22 and _now.weekday() == 5)(
            __import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('America/Chicago'))
        )
    ),
    'FILT_F5_microcap_trap_BLOCK': lambda c: (
        c.get('bs_h1') is not None and 1.1 <= c['bs_h1'] < 1.3
        and c.get('mcap') is not None and 500_000 <= c['mcap'] < 2_000_000
        and c.get('liquidity_usd') is not None and 100_000 <= c['liquidity_usd'] < 250_000
    ),
    # ── filter_falling_knife phantom mirror — ENFORCED 2026-05-15 ──────
    # Mirror of dip_scanner.py filter_falling_knife. RAGEGUY 2026-05-15
    # 03:05 UTC: 4 triggers stacked but mtf=-1 AND 1m_last_close=-0.83%
    # → stop -8.5% in 21min. Audit validation: BLOCK n=5, 1W/4L, net
    # +$7.17 / 5 days (only winner blocked: MASCOTS +$1.40).
    'FILT_falling_knife_BLOCK': lambda c: (
        c.get('chart_mtf_score') is not None and c['chart_mtf_score'] <= -1
        and c.get('1m_last_close_pct') is not None and c['1m_last_close_pct'] < 0
    ),
    # ── trigger_post_capit_breakout phantom mirror — ENFORCED 2026-05-15 ──
    # Mirror of dip_scanner.py trigger_post_capit_breakout. Positive
    # V-bottom reversal trigger with carve-outs on filter_turn /
    # filter_sweep_too_recent / filter_chasing_top. signal_events mining
    # (n=25 forward-traceable, 26h): 20% reach +5pp, 24% reach -7pp,
    # fat-tail wins observed (CBRS +434pp). ENFORCED with carve-outs.
    'AT_post_capit_breakout': lambda c: (
        c.get('1m_last_close_pct') is not None and c['1m_last_close_pct'] >= 2.0
        and c.get('1m_volume_spike') is not None and c['1m_volume_spike'] >= 2.0
        and c.get('pc_h1') is not None and c['pc_h1'] < 0
    ),
    # ─── Cascade V-bottom — phantom parity 2026-05-14 PM ───────────────
    # Mirror of trigger_cascade_v_bottom SHADOW (dip_scanner.py).
    # Ground-truth: BURNIE 2026-05-14 15:53:18 CT V-bottom after -5.12% 1m
    # cascade — entry candidate had cum_30s=+1.20%, cpos=0.99, vbst=2.6x.
    # Predicate fails closed if any of the 4 features is missing in the
    # snapshot (1m_volume_spike, 1m_cum_3min_pct, 1s_close_pos_60s,
    # 1s_vol_burst_on_reversal_ratio).
    'AT_cascade_v_bottom': lambda c: (
        c.get('1m_cum_3min_pct') is not None and c['1m_cum_3min_pct'] <= -3.0
        and c.get('1m_volume_spike') is not None and c['1m_volume_spike'] >= 3.0
        and c.get('1s_close_pos_60s') is not None and c['1s_close_pos_60s'] >= 0.85
        and c.get('1s_vol_burst_on_reversal_ratio') is not None
        and c['1s_vol_burst_on_reversal_ratio'] >= 1.5
    ),
    # ─── UptrendScanner SHADOW Phase 1 mirrors — 2026-05-14 evening ───
    # Phantom parity for feeds/uptrend_scanner.py. Predicates PASS when the
    # corresponding shadow trigger would WOULD-FIRE on this snapshot. All
    # require the same gates (mtf=bull, 5m_state=uptrend, chart_score>=50).
    # Fail-closed: missing fields => predicate returns False.
    # Snapshot enrichment status: chart_mtf_alignment, chart_structure_5m_state,
    # chart_structure_5m_recent_bos_dir, chart_reaccum_verdict,
    # chart_trendline_5m_breakout_up, chart_vp_above_poc, chart_pattern_5m_dir,
    # chart_pattern_5m_conf, chart_sr_5m_at_resistance, chart_score, and
    # 1m_volume_spike are expected from the existing chart_reader integration.
    # If any is None at snapshot time, the trigger fails-closed.
    'AG_uptrend_gates_pass':  lambda c: (
        c.get('chart_mtf_alignment') in ('bull', 'strong_bull')
        and c.get('chart_structure_5m_state') == 'uptrend'
        and c.get('chart_score') is not None and c['chart_score'] >= 50
    ),
    'AH_uptrend_breakout_resist': lambda c: (
        # gates
        c.get('chart_mtf_alignment') in ('bull', 'strong_bull')
        and c.get('chart_structure_5m_state') == 'uptrend'
        and c.get('chart_score') is not None and c['chart_score'] >= 50
        # trigger T1
        and c.get('chart_structure_5m_recent_bos_dir') == 'up'
        and c.get('1m_volume_spike') is not None and c['1m_volume_spike'] >= 1.5
        and c.get('chart_vp_above_poc') is True
    ),
    'AI_uptrend_range_expansion': lambda c: (
        # gates
        c.get('chart_mtf_alignment') in ('bull', 'strong_bull')
        and c.get('chart_structure_5m_state') == 'uptrend'
        and c.get('chart_score') is not None and c['chart_score'] >= 50
        # trigger T2
        and c.get('chart_trendline_5m_breakout_up') is True
        and c.get('chart_pattern_5m_dir') == 'bullish'
        and c.get('chart_pattern_5m_conf') is not None and c['chart_pattern_5m_conf'] >= 60
    ),
    'AJ_uptrend_continuation': lambda c: (
        # gates
        c.get('chart_mtf_alignment') in ('bull', 'strong_bull')
        and c.get('chart_structure_5m_state') == 'uptrend'
        and c.get('chart_score') is not None and c['chart_score'] >= 50
        # trigger T3
        and c['chart_score'] >= 60
        and c.get('chart_reaccum_verdict') in ('accum', 'trending')
        and c.get('chart_sr_5m_at_resistance') is not True
        and c.get('chart_vp_above_poc') is True
    ),
    # ── Chart CNN phantom mirrors — SHADOW 2026-05-15 ─────────────
    'CNN_outcome_above_60': lambda c: (
        c.get('cnn_outcome_prob') is not None and c['cnn_outcome_prob'] >= 0.60
    ),
    'CNN_outcome_above_70': lambda c: (
        c.get('cnn_outcome_prob') is not None and c['cnn_outcome_prob'] >= 0.70
    ),
    'CNN_pattern_double_bottom': lambda c: (
        c.get('cnn_pattern') == 'double_bottom'
    ),
    # filter_cluster_19_rug parity — ENFORCED 2026-05-15
    'FILT_cluster_19_rug_BLOCK': lambda c: c.get('cnn_cluster_19_rug') is True,
}


# ── Token discovery ───────────────────────────────────────────────────────

def fetch_trending_tokens():
    """Pull GT trending Solana pools (3 pages = ~60 candidates)."""
    pools = []
    for page in (1, 2, 3):
        try:
            r = requests.get(f'https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page={page}', timeout=10)
            pools.extend(r.json().get('data') or [])
        except Exception:
            pass
        time.sleep(2.5)  # respect 25/min GT rate limit
    return pools


# ── Expanded candidate sources (mirrors bot's _fetch_candidates) ──────────

_DS_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_SEARCH_TERMS = ["sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "ai", "baby", "pump"]


def _safe_get(url, timeout=10):
    try:
        r = requests.get(url, headers=_DS_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def fetch_dexscreener_boosts_addrs():
    """DexScreener token-boosts/top — returns list of token addresses."""
    data = _safe_get("https://api.dexscreener.com/token-boosts/top/v1")
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("pairs", [])
    return [it.get("tokenAddress") or it.get("address") for it in (items or [])
            if it.get("chainId") == "solana" and (it.get("tokenAddress") or it.get("address"))]


def fetch_dexscreener_profiles_addrs():
    """DexScreener token-profiles/latest — returns list of token addresses."""
    data = _safe_get("https://api.dexscreener.com/token-profiles/latest/v1")
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("pairs", [])
    return [it.get("tokenAddress") or it.get("address") for it in (items or [])
            if it.get("chainId") == "solana" and (it.get("tokenAddress") or it.get("address"))]


def fetch_dexscreener_search_addrs():
    """DexScreener search across keyword list — returns deduped token addresses."""
    addrs = set()
    for kw in _SEARCH_TERMS:
        data = _safe_get(
            f"https://api.dexscreener.com/latest/dex/search?q={kw}&chainId=solana",
            timeout=8,
        )
        for p in (data or {}).get("pairs", []) or []:
            if p.get("chainId") != "solana":
                continue
            ta = (p.get("baseToken") or {}).get("address", "")
            if ta:
                addrs.add(ta)
        time.sleep(0.3)  # gentle on DS
    return list(addrs)


def fetch_axiom_trending_addrs():
    """Axiom users-trending-v2 via the same fetcher the bot uses.
    Skips silently if no auth is available locally."""
    # Axiom requires an authenticated token. Locally, we don't have the bot's
    # auth manager — try to use the saved token file directly.
    token_file = Path.home() / ".axiom_tokens.json"
    if not token_file.exists():
        # Fall back: try the bot's persisted token at /data path (Railway only)
        return []
    try:
        with open(token_file) as f:
            saved = json.load(f)
        access = saved.get("auth_token") or saved.get("access")
        if not access:
            return []
        cookie = f"auth-access-token={access}"
        for server in ("https://api2.axiom.trade", "https://api3.axiom.trade"):
            try:
                r = requests.get(
                    f"{server}/users-trending-v2?timePeriod=1h",
                    headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0",
                             "Accept": "application/json"},
                    timeout=8,
                )
                if r.status_code == 200:
                    data = r.json()
                    pairs = data if isinstance(data, list) else (data.get("pairs") or [])
                    return [(p.get("baseToken") or {}).get("address", "")
                            for p in pairs if (p.get("baseToken") or {}).get("address")]
            except Exception:
                continue
    except Exception:
        pass
    return []


def fetch_ds_pairs_for_addrs(addrs):
    """Batch-enrich token addresses via DexScreener /tokens. Returns list of
    DS-format pair dicts (one highest-liq pair per base address)."""
    pairs = []
    addrs = list(dict.fromkeys(addrs))  # dedupe preserve order
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i + 30]
        url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
        data = _safe_get(url)
        if not data:
            continue
        # Pick highest-liq pair per base address
        best = {}
        for p in data.get("pairs") or []:
            if p.get("chainId") != "solana":
                continue
            ta = (p.get("baseToken") or {}).get("address", "")
            if not ta:
                continue
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            cur = best.get(ta)
            if cur is None or liq > float((cur.get("liquidity") or {}).get("usd") or 0):
                best[ta] = p
        pairs.extend(best.values())
        time.sleep(0.3)
    return pairs


def normalize_ds(pair):
    """DS-format pair → normalized candidate dict. Mirrors normalize() but
    reads from DS field names. Returns None if outside mcap window or
    missing required fields."""
    if pair.get("chainId") != "solana":
        return None
    base = pair.get("baseToken") or {}
    token = base.get("address")
    pair_addr = pair.get("pairAddress")
    if not token or not pair_addr:
        return None
    name = base.get("symbol") or base.get("name") or "?"
    try:
        mcap = float(pair.get("marketCap") or pair.get("fdv") or 0)
    except (ValueError, TypeError):
        return None
    if mcap < 1_000_000 or mcap > 100_000_000:
        return None
    vol = pair.get("volume") or {}
    pc = pair.get("priceChange") or {}
    pc_h6 = float(pc.get("h6") or 0)
    pc_h24 = float(pc.get("h24") or 0)
    txns = pair.get("txns") or {}
    m5_txns = txns.get("m5") or {}
    h1_txns = txns.get("h1") or {}
    h6_txns = txns.get("h6") or {}
    b_m5 = int(m5_txns.get("buys") or 0)
    s_m5 = int(m5_txns.get("sells") or 0)
    bs_m5 = (b_m5 / s_m5) if s_m5 > 0 else None
    b_h1 = int(h1_txns.get("buys") or 0)
    s_h1 = int(h1_txns.get("sells") or 0)
    bs_h1 = (b_h1 / s_h1) if s_h1 > 0 else None
    b_h6 = int(h6_txns.get("buys") or 0)
    s_h6 = int(h6_txns.get("sells") or 0)
    bs_h6 = (b_h6 / s_h6) if s_h6 > 0 else None
    h1_total_txns = b_h1 + s_h1
    vol_h1_val = float(vol.get("h1") or 0)
    avg_trade_size_h1 = (vol_h1_val / h1_total_txns) if h1_total_txns > 0 else None
    liq_usd = float((pair.get("liquidity") or {}).get("usd") or 0)
    return {
        "symbol": name[:13],
        "pair": pair_addr, "token": token, "mcap": mcap,
        "vol_h1": vol_h1_val,
        "liq": liq_usd, "liquidity_usd": liq_usd,
        "pc_m5": float(pc.get("m5") or 0),
        "pc_h1": float(pc.get("h1") or 0),
        "pc_h6": pc_h6, "pc_h24": pc_h24,
        "peak_h24_6h_pct": max(pc_h24, pc_h6, 0),
        "price": float(pair.get("priceUsd") or 0),
        "bs_m5": bs_m5, "bs_h1": bs_h1, "bs_h6": bs_h6,
        "avg_trade_size_h1_usd": avg_trade_size_h1,
    }


def normalize(pools):
    out = []
    for p in pools:
        attrs = p.get('attributes') or {}
        pair = attrs.get('address')
        name = attrs.get('name') or '?'
        base_id = ((p.get('relationships') or {}).get('base_token') or {}).get('data', {}).get('id') or ''
        token = base_id.replace('solana_','') if base_id else ''
        if not token or not pair: continue
        try: mcap = float(attrs.get('market_cap_usd') or attrs.get('fdv_usd') or 0)
        except: continue
        if mcap < 1_000_000 or mcap > 100_000_000: continue
        vol = attrs.get('volume_usd') or {}
        pc = attrs.get('price_change_percentage') or {}
        pc_h6 = float(pc.get('h6') or 0)
        pc_h24 = float(pc.get('h24') or 0)
        # Transaction breakdowns per timeframe — used for bs_m5 (buy/sell ratio
        # on 5m) and avg_trade_size_h1 (h1 vol / total h1 txns).
        txns = attrs.get('transactions') or {}
        m5_txns = txns.get('m5') or {}
        h1_txns = txns.get('h1') or {}
        h6_txns = txns.get('h6') or {}
        b_m5 = int(m5_txns.get('buys') or 0)
        s_m5 = int(m5_txns.get('sells') or 0)
        bs_m5 = (b_m5 / s_m5) if s_m5 > 0 else None
        b_h1 = int(h1_txns.get('buys') or 0)
        s_h1 = int(h1_txns.get('sells') or 0)
        bs_h1 = (b_h1 / s_h1) if s_h1 > 0 else None
        b_h6 = int(h6_txns.get('buys') or 0)
        s_h6 = int(h6_txns.get('sells') or 0)
        bs_h6 = (b_h6 / s_h6) if s_h6 > 0 else None
        h1_total_txns = b_h1 + s_h1
        vol_h1_val = float(vol.get('h1') or 0)
        avg_trade_size_h1 = (vol_h1_val / h1_total_txns) if h1_total_txns > 0 else None
        liq_usd = float(attrs.get('reserve_in_usd') or 0)
        out.append({
            'symbol': name.split('/')[0].strip()[:13],
            'pair': pair, 'token': token, 'mcap': mcap,
            'vol_h1': float(vol.get('h1') or 0),
            'liq': liq_usd, 'liquidity_usd': liq_usd,
            'pc_m5': float(pc.get('m5') or 0),
            'pc_h1': float(pc.get('h1') or 0),
            'pc_h6': pc_h6, 'pc_h24': pc_h24,
            'peak_h24_6h_pct': max(pc_h24, pc_h6, 0),
            'price': float(attrs.get('base_token_price_usd') or 0),
            'bs_m5': bs_m5, 'bs_h1': bs_h1, 'bs_h6': bs_h6,
            'avg_trade_size_h1_usd': avg_trade_size_h1,
        })
    return out


# ── DexScreener OHLCV (1m, 5m) — reliable, no rate-limit pain ──────────────

_DEXS_BASE = "https://io.dexscreener.com/u/chart"
_RES_MAP = {1: "1", 5: "5", 15: "15", 60: "60"}

def fetch_dexs_5m(pair_address, dex_slug='pumpswap'):
    """Fetch 5m bars via DexScreener internal API. Returns last 24 candles."""
    url = f"{_DEXS_BASE}/bars/solana/{pair_address}?res=5&cb=24&q=USD"
    try:
        sess = cf_requests.Session(impersonate='chrome')
        resp = sess.get(url, timeout=10, headers={
            "Origin": "https://dexscreener.com",
            "Referer": "https://dexscreener.com/",
        })
        if resp.status_code != 200:
            return []
        # Binary format — try parsing as JSON if returned that way
        try:
            data = resp.json()
            return data.get('bars') or data.get('data') or []
        except Exception:
            return []
    except Exception:
        return []


def fetch_gt_ohlcv_with_retry(pair, agg, limit=24, attempts=3):
    """GT 5m candles with retry. Slower but works."""
    for attempt in range(attempts):
        try:
            r = requests.get(
                f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/ohlcv/minute?aggregate={agg}&limit={limit}',
                timeout=10
            )
            if r.status_code == 200:
                return ((r.json().get('data') or {}).get('attributes') or {}).get('ohlcv_list') or []
        except Exception:
            pass
        time.sleep(3 + attempt * 2)  # backoff
    return []


def compute_mtf_features(c):
    """Multi-timeframe momentum stacking. Fetches 1m and 15m candles
    (5m already fetched separately) and derives:
      - mtf_green_count (0-3): how many of last 1m, 5m, 15m closed green
      - mtf_vol_align (0-3): how many show vol_spike > 1.0
      - mtf_textbook_pullback: 1 if 15m red AND 5m red AND 1m green
    Each fail-opens (returns 0/False) on missing data.
    """
    out = {
        'mtf_green_count': 0, 'mtf_vol_align': 0, 'mtf_textbook_pullback': 0,
        # 1m last-close + vol spike — used by filter_confirmation_candle
        '1m_last_close_pct': None, '1m_volume_spike': None,
    }
    try:
        # 1m: last 6 candles is enough
        ohlcv_1m = fetch_gt_ohlcv_with_retry(c['pair'], agg=1, limit=6)
        time.sleep(1.5)  # GT pacing
        # 15m: last 6 candles is enough
        ohlcv_15m = fetch_gt_ohlcv_with_retry(c['pair'], agg=15, limit=6)
        time.sleep(1.5)
        ohlcv_5m = fetch_gt_ohlcv_with_retry(c['pair'], agg=5, limit=6)

        def _parse_last_and_vol_align(ohlcv):
            """Return (is_green, vol_spike_ratio, last_close_pct) for the most recent candle."""
            if not ohlcv or len(ohlcv) < 2:
                return (False, 0.0, None)
            last = ohlcv[0]  # GT returns newest-first
            opn, high, low, close, vol = float(last[1]), float(last[2]), float(last[3]), float(last[4]), float(last[5])
            is_green = close > opn
            prior_vols = [float(k[5]) for k in ohlcv[1:5]]  # next 4 most-recent
            avg = sum(prior_vols) / len(prior_vols) if prior_vols else 0.0
            spike = (vol / avg) if avg > 0 else 0.0
            last_close_pct = ((close / opn) - 1) * 100 if opn > 0 else 0.0
            return (is_green, spike, last_close_pct)

        g1, v1, lcp1 = _parse_last_and_vol_align(ohlcv_1m)
        g5, v5, _ = _parse_last_and_vol_align(ohlcv_5m)
        g15, v15, _ = _parse_last_and_vol_align(ohlcv_15m)
        out['mtf_green_count'] = int(g1) + int(g5) + int(g15)
        out['mtf_vol_align'] = int(v1 > 1.0) + int(v5 > 1.0) + int(v15 > 1.0)
        # Textbook pullback resolving: 15m red AND 5m red AND 1m green
        out['mtf_textbook_pullback'] = 1 if (not g15 and not g5 and g1) else 0
        # 1m features for filter_confirmation_candle
        out['1m_last_close_pct'] = lcp1
        out['1m_volume_spike'] = v1
        # chart_mtf_score proxy — linear map from greens-count (0..3) to
        # production's mtf score range (-3..+3): 0→-3, 1→-1, 2→+1, 3→+3.
        # Used by FILT_falling_knife_BLOCK phantom mirror. Not exact (prod
        # chart_reader factors S/R levels + trend slopes) but matches the
        # central "mtf bearish vs bullish" axis the filter cares about.
        out['chart_mtf_score'] = 2 * out['mtf_green_count'] - 3
    except Exception:
        pass
    return out


def compute_d1_features(c):
    """Phantom parity for the production D1 features (chart_trend +
    chart_micro_patterns). Fetches 30+ 1m bars and computes the same
    slope/HH-LH/MA-distance/named-pattern features production emits.

    Returns dict that's merged into the candidate. Fail-open on any error.
    """
    out = {}
    try:
        # Fetch 30 1m bars (enough for all D1 features)
        ohlcv_1m = fetch_gt_ohlcv_with_retry(c['pair'], agg=1, limit=60)
        time.sleep(1.0)  # GT pacing
        if not ohlcv_1m or len(ohlcv_1m) < 5:
            return out
        # GT returns newest-first; flip to oldest-first
        rows = list(reversed(ohlcv_1m))
        # Convert to simple candle objects with .open/.high/.low/.close
        class _C:
            __slots__ = ('open', 'high', 'low', 'close', 'volume')
            def __init__(self, o, h, l, cl, v):
                self.open = o; self.high = h; self.low = l
                self.close = cl; self.volume = v
        candles = []
        for row in rows:
            try:
                candles.append(_C(
                    float(row[1]), float(row[2]), float(row[3]),
                    float(row[4]), float(row[5]),
                ))
            except Exception:
                continue
        if len(candles) < 5:
            return out
        # Trend features
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from feeds.chart_trend_features import compute_chart_trend
            out.update(compute_chart_trend(candles))
        except Exception:
            pass
        # Micro pattern features
        try:
            from feeds.chart_micro_patterns import compute_micro_patterns
            out.update(compute_micro_patterns(candles))
        except Exception:
            pass
        # trigger_extreme_sweep_1m phantom — ENFORCED 2026-05-13 PM.
        # Max lower_wick / body ratio across last 5 1m bars (oldest-first
        # order after reversal above, so [-5:] is the most recent 5).
        try:
            _last5 = candles[-5:] if len(candles) >= 5 else []
            _max_ratio = 0.0
            for _b in _last5:
                _body = abs(_b.close - _b.open)
                if _body <= 0:
                    continue
                _lw = min(_b.open, _b.close) - _b.low
                if _lw <= 0:
                    continue
                _r = _lw / _body
                if _r > _max_ratio:
                    _max_ratio = _r
            out['1m_max_wick_body_ratio_last5'] = round(_max_ratio, 2)
        except Exception:
            pass
    except Exception:
        pass
    return out


def compute_rsi_overbought_features(c):
    """Phantom parity for production filter_rsi_overbought gate (2026-05-11).

    Fetches 5m + 15m bars, computes RSI/BB via compute_rsi_bb (same function
    production uses), then evaluates filter_rsi_overbought_verdict.

    2026-05-12: also computes pct_above_vwap_1h from the same 15m bars
    (no extra fetch cost). Closes phantom-parity for patient_bottom_recovery
    trigger. top10_buyer_within_60s_count and hours_since_graduation still
    not in phantom — would require recent_trades + grad-status fetches.

    Returns dict with rsi_5m, rsi_15m, bb_pos_5m, bb_pos_15m,
    filter_rsi_overbought_verdict, AND pct_above_vwap_1h. Fail-open.
    """
    out = {}
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _ROOT = _Path(__file__).resolve().parent.parent
        if str(_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_ROOT))
        from feeds.tier2_features import (
            compute_rsi_bb, compute_anchored_vwap_1h, compute_bottom_signature_v1,
        )

        ohlcv_5m = fetch_gt_ohlcv_with_retry(c['pair'], agg=5, limit=30)
        time.sleep(1.0)
        ohlcv_15m = fetch_gt_ohlcv_with_retry(c['pair'], agg=15, limit=30)
        time.sleep(1.0)
        # 1m for bottom_signature_v1 — SHADOW 2026-05-13. Need 30 bars for
        # time_since_local_low + 6 for decay ratio.
        ohlcv_1m = fetch_gt_ohlcv_with_retry(c['pair'], agg=1, limit=30)
        time.sleep(1.0)
        if not ohlcv_5m and not ohlcv_15m:
            return out

        cur_price_for_vwap = float(c.get('price') or c.get('snapshot_close') or 0)

        class _C:
            __slots__ = ('open', 'high', 'low', 'close', 'volume')
            def __init__(self, o, h, l, cl, v):
                self.open = o; self.high = h; self.low = l
                self.close = cl; self.volume = v

        def to_candles(ohlcv):
            if not ohlcv:
                return []
            rows = list(reversed(ohlcv))  # GT newest-first → oldest-first
            res = []
            for row in rows:
                try:
                    res.append(_C(float(row[1]), float(row[2]), float(row[3]),
                                  float(row[4]), float(row[5])))
                except Exception:
                    continue
            return res

        c5 = to_candles(ohlcv_5m)
        c15 = to_candles(ohlcv_15m)
        c1 = to_candles(ohlcv_1m)
        rsi_features = compute_rsi_bb(c5, c15)
        out.update(rsi_features)

        # vwap_1h from 15m bars (no extra fetch). Required for
        # patient_bottom_recovery phantom mirror.
        if c15 and cur_price_for_vwap > 0:
            vwap_features = compute_anchored_vwap_1h(c15, cur_price_for_vwap)
            out.update(vwap_features)

        # bottom_signature_v1 — SHADOW 2026-05-13. Phantom parity mirror.
        if c1 or c5:
            out.update(compute_bottom_signature_v1(c1, c5))

        # Evaluate the filter verdict (mirrors dip_scanner.py)
        rsi5 = rsi_features.get('rsi_5m')
        block_reasons = []
        if rsi5 is not None and rsi5 >= 50:
            block_reasons.append(
                f'rsi_5m={rsi5:.1f}>=50 (5m momentum reset, not oversold)'
            )
        out['filter_rsi_overbought_verdict'] = 'BLOCK' if block_reasons else 'PASS'
        out['filter_rsi_overbought_block_reasons'] = block_reasons
    except Exception:
        pass
    return out


def compute_1s_features(c):
    """Fetch 30S bars from DexScreener and compute "did a base form before
    entry?" features. SHADOW only — mirrors the bot's 2026-05-11 instrumentation.

    Returns dict with 1s_bars_60s, 1s_range_pct_60s, 1s_red_pct_60s,
    1s_close_pos_60s, 1s_vol_decay_120s. Fail-open on missing data.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _ROOT = _Path(__file__).resolve().parent.parent
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
    try:
        from feeds.dexscreener_chart_format import parse_chart_bars
    except Exception:
        return {}
    out = {}
    try:
        pair = c.get('pair')
        if not pair:
            return out
        # Resolve dex slug
        dex_id = (c.get('dex_id') or '').lower()
        if not dex_id:
            # Resolve via API as fallback
            try:
                d = _safe_get(f'https://api.dexscreener.com/latest/dex/pairs/solana/{pair}', timeout=8)
                if d and d.get('pairs'):
                    dex_id = (d['pairs'][0].get('dexId') or '').lower()
            except Exception:
                pass
        slug = {'pumpswap': 'pumpfundex', 'pumpfun': 'pumpfundex',
                'raydium': 'solamm', 'meteora': 'meteora'}.get(dex_id, dex_id or 'pumpfundex')
        url = (f'https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}'
               f'?res=1S&cb=999&q=So11111111111111111111111111111111111111112')
        r = cf_requests.get(url, impersonate='chrome', timeout=8,
                            headers={'Origin': 'https://dexscreener.com',
                                     'Referer': 'https://dexscreener.com/'})
        if r.status_code != 200:
            return out
        bars = parse_chart_bars(r.content)
        if not bars:
            return out
        now_ms = int(time.time() * 1000)
        pre60 = [b for b in bars if now_ms - 60000 <= b['ts_ms'] < now_ms]
        pre120 = [b for b in bars if now_ms - 120000 <= b['ts_ms'] < now_ms]
        out['1s_bars_60s'] = len(pre60)
        out['1s_bars_120s'] = len(pre120)
        if pre60:
            h = max(b['high'] for b in pre60)
            l = min(b['low'] for b in pre60)
            mid = (h + l) / 2
            out['1s_range_pct_60s'] = (h - l) / mid * 100 if mid > 0 else 0
            out['1s_red_count_60s'] = sum(1 for b in pre60 if b['close'] < b['open'])
            out['1s_red_pct_60s'] = out['1s_red_count_60s'] / len(pre60)
            last_close = pre60[-1]['close']
            out['1s_close_pos_60s'] = (last_close - l) / (h - l) if h > l else 0.5
        if pre120 and len(pre120) >= 4:
            mid_idx = len(pre120) // 2
            early_v = sum(b['volume_usd'] for b in pre120[:mid_idx]) / mid_idx
            late_v = sum(b['volume_usd'] for b in pre120[mid_idx:]) / (len(pre120) - mid_idx)
            if early_v > 0:
                out['1s_vol_decay_120s'] = late_v / early_v

        # #4 sweep-reject detection — SHADOW (mirrors dip_scanner.py).
        # Long lower wick + green close + high volume in last 3 30S bars.
        if pre120 and len(pre120) >= 6:
            swr = False
            swr_idx = None
            for i in range(max(0, len(pre120) - 3), len(pre120)):
                b = pre120[i]
                o_, h_, l_, c_, v_ = b['open'], b['high'], b['low'], b['close'], b['volume_usd']
                body = abs(c_ - o_)
                lower_wick = min(o_, c_) - l_
                if body <= 0 or lower_wick <= 0:
                    continue
                start = max(0, i - 5)
                ctx = pre120[start:i]
                avg_v = sum(b['volume_usd'] for b in ctx) / len(ctx) if ctx else 0
                if (lower_wick > 1.5 * body
                        and c_ > o_
                        and avg_v > 0 and v_ > 1.5 * avg_v):
                    swr = True
                    swr_idx = i
                    break
            out['1s_sweep_reject_detected'] = swr
            out['1s_sweep_reject_bar_idx'] = swr_idx

        # #4b cascade-reversal detection — SHADOW (mirrors dip_scanner.py).
        # 5+ consecutive red 1s bars followed by green reversal closing in
        # top 30% of post-cascade range. Catches Goblin-style multi-bar
        # capitulation bottoms that single-bar sweep_reject misses.
        pre180 = [b for b in bars if now_ms - 180000 <= b['ts_ms'] < now_ms]
        if pre180 and len(pre180) >= 8:
            max_red_run = 0
            max_red_end_idx = -1
            cur_run = 0
            for i, b in enumerate(pre180):
                if b['close'] < b['open']:
                    cur_run += 1
                    if cur_run > max_red_run:
                        max_red_run = cur_run
                        max_red_end_idx = i
                else:
                    cur_run = 0
            cascade_rev = False
            cascade_rev_cp = None
            cascade_rev_pct = None
            if max_red_run >= 5 and max_red_end_idx >= 0:
                after = pre180[max_red_end_idx + 1:]
                green_after = [b for b in after if b['close'] > b['open']]
                if green_after and after:
                    rev = green_after[0]
                    casc_bars = pre180[
                        max_red_end_idx - max_red_run + 1:max_red_end_idx + 1
                    ]
                    casc_low = min(b['low'] for b in casc_bars)
                    range_h = max(b['high'] for b in after)
                    if range_h > casc_low:
                        cascade_rev_cp = (rev['close'] - casc_low) / (range_h - casc_low)
                        if cascade_rev_cp >= 0.7:
                            cascade_rev = True
                            if casc_low > 0:
                                cascade_rev_pct = (rev['close'] / casc_low - 1) * 100
            out['1s_cascade_length'] = max_red_run
            out['1s_cascade_reversal_detected'] = cascade_rev
            out['1s_cascade_reversal_close_pos'] = cascade_rev_cp
            out['1s_cascade_reversal_pct'] = cascade_rev_pct

        # #5 structural stop placement — SHADOW (mirrors dip_scanner.py).
        if pre60 and len(pre60) >= 2:
            recent_low = min(b['low'] for b in pre60)
            last_close = pre60[-1]['close']
            if last_close > 0:
                out['1s_structural_stop_pct'] = (
                    (last_close - recent_low) / last_close * 100 + 0.5
                )

        # #1 derived: 1s_base_confirmed_at_entry — SHADOW (mirrors dip_scanner.py).
        if out.get('1s_bars_60s') is not None:
            out['1s_base_confirmed_at_entry'] = (
                out['1s_bars_60s'] >= 2
                and (out.get('1s_close_pos_60s') or 0) >= 0.5
                and (out.get('1s_red_pct_60s') or 0) <= 0.5
            )
        else:
            out['1s_base_confirmed_at_entry'] = True  # fail-open
    except Exception:
        pass
    return out


def compute_cnn_features(c):
    """Run CNN on the candidate's pre-entry candles. Returns dict
    with cnn_pattern, cnn_pattern_conf, cnn_outcome_prob. Fail-open.

    Phantom parity with dip_scanner Task 12 — same inference singleton.
    """
    out = {'cnn_pattern': None, 'cnn_pattern_conf': None, 'cnn_outcome_prob': None}
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).parent.parent))
        from core.chart_cnn_inference import get_inference
        from feeds.candle_utils import Candle as _Candle
        import time as _t
        c1_raw = fetch_gt_ohlcv_with_retry(c['pair'], agg=1, limit=60)
        _t.sleep(1.0)
        c5_raw = fetch_gt_ohlcv_with_retry(c['pair'], agg=5, limit=60)
        _t.sleep(1.0)
        c15_raw = fetch_gt_ohlcv_with_retry(c['pair'], agg=15, limit=60)
        def _to_candles(raw):
            # GT format: [ts, o, h, l, c, v], newest-first → reverse for oldest-first
            return [
                _Candle(open_time=int(r[0]), open=float(r[1]), high=float(r[2]),
                        low=float(r[3]), close=float(r[4]), volume=float(r[5]),
                        close_time=int(r[0]) + 60)
                for r in reversed(raw or [])
            ]
        c1 = _to_candles(c1_raw)
        c5 = _to_candles(c5_raw)
        c15 = _to_candles(c15_raw)
        inf = get_inference()
        if inf.disabled:
            return out
        r = inf.predict(c.get('token', ''), c1, c5, c15)
        if r:
            out['cnn_pattern'] = r.get('pattern')
            out['cnn_pattern_conf'] = r.get('pattern_conf')
            out['cnn_outcome_prob'] = r.get('outcome_prob')
        # Phantom parity with filter_cluster_19_rug: classify chart into
        # 20-cluster space. Cluster 19 == rug shape (67% historical rug).
        try:
            from core.chart_cluster_inference import get_cluster_inference
            _clu_inf = get_cluster_inference()
            if not _clu_inf.disabled:
                _cl = _clu_inf.classify(c.get('token', ''), c1, c5, c15)
                out['cnn_cluster_id'] = _cl
                out['cnn_cluster_19_rug'] = (_cl == 19)
        except Exception:
            pass
        # Phantom parity with fusion_constrained_score_shadow: 14-feature LR
        # over chart MTF + on-chain holders + CNN cluster + 1m action + regime.
        # Reads from `c` (candidate dict) which already has the on-chain fields
        # populated by upstream phantom enrichment. Stamps a P(win) in [0, 1]
        # or None when the model is unavailable.
        try:
            from models.fusion_constrained import get_fusion_constrained
            _fc_inf = get_fusion_constrained()
            if not _fc_inf.disabled:
                # Build a minimal entry_meta-like dict from candidate fields
                _em_proxy = dict(c)
                _em_proxy['cnn_cluster_id'] = out.get('cnn_cluster_id')
                from datetime import datetime as _dt, timezone as _tz
                out['fusion_constrained_score_shadow'] = _fc_inf.score_from_entry_meta(
                    _em_proxy, time_iso=_dt.now(_tz.utc).isoformat()
                )
        except Exception:
            pass
    except Exception:
        pass
    return out


def compute_1h_features(c):
    """Fetch 48 1h candles for round-2 triggers (pullback_in_uptrend
    + vol_surge_recent). Returns dict merged into candidate. Fail-open."""
    out = {}
    try:
        url = f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{c["pair"]}/ohlcv/hour?aggregate=1&limit=48'
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return out
        ohlcv = ((r.json().get('data') or {}).get('attributes') or {}).get('ohlcv_list') or []
        if len(ohlcv) < 3:
            return out
        # GT newest-first; flip to oldest-first
        rows = list(reversed(ohlcv))
        # pullback_in_uptrend: 1h_last3_n_green
        last3 = rows[-3:]
        n_1h_green = sum(1 for r in last3 if float(r[4]) > float(r[1]))
        out['1h_last3_n_green'] = n_1h_green
        # vol_surge_recent: recent_8h_avg / prior_40h_avg
        if len(rows) >= 12:
            recent_n = min(8, max(4, len(rows) // 6))
            recent = rows[-recent_n:]
            prior = rows[:-recent_n]
            if prior:
                recent_avg = sum(float(r[5]) for r in recent) / len(recent)
                prior_avg = sum(float(r[5]) for r in prior) / len(prior)
                if prior_avg > 0:
                    out['vol_surge_ratio_recent_prior'] = round(recent_avg / prior_avg, 3)
        # filter_1h_v_bottom_fake_recovery phantom — last 2 1h bars
        if len(rows) >= 2:
            c1, c2 = rows[-2], rows[-1]
            c1_o, c1_c = float(c1[1]), float(c1[4])
            c2_o, c2_c = float(c2[1]), float(c2[4])
            out['1h_v_bottom_recovery'] = (
                c1_c < c1_o and c2_c > c2_o and c2_c >= c1_o
            )
    except Exception:
        pass
    return out


def compute_5m_features(c):
    """Fetch 5m candles via GT (with retry) and compute pct_in_5m_range, candle pattern, etc."""
    ohlcv = fetch_gt_ohlcv_with_retry(c['pair'], agg=5, limit=24)
    if not ohlcv:
        return None
    last = ohlcv[0]
    ts, opn, high, low, close, vol = float(last[0]), float(last[1]), float(last[2]), float(last[3]), float(last[4]), float(last[5])
    rng = high - low
    pct_in_5m_range = round((close - low) / rng, 3) if rng > 0 else 0.5
    body = abs(close - opn)
    upper_wick = high - max(close, opn)
    lower_wick = min(close, opn) - low
    is_bull_marub = (close > opn) and rng > 0 and (upper_wick / rng < 0.1) and (body / rng > 0.7)
    is_bear_marub = (close < opn) and rng > 0 and (lower_wick / rng < 0.1) and (body / rng > 0.7)
    is_doji = rng > 0 and (body / rng < 0.1)
    candle = 'bullish_marubozu' if is_bull_marub else ('bearish_marubozu' if is_bear_marub else ('doji' if is_doji else 'other'))
    highs = [float(b[2]) for b in ohlcv[:12]]
    peak_idx = highs.index(max(highs)) if highs else 0
    body_to_range = round(body / rng, 3) if rng > 0 else 0.0

    # trigger_controlled_greens_5m phantom — ENFORCED 2026-05-13 PM.
    # Count of last-8 5m candles that are green AND non-marubozu
    # (body/range < 0.80). GT returns newest-first, so first 8 == last 8.
    _cg_n_norm_green = 0
    for b in ohlcv[:8]:
        try:
            _bo, _bh, _bl, _bc = float(b[1]), float(b[2]), float(b[3]), float(b[4])
            if _bc <= _bo:
                continue
            _bb = abs(_bc - _bo)
            _br = _bh - _bl
            if _br <= 0:
                continue
            if (_bb / _br) < 0.80:
                _cg_n_norm_green += 1
        except Exception:
            continue

    # trigger_pullback_in_uptrend phantom — last 5 5m bars green count.
    # GT newest-first, so first 5 == last 5. Last 5m bar = ohlcv[0].
    _5m_last5_n_green = 0
    _last_5m_green = False
    for _i, b in enumerate(ohlcv[:5]):
        try:
            _bo = float(b[1]); _bc = float(b[4])
            if _bc > _bo:
                _5m_last5_n_green += 1
                if _i == 0:
                    _last_5m_green = True
        except Exception:
            continue

    # trigger_bullish_engulfing_5m phantom — last 2 5m bars
    _bullish_engulfing_5m = False
    try:
        if len(ohlcv) >= 2:
            _be_c2 = ohlcv[0]; _be_c1 = ohlcv[1]  # GT newest-first
            _c1_o, _c1_c = float(_be_c1[1]), float(_be_c1[4])
            _c2_o, _c2_c = float(_be_c2[1]), float(_be_c2[4])
            _c1_body = abs(_c1_c - _c1_o); _c2_body = abs(_c2_c - _c2_o)
            if (_c1_c < _c1_o and _c2_c > _c2_o
                    and _c2_o <= _c1_c
                    and _c2_c >= _c1_o
                    and _c2_body > _c1_body):
                _bullish_engulfing_5m = True
    except Exception:
        pass

    return {
        'pct_in_5m_range': pct_in_5m_range,
        'candle_5m': candle,
        'body_to_range_5m': body_to_range,
        'min_since_peak_5m': peak_idx * 5,
        'ratio_to_recent_peak': round(close / max(highs), 3) if highs and max(highs) > 0 else 1.0,
        'snapshot_close': close,
        '5m_n_normal_greens_last8': _cg_n_norm_green,
        '5m_last5_n_green': _5m_last5_n_green,
        'last_5m_green': _last_5m_green,
        'bullish_engulfing_5m': _bullish_engulfing_5m,
    }


# ── Jupiter slippage ($5k buy/sell impact) ──────────────────────────────────

_SOL_MINT = "So11111111111111111111111111111111111111112"
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_JUP_QUOTE = "https://api.jup.ag/swap/v1/quote"


def fetch_sol_price():
    """1 SOL → USDC via Jupiter to anchor slippage USD sizing."""
    try:
        r = requests.get(_JUP_QUOTE, params={
            'inputMint': _SOL_MINT, 'outputMint': _USDC_MINT,
            'amount': 1_000_000_000,  # 1 SOL = 1e9 lamports
            'slippageBps': 50,
        }, timeout=8)
        if r.status_code == 200:
            j = r.json()
            return float(j.get('outAmount', 0)) / 1e6  # USDC has 6 decimals
    except Exception:
        pass
    return 200.0  # fallback estimate


def _load_slip_history():
    """Load per-token slip-history dict from disk. Each entry is a list of
    (ts_unix, buy_pct, sell_pct) tuples — last 10 retained per token."""
    if not SLIP_HIST_PATH.exists():
        return {}
    try:
        with open(SLIP_HIST_PATH) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_slip_history(hist):
    try:
        with open(SLIP_HIST_PATH, 'w') as f:
            json.dump(hist, f, default=str)
    except Exception:
        pass


def _compute_slip_velocity(samples):
    """Linear-fit slope of slip_sell over time (pct/min). Needs >=3 samples
    with non-null sell. Returns (vel_per_min, n_samples, trajectory)."""
    sells = [(t, s) for (t, _b, s) in samples if s is not None]
    if len(sells) < 3:
        return (None, len(sells), 'insufficient')
    t0 = sells[0][0]
    xs = [t - t0 for (t, _) in sells]
    ys = [s for (_, s) in sells]
    n = len(sells)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n))
    slope = (num / den) if den > 0 else 0.0
    vel_per_min = slope * 60.0
    if vel_per_min > 0.05:
        traj = 'rising'
    elif vel_per_min < -0.05:
        traj = 'falling'
    else:
        traj = 'flat'
    return (round(vel_per_min, 4), n, traj)


def fetch_slip_5k(token_address, sol_price):
    """Round-trip $5k impact for buy and sell. Returns (buy_pct, sell_pct) or (None, None)."""
    try:
        sol_amt = 5000.0 / max(sol_price, 1.0)
        lamports = max(int(sol_amt * 1e9), 1_000_000)
        r1 = requests.get(_JUP_QUOTE, params={
            'inputMint': _SOL_MINT, 'outputMint': token_address,
            'amount': lamports, 'slippageBps': 300,
        }, timeout=8)
        if r1.status_code != 200:
            return (None, None)
        bq = r1.json()
        if not bq.get('outAmount'):
            return (None, None)
        bi = float(bq.get('priceImpactPct') or 0) * 100
        r2 = requests.get(_JUP_QUOTE, params={
            'inputMint': token_address, 'outputMint': _SOL_MINT,
            'amount': int(bq['outAmount']), 'slippageBps': 300,
        }, timeout=8)
        if r2.status_code != 200:
            return (bi, None)
        sq = r2.json()
        si = float(sq.get('priceImpactPct') or 0) * 100
        return (round(bi, 4), round(si, 4))
    except Exception:
        return (None, None)


# ── Snapshot + Resolve ────────────────────────────────────────────────────

def take_snapshot():
    print(f'[{datetime.now().isoformat()}] Taking snapshot...')

    # Source 1: GT trending (existing)
    pools = fetch_trending_tokens()
    gt_candidates = normalize(pools)
    print(f'  Source GT trending: {len(gt_candidates)} candidates in range')

    # Sources 2-5: DS boosts/profiles/search + Axiom — collect addresses,
    # then enrich via DS /tokens batch (mirrors bot's _fetch_candidates)
    ds_addrs = []
    ds_addrs += fetch_dexscreener_boosts_addrs()
    ds_addrs += fetch_dexscreener_profiles_addrs()
    ds_addrs += fetch_dexscreener_search_addrs()
    ds_addrs += fetch_axiom_trending_addrs()
    # Drop tokens already covered by GT trending (dedupe by token addr)
    gt_tokens = {c['token'] for c in gt_candidates}
    ds_addrs = [a for a in ds_addrs if a and a not in gt_tokens]
    ds_addrs = list(dict.fromkeys(ds_addrs))  # dedupe preserve order
    print(f'  Source DS+Axiom (after dedup): {len(ds_addrs)} unique extra addrs')
    ds_pairs = fetch_ds_pairs_for_addrs(ds_addrs) if ds_addrs else []
    ds_candidates = []
    for p in ds_pairs:
        c = normalize_ds(p)
        if c:
            ds_candidates.append(c)
    print(f'  Source DS+Axiom: {len(ds_candidates)} candidates in range')

    # Combine, dedupe by token, sort by vol_h1
    all_by_token = {c['token']: c for c in gt_candidates}
    for c in ds_candidates:
        if c['token'] not in all_by_token:
            all_by_token[c['token']] = c
    candidates = list(all_by_token.values())
    print(f'  Combined unique: {len(candidates)} candidates in mcap range')
    # Top 30 by vol_h1 (limit to control GT rate during enrichment fetches)
    candidates.sort(key=lambda x: -x['vol_h1'])
    top = candidates[:30]

    # Regime breadth: pct of cohort red on h1. Computed once per snapshot,
    # attached to every candidate.
    if top:
        regime_h1_neg_pct = round(sum(1 for c in top if c['pc_h1'] < 0) / len(top) * 100, 1)
    else:
        regime_h1_neg_pct = 0.0

    # SOL price for Jupiter slippage anchoring
    sol_price = fetch_sol_price()
    print(f'  regime_h1_neg_pct={regime_h1_neg_pct}%  sol_price=${sol_price:.2f}')

    # Slip-history ring buffer (per-token, persisted across runs)
    slip_hist = _load_slip_history()

    enriched = []
    for c in top:
        feats = compute_5m_features(c)
        if feats:
            c.update(feats)
        # Multi-timeframe momentum (adds 2 GT calls per token)
        c.update(compute_mtf_features(c))
        # 1s base-formation features (1 DS call per token) — SHADOW 2026-05-11
        c.update(compute_1s_features(c))
        # D1 chart features (trend slope/HH-LH/MA distance + micro patterns) —
        # phantom parity with production (1 GT call per token, ~1s pacing).
        c.update(compute_d1_features(c))
        # 1h features for pullback_in_uptrend + vol_surge_recent triggers
        # (1 GT call per token, fail-open).
        c.update(compute_1h_features(c))
        # Chart CNN — SHADOW 2026-05-15 (3 GT calls: 1m+5m+15m bars).
        c.update(compute_cnn_features(c))
        # filter_rsi_overbought phantom parity (2 GT calls: 5m + 15m bars).
        c.update(compute_rsi_overbought_features(c))
        # Jupiter $5k buy/sell slippage
        bi, si = fetch_slip_5k(c['token'], sol_price)
        if bi is not None: c['slip_buy_5000_pct'] = bi
        if si is not None: c['slip_sell_5000_pct'] = si
        c['regime_h1_neg_pct'] = regime_h1_neg_pct

        # Append current quote to per-token ring buffer (last 10 entries),
        # then derive velocity/trajectory from history.
        if bi is not None or si is not None:
            buf = slip_hist.setdefault(c['token'], [])
            buf.append([time.time(), bi, si])
            slip_hist[c['token']] = buf[-10:]
            vel, n, traj = _compute_slip_velocity(buf[-10:])
            if vel is not None:
                c['slip_sell_5k_velocity_pct_per_min'] = vel
            c['slip_sell_5k_samples'] = n
            c['slip_sell_5k_trajectory'] = traj

        # Compute combo verdicts
        c['verdicts'] = {name: 'PASS' if fn(c) else 'BLOCK' for name, fn in COMBOS.items()}
        enriched.append(c)
        time.sleep(2)  # GT rate limit pacing

    _save_slip_history(slip_hist)
    snap_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    snap = {
        'id': snap_id,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'candidates': enriched,
        'resolved': False,
    }
    path = SNAPSHOT_DIR / f'{snap_id}.json'
    with open(path, 'w') as f:
        json.dump(snap, f, indent=2, default=str)
    print(f'  Saved {len(enriched)} candidates to {path}')
    return snap


def simulate_phantom_strategy(entry_price, ohlcv_after, position_usd=20.0):
    """Phantom-bot full lifecycle simulation.

    Strategy mirrors live bot: TP1=+5% sell 50%, TP2=+12% sell 100%, 3.5%
    trail post-TP1 on remaining 50%, -10% hard stop, 24h max-hold timeout.
    (TP1 lowered 8 -> 5 on 2026-05-12.)

    ohlcv_after is GT-format newest-first list of [ts_ms, open, high, low,
    close, vol] candles covering [entry_ts, now]. Reverses internally so we
    iterate oldest-first.

    Returns dict with: phantom_pnl_pct, phantom_pnl_usd, exit_reason,
    exit_pct, hit_tp1 (bool), max_drawdown_pct.
    """
    if not ohlcv_after:
        return {
            'phantom_pnl_pct': None, 'phantom_pnl_usd': None,
            'exit_reason': 'no_ohlcv', 'hit_tp1': False,
        }
    candles = list(reversed(ohlcv_after))  # oldest-first
    tp1_price = entry_price * 1.05
    tp2_price = entry_price * 1.12
    stop_price = entry_price * 0.90
    trail_pct = 0.035

    half_sold = False
    half_sold_price = None
    peak_after_tp1 = None
    exit_reason = None
    exit_pct = None  # final P&L %

    # Fractions of original position remaining (1.0 = full, 0.5 = after TP1)
    remaining_frac = 1.0
    # Realized P&L $ from TP1 partial
    realized_pnl = 0.0

    for k in candles:
        if len(k) < 5:
            continue
        try:
            high = float(k[2])
            low = float(k[3])
            close = float(k[4])
        except (ValueError, TypeError):
            continue

        # Stop check FIRST (worst case)
        if not half_sold and low <= stop_price:
            # Stop hit before TP1 — close full position at stop
            exit_pct = -10.0
            exit_reason = 'stop'
            realized_pnl = position_usd * -0.10
            remaining_frac = 0.0
            break

        # TP2 check (only if TP1 already hit)
        if half_sold and high >= tp2_price:
            # Sell remaining 50% at TP2 (+12%)
            realized_pnl += (position_usd * 0.5) * 0.12
            remaining_frac = 0.0
            exit_reason = 'tp2'
            exit_pct = 0.12 * 100  # represents the average of 8% and 12%, computed below
            break

        # TP1 hit (sell 50% at +8%)
        if not half_sold and high >= tp1_price:
            half_sold = True
            half_sold_price = tp1_price
            realized_pnl += (position_usd * 0.5) * 0.08
            remaining_frac = 0.5
            peak_after_tp1 = max(close, tp1_price)

        # Trail after TP1
        if half_sold:
            if close > (peak_after_tp1 or 0):
                peak_after_tp1 = close
            trail_stop = peak_after_tp1 * (1 - trail_pct)
            if low <= trail_stop:
                # Sell remaining 50% at trail-stop price
                trail_pct_gain = (trail_stop / entry_price - 1.0) * 100
                realized_pnl += (position_usd * 0.5) * (trail_stop / entry_price - 1.0)
                remaining_frac = 0.0
                exit_reason = 'trail'
                exit_pct = trail_pct_gain
                break

    # No exit triggered — mark to last close
    if exit_reason is None:
        last_close = float(candles[-1][4])
        if half_sold:
            # Mark remaining 50% to last close
            mark_gain = (last_close / entry_price - 1.0)
            realized_pnl += (position_usd * 0.5) * mark_gain
            exit_reason = 'open_at_resolve_post_tp1'
        else:
            mark_gain = (last_close / entry_price - 1.0)
            realized_pnl = position_usd * mark_gain
            exit_reason = 'open_at_resolve'
        exit_pct = (last_close / entry_price - 1.0) * 100

    pnl_pct = (realized_pnl / position_usd) * 100
    return {
        'phantom_pnl_pct': round(pnl_pct, 3),
        'phantom_pnl_usd': round(realized_pnl, 3),
        'exit_reason': exit_reason,
        'exit_pct': round(exit_pct, 3) if exit_pct is not None else None,
        'hit_tp1': half_sold,
    }


def simulate_phantom_tp1_100pct(entry_price, ohlcv_after, position_usd=20.0):
    """Alternative strategy: TP1=+5% sells 100%. -10% stop. No TP2/trail.

    Compute alongside the live ladder for direct comparison. Tracks TP1=5
    matching new live threshold (was 1.08 before 2026-05-12).
    """
    if not ohlcv_after:
        return {'phantom_pnl_pct': None, 'phantom_pnl_usd': None,
                'exit_reason': 'no_ohlcv', 'hit_tp1': False}
    candles = list(reversed(ohlcv_after))
    tp1_price = entry_price * 1.05
    stop_price = entry_price * 0.90
    for k in candles:
        if len(k) < 5:
            continue
        try:
            high = float(k[2]); low = float(k[3]); close = float(k[4])
        except (ValueError, TypeError):
            continue
        if low <= stop_price:
            return {'phantom_pnl_pct': -10.0, 'phantom_pnl_usd': position_usd * -0.10,
                    'exit_reason': 'stop', 'exit_pct': -10.0, 'hit_tp1': False}
        if high >= tp1_price:
            return {'phantom_pnl_pct': 5.0, 'phantom_pnl_usd': position_usd * 0.05,
                    'exit_reason': 'tp1_full', 'exit_pct': 5.0, 'hit_tp1': True}
    last_close = float(candles[-1][4])
    pct = (last_close / entry_price - 1.0) * 100
    return {'phantom_pnl_pct': round(pct, 3),
            'phantom_pnl_usd': round(position_usd * (last_close / entry_price - 1.0), 3),
            'exit_reason': 'open_at_resolve', 'exit_pct': round(pct, 3),
            'hit_tp1': False}


def simulate_phantom_smart_bearflip(entry_price, ohlcv_after, position_usd=20.0,
                                    consec_green_req=3, min_pnl_pct=3.0,
                                    min_red_body_pct=0.3):
    """LIVE strategy mirror: TP1=+5% sells 50%, then bear-flip exit on remainder.

    After TP1, watch for: 3 prior consecutive green 1m candles + current
    red candle with body > 0.3% AND position pnl > +3%. Exit remainder
    immediately on bear flip. Matches the enforced smart_bearflip block
    in core/position_manager.py and the new TP1=5 threshold (2026-05-12).

    NOTE: aggregating 5m candles loses fine-grained bear-flip detection.
    For accurate phantom we'd need 1m candles. For now this approximates
    using 5m green/red transitions — under-detects but directionally
    correct.
    """
    if not ohlcv_after:
        return {'phantom_pnl_pct': None, 'phantom_pnl_usd': None,
                'exit_reason': 'no_ohlcv', 'hit_tp1': False}
    candles = list(reversed(ohlcv_after))
    tp1_price = entry_price * 1.05
    stop_price = entry_price * 0.90
    half_sold = False
    realized_pnl = 0.0
    consec_green = 0

    for k in candles:
        if len(k) < 5:
            continue
        try:
            o = float(k[1]); high = float(k[2]); low = float(k[3]); close = float(k[4])
        except (ValueError, TypeError):
            continue
        if not half_sold and low <= stop_price:
            return {'phantom_pnl_pct': -10.0, 'phantom_pnl_usd': position_usd * -0.10,
                    'exit_reason': 'stop', 'exit_pct': -10.0, 'hit_tp1': False}
        if not half_sold and high >= tp1_price:
            half_sold = True
            realized_pnl += position_usd * 0.5 * 0.05
        if half_sold:
            cur_green = close > o
            if cur_green:
                consec_green += 1
            else:
                cur_pnl_pct = (close / entry_price - 1.0) * 100
                body_pct = abs(close - o) / o * 100 if o > 0 else 0
                if (consec_green >= consec_green_req
                        and cur_pnl_pct > min_pnl_pct
                        and body_pct > min_red_body_pct):
                    realized_pnl += position_usd * 0.5 * (close / entry_price - 1.0)
                    pct = (realized_pnl / position_usd) * 100
                    return {'phantom_pnl_pct': round(pct, 3),
                            'phantom_pnl_usd': round(realized_pnl, 3),
                            'exit_reason': 'smart_bearflip',
                            'exit_pct': round(cur_pnl_pct, 3), 'hit_tp1': True}
                consec_green = 0

    last_close = float(candles[-1][4])
    if half_sold:
        realized_pnl += position_usd * 0.5 * (last_close / entry_price - 1.0)
        reason = 'open_at_resolve_post_tp1'
    else:
        realized_pnl = position_usd * (last_close / entry_price - 1.0)
        reason = 'open_at_resolve'
    pct = (realized_pnl / position_usd) * 100
    return {'phantom_pnl_pct': round(pct, 3),
            'phantom_pnl_usd': round(realized_pnl, 3),
            'exit_reason': reason,
            'exit_pct': round((last_close / entry_price - 1.0) * 100, 3),
            'hit_tp1': half_sold}


def resolve_pending():
    """Resolve any snapshot >= 2.5h old. For each candidate:
       1. Fetch current price (legacy +/-8% snapshot outcome — preserved)
       2. Fetch 5m OHLCV for the elapsed window and run phantom-bot
          full-lifecycle simulation (TP1/TP2/trail/stop)."""
    now = datetime.now(timezone.utc)
    pending = []
    for f in SNAPSHOT_DIR.glob('*.json'):
        if f.name.startswith('_'): continue
        with open(f) as fh:
            snap = json.load(fh)
        if snap.get('resolved'): continue
        ts = datetime.fromisoformat(snap['timestamp_utc'])
        age_h = (now - ts).total_seconds() / 3600
        if age_h >= 2.5:
            pending.append((f, snap, age_h))
    if not pending:
        print('  No snapshots ready to resolve.')
        return []
    print(f'  Resolving {len(pending)} pending snapshots...')
    resolved_outcomes = []
    for path, snap, age_h in pending:
        snap_ts = datetime.fromisoformat(snap['timestamp_utc']).timestamp()
        for c in snap['candidates']:
            entry_price = c.get('snapshot_close') or c.get('price')
            if not entry_price:
                c['outcome'] = 'no_entry_price'
                continue
            try:
                # Legacy: current price via DexScreener
                r = requests.get(f'https://api.dexscreener.com/latest/dex/tokens/{c["token"]}', timeout=10)
                pairs = r.json().get('pairs') or []
                cur_pair = next((p for p in pairs if p.get('pairAddress') == c['pair']), pairs[0] if pairs else None)
                if not cur_pair:
                    c['outcome'] = 'no_current_price'
                    continue
                cur_price = float(cur_pair.get('priceUsd') or 0)
                pct_change = (cur_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
                if pct_change >= 8:
                    outcome = 'win'
                elif pct_change <= -8:
                    outcome = 'loss'
                else:
                    outcome = 'flat'
                c['cur_price'] = cur_price
                c['pct_change_since_snap'] = round(pct_change, 2)
                c['outcome'] = outcome
                c['resolved_age_h'] = round(age_h, 2)

                # Phantom-bot: fetch 5m candles since entry, simulate lifecycle
                # 2.5h elapsed = ~30 5m candles; fetch 36 to have margin
                ohlcv_5m = fetch_gt_ohlcv_with_retry(c['pair'], agg=5, limit=36)
                # Filter to candles AFTER snapshot_ts only. GT returns timestamps
                # in SECONDS (not ms), newest-first.
                if ohlcv_5m:
                    ohlcv_after = [k for k in ohlcv_5m if float(k[0]) >= snap_ts]
                else:
                    ohlcv_after = []
                phantom = simulate_phantom_strategy(entry_price, ohlcv_after)
                c['phantom_pnl_pct'] = phantom.get('phantom_pnl_pct')
                c['phantom_pnl_usd'] = phantom.get('phantom_pnl_usd')
                c['phantom_exit_reason'] = phantom.get('exit_reason')
                c['phantom_hit_tp1'] = phantom.get('hit_tp1')
                # NEW 2026-05-07: also simulate alternative exit strategies.
                # Every shadow exit gets a phantom column for forward eval.
                phantom_v2 = simulate_phantom_tp1_100pct(entry_price, ohlcv_after)
                c['phantom_pnl_pct_tp1_100pct'] = phantom_v2.get('phantom_pnl_pct')
                c['phantom_exit_reason_tp1_100pct'] = phantom_v2.get('exit_reason')
                phantom_sbf = simulate_phantom_smart_bearflip(entry_price, ohlcv_after)
                c['phantom_pnl_pct_smart_bearflip'] = phantom_sbf.get('phantom_pnl_pct')
                c['phantom_exit_reason_smart_bearflip'] = phantom_sbf.get('exit_reason')
                resolved_outcomes.append((c, snap['id']))
            except Exception as e:
                c['outcome'] = f'err: {e}'
            time.sleep(0.5)  # GT pacing for 5m fetch
        snap['resolved'] = True
        snap['resolved_at'] = datetime.now(timezone.utc).isoformat()
        with open(path, 'w') as fh:
            json.dump(snap, fh, indent=2, default=str)
    return resolved_outcomes


def _empty_stats():
    return {
        # Volume of PASS verdicts (unchanged)
        'pass': 0,
        # All metrics below are computed ONLY over candidates with phantom
        # data — keeps WR and TP1% on the same denominator.
        'phantom_n': 0,
        'phantom_wins': 0,      # phantom_pnl_pct >= +4 (clean TP-region exit)
        'phantom_losses': 0,    # phantom_pnl_pct <= -4 (stop-region exit)
        'phantom_flats': 0,     # in between
        'phantom_pnl_usd_total': 0.0,
        'phantom_pct_total': 0.0,
        'phantom_tp1_hit_count': 0,
        'phantom_exit_reasons': {},
        # Alt exit-strategy phantoms (added 2026-05-07).
        # tp1_100pct: TP1 sells 100% (matches new live behavior).
        # smart_bearflip: 50% TP1 + bear-flip on remainder (shadow).
        'phantom_tp1_100pct_pct_total': 0.0,
        'phantom_tp1_100pct_n': 0,
        'phantom_smart_bearflip_pct_total': 0.0,
        'phantom_smart_bearflip_n': 0,
    }


def aggregate_stats():
    """Recompute aggregate stats across all resolved snapshots.

    All headline metrics (WR, avg%, TP1%) are computed on the SAME subset:
    candidates with phantom-bot data. Win/loss/flat are derived from
    phantom_pnl_pct (the simulated full-strategy P&L), not the legacy
    +/-8% snapshot outcome — so a trade that hits TP1 then trails back
    to flat counts as flat, consistent with how the bot would actually
    book it.

    Win threshold: phantom_pnl_pct >= +4 (covers TP1-only +4% partial,
    full TP-trail +8-12%, and any in-between trail outcomes).
    Loss threshold: phantom_pnl_pct <= -4 (stop-region).
    """
    combo_stats = {name: _empty_stats() for name in COMBOS}
    n_total_resolved = 0
    for f in sorted(SNAPSHOT_DIR.glob('*.json')):
        if f.name.startswith('_'): continue
        with open(f) as fh:
            snap = json.load(fh)
        if not snap.get('resolved'): continue
        for c in snap['candidates']:
            outcome = c.get('outcome')
            if outcome not in ('win', 'loss', 'flat'): continue
            n_total_resolved += 1
            phantom_pct = c.get('phantom_pnl_pct')
            phantom_usd = c.get('phantom_pnl_usd')
            phantom_exit = c.get('phantom_exit_reason') or 'unknown'
            phantom_tp1 = c.get('phantom_hit_tp1', False)
            phantom_available = (phantom_pct is not None and phantom_usd is not None)
            for combo_name, verdict in (c.get('verdicts') or {}).items():
                if verdict != 'PASS': continue
                stats = combo_stats.setdefault(combo_name, _empty_stats())
                stats['pass'] += 1
                if not phantom_available:
                    continue
                stats['phantom_n'] += 1
                stats['phantom_pnl_usd_total'] += float(phantom_usd)
                stats['phantom_pct_total'] += float(phantom_pct)
                if phantom_pct >= 4:
                    stats['phantom_wins'] += 1
                elif phantom_pct <= -4:
                    stats['phantom_losses'] += 1
                else:
                    stats['phantom_flats'] += 1
                if phantom_tp1:
                    stats['phantom_tp1_hit_count'] += 1
                stats['phantom_exit_reasons'][phantom_exit] = (
                    stats['phantom_exit_reasons'].get(phantom_exit, 0) + 1
                )
                # Alt exit-strategies (2026-05-07): also accumulate.
                p_v2 = c.get('phantom_pnl_pct_tp1_100pct')
                if p_v2 is not None:
                    stats['phantom_tp1_100pct_pct_total'] += float(p_v2)
                    stats['phantom_tp1_100pct_n'] += 1
                p_sbf = c.get('phantom_pnl_pct_smart_bearflip')
                if p_sbf is not None:
                    stats['phantom_smart_bearflip_pct_total'] += float(p_sbf)
                    stats['phantom_smart_bearflip_n'] += 1
    out = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'n_total_resolved': n_total_resolved,
        'combos': combo_stats,
    }
    with open(AGGREGATE_PATH, 'w') as f:
        json.dump(out, f, indent=2)
    return out


def print_status():
    if not AGGREGATE_PATH.exists():
        print('No aggregate yet.'); return
    with open(AGGREGATE_PATH) as f:
        agg = json.load(f)
    print(f'Total resolved candidates: {agg["n_total_resolved"]}')
    print(f'Updated: {agg["updated_at"]}')
    print()
    print('All metrics below over phantom-data subset (n_phantom).')
    print('Win = phantom_pnl_pct >= +4. Loss = phantom_pnl_pct <= -4.')
    print()
    print(f'{"combo":<25}{"pass":>5}{"n_ph":>5}{"wins":>5}{"loss":>5}{"WR":>7}{"avg%":>7}{"total_$":>9}{"$/trade":>9}{"tp1%":>6}'
          f'{"alt_100pct%":>12}{"alt_sbf%":>10}')
    print('-' * 120)
    for name, s in sorted(agg['combos'].items()):
        p = s.get('pass', 0)
        ph_n = s.get('phantom_n', 0)
        wins = s.get('phantom_wins', 0)
        losses = s.get('phantom_losses', 0)
        decided = wins + losses
        wr = (wins / decided * 100) if decided else 0
        ph_total = s.get('phantom_pnl_usd_total', 0)
        ph_per = (ph_total / ph_n) if ph_n else 0
        avg_pct = (s.get('phantom_pct_total', 0) / ph_n) if ph_n else 0
        tp1_n = s.get('phantom_tp1_hit_count', 0)
        tp1_pct = (tp1_n / ph_n * 100) if ph_n else 0
        # Alt exit strategies
        alt_100_n = s.get('phantom_tp1_100pct_n', 0)
        alt_100_avg = (s.get('phantom_tp1_100pct_pct_total', 0) / alt_100_n) if alt_100_n else 0
        alt_sbf_n = s.get('phantom_smart_bearflip_n', 0)
        alt_sbf_avg = (s.get('phantom_smart_bearflip_pct_total', 0) / alt_sbf_n) if alt_sbf_n else 0
        print(f'{name:<25}{p:>5}{ph_n:>5}{wins:>5}{losses:>5}{wr:>6.0f}%'
              f'{avg_pct:+6.1f}%{ph_total:>+8.2f}{ph_per:>+8.2f}{tp1_pct:>5.0f}%'
              f'{alt_100_avg:>+11.1f}%{alt_sbf_avg:>+9.1f}%')


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == 'status':
            print_status(); return
        if cmd == 'purge':
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            removed = 0
            for f in SNAPSHOT_DIR.glob('*.json'):
                if f.name.startswith('_'): continue
                with open(f) as fh:
                    snap = json.load(fh)
                ts = datetime.fromisoformat(snap['timestamp_utc'])
                if ts < cutoff:
                    f.unlink(); removed += 1
            print(f'Removed {removed} snapshots older than 7 days.'); return
    # Default: resolve old + take new + update aggregate
    resolve_pending()
    take_snapshot()
    aggregate_stats()
    print_status()


if __name__ == '__main__':
    main()
