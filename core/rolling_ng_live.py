"""Live accessor for the rolling NG scorer (2026-06-04).

Lazy-loads the nightly-retrained model from DATA_DIR, hot-reloads when the file changes
(so a fresh retrain is picked up without restart), and scores one live entry. Shared by
dip_scanner (shadow stamp) and main.py (retrain loop). Fail-open everywhere: no model,
disabled, or any error -> NEUTRAL/0.0 (never blocks, never raises into the scan loop).

MEASURE-ONLY: the stamp records a verdict; nothing gates on it yet. Flip to de-size only
after the live-wired model is confirmed against the backtest (train/serve-skew check).
"""
from __future__ import annotations
import os
from typing import Any, Dict, Tuple

_scorer = None
_mtime = None


def model_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "."), "rolling_ng_scorer")


def _enabled() -> bool:
    return os.environ.get("ROLLING_NG_SHADOW", "1").strip().lower() not in ("0", "false", "no", "off")


def get_scorer():
    """Return the loaded scorer, hot-reloading if the saved file changed. None if absent."""
    global _scorer, _mtime
    p = model_path() + ".joblib"
    if not os.path.exists(p):
        return None
    try:
        m = os.path.getmtime(p)
    except OSError:
        return None
    if _scorer is None or m != _mtime:
        from core.rolling_ng_scorer import RollingNGScorer
        s = RollingNGScorer().load(model_path())
        if s is not None:
            _scorer, _mtime = s, m
    return _scorer


def score_entry(entry_meta: Dict[str, Any]) -> Tuple[str, float]:
    """(verdict, proba) for one entry. NEUTRAL/0.0 if disabled or no model (fail-open)."""
    if not _enabled():
        return "NEUTRAL", 0.0
    s = get_scorer()
    if s is None:
        return "NEUTRAL", 0.0
    try:
        proba = s.score(entry_meta)
        return ("BLOCK" if proba >= s.threshold else "PASS"), float(proba)
    except Exception:
        return "NEUTRAL", 0.0
