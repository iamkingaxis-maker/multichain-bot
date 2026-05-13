"""
Tier-2 entry-meta features for dip_scanner.

These are computed from data we already fetch (candles + recent_trades).
Each function fail-opens (returns empty dict on bad input or insufficient data).

Features:
  1. compute_anchored_vwap_1h   → vwap_1h_usd, pct_above_vwap_1h
  2. compute_pct_off_peak       → pct_off_peak, minutes_since_peak
  3. compute_higher_low_5m      → higher_low_5m, hl_delta_pct, n_swing_lows_found
  4. compute_rsi_bb             → rsi_5m, rsi_15m, bb_pos_5m, bb_pos_15m
  5. compute_bundle_v2          → top10_buyer_within_60s_count,
                                   top10_buyer_time_spread_sec,
                                   bundle_v2_suspected
  6. compute_trade_size_shift   → buy_size_mean_60s, buy_size_stddev_60s,
                                   buy_size_max_60s, buy_size_trend_ratio,
                                   buy_size_max_trend
  7. compute_bottom_signature_v1 → sell_volume_decay_ratio_30s,
                                   time_since_local_low_s,
                                   lower_wick_ratio_5m,
                                   consec_higher_lows_1m
"""
from __future__ import annotations
from datetime import datetime, timezone
from math import sqrt
from typing import Any, Dict, List, Optional, Sequence


# =================================================================
# 1. Anchored VWAP — 1h window from 15m candles (4 most-recent)
# =================================================================
def compute_anchored_vwap_1h(candles_15m: Sequence[Any], current_price: float) -> Dict[str, Any]:
    """Anchor: 1h ago. Uses last 4 × 15m candles."""
    if not candles_15m or len(candles_15m) < 2 or current_price <= 0:
        return {}
    cs = list(candles_15m[-4:])
    num = den = 0.0
    for k in cs:
        typ = (k.high + k.low + k.close) / 3.0
        num += typ * k.volume
        den += k.volume
    if den <= 0:
        return {}
    vwap = num / den
    if vwap <= 0:
        return {}
    pct_above = (current_price / vwap - 1) * 100
    return {
        "vwap_1h_usd": round(vwap, 8),
        "pct_above_vwap_1h": round(pct_above, 2),
        "vwap_1h_candles": len(cs),
    }


# =================================================================
# 2. pct_off_peak + minutes_since_peak
# =================================================================
def compute_pct_off_peak(
    pc_h24_now: float,
    peak_h24_6h_pct: float,
    time_since_h24_peak_secs: Optional[float] = None,
) -> Dict[str, Any]:
    """
    pct_off_peak = pc_h24_now - peak_h24_6h_pct (delta in pct points).
    Negative means we're below peak (the normal case for a dip-buy).
    minutes_since_peak derived from time_since_h24_peak_secs (already
    in trajectory_features).
    """
    out: Dict[str, Any] = {
        "pct_off_peak": round(pc_h24_now - peak_h24_6h_pct, 2),
    }
    if time_since_h24_peak_secs is not None:
        out["minutes_since_peak"] = round(time_since_h24_peak_secs / 60.0, 1)
    return out


# =================================================================
# 3. Higher-low confirmation on 5m candles
# =================================================================
def compute_higher_low_5m(candles_5m: Sequence[Any]) -> Dict[str, Any]:
    """
    Identify swing lows: a 5m candle whose low is lower than both
    neighbors. Compare the two most-recent swing lows. If the more
    recent one is HIGHER than the prior one, that's a higher low.

    Need >=5 candles and >=2 swing lows to return non-empty.
    """
    if not candles_5m or len(candles_5m) < 5:
        return {}
    cs = list(candles_5m)
    lows = [k.low for k in cs]
    # Find swing-low indices: i where lows[i] < lows[i-1] AND lows[i] < lows[i+1].
    # Skip first/last (no neighbor).
    swing_lows = []
    for i in range(1, len(lows) - 1):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append((i, lows[i]))
    if len(swing_lows) < 2:
        return {"n_swing_lows_found": len(swing_lows)}
    # Take the two most-recent swing lows
    prev_i, prev_lo = swing_lows[-2]
    curr_i, curr_lo = swing_lows[-1]
    higher = curr_lo > prev_lo
    if prev_lo > 0:
        delta_pct = (curr_lo / prev_lo - 1) * 100
    else:
        delta_pct = 0.0
    return {
        "higher_low_5m": higher,
        "hl_delta_pct": round(delta_pct, 2),
        "n_swing_lows_found": len(swing_lows),
        "hl_curr_idx_from_end": len(cs) - 1 - curr_i,
        "hl_prev_idx_from_end": len(cs) - 1 - prev_i,
    }


# =================================================================
# 4. RSI(14) + Bollinger band position(20, 2)
# =================================================================
def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # Wilder smoothing — start with simple avg over first `period`
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _bb_pos(closes: List[float], period: int = 20, num_std: float = 2.0) -> Optional[float]:
    """Position in the band: 0 = at lower band, 1 = at upper band, 0.5 = at middle."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((c - mean) ** 2 for c in window) / period
    std = sqrt(var)
    if std == 0:
        return 0.5
    upper = mean + num_std * std
    lower = mean - num_std * std
    cur = closes[-1]
    rng = upper - lower
    if rng == 0:
        return 0.5
    return max(0.0, min(1.0, (cur - lower) / rng))


def compute_rsi_bb(candles_5m: Sequence[Any], candles_15m: Sequence[Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if candles_5m and len(candles_5m) >= 15:
        closes_5m = [k.close for k in candles_5m]
        rsi5 = _rsi(closes_5m, 14)
        bb5 = _bb_pos(closes_5m, 20, 2.0)
        if rsi5 is not None:
            out["rsi_5m"] = round(rsi5, 2)
        if bb5 is not None:
            out["bb_pos_5m"] = round(bb5, 3)
    if candles_15m and len(candles_15m) >= 15:
        closes_15m = [k.close for k in candles_15m]
        rsi15 = _rsi(closes_15m, 14)
        bb15 = _bb_pos(closes_15m, 20, 2.0)
        if rsi15 is not None:
            out["rsi_15m"] = round(rsi15, 2)
        if bb15 is not None:
            out["bb_pos_15m"] = round(bb15, 3)
    return out


# =================================================================
# 5. Bundle-v2 detector — top-10 buyer cluster timing
# =================================================================
def _parse_ts(ts: Any) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def compute_bundle_v2(
    recent_trades: Sequence[Dict[str, Any]],
    pair_age_hours: float,
) -> Dict[str, Any]:
    """
    Look at the top-10 BUYERS (by volume), check the time-spread of their
    first observed trade. If all 10 entered within the same 60s, that's a
    bundled-launch signature regardless of holder concentration.

    Requires `maker` field on trades (DexScreener primary, GT fallback strips
    it). Without it, returns empty dict (fail-open).
    """
    if not recent_trades:
        return {}
    buys = [t for t in recent_trades if t.get("kind") == "buy" and t.get("maker")]
    if len(buys) < 5:
        return {}
    # Aggregate per-maker volume + first-seen timestamp.
    per_maker_vol: Dict[str, float] = {}
    per_maker_first_ts: Dict[str, float] = {}
    for t in buys:
        m = str(t.get("maker", ""))
        v = float(t.get("volume_usd") or 0)
        ts = _parse_ts(t.get("ts"))
        per_maker_vol[m] = per_maker_vol.get(m, 0.0) + v
        if ts is not None:
            cur = per_maker_first_ts.get(m)
            if cur is None or ts < cur:
                per_maker_first_ts[m] = ts
    # Top 10 buyers by volume
    top10 = sorted(per_maker_vol.items(), key=lambda kv: -kv[1])[:10]
    top10_makers = [m for m, _ in top10]
    times = [per_maker_first_ts[m] for m in top10_makers if m in per_maker_first_ts]
    if len(times) < 3:
        return {}
    spread = max(times) - min(times)
    # How many of top-10 fall within first 60s of the OLDEST top-10 trade?
    base = min(times)
    within_60s = sum(1 for t in times if (t - base) <= 60.0)
    out = {
        "top10_buyer_time_spread_sec": round(spread, 1),
        "top10_buyer_within_60s_count": within_60s,
        "top10_buyer_n_with_ts": len(times),
    }
    # Suspected bundle: 8+ of top 10 within 60s AND token young (<6h)
    if within_60s >= 8 and pair_age_hours < 6.0:
        out["bundle_v2_suspected"] = True
    else:
        out["bundle_v2_suspected"] = False
    return out


# =================================================================
# 6. Trade-size distribution shift
# =================================================================
def compute_bottom_signature_v1(
    candles_1m: Sequence[Any],
    candles_5m: Sequence[Any],
) -> Dict[str, Any]:
    """Bottom-detection feature pack v1 (SHADOW 2026-05-13).

    Four universal-coverage features designed to fire on REAL bottoms and
    not knife-catches. All compute from 1m and 5m candles we already fetch
    — no extra API calls, ~100% coverage expected.

    1. sell_volume_decay_ratio_30s:
       last 1m volume / mean of prior 5 × 1m volume.
       <0.5 = activity has dried up (sellers exhausted)
       >1.5 = volume surge (could be capitulation OR new selling)
       Interpretation requires pairing with candle color.

    2. time_since_local_low_s:
       seconds since the lowest 1m low in the last 30 minutes.
       0 = just made the local low (still falling).
       >300 = price has held above the local low for >5 minutes (bottom
       forming).

    3. lower_wick_ratio_5m:
       last 5m candle's lower wick divided by its body.
       >2 = clear lower-wick rejection (long wick, small body).
       <1 = either no wick OR wick smaller than body (continuation, not
       rejection).

    4. consec_higher_lows_1m:
       longest streak of consecutive higher-lows ending at the most-recent
       1m candle (looking back up to 5 bars).
       3+ = clear reversal structure forming.
       0 = no structure (last bar made a lower low).

    Fail-open: returns empty dict on bad input.
    """
    out: Dict[str, Any] = {}

    # 1. sell_volume_decay_ratio_30s
    try:
        if candles_1m and len(candles_1m) >= 6:
            last_vol = float(candles_1m[-1].volume or 0)
            prev5 = candles_1m[-6:-1]
            prev5_mean = sum(float(c.volume or 0) for c in prev5) / 5.0
            if prev5_mean > 0:
                out["sell_volume_decay_ratio_30s"] = round(last_vol / prev5_mean, 3)
    except Exception:
        pass

    # 2. time_since_local_low_s
    try:
        if candles_1m and len(candles_1m) >= 5:
            window = list(candles_1m[-30:])
            if window:
                min_idx = 0
                min_low = window[0].low
                for i, c in enumerate(window):
                    if c.low < min_low:
                        min_low = c.low
                        min_idx = i
                idx_from_end = len(window) - 1 - min_idx
                out["time_since_local_low_s"] = idx_from_end * 60
    except Exception:
        pass

    # 3. lower_wick_ratio_5m
    try:
        if candles_5m and len(candles_5m) >= 1:
            c = candles_5m[-1]
            body = abs(float(c.close) - float(c.open))
            lower_wick = min(float(c.open), float(c.close)) - float(c.low)
            if body > 1e-12:
                out["lower_wick_ratio_5m"] = round(lower_wick / body, 2)
            else:
                denom = max(float(c.close) * 0.001, 1e-9)
                out["lower_wick_ratio_5m"] = round(lower_wick / denom, 2)
    except Exception:
        pass

    # 4. consec_higher_lows_1m (count streak ending at most-recent bar)
    try:
        if candles_1m and len(candles_1m) >= 2:
            window = list(candles_1m[-6:])
            count = 0
            for i in range(len(window) - 1, 0, -1):
                if window[i].low > window[i - 1].low:
                    count += 1
                else:
                    break
            out["consec_higher_lows_1m"] = count
    except Exception:
        pass

    return out


def compute_trade_size_shift(recent_trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Split buys into last-60s vs prior-60s windows. Compute mean/stddev/max
    in each, plus trend ratios. Fail-open if timestamps unavailable.
    """
    if not recent_trades:
        return {}
    buys = [t for t in recent_trades if t.get("kind") == "buy"]
    if len(buys) < 4:
        return {}
    timestamped = []
    for t in buys:
        ts = _parse_ts(t.get("ts"))
        v = float(t.get("volume_usd") or 0)
        if ts is not None and v > 0:
            timestamped.append((ts, v))
    if len(timestamped) < 4:
        return {}
    # Most recent timestamp = anchor
    anchor = max(t for t, _ in timestamped)
    last_60 = [v for ts, v in timestamped if anchor - ts <= 60.0]
    prior_60 = [v for ts, v in timestamped if 60.0 < anchor - ts <= 120.0]

    def _stats(vs: List[float]):
        if not vs:
            return (0, 0.0, 0.0, 0.0)
        m = sum(vs) / len(vs)
        var = sum((v - m) ** 2 for v in vs) / len(vs)
        return (len(vs), m, sqrt(var), max(vs))

    n_last, mean_last, std_last, max_last = _stats(last_60)
    n_prior, mean_prior, std_prior, max_prior = _stats(prior_60)
    out = {
        "buy_size_n_last60s": n_last,
        "buy_size_mean_last60s": round(mean_last, 2),
        "buy_size_stddev_last60s": round(std_last, 2),
        "buy_size_max_last60s": round(max_last, 2),
        "buy_size_n_prior60s": n_prior,
        "buy_size_mean_prior60s": round(mean_prior, 2),
        "buy_size_max_prior60s": round(max_prior, 2),
    }
    if mean_prior > 0 and n_prior >= 2:
        out["buy_size_mean_trend"] = round(mean_last / mean_prior, 3)
    if max_prior > 0 and n_prior >= 2:
        out["buy_size_max_trend"] = round(max_last / max_prior, 3)
    return out
