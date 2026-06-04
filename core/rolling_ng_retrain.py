"""Rolling NG scorer — nightly retrain from the bot's own trade log (2026-06-04).

The scorer must train on the BOT'S OWN closed trades (not the universe recorder) so the
feature keys match what dip_scanner stamps live at scoring time. Walk-forward validated
the method (AUC 0.66, tail-safe); this trains the live model on a trailing window.

build_training_set is pure (FIFO-match buys->sells, episode peak, never-green label) so it
is unit-testable; retrain_and_save is the IO wrapper main.py calls daily. Fail-safe: any
error leaves the previous saved model in place (never crashes the bot loop).
"""
from __future__ import annotations
import json
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple

NG_PEAK_PCT = 3.0   # never-green = episode peak < 3% (matches never_runner_peak_max)
DEFAULT_LOOKBACK_DAYS = 7


def build_training_set(trades: List[Dict[str, Any]]
                       ) -> Tuple[List[Dict[str, Any]], List[int], List[str]]:
    """FIFO-match buys->sells per (bot,address); one row per closed episode.
    Returns (X_rows=entry_meta dicts, y=never-green labels, groups=token addresses)."""
    buys = [t for t in trades if t.get("type") == "buy"]
    sells = [t for t in trades if t.get("type") == "sell" and t.get("pnl_pct") is not None]
    sb: Dict[Any, List[dict]] = defaultdict(list)
    for s in sells:
        sb[(s.get("bot_id"), s.get("address"))].append(s)
    for k in sb:
        sb[k].sort(key=lambda x: x.get("time") or "")
    used: Dict[Any, int] = defaultdict(int)
    X: List[Dict[str, Any]] = []
    y: List[int] = []
    groups: List[str] = []
    for b in sorted(buys, key=lambda x: x.get("time") or ""):
        key = (b.get("bot_id"), b.get("address"))
        legs = sb.get(key, [])
        bt = b.get("time") or ""
        frac = 0.0
        i = used[key]
        peak = 0.0
        nlegs = 0
        while i < len(legs) and frac < 0.999:
            leg = legs[i]
            if (leg.get("time") or "") < bt:
                i += 1
                continue
            if leg.get("pnl_pct") is None:
                i += 1
                continue
            frac += leg.get("sell_fraction") or 0.0
            pk = leg.get("peak_pnl_pct")
            if isinstance(pk, (int, float)):
                peak = max(peak, pk)
            nlegs += 1
            i += 1
        used[key] = i
        if nlegs == 0 or frac < 0.999:
            continue  # not a fully-closed episode -> no label yet
        em = b.get("entry_meta")
        if not isinstance(em, dict):
            continue
        X.append(em)
        y.append(1 if peak < NG_PEAK_PCT else 0)
        groups.append(str(b.get("address")))
    return X, y, groups


def _load_trades(data_dir: str) -> List[Dict[str, Any]]:
    path = os.path.join(data_dir, "trades.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return d if isinstance(d, list) else d.get("trades", [])


def _within_lookback(trades: List[Dict[str, Any]], lookback_days: int,
                     now_epoch: float) -> List[Dict[str, Any]]:
    # trade timestamps are ISO strings; compare lexically against a cutoff ISO date.
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(now_epoch - lookback_days * 86400))
    return [t for t in trades if (t.get("time") or "")[:10] >= cutoff]


def retrain_and_save(data_dir: str, out_path: str,
                     lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                     now_epoch: float | None = None) -> Dict[str, Any]:
    """Read trailing trade log, build set, train, save. Returns a status dict.
    Fail-safe: on any problem returns {trained: False, reason}, leaving prior model intact."""
    from core.rolling_ng_scorer import RollingNGScorer
    try:
        trades = _load_trades(data_dir)
        if not trades:
            return {"trained": False, "reason": "no_trade_log"}
        ne = now_epoch if now_epoch is not None else time.time()
        window = _within_lookback(trades, lookback_days, ne)
        X, y, groups = build_training_set(window)
        if len(X) < 80 or len(set(y)) < 2:
            return {"trained": False, "reason": f"insufficient (n={len(X)}, classes={len(set(y))})"}
        s = RollingNGScorer().train(X, y, groups)
        if s.clf is None:
            return {"trained": False, "reason": "train_failed"}
        s.save(out_path)
        return {"trained": True, "n": len(X), "ng_rate": round(sum(y) / len(y), 3),
                "n_feats": len(s.feats), "threshold": round(s.threshold, 4)}
    except Exception as e:  # never crash the caller
        return {"trained": False, "reason": f"error:{e}"}
