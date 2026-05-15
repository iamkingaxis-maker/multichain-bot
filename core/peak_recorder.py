"""Live per-position peak signal recorder + shadow scorer.

Phase 3 component for the intelligent TP system. Runs alongside the existing
position_manager — entirely ADDITIVE, never modifies trading state, never
affects exit decisions. Logs to disk + Railway logs in shadow mode.

Architecture:
  - PeakRecorder owns a dict of per-token-address recording state
  - record_minute() called periodically by position_manager during hold
  - Computes the 8 candidate peak signals from cached 1m/5m/15m candles
  - Computes composite peak score using configurable weights
  - Appends per-minute snapshot to trade trace
  - Logs shadow "would exit at score>=X" without acting
  - finalize() called on exit; dumps full trace to disk

Output: .live_traces/{date}_{symbol}_{open_time}.json
Each trace contains:
  - tok, addr, pair, entry_price, entry_time
  - minutes: list of per-minute snapshots:
    {time, minute_idx, pnl_pct, signals: {S1..S8}, composite_score, shadow_exit}
  - close: {time, reason, pnl, peak_pnl, peak_minute}

Forward data after 24-48h gives us a clean cohort to:
  1. Validate which signals fire AT/before peaks (Phase 1.5)
  2. Tune composite weights (Phase 2)
  3. Decide enforcement threshold (Phase 4)

Safety:
  - All operations wrapped in try/except; recorder failure NEVER raises into
    the trading loop
  - No mutation of PositionState
  - Shadow logs use distinct prefix [PEAK_RECORDER] for log filtering
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


# Default composite weights — placeholder; tune after forward data accumulates
DEFAULT_WEIGHTS = {
    'S1_vol_exhaustion': 1.5,
    'S2_wick_rejection': 1.0,
    'S3_failed_hh': 1.0,
    'S4_mtf_bearish_flip': 2.0,
    'S5_vol_divergence': 1.5,
    'S6_stall': 1.0,
    'S7_wick_cluster': 1.0,
    'S8_tf_discord': 1.0,
}

# Default shadow exit threshold — if composite score >= threshold AND
# pnl_pct >= MIN_PNL_PCT, log as shadow exit candidate
SHADOW_THRESHOLD = 3.0
MIN_PNL_PCT = 3.0


def _vol_spike(prior_vols, current_vol, ratio_threshold=1.0):
    if not prior_vols:
        return False
    avg = sum(prior_vols) / len(prior_vols)
    return avg > 0 and current_vol / avg > ratio_threshold


def _candle_is_green(c):
    return c.close > c.open


def _candle_score(c):
    if c is None or c.open == 0:
        return 0
    body_pct = (c.close - c.open) / abs(c.open) * 100
    if body_pct > 1:
        return 1
    elif body_pct < -1:
        return -1
    return 0


def compute_signals(candles_1m, candles_5m, candles_15m, entry_price):
    """Compute the 8 candidate peak signals at the latest 1m candle.

    Args:
      candles_1m: list of Candle, chronological. Last is current minute.
      candles_5m: list of Candle, can be empty if not refreshed.
      candles_15m: list of Candle, can be empty.
      entry_price: position entry price (USD-per-token, must match candle units).

    Returns dict with bool signals + meta keys (_pnl_pct, _high_pct, _mtf_score).
    """
    if not candles_1m:
        return {}
    cur = candles_1m[-1]
    n = len(candles_1m)
    pnl_pct = (cur.close - entry_price) / entry_price * 100
    high_pct = (cur.high - entry_price) / entry_price * 100

    sigs = {
        'S1_vol_exhaustion': False,
        'S2_wick_rejection': False,
        'S3_failed_hh': False,
        'S4_mtf_bearish_flip': False,
        'S5_vol_divergence': False,
        'S6_stall': False,
        'S7_wick_cluster': False,
        'S8_tf_discord': False,
        '_pnl_pct': pnl_pct,
        '_high_pct': high_pct,
        '_mtf_score': 0,
    }

    if pnl_pct < MIN_PNL_PCT:
        return sigs

    # S1: vol exhaustion — pnl >= +3, vol < 0.5x trailing 4-bar avg, green
    if n >= 5:
        prior_vols = [candles_1m[-i - 1].volume for i in range(1, 5)]
        avg_prior = sum(prior_vols) / 4 if prior_vols else 0
        sigs['S1_vol_exhaustion'] = (
            avg_prior > 0
            and cur.volume / avg_prior < 0.5
            and _candle_is_green(cur)
        )

    # S2: upper-wick rejection — green candle with upper_wick > 2*body
    body = cur.close - cur.open
    upper_wick = cur.high - cur.close
    sigs['S2_wick_rejection'] = body > 0 and upper_wick > 2 * body

    # S3: failed higher-high — recent 3+ HHs broken
    if n >= 5:
        recent = candles_1m[-5:]
        hh_streak = 0
        for j in range(1, len(recent) - 1):
            if recent[j].high > recent[j - 1].high:
                hh_streak += 1
            else:
                hh_streak = 0
        curr_lower_high = cur.high < recent[-2].high
        sigs['S3_failed_hh'] = hh_streak >= 2 and curr_lower_high

    # S4: MTF bearish flip — composite of 1m, 5m, 15m candle scores
    last5 = candles_5m[-1] if candles_5m else None
    last15 = candles_15m[-1] if candles_15m else None
    mtf_score = _candle_score(cur) + _candle_score(last5) + _candle_score(last15)
    sigs['_mtf_score'] = mtf_score
    sigs['S4_mtf_bearish_flip'] = mtf_score <= -1

    # S5: vol divergence — new high but vol < median of prior 5
    if n >= 6:
        prior_5 = candles_1m[-6:-1]
        max_prior_high = max(c.high for c in prior_5)
        prior_vols = sorted([c.volume for c in prior_5])
        median_vol = prior_vols[len(prior_vols) // 2] if prior_vols else 0
        is_new_high = cur.high > max_prior_high
        sigs['S5_vol_divergence'] = (
            is_new_high and median_vol > 0 and cur.volume < median_vol
        )

    # S6: stall — 4+ consec flat/red 1m
    if n >= 4:
        recent_4 = candles_1m[-4:]
        sigs['S6_stall'] = all(c.close <= c.open for c in recent_4)

    # S7: wick cluster — 2+ of last 3 with upper_wick:body > 1.5
    if n >= 3:
        recent_3 = candles_1m[-3:]
        wick_count = 0
        for c in recent_3:
            body_abs = abs(c.close - c.open)
            wick = c.high - max(c.open, c.close)
            if body_abs > 0 and wick / body_abs > 1.5:
                wick_count += 1
        sigs['S7_wick_cluster'] = wick_count >= 2

    # S8: TF discord — 1m green, 5m red
    if last5 is not None:
        sigs['S8_tf_discord'] = _candle_is_green(cur) and last5.close < last5.open

    return sigs


def composite_score(signals: Dict[str, Any], weights: Dict[str, float]) -> float:
    score = 0.0
    for sig_name, w in weights.items():
        if signals.get(sig_name):
            score += w
    return score


class PeakRecorder:
    """Records per-position peak-detection traces in shadow mode."""

    # Save active in-flight state to disk every N seconds so a restart
    # mid-position doesn't lose all the minute snapshots accumulated so far.
    _STATE_SAVE_INTERVAL_SECS = 30.0

    def __init__(self, output_dir: str = None, weights: Dict[str, float] = None,
                 shadow_threshold: float = SHADOW_THRESHOLD):
        # On Railway, DATA_DIR points to persistent volume /data. Locally,
        # falls back to .live_traces in repo root.
        if output_dir is None:
            data_dir = os.environ.get('DATA_DIR', '.')
            output_dir = os.path.join(data_dir, 'live_traces')
        self.output_dir = Path(output_dir)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f'[PEAK_RECORDER] cannot create output dir: {e}')
        self.weights = weights or DEFAULT_WEIGHTS
        self.shadow_threshold = shadow_threshold
        self.state: Dict[str, dict] = {}  # addr -> recording state
        self._state_path = self.output_dir / '_active_state.json'
        self._last_state_save_ts: float = 0.0
        self._load_state_from_disk()

    def _load_state_from_disk(self):
        """Reload in-flight state for any positions that were open pre-restart."""
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.state = data
                logger.info(
                    f'[PEAK_RECORDER] Reloaded in-flight state for '
                    f'{len(self.state)} position(s) from {self._state_path.name}'
                )
        except Exception as e:
            logger.warning(f'[PEAK_RECORDER] state load err: {e}')

    def _save_state_to_disk(self):
        """Atomic write of current in-flight state (debounced)."""
        try:
            tmp = str(self._state_path) + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.state, f)
            os.replace(tmp, str(self._state_path))
        except Exception as e:
            logger.warning(f'[PEAK_RECORDER] state save err: {e}')

    def init_position(self, token_address: str, token_symbol: str,
                       pair_address: str, entry_price: float, entry_time,
                       entry_meta: dict | None = None):
        """Called when a new position opens."""
        try:
            entry_time_iso = (entry_time.isoformat()
                              if hasattr(entry_time, 'isoformat') else str(entry_time))
            self.state[token_address] = {
                'tok': token_symbol,
                'addr': token_address,
                'pair': pair_address,
                'entry_price': entry_price,
                'entry_time': entry_time_iso,
                'minutes': [],
                'last_record_minute_ts': 0,
                'shadow_exit_logged': False,
            }
            # Stamp CNN prediction at init — correlates entry-time pattern
            # with eventual outcome for forward validation. SHADOW only.
            try:
                if isinstance(entry_meta, dict):
                    _cnn_init = entry_meta.get('cnn_pattern')
                    if _cnn_init is not None:
                        self.state[token_address]['cnn_pattern_at_entry'] = _cnn_init
                        self.state[token_address]['cnn_pattern_conf_at_entry'] = entry_meta.get('cnn_pattern_conf')
                        self.state[token_address]['cnn_outcome_prob_at_entry'] = entry_meta.get('cnn_outcome_prob')
            except Exception as _e:
                logger.debug(f'[PEAK_RECORDER] cnn stamp err: {_e}')
            logger.info(f'[PEAK_RECORDER] init {token_symbol} entry=${entry_price:.8f}')
        except Exception as e:
            logger.warning(f'[PEAK_RECORDER] init err: {e}')

    def record_minute(self, token_address: str, candles_1m: list,
                       candles_5m: list = None, candles_15m: list = None):
        """Called periodically with the latest candles. Computes signals,
        composite score, appends snapshot. Returns score (informational)."""
        s = self.state.get(token_address)
        if not s or not candles_1m:
            return 0.0
        try:
            cur = candles_1m[-1]
            # Don't double-record same minute
            if cur.open_time <= s['last_record_minute_ts']:
                return 0.0
            s['last_record_minute_ts'] = cur.open_time
            entry_price = s['entry_price']

            sigs = compute_signals(candles_1m, candles_5m or [],
                                    candles_15m or [], entry_price)
            score = composite_score(sigs, self.weights)
            snapshot = {
                'minute_open_time': cur.open_time,
                'close': cur.close,
                'high': cur.high,
                'low': cur.low,
                'volume': cur.volume,
                'pnl_pct': sigs.get('_pnl_pct'),
                'high_pct': sigs.get('_high_pct'),
                'mtf_score': sigs.get('_mtf_score'),
                'composite_score': score,
                'signals': {k: bool(v) for k, v in sigs.items()
                            if k.startswith('S') and not k.startswith('_')},
            }
            s['minutes'].append(snapshot)

            # Debounced state persist — survives bot restarts mid-hold
            now_ts = time.time()
            if (now_ts - self._last_state_save_ts) >= self._STATE_SAVE_INTERVAL_SECS:
                self._save_state_to_disk()
                self._last_state_save_ts = now_ts

            # Shadow exit log
            if (score >= self.shadow_threshold
                    and (sigs.get('_pnl_pct') or 0) >= MIN_PNL_PCT
                    and not s['shadow_exit_logged']):
                fired_sigs = [k for k, v in sigs.items()
                              if k.startswith('S') and v]
                logger.info(
                    f'[PEAK_RECORDER] SHADOW_EXIT {s["tok"]} '
                    f'score={score:.1f} pnl={sigs.get("_pnl_pct"):.1f}% '
                    f'sigs={",".join(fired_sigs)}'
                )
                s['shadow_exit_logged'] = True
            return score
        except Exception as e:
            logger.warning(f'[PEAK_RECORDER] record_minute err: {e}')
            return 0.0

    def finalize(self, token_address: str, exit_reason: str = '',
                  exit_pnl: float = 0.0, exit_time=None):
        """Called when position closes. Dumps trace to disk."""
        s = self.state.pop(token_address, None)
        if not s:
            return
        try:
            exit_time_iso = (exit_time.isoformat()
                             if hasattr(exit_time, 'isoformat')
                             else str(exit_time or ''))
            # Find peak minute
            minutes = s['minutes']
            peak_idx = -1
            peak_high_pct = 0
            for i, m in enumerate(minutes):
                hp = m.get('high_pct') or 0
                if hp > peak_high_pct:
                    peak_high_pct = hp
                    peak_idx = i
            trace = {
                'tok': s['tok'],
                'addr': s['addr'],
                'pair': s['pair'],
                'entry_price': s['entry_price'],
                'entry_time': s['entry_time'],
                'exit_reason': exit_reason,
                'exit_pnl': exit_pnl,
                'exit_time': exit_time_iso,
                'peak_minute_idx': peak_idx,
                'peak_high_pct': peak_high_pct,
                'shadow_exit_fired': s['shadow_exit_logged'],
                'minutes': minutes,
                'weights_used': self.weights,
                'shadow_threshold': self.shadow_threshold,
                'cnn_pattern_at_entry': s.get('cnn_pattern_at_entry'),
                'cnn_pattern_conf_at_entry': s.get('cnn_pattern_conf_at_entry'),
                'cnn_outcome_prob_at_entry': s.get('cnn_outcome_prob_at_entry'),
            }
            safe_tok = re.sub(r'[^A-Za-z0-9_-]', '_', s['tok'] or 'UNK')
            entry_iso = s['entry_time'][:19].replace(':', '-')
            fname = f'{entry_iso}_{safe_tok}.json'
            out_path = self.output_dir / fname
            with open(out_path, 'w') as fh:
                json.dump(trace, fh)
            logger.info(
                f'[PEAK_RECORDER] finalize {s["tok"]} '
                f'minutes={len(minutes)} peak={peak_high_pct:.1f}% '
                f'reason={exit_reason} pnl=${exit_pnl:+.2f} '
                f'shadow_fired={s["shadow_exit_logged"]} '
                f'-> {fname}'
            )
            # Persist updated state (the finalized addr was popped above)
            self._save_state_to_disk()
        except Exception as e:
            logger.warning(f'[PEAK_RECORDER] finalize err: {e}')


# Module-level singleton — one recorder for the entire bot
_RECORDER: Optional[PeakRecorder] = None


def get_recorder() -> PeakRecorder:
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = PeakRecorder()
    return _RECORDER
