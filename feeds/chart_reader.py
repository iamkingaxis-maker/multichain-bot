"""
Phase 6 of chart-reading rebuild — orchestrator + composite scoring.

Single entry point that runs all 5 chart-reading phases per signal
and returns a unified ChartContext object. The chart_reader replaces
the snapshot-feature approach with structural chart analysis.

ChartContext fields (full per-signal chart picture):
  candle_pattern_5m / 15m   — Phase 1 series summaries
  candle_confluence         — Phase 1 5m+15m confluence tag
  mtf_alignment             — Phase 2 multi-timeframe trend dict
  sr_5m / sr_15m            — Phase 3 support/resistance per timeframe
  vp_5m                     — Phase 4 volume profile
  pattern_5m / pattern_15m  — Phase 5 chart pattern detection per timeframe
  composite_score           — combined 0-100 score across all phases
  composite_verdict         — bullish / bearish / mixed / neutral
  composite_reasons         — list of plain-text reasons supporting verdict

Composite scoring is a deliberate "vote" across the 5 layers, not
a learned weighting. Each layer contributes to the score based on
how clear its signal is. Threshold for verdicts is conservative —
prefer "neutral" to false positives.

Scoring rules (each ranges -25 to +25, total -125 to +125, normalized to 0-100 around midpoint):
  candle_confluence:
    strong_bullish: +20, bullish: +10, mixed: -5, bearish: -10, strong_bearish: -20
  mtf_alignment:
    strong_bull: +25, bull: +15, mixed: 0, bear: -15, strong_bear: -25
    +/-5 boost if score == +/-4 (perfect alignment)
  sr_5m + sr_15m (combined):
    at_support both timeframes: +20 (multi-confirm support)
    at_support one timeframe:   +10
    at_resistance both:         -20
    at_resistance one:          -10
    below_broken_support:       -15 (additional)
  volume_profile_5m:
    current_above_poc:          +5
    at_hvn AND below current:   +10 (demand floor support)
    at_hvn AND above current:   -10 (resistance ceiling)
    in_lvn:                     0 (vacuum — direction unclear)
  chart_pattern_5m + 15m:
    bullish + confidence>=70:   +20
    bullish + confidence>=40:   +10
    bearish + confidence>=70:   -20
    bearish + confidence>=40:   -10
    neutral / none:             0

Final score = sum normalized to 0-100 (50 = neutral midpoint).

Verdict:
  >=75 → strong_bullish
  >=60 → bullish
  40-60 → neutral
  <=25 → strong_bearish
  <=40 → bearish
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from feeds.candle_utils import Candle
from feeds.chart_data import ChartData, assemble_chart_data
from feeds.candle_patterns import analyze_series, confluence
from feeds.multi_timeframe import alignment
from feeds.support_resistance import analyze as analyze_sr
from feeds.volume_profile import analyze as analyze_vp
from feeds.chart_patterns import detect_patterns
from feeds.gecko_ohlcv import GeckoTerminalClient

logger = logging.getLogger(__name__)


@dataclass
class ChartContext:
    """Unified chart-reading output for one signal at one moment."""
    pool_address: str = ""
    has_full_coverage: bool = False
    coverage_summary: str = ""

    # Phase 1 — candle patterns
    candle_5m: Dict[str, Any] = field(default_factory=dict)
    candle_15m: Dict[str, Any] = field(default_factory=dict)
    candle_confluence: str = "none"

    # Phase 2 — multi-timeframe alignment
    mtf: Dict[str, Any] = field(default_factory=dict)

    # Phase 3 — support / resistance
    sr_5m: Dict[str, Any] = field(default_factory=dict)
    sr_15m: Dict[str, Any] = field(default_factory=dict)

    # Phase 4 — volume profile
    vp_5m: Dict[str, Any] = field(default_factory=dict)

    # Phase 5 — chart patterns
    pattern_5m: Dict[str, Any] = field(default_factory=dict)
    pattern_15m: Dict[str, Any] = field(default_factory=dict)

    # Composite synthesis
    composite_score: float = 50.0
    composite_verdict: str = "neutral"
    composite_reasons: List[str] = field(default_factory=list)


# ── Composite scoring ────────────────────────────────────────────────

def _score_candle_confluence(c: str) -> tuple[float, Optional[str]]:
    if c == "strong_bullish": return +20, "candle_confluence=strong_bullish (+20)"
    if c == "bullish":        return +10, "candle_confluence=bullish (+10)"
    if c == "bearish":        return -10, "candle_confluence=bearish (-10)"
    if c == "strong_bearish": return -20, "candle_confluence=strong_bearish (-20)"
    if c == "mixed":          return -5, "candle_confluence=mixed (-5)"
    return 0, None


def _score_mtf(mtf: Dict[str, Any]) -> tuple[float, Optional[str]]:
    a = mtf.get("alignment", "flat")
    score_field = mtf.get("score", 0)
    base = 0
    if a == "strong_bull": base = 25
    elif a == "bull":      base = 15
    elif a == "bear":      base = -15
    elif a == "strong_bear": base = -25
    # +/-5 perfect-alignment bonus (4/4)
    if score_field == 4:  base += 5
    if score_field == -4: base -= 5
    if base != 0:
        return base, f"mtf={a} (score={score_field}, contrib={base:+d})"
    return 0, None


def _score_sr(sr_5m: Dict[str, Any], sr_15m: Dict[str, Any]) -> tuple[float, List[str]]:
    score = 0.0
    reasons = []
    at_supp_5 = sr_5m.get("at_support", False)
    at_supp_15 = sr_15m.get("at_support", False)
    at_res_5 = sr_5m.get("at_resistance", False)
    at_res_15 = sr_15m.get("at_resistance", False)
    broken_5 = sr_5m.get("below_broken_support", False)
    broken_15 = sr_15m.get("below_broken_support", False)

    if at_supp_5 and at_supp_15:
        score += 20
        reasons.append("at_support both timeframes (+20)")
    elif at_supp_5 or at_supp_15:
        score += 10
        which = "5m" if at_supp_5 else "15m"
        reasons.append(f"at_support {which} (+10)")
    elif at_res_5 and at_res_15:
        score -= 20
        reasons.append("at_resistance both timeframes (-20)")
    elif at_res_5 or at_res_15:
        score -= 10
        which = "5m" if at_res_5 else "15m"
        reasons.append(f"at_resistance {which} (-10)")

    if broken_5 or broken_15:
        score -= 15
        reasons.append("below_broken_support (-15)")

    return score, reasons


def _score_vp(vp: Dict[str, Any], current_price: Optional[float]) -> tuple[float, List[str]]:
    score = 0.0
    reasons = []
    if vp.get("current_above_poc"):
        score += 5
        reasons.append("above POC (+5)")
    nhb_pct = vp.get("nearest_hvn_below_pct")
    nha_pct = vp.get("nearest_hvn_above_pct")
    # at_hvn here means within 1% — Phase 4 sets this; we apply
    # demand-floor vs ceiling logic
    at_hvn = vp.get("at_hvn", False)
    if at_hvn:
        # Decide demand-floor vs ceiling based on which side is nearer
        below_close = nhb_pct is not None and nhb_pct <= 1.0
        above_close = nha_pct is not None and nha_pct <= 1.0
        if below_close and not above_close:
            score += 10
            reasons.append(f"at HVN below ({nhb_pct:.1f}% — demand floor +10)")
        elif above_close and not below_close:
            score -= 10
            reasons.append(f"at HVN above ({nha_pct:.1f}% — ceiling -10)")
        # Both sides close → in tight range, no clear bias
    return score, reasons


def _score_chart_pattern(p: Dict[str, Any], tf: str) -> tuple[float, Optional[str]]:
    name = p.get("pattern")
    direction = p.get("direction", "none")
    conf = p.get("confidence", 0)
    if not name or direction == "none" or direction == "neutral":
        return 0, None
    if direction == "bullish":
        if conf >= 70:
            return 20, f"chart_pattern_{tf}={name} conf={conf} (bullish +20)"
        if conf >= 40:
            return 10, f"chart_pattern_{tf}={name} conf={conf} (bullish +10)"
    if direction == "bearish":
        if conf >= 70:
            return -20, f"chart_pattern_{tf}={name} conf={conf} (bearish -20)"
        if conf >= 40:
            return -10, f"chart_pattern_{tf}={name} conf={conf} (bearish -10)"
    return 0, None


def compute_composite(
    candle_5m: Dict[str, Any],
    candle_15m: Dict[str, Any],
    candle_confluence: str,
    mtf: Dict[str, Any],
    sr_5m: Dict[str, Any],
    sr_15m: Dict[str, Any],
    vp_5m: Dict[str, Any],
    pattern_5m: Dict[str, Any],
    pattern_15m: Dict[str, Any],
) -> tuple[float, str, List[str]]:
    """Return (score 0-100, verdict, reasons list)."""
    raw = 0.0
    reasons: List[str] = []

    s, r = _score_candle_confluence(candle_confluence)
    raw += s
    if r: reasons.append(r)

    s, r = _score_mtf(mtf)
    raw += s
    if r: reasons.append(r)

    s, rs = _score_sr(sr_5m, sr_15m)
    raw += s
    reasons.extend(rs)

    cur_price = vp_5m.get("current_price")
    s, rs = _score_vp(vp_5m, cur_price)
    raw += s
    reasons.extend(rs)

    s, r = _score_chart_pattern(pattern_5m, "5m")
    raw += s
    if r: reasons.append(r)

    s, r = _score_chart_pattern(pattern_15m, "15m")
    raw += s
    if r: reasons.append(r)

    # Map raw [-125, +125] → score [0, 100], 50 = neutral
    # Linear interpolation, clamped
    score = max(0.0, min(100.0, 50.0 + raw * (50.0 / 125.0)))

    if score >= 75:
        verdict = "strong_bullish"
    elif score >= 60:
        verdict = "bullish"
    elif score <= 25:
        verdict = "strong_bearish"
    elif score <= 40:
        verdict = "bearish"
    else:
        verdict = "neutral"

    return round(score, 1), verdict, reasons


# ── Main entry point ─────────────────────────────────────────────────

async def read_chart(
    gt_client: GeckoTerminalClient,
    pool_address: str,
) -> ChartContext:
    """Run all chart-reading phases on a pool, return unified context.

    Single async call per signal. ~500ms wall-clock cost (4 parallel
    GT fetches + pure-logic phases). 60s GT cache means same pool
    re-read within a minute is instant.
    """
    if not pool_address:
        return ChartContext()

    try:
        cd = await assemble_chart_data(gt_client, pool_address)
    except Exception as e:
        logger.warning(f"[ChartReader] assemble failed for {pool_address[:12]}: {e}")
        return ChartContext(pool_address=pool_address)

    ctx = ChartContext(
        pool_address=pool_address,
        has_full_coverage=cd.has_full_coverage(),
        coverage_summary=cd.coverage_summary(),
    )

    # Phase 1 — candle patterns (5m + 15m)
    try:
        ctx.candle_5m = analyze_series(cd.candles_5m, lookback=5)
        ctx.candle_15m = analyze_series(cd.candles_15m, lookback=5)
        ctx.candle_confluence = confluence(ctx.candle_5m, ctx.candle_15m)
    except Exception as e:
        logger.debug(f"[ChartReader] candle phase err: {e}")

    # Phase 2 — multi-timeframe alignment
    try:
        ctx.mtf = alignment(cd.candles_1m, cd.candles_5m, cd.candles_15m, cd.candles_1h)
    except Exception as e:
        logger.debug(f"[ChartReader] mtf phase err: {e}")
        ctx.mtf = {}

    # Phase 3 — S/R (5m + 15m)
    try:
        ctx.sr_5m = analyze_sr(cd.candles_5m, pivot_n=3)
        ctx.sr_15m = analyze_sr(cd.candles_15m, pivot_n=3)
    except Exception as e:
        logger.debug(f"[ChartReader] sr phase err: {e}")

    # Phase 4 — volume profile (5m, 12h window)
    try:
        ctx.vp_5m = analyze_vp(cd.candles_5m, num_bins=20)
    except Exception as e:
        logger.debug(f"[ChartReader] vp phase err: {e}")

    # Phase 5 — chart patterns (5m + 15m)
    try:
        ctx.pattern_5m = detect_patterns(cd.candles_5m, min_confidence=40)
        ctx.pattern_15m = detect_patterns(cd.candles_15m, min_confidence=40)
    except Exception as e:
        logger.debug(f"[ChartReader] pattern phase err: {e}")

    # Composite
    try:
        score, verdict, reasons = compute_composite(
            ctx.candle_5m, ctx.candle_15m, ctx.candle_confluence,
            ctx.mtf, ctx.sr_5m, ctx.sr_15m, ctx.vp_5m,
            ctx.pattern_5m, ctx.pattern_15m,
        )
        ctx.composite_score = score
        ctx.composite_verdict = verdict
        ctx.composite_reasons = reasons
    except Exception as e:
        logger.debug(f"[ChartReader] composite err: {e}")

    return ctx
