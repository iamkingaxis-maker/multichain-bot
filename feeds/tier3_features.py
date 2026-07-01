"""
Tier-3 entry-meta features for dip_scanner.

Narrow but potentially decisive features. All computed from data already
fetched (candles + recent_trades + liquidity_flow state). Each function
fail-opens (returns {} on bad input).

Features:
  1. compute_support_touches      → support_level_usd, support_touches_30m, support_strength
  2. compute_wick_body_ratios     → wick_body_5m_avg, wick_body_5m_max, upper_wick_dom
  3. compute_freq_derivative      → trades_per_sec_now, trades_per_sec_prior, freq_acceleration
  4. compute_net_flow_windows     → net_flow_15s_usd, net_flow_60s_usd, net_flow_5m_usd
  5. compute_hours_since_grad     → hours_since_graduation (numeric)

Note: 1h LP add/remove deltas — extend liquidity_flow.LiquidityFlowTracker
itself rather than this module, since it owns the state.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


def _parse_ts(ts: Any) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# =================================================================
# 1. Support level touches
# =================================================================
def compute_support_touches(
    candles_5m: Sequence[Any], tolerance_pct: float = 0.5
) -> Dict[str, Any]:
    """
    Identify the most-significant support level from recent swing lows,
    then count how many times price has touched within tolerance_pct.

    Approach: take the lowest swing low in the last ~30m (6 × 5m candles).
    A touch = a candle's low within tolerance_pct of that level.
    """
    if not candles_5m or len(candles_5m) < 6:
        return {}
    cs = list(candles_5m[-6:])
    # Swing-low detection on full series (need neighbors)
    swing_lows = []
    full = list(candles_5m)
    for i in range(1, len(full) - 1):
        if full[i].low < full[i - 1].low and full[i].low < full[i + 1].low:
            swing_lows.append(full[i].low)
    if not swing_lows:
        return {}
    # Most significant support = lowest of the recent swing lows
    support = min(swing_lows[-3:]) if len(swing_lows) >= 3 else min(swing_lows)
    if support <= 0:
        return {}
    # Count touches of any candle in last 6 within tolerance
    tol = support * (tolerance_pct / 100.0)
    touches = sum(1 for k in cs if abs(k.low - support) <= tol)
    cur = cs[-1].close
    pct_above_support = ((cur - support) / support * 100) if support > 0 else 0.0
    # Strength: 1 touch = strong (untested level holds), 3+ = weak (likely break)
    if touches <= 1:
        strength = "strong"
    elif touches == 2:
        strength = "moderate"
    else:
        strength = "weak"
    return {
        "support_level_usd": round(support, 8),
        "support_touches_30m": touches,
        "support_strength": strength,
        "pct_above_support": round(pct_above_support, 2),
    }


# =================================================================
# 2. Wick:body ratios — last 3 × 5m candles
# =================================================================
def compute_wick_body_ratios(candles_5m: Sequence[Any]) -> Dict[str, Any]:
    """
    Wick:body ratio = (high - low - |close - open|) / max(|close - open|, eps).
    Long wicks signal manipulation, illiquidity, or capitulation.
    Track upper-wick dominance separately — upper wicks on a dip = sellers
    rejecting bounces = bearish.
    """
    if not candles_5m or len(candles_5m) < 3:
        return {}
    cs = list(candles_5m[-3:])
    ratios = []
    upper_dom = []
    for k in cs:
        rng = k.high - k.low
        body = abs(k.close - k.open)
        wick = max(rng - body, 0.0)
        eps = max(body, k.close * 0.0001)
        if eps > 0:
            ratios.append(wick / eps)
        # Upper wick = high - max(open, close); lower wick = min(open, close) - low
        upper = k.high - max(k.open, k.close)
        lower = min(k.open, k.close) - k.low
        total_wick = max(upper + lower, 0.0001)
        upper_dom.append(upper / total_wick)
    if not ratios:
        return {}
    return {
        "wick_body_5m_avg": round(sum(ratios) / len(ratios), 3),
        "wick_body_5m_max": round(max(ratios), 3),
        "upper_wick_dom_5m_avg": round(sum(upper_dom) / len(upper_dom), 3),
    }


# =================================================================
# 3. Trade-frequency derivative (acceleration)
# =================================================================
def compute_freq_derivative(recent_trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Split trades into last-60s window vs the 60s before that.
    Compute trades-per-second in each, plus the ratio (acceleration).
    Ratio > 1.5 = strong momentum entering. < 0.5 = exhaustion.
    """
    if not recent_trades or len(recent_trades) < 4:
        return {}
    timestamped = []
    for t in recent_trades:
        ts = _parse_ts(t.get("ts"))
        if ts is not None:
            timestamped.append((ts, t.get("kind")))
    if len(timestamped) < 4:
        return {}
    anchor = max(t for t, _ in timestamped)
    n_last = sum(1 for ts, _ in timestamped if anchor - ts <= 60.0)
    n_prior = sum(1 for ts, _ in timestamped if 60.0 < anchor - ts <= 120.0)
    rate_last = n_last / 60.0
    rate_prior = n_prior / 60.0
    out: Dict[str, Any] = {
        "trades_per_sec_last60s": round(rate_last, 3),
        "trades_per_sec_prior60s": round(rate_prior, 3),
        "freq_n_last60s": n_last,
        "freq_n_prior60s": n_prior,
    }
    if rate_prior > 0:
        out["freq_acceleration"] = round(rate_last / rate_prior, 3)
    return out


# =================================================================
# 4. Net flow USD over multiple windows
# =================================================================
def compute_net_flow_windows(recent_trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Net dollar flow (buys - sells) over 15s, 60s, 5min windows.
    Dollar-weighted version of buy_pressure. Positive = inflow, negative = outflow.
    """
    if not recent_trades:
        return {}
    rows = []
    for t in recent_trades:
        ts = _parse_ts(t.get("ts"))
        if ts is None:
            continue
        v = float(t.get("volume_usd") or 0)
        kind = t.get("kind")
        sign = 1 if kind == "buy" else -1 if kind == "sell" else 0
        rows.append((ts, sign * v))
    if not rows:
        return {}
    anchor = max(t for t, _ in rows)
    out = {}
    for win_secs, label in ((15, "15s"), (60, "60s"), (300, "5m")):
        win = [v for ts, v in rows if anchor - ts <= win_secs]
        if win:
            net = sum(win)
            gross = sum(abs(v) for v in win)
            out[f"net_flow_{label}_usd"] = round(net, 2)
            out[f"net_flow_{label}_n"] = len(win)
            # 2026-07-01 FIX (4-agent diagnosis P3): the 15s imbalance on a
            # 1-2 trade window is pure noise that reads -1.0 at every genuine
            # flush (the last trades at a capitulation ARE sells) — it flipped
            # the nf15>=0 entry clause from inert (on stale ~2min data, bounce
            # already begun) to a family-killer on fresh data (blocked 6 of 11
            # badday bots; token "bull" was bought only by the 2 non-nf15 bots).
            # Emit the 15s imbalance only when the window has >=3 trades;
            # missing -> entry_gate + demand-turn fail OPEN (both None-safe).
            # usd/n stay emitted (informational; consumers use `or 0`).
            if gross > 0 and not (label == "15s" and len(win) < 3):
                out[f"net_flow_{label}_imbalance"] = round(net / gross, 3)
    return out


# =================================================================
# 5. Hours since graduation (numeric, complements categorical status)
# =================================================================
def compute_hours_since_grad(
    graduation_status: str, pair_age_hours: float
) -> Dict[str, Any]:
    """
    For just_graduated tokens, hours_since_graduation ≈ pair_age_hours
    (the pair on PumpSwap is created at graduation). For
    post_graduated_aged, same. For pre_graduation or established, no
    graduation event applies and we return -1.

    Bucket suggestions:
      0-2h    fresh-grad pump dynamics
      2-6h    early-grad consolidation
      6-24h   maturing
      24h+    aged grad
    """
    if graduation_status in ("just_graduated", "post_graduated_aged"):
        h = max(pair_age_hours, 0.0)
        if h < 2:
            bucket = "0-2h"
        elif h < 6:
            bucket = "2-6h"
        elif h < 24:
            bucket = "6-24h"
        else:
            bucket = "24h+"
        return {
            "hours_since_graduation": round(h, 2),
            "graduation_age_bucket": bucket,
        }
    return {
        "hours_since_graduation": -1.0,
        "graduation_age_bucket": "n/a",
    }
