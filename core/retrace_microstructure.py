# core/retrace_microstructure.py
"""Retrace-continuation vs distribution-top micro-structure gate (2026-07-09).

From a 12-agent on-chain fleet over 554k trades / 334 tokens: the pump % does NOT
separate a retrace that resumes from one that tops — the tell is the on-chain
trade-flow shape at the retrace. Survivors of adversarial, forward-only,
union-counted, whale-robust testing:

  B) SELL-SIDE DISTRIBUTION (CONFIRMED, whale-robust, 78 tokens) — heavy AND
     accelerating sells into the entry = a top (48.8% continue vs 63.3%). The
     one live-blocking rule. Pure skip: worst case we pass on some continuations.
  C) DOLLAR NET-FLOW PERSISTENCE (WEAKENED corroborator) — dollars stepping back
     in and SUSTAINING (>=$300 across >=2 of 3 sub-windows, NOT a single tick —
     the Bullchuriki correction). Shadow first.

All forward-only: computed from the last ~60s of trades at the decision instant
(ref_ts = "now", since we fire on the dip). Trades: {kind:'buy'|'sell',
volume_usd:float, ts:ISO8601|epoch}. Pure; never raises.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Sequence


def _epoch(ts) -> Optional[float]:
    """ISO8601 or epoch -> epoch seconds. None on unparseable."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        from datetime import datetime
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _window(trades: Sequence[Dict[str, Any]], ref: float, lo: float, hi: float):
    """Trades with ref+lo <= ts < ref+hi (lo/hi are negative offsets, seconds)."""
    out = []
    for t in trades or ():
        e = _epoch(t.get("ts"))
        if e is None:
            continue
        d = e - ref
        if lo <= d < hi:
            out.append(t)
    return out


def _sum_usd(trades, kind: Optional[str] = None) -> float:
    s = 0.0
    for t in trades:
        if kind is not None and str(t.get("kind", "")).lower() != kind:
            continue
        try:
            v = float(t.get("volume_usd") or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            s += v
    return s


def sell_distribution_flag(trades, ref_ts,
                           sell_rate_min: float = 18.0,
                           traj_min: float = 1.0) -> Dict[str, Any]:
    """Step B (CONFIRMED AVOID). Block when sell $/s over the last 60s is heavy
    (>= sell_rate_min, ~q75) AND accelerating (last-30s / prior-30s >= traj_min):
    a holder distributing INTO the low -> ~coin-flip. FAIL-OPEN: too few trades
    to judge -> do not block. Returns {block, sell_rate_60, sell_traj, ...}."""
    ref = _epoch(ref_ts)
    if ref is None:
        return {"block": False, "reason": "no ref ts (fail-open)"}
    w60 = _window(trades, ref, -60.0, 0.0)
    if len(w60) < 3:
        return {"block": False, "reason": "too few trades (fail-open)",
                "sell_rate_60": None, "sell_traj": None}
    sell60 = _sum_usd(w60, "sell")
    sell_rate_60 = sell60 / 60.0
    early = _sum_usd(_window(trades, ref, -60.0, -30.0), "sell")
    late = _sum_usd(_window(trades, ref, -30.0, 0.0), "sell")
    sell_traj = late / early if early > 1e-9 else (2.0 if late > 0 else 0.0)
    block = (sell_rate_60 >= float(sell_rate_min)) and (sell_traj >= float(traj_min))
    return {"block": block, "sell_rate_60": round(sell_rate_60, 2),
            "sell_traj": round(sell_traj, 2), "n_trades_60": len(w60),
            "reason": ("heavy+accelerating sell distribution" if block
                       else "sells not distributing")}


def net_flow_persistence(trades, ref_ts,
                         cum_min: float = 300.0,
                         min_pos_subwins: int = 2) -> Dict[str, Any]:
    """Step C (WEAKENED corroborator, SHADOW first). Dollars stepping back in
    with PERSISTENCE: cum net-flow (buy$-sell$) over the last 60s >= cum_min AND
    net-positive in >= min_pos_subwins of three 20s sub-windows (not a single
    tick — the Bullchuriki correction). FAIL-OPEN on too few trades."""
    ref = _epoch(ref_ts)
    if ref is None:
        return {"confirm": False, "reason": "no ref ts"}
    w60 = _window(trades, ref, -60.0, 0.0)
    if len(w60) < 3:
        return {"confirm": False, "reason": "too few trades",
                "cum_nf_60": None, "pos_subwins": None}
    cum_nf = _sum_usd(w60, "buy") - _sum_usd(w60, "sell")
    pos = 0
    for k in range(3):
        sw = _window(trades, ref, -60.0 + 20.0 * k, -60.0 + 20.0 * (k + 1))
        if (_sum_usd(sw, "buy") - _sum_usd(sw, "sell")) > 0:
            pos += 1
    confirm = (cum_nf >= float(cum_min)) and (pos >= int(min_pos_subwins))
    return {"confirm": confirm, "cum_nf_60": round(cum_nf, 2),
            "pos_subwins": pos,
            "reason": ("sustained dollar inflow" if confirm
                       else "inflow insufficient/not persistent")}


def lp_rug_flag(meta) -> bool:
    """LP-drain rug flag (2026-07-09 CLOPY autopsy fleet). True when the entry
    meta shows LP was PULLED in the last 15 min: lp_event_verdict==REMOVE_15MIN
    AND lp_delta_15m_pct <= -15. This signal was present at every doomed CLOPY
    entry (-98.6% rug). Consumed as EXIT insurance only (TP1 sells 100% instead
    of 75% on flagged positions) — the entry-veto and size-derate variants were
    adversarially REFUTED (56% winner-kill / kills fat-tail winners). Pure;
    fail-CLOSED to False on missing/malformed meta (no flag = normal exits)."""
    try:
        if not meta:
            return False
        if str(meta.get("lp_event_verdict", "")).upper() != "REMOVE_15MIN":
            return False
        d = meta.get("lp_delta_15m_pct")
        return isinstance(d, (int, float)) and float(d) <= -15.0
    except Exception:
        return False


def retrace_micro_eval(trades, ref_ts,
                       sell_rate_min: float = 18.0, traj_min: float = 1.0,
                       cum_min: float = 300.0, min_pos_subwins: int = 2) -> Dict[str, Any]:
    """Combine. `avoid_block` = the shippable hard skip (Step B). `flow_confirm`
    = shadow corroborator (Step C). Caller: block on avoid_block (if enabled),
    log the rest to shadow. Pure; never raises."""
    b = sell_distribution_flag(trades, ref_ts, sell_rate_min, traj_min)
    c = net_flow_persistence(trades, ref_ts, cum_min, min_pos_subwins)
    return {"avoid_block": bool(b.get("block")), "flow_confirm": bool(c.get("confirm")),
            "sell": b, "flow": c}
