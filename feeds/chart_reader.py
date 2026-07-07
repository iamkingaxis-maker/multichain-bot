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

import asyncio
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
from feeds.trendlines import analyze as analyze_trendlines
from feeds.market_structure import analyze as analyze_structure
from feeds.liquidity_sweeps import analyze as analyze_sweeps
from feeds.stop_clusters import analyze as analyze_stop_clusters
from feeds.reaccumulation import analyze as analyze_reaccum
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

    # Phase 7 — trendlines & channels (5m / 15m / 1h)
    trendlines_5m: Dict[str, Any] = field(default_factory=dict)
    trendlines_15m: Dict[str, Any] = field(default_factory=dict)
    trendlines_1h: Dict[str, Any] = field(default_factory=dict)

    # Phase 8 — market structure / BOS / CHoCH (5m / 15m / 1h)
    structure_5m: Dict[str, Any] = field(default_factory=dict)
    structure_15m: Dict[str, Any] = field(default_factory=dict)
    structure_1h: Dict[str, Any] = field(default_factory=dict)

    # Phase 9 — liquidity sweeps (5m / 15m)
    sweeps_5m: Dict[str, Any] = field(default_factory=dict)
    sweeps_15m: Dict[str, Any] = field(default_factory=dict)

    # Phase 10 — stop clusters (5m / 15m)
    stop_clusters_5m: Dict[str, Any] = field(default_factory=dict)
    stop_clusters_15m: Dict[str, Any] = field(default_factory=dict)

    # Phase 11 — reaccumulation pattern (5m, 12h window)
    reaccum_5m: Dict[str, Any] = field(default_factory=dict)

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


def _score_structure(s: Dict[str, Any], tf: str) -> tuple[float, Optional[str]]:
    """Phase 8 verdict scoring.

    REVERSAL_UP   = +18  — CHoCH from down to up: highest-conviction long
    REVERSAL_DOWN = -18
    TREND_UP      = +8   — uptrend continuation, possibly with bullish BOS
    TREND_DOWN    = -8
    RANGING / NEUTRAL = 0
    """
    v = s.get("structure_verdict")
    if v == "REVERSAL_UP":
        return 18, f"structure_{tf}=REVERSAL_UP CHoCH (+18)"
    if v == "REVERSAL_DOWN":
        return -18, f"structure_{tf}=REVERSAL_DOWN CHoCH (-18)"
    if v == "TREND_UP":
        return 8, f"structure_{tf}=TREND_UP (+8)"
    if v == "TREND_DOWN":
        return -8, f"structure_{tf}=TREND_DOWN (-8)"
    return 0, None


def _score_sweeps(s: Dict[str, Any], tf: str) -> tuple[float, Optional[str]]:
    """Phase 9 verdict scoring.

    BULLISH_SWEEP = +14  — sweep low + reversal: classic accumulation entry
    BEARISH_SWEEP = -14
    NONE          = 0
    """
    v = s.get("sweep_verdict")
    if v == "BULLISH_SWEEP":
        return 14, f"sweep_{tf}=BULLISH_SWEEP (+14)"
    if v == "BEARISH_SWEEP":
        return -14, f"sweep_{tf}=BEARISH_SWEEP (-14)"
    return 0, None


def _score_trendline(tl: Dict[str, Any], tf: str) -> tuple[float, Optional[str]]:
    """Score per-timeframe trendline verdict.

    BREAKOUT_UP (volume-confirmed) = +12 — directional break, high signal
    BREAKDOWN  (volume-confirmed)  = -12
    PASS  (in ascending channel near support)   = +6 — bounce setup
    BLOCK (in descending channel near resistance) = -6 — rejection setup
    NEUTRAL = 0

    Per-timeframe so multi-TF agreement compounds. Three TFs × ±12 max
    = ±36 max contribution to composite — meaningful but not dominating.
    """
    v = tl.get("trendline_verdict")
    if v == "BREAKOUT_UP":
        return 12, f"trendline_{tf}=BREAKOUT_UP (+12)"
    if v == "BREAKDOWN":
        return -12, f"trendline_{tf}=BREAKDOWN (-12)"
    if v == "PASS":
        return 6, f"trendline_{tf}=PASS asc-channel-near-support (+6)"
    if v == "BLOCK":
        return -6, f"trendline_{tf}=BLOCK desc-channel-near-resistance (-6)"
    return 0, None


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
    trendlines_5m: Optional[Dict[str, Any]] = None,
    trendlines_15m: Optional[Dict[str, Any]] = None,
    trendlines_1h: Optional[Dict[str, Any]] = None,
    structure_5m: Optional[Dict[str, Any]] = None,
    structure_15m: Optional[Dict[str, Any]] = None,
    structure_1h: Optional[Dict[str, Any]] = None,
    sweeps_5m: Optional[Dict[str, Any]] = None,
    sweeps_15m: Optional[Dict[str, Any]] = None,
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

    # Phase 7 — trendlines on 5m / 15m / 1h
    for tl, tf in [(trendlines_5m, "5m"), (trendlines_15m, "15m"), (trendlines_1h, "1h")]:
        if tl:
            s, r = _score_trendline(tl, tf)
            raw += s
            if r: reasons.append(r)

    # Phase 8 — market structure on 5m / 15m / 1h
    for st, tf in [(structure_5m, "5m"), (structure_15m, "15m"), (structure_1h, "1h")]:
        if st:
            s, r = _score_structure(st, tf)
            raw += s
            if r: reasons.append(r)

    # Phase 9 — liquidity sweeps on 5m / 15m
    for sw, tf in [(sweeps_5m, "5m"), (sweeps_15m, "15m")]:
        if sw:
            s, r = _score_sweeps(sw, tf)
            raw += s
            if r: reasons.append(r)

    # Map raw [-269, +269] → score [0, 100], 50 = neutral
    # (Range expanded for Phase 7: ±36 (3TF×12), Phase 8: ±54 (3TF×18),
    # Phase 9: ±28 (2TF×14). Total new contribution ±118 added to base ±125.)
    score = max(0.0, min(100.0, 50.0 + raw * (50.0 / 269.0)))

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
    *,
    chart_data: Optional[ChartData] = None,
) -> ChartContext:
    """Run all chart-reading phases on a pool, return unified context.

    Single async call per signal. ~500ms wall-clock cost (4 parallel
    GT fetches + pure-logic phases). 60s GT cache means same pool
    re-read within a minute is instant.

    Pass `chart_data` if the caller has already assembled it for other
    derivations (e.g. dip_scanner uses the same candles for m1/range/
    vwap features); the chart-reader will reuse it instead of re-fetching.
    Without that path the same pool would be fetched twice per signal,
    doubling GT API pressure and producing rate-limit losses on the
    structural-feature side.
    """
    if not pool_address:
        return ChartContext()

    if chart_data is None:
        try:
            chart_data = await assemble_chart_data(gt_client, pool_address)
        except Exception as e:
            logger.warning(f"[ChartReader] assemble failed for {pool_address[:12]}: {e}")
            return ChartContext(pool_address=pool_address)
    cd = chart_data

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

    await asyncio.sleep(0)  # loop-unstarve (2026-07-07): yield between phases
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

    await asyncio.sleep(0)  # loop-unstarve
    # Phase 5 — chart patterns (5m + 15m)
    try:
        ctx.pattern_5m = detect_patterns(cd.candles_5m, min_confidence=40)
        ctx.pattern_15m = detect_patterns(cd.candles_15m, min_confidence=40)
    except Exception as e:
        logger.debug(f"[ChartReader] pattern phase err: {e}")

    await asyncio.sleep(0)  # loop-unstarve — before O(pivots^2) trendline fit
    # Phase 7 — trendlines & channels on all 3 timeframes
    try:
        ctx.trendlines_5m = analyze_trendlines(cd.candles_5m, pivot_n=3)
        ctx.trendlines_15m = analyze_trendlines(cd.candles_15m, pivot_n=3)
        ctx.trendlines_1h = analyze_trendlines(cd.candles_1h, pivot_n=2)
    except Exception as e:
        logger.debug(f"[ChartReader] trendline phase err: {e}")

    await asyncio.sleep(0)  # loop-unstarve — before 3-TF structure analysis
    # Phase 8 — market structure / BOS / CHoCH
    try:
        ctx.structure_5m = analyze_structure(cd.candles_5m, pivot_n=3)
        ctx.structure_15m = analyze_structure(cd.candles_15m, pivot_n=3)
        ctx.structure_1h = analyze_structure(cd.candles_1h, pivot_n=2)
    except Exception as e:
        logger.debug(f"[ChartReader] structure phase err: {e}")

    # Phase 9 — liquidity sweeps (reuses Phase 8 swings to avoid re-pivoting)
    try:
        ctx.sweeps_5m = analyze_sweeps(
            cd.candles_5m, pivot_n=3,
            swings=ctx.structure_5m.get("_swings"),
        )
        ctx.sweeps_15m = analyze_sweeps(
            cd.candles_15m, pivot_n=3,
            swings=ctx.structure_15m.get("_swings"),
        )
    except Exception as e:
        logger.debug(f"[ChartReader] sweeps phase err: {e}")

    await asyncio.sleep(0)  # loop-unstarve — before O(band^2) stop-cluster scan
    # Phase 10 — stop-cluster level detection
    try:
        ctx.stop_clusters_5m = analyze_stop_clusters(cd.candles_5m, pivot_n=2)
        ctx.stop_clusters_15m = analyze_stop_clusters(cd.candles_15m, pivot_n=2)
    except Exception as e:
        logger.debug(f"[ChartReader] stop-cluster phase err: {e}")

    # Phase 11 — reaccumulation pattern (5m only — needs the 12h window)
    try:
        ctx.reaccum_5m = analyze_reaccum(cd.candles_5m)
    except Exception as e:
        logger.debug(f"[ChartReader] reaccum phase err: {e}")

    await asyncio.sleep(0)  # loop-unstarve — before composite aggregation
    # Composite
    try:
        score, verdict, reasons = compute_composite(
            ctx.candle_5m, ctx.candle_15m, ctx.candle_confluence,
            ctx.mtf, ctx.sr_5m, ctx.sr_15m, ctx.vp_5m,
            ctx.pattern_5m, ctx.pattern_15m,
            ctx.trendlines_5m, ctx.trendlines_15m, ctx.trendlines_1h,
            ctx.structure_5m, ctx.structure_15m, ctx.structure_1h,
            ctx.sweeps_5m, ctx.sweeps_15m,
        )
        ctx.composite_score = score
        ctx.composite_verdict = verdict
        ctx.composite_reasons = reasons
    except Exception as e:
        logger.debug(f"[ChartReader] composite err: {e}")

    return ctx
