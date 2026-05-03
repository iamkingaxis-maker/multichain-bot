"""
Phase 11 of chart-reading rebuild — reaccumulation-after-dump pattern.

Memecoin-specific. The classic "dead chart that's about to revive"
signature on a $1M-$100M token:
  1. Token dumped >= 50% from a recent peak.
  2. Then sat sideways for 4+ hours in a tight range with low volume
     (the "dead phase").
  3. Then volume started returning — buyers stepping back in.

This pattern produces the second leg, which is often a 2-3x. Detecting
it requires more than a snapshot — it needs to read the structure of
the hours before now.

Algorithm:
  Use 5m candles (12-hour window via 144 candles). Walk back to find:
    - peak: highest high in the first half of the window
    - trough: lowest low after the peak
    - drawdown_pct = (peak - trough) / peak
  Then check the post-trough phase:
    - duration: how many candles since trough
    - range_pct: width of the post-trough range as % of trough price
    - vol_decline_then_return: did volume drop and then start rising?

A token is in `reaccumulation` if:
  drawdown >= 30%, post-trough duration >= 24 candles (2h on 5m),
  post-trough range <= 15% of trough, AND last 6 candles' volume
  > avg of post-trough volume.

Outputs a verdict (REACCUMULATING / DEAD / DUMPING / NEUTRAL) and
the underlying numerics so downstream filters can tune sensitivity.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional

from feeds.candle_utils import Candle


def analyze(
    candles: List[Candle],
    *,
    min_drawdown_pct: float = 30.0,
    min_post_trough_candles: int = 24,
    max_post_trough_range_pct: float = 15.0,
    vol_return_window: int = 6,
) -> Dict[str, Any]:
    """Detect reaccumulation pattern on a 5m candle series.

    Returns:
      reaccum_verdict      REACCUMULATING / DEAD / DUMPING / NEUTRAL
      drawdown_pct         peak-to-trough drawdown %
      post_trough_candles  candles since trough
      post_trough_range_pct  width of post-trough range (% of trough)
      vol_ratio_recent_vs_post_trough_avg   1.0 = no return; >1.5 = active return
      peak_index, trough_index
      peak_price, trough_price
    """
    blank = {
        "reaccum_verdict": "?",
        "drawdown_pct": None,
        "post_trough_candles": 0,
        "post_trough_range_pct": None,
        "vol_ratio_recent_vs_post_trough_avg": None,
        "peak_index": None, "trough_index": None,
        "peak_price": None, "trough_price": None,
    }
    if not candles or len(candles) < 30:
        return blank

    # Find peak in first 60% of window, trough after it
    cutoff = max(int(len(candles) * 0.6), 6)
    first_half = candles[:cutoff]
    second_half = candles[cutoff:]
    if not first_half or not second_half:
        return blank

    peak_idx = 0
    peak_price = first_half[0].high
    for i, c in enumerate(first_half):
        if c.high > peak_price:
            peak_price = c.high
            peak_idx = i

    # Trough = lowest low AFTER peak
    after_peak = candles[peak_idx + 1 :]
    if not after_peak:
        return blank
    trough_offset = 0
    trough_price = after_peak[0].low
    for i, c in enumerate(after_peak):
        if c.low < trough_price:
            trough_price = c.low
            trough_offset = i
    trough_idx = peak_idx + 1 + trough_offset

    if peak_price <= 0 or trough_price <= 0:
        return blank

    drawdown_pct = (peak_price - trough_price) / peak_price * 100.0

    # Post-trough phase
    post = candles[trough_idx + 1 :]
    post_count = len(post)
    if post_count == 0:
        return {
            **blank,
            "drawdown_pct": round(drawdown_pct, 2),
            "peak_index": peak_idx,
            "trough_index": trough_idx,
            "peak_price": round(peak_price, 10),
            "trough_price": round(trough_price, 10),
            "reaccum_verdict": "DUMPING" if drawdown_pct >= min_drawdown_pct else "NEUTRAL",
        }

    post_high = max(c.high for c in post)
    post_low = min(c.low for c in post)
    post_range_pct = ((post_high - post_low) / max(trough_price, 1e-12)) * 100.0

    # Volume comparison: last `vol_return_window` candles vs the average of
    # all post-trough candles BEFORE the recent window.
    recent = post[-vol_return_window:] if post_count >= vol_return_window else post
    older_post = post[:-vol_return_window] if post_count > vol_return_window else []
    recent_avg_vol = sum(c.volume for c in recent) / max(len(recent), 1)
    older_avg_vol = sum(c.volume for c in older_post) / max(len(older_post), 1) if older_post else 0.0
    vol_ratio = (recent_avg_vol / older_avg_vol) if older_avg_vol > 0 else 0.0

    # Verdict
    verdict = "NEUTRAL"
    if drawdown_pct >= min_drawdown_pct:
        if post_count >= min_post_trough_candles:
            if post_range_pct <= max_post_trough_range_pct:
                # Sideways post-dump
                if vol_ratio >= 1.5:
                    verdict = "REACCUMULATING"  # volume returning
                else:
                    verdict = "DEAD"  # still dormant
            else:
                # Range too wide — still volatile / dumping
                verdict = "DUMPING"
        else:
            verdict = "DUMPING"

    return {
        "reaccum_verdict": verdict,
        "drawdown_pct": round(drawdown_pct, 2),
        "post_trough_candles": post_count,
        "post_trough_range_pct": round(post_range_pct, 2),
        "vol_ratio_recent_vs_post_trough_avg": round(vol_ratio, 3),
        "peak_index": peak_idx, "trough_index": trough_idx,
        "peak_price": round(peak_price, 10),
        "trough_price": round(trough_price, 10),
    }
