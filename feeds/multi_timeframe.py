"""
Phase 2 of chart-reading rebuild — multi-timeframe trend alignment.

For each timeframe (1m, 5m, 15m, 1h), compute a directional verdict:
  bull   — EMA9 > EMA21 AND EMA9 slope is positive (rising)
  bear   — EMA9 < EMA21 AND EMA9 slope is negative (falling)
  flat   — anything else (sideways / mixed)

Then aggregate across timeframes into a single alignment score
(0-4) and a per-timeframe breakdown.

The rule a chart trader follows: "the safest entries are when ALL
timeframes agree." If 1m, 5m, 15m, 1h are all bullish, the trade
has support across every level a swing-or-day trader looks at. If
1m is bullish but 1h is bearish, you're fighting the higher
timeframe — the bounce is corrective inside a downtrend.

Why EMA9/EMA21:
  - EMA9 captures recent direction (last ~9 candles weighted)
  - EMA21 captures the broader timeframe trend
  - The cross between them is the standard "fast vs slow" trend
    signal across crypto + traditional markets
  - Slope of EMA9 disambiguates "trending" from "consolidating"
    when EMA9 ≈ EMA21

Why the aggregate score (0-4):
  - 4/4 — all timeframes bullish ("perfect alignment", strongest
    setup)
  - 3/4 — typically the "missing" timeframe is 1m (noisy) — still
    a strong trend on bigger timeframes
  - 2/4 — split / contradictory signals; high-uncertainty
  - 1/4 — only one timeframe bullish, others bearish or flat —
    counter-trend entry
  - 0/4 — no timeframe showing bullish trend; avoid

Used as both a feature in entry_meta and (later) as a gate inside
filter_two_pattern's evaluation.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional

from feeds.candle_utils import Candle


def _ema(values: List[float], period: int) -> List[float]:
    """Return the rolling EMA series — one value per input value.
    First period values use simple-mean seeding so the EMA series
    starts close to the actual data instead of biased toward the
    first input.
    """
    if not values:
        return []
    ema_series: List[float] = []
    if len(values) <= period:
        # Series shorter than period — return cumulative SMA per index.
        running = 0.0
        for i, v in enumerate(values):
            running += v
            ema_series.append(running / (i + 1))
        return ema_series
    # SMA seed for first `period` values
    sma_seed = sum(values[:period]) / period
    ema_series = [sma_seed] * period
    alpha = 2.0 / (period + 1)
    for v in values[period:]:
        ema_series.append(alpha * v + (1 - alpha) * ema_series[-1])
    return ema_series


def trend_for_timeframe(candles: List[Candle]) -> Dict[str, Any]:
    """Return a verdict + supporting numbers for a single timeframe.

    verdict: 'bull' | 'bear' | 'flat'
    ema9, ema21: latest values
    slope_pct: % change in EMA9 over the last 5 candles (or available)
    sample_n: number of candles available
    """
    if not candles:
        return {
            "verdict": "flat", "ema9": None, "ema21": None,
            "slope_pct": None, "sample_n": 0,
        }

    closes = [c.close for c in candles]
    if len(closes) < 5:
        # Not enough data to compute even a short EMA reliably
        return {
            "verdict": "flat", "ema9": None, "ema21": None,
            "slope_pct": None, "sample_n": len(closes),
        }

    e9 = _ema(closes, 9)
    e21 = _ema(closes, 21)
    ema9 = e9[-1]
    ema21 = e21[-1]

    # Slope: EMA9 % change over the last 5 candles
    slope_pct = None
    if len(e9) >= 5 and e9[-5] > 0:
        slope_pct = (ema9 / e9[-5] - 1) * 100

    # Verdict
    if ema21 > 0:
        diff_pct = (ema9 - ema21) / ema21 * 100
    else:
        diff_pct = 0.0

    # Threshold for "ema9 vs ema21 is meaningful": at least 0.2%.
    # Below that, EMAs are basically tied — verdict = flat regardless
    # of slope. This avoids tagging consolidating sideways action as
    # a trend.
    if abs(diff_pct) < 0.2:
        verdict = "flat"
    elif diff_pct > 0 and (slope_pct is None or slope_pct >= 0):
        verdict = "bull"
    elif diff_pct < 0 and (slope_pct is None or slope_pct <= 0):
        verdict = "bear"
    else:
        # ema9 above ema21 but slope negative (or vice versa) — mixed
        verdict = "flat"

    return {
        "verdict": verdict,
        "ema9": round(ema9, 8),
        "ema21": round(ema21, 8),
        "slope_pct": round(slope_pct, 3) if slope_pct is not None else None,
        "sample_n": len(closes),
    }


def alignment(
    candles_1m: List[Candle],
    candles_5m: List[Candle],
    candles_15m: List[Candle],
    candles_1h: List[Candle],
) -> Dict[str, Any]:
    """Aggregate trend verdicts across all four timeframes.

    Returns:
      bull_count    — count of timeframes with verdict='bull'
      bear_count    — count of timeframes with verdict='bear'
      flat_count    — count of timeframes with verdict='flat'
      score         — bull_count - bear_count, range [-4, +4]
      verdicts      — dict of timeframe → verdict
      details       — dict of timeframe → full trend dict
      alignment     — 'strong_bull' if bull>=3 and bear==0
                    | 'bull' if bull>=2 and bear==0
                    | 'mixed' if bull and bear both >=1
                    | 'bear' if bear>=2 and bull==0
                    | 'strong_bear' if bear>=3 and bull==0
                    | 'flat' if all flat
    """
    details = {
        "1m": trend_for_timeframe(candles_1m),
        "5m": trend_for_timeframe(candles_5m),
        "15m": trend_for_timeframe(candles_15m),
        "1h": trend_for_timeframe(candles_1h),
    }
    verdicts = {tf: d["verdict"] for tf, d in details.items()}
    bull = sum(1 for v in verdicts.values() if v == "bull")
    bear = sum(1 for v in verdicts.values() if v == "bear")
    flat = sum(1 for v in verdicts.values() if v == "flat")

    if bull >= 3 and bear == 0:
        align = "strong_bull"
    elif bull >= 2 and bear == 0:
        align = "bull"
    elif bear >= 3 and bull == 0:
        align = "strong_bear"
    elif bear >= 2 and bull == 0:
        align = "bear"
    elif bull >= 1 and bear >= 1:
        align = "mixed"
    else:
        align = "flat"

    return {
        "bull_count": bull,
        "bear_count": bear,
        "flat_count": flat,
        "score": bull - bear,
        "verdicts": verdicts,
        "alignment": align,
        "details": details,
    }
